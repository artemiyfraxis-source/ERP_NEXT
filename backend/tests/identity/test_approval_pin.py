"""Разрешительный ПИН (владелец 29.10.2026, identity/approval_pin.py): свой второй ПИН, не PIN входа. Задаёт и меняет его
только сам сотрудник в своей карточке (текущий пароль или PIN входа), администратор только сбрасывает в «Сотрудниках»;
подтверждение — под пределами входа по PIN."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.application import (
    LOGIN_FAIL_LIMIT, ApproverRefused, AuthenticationError, AuthenticationRateLimited, PinRefused,
)
from app.modules.identity.approval_pin import confirm_with_approval_pin
from app.modules.identity.domain import IdentityUser, hash_password, hash_pin, verify_password
from app.modules.identity.infrastructure import (
    IdentityCredentialRow, IdentityLoginThrottleRow, SqlAlchemyIdentityUnitOfWork, provision_user, seed_level_matrix,
)
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

NOW = datetime(2026, 10, 29, 10, 0)  # naive — read as UTC (shared/clock.as_utc)
PASSWORD = "worker-password-1"
LOGIN_PIN = "135790"
APPROVAL = "482913"
WORKER = UUID("72000000-0000-0000-0000-000000000001")  # Зингер, коммерческий
OWNER = UUID("72000000-0000-0000-0000-000000000002")  # суперадминистратор
ADMIN = UUID("72000000-0000-0000-0000-000000000003")  # администратор (не супер)
NO_PIN = UUID("72000000-0000-0000-0000-000000000004")  # входит только по паролю: PIN входа не назначен
CLIENT = "client:test"


def _user(user_id: UUID, login: str, name: str, level: str) -> IdentityUser:
    return IdentityUser(
        id=user_id, login=login, email=f"{login}@example.test", name=name, department="Офис", role="staff", site=None,
        active=True, access_level=level,
    )


def _principal(user_id: UUID, name: str, level: str) -> Principal:
    return Principal(
        actor=Actor(id=user_id, name=name, department="Офис"), email=f"{name}@example.test", roles=frozenset({level}),
        permissions={},
    )


ZINGER = _principal(WORKER, "Зингер", "commercial")
ARTEMIY = _principal(OWNER, "Артемий", "superadmin")
ADMIN_P = _principal(ADMIN, "Админ", "admin")


@pytest.fixture()
def env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    password, pin = hash_password(PASSWORD), hash_pin(LOGIN_PIN)
    with sessions() as session:
        for user, login_pin in (
            (_user(WORKER, "zinger", "Зингер", "commercial"), pin),
            (_user(OWNER, "artemiy", "Артемий", "superadmin"), pin),
            (_user(ADMIN, "admin2", "Админ", "admin"), pin),
            (_user(NO_PIN, "nopin", "Беспинов", "commercial"), None),
        ):
            provision_user(session, user, password, pin_hash=login_pin)
        seed_level_matrix(session)
        session.commit()
    previous = getattr(app.state, "identity_uow_factory", None)
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: ZINGER
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    if previous is None:
        del app.state.identity_uow_factory
    else:
        app.state.identity_uow_factory = previous
    engine.dispose()


def _as(principal: Principal) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: principal


def _set(client, current: str = PASSWORD, pin: str = APPROVAL):
    return client.post("/api/auth/approval-pin", json={"current": current, "pin": pin})


def _credential(sessions, user_id: UUID = WORKER) -> IdentityCredentialRow:
    with sessions() as session:
        return session.get(IdentityCredentialRow, user_id)


def _events(sessions, event_type: str) -> list[AuditEventRow]:
    with sessions() as session:
        return list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == event_type)))


def _failures(sessions, key: str) -> int:
    with sessions() as session:
        row = session.get(IdentityLoginThrottleRow, key)
        return row.failures if row is not None else 0


def _confirm(sessions, pin: str, user_id: UUID = WORKER, section: str | None = None):
    return confirm_with_approval_pin(
        user_id, pin, "noupd", NOW, SqlAlchemyIdentityUnitOfWork(sessions), client_key=CLIENT, section=section,
    )


# ── The own card: set and change ──────────────────────────────────────────────────────────────────────


def test_the_person_sets_it_with_the_password_and_the_card_shows_only_set_and_when(env):
    client, sessions = env
    before = client.get("/api/auth/approval-pin")
    made = _set(client)
    after = client.get("/api/auth/approval-pin")

    assert before.json() == {"set": False, "set_at": None}
    assert made.status_code == 200, made.text
    assert made.json() == after.json() == {"set": True, "set_at": "2026-10-29T10:00:00+00:00"}
    assert APPROVAL not in made.text
    stored = _credential(sessions)
    assert verify_password(APPROVAL, stored.approval_pin_hash) and stored.approval_pin_hash.startswith("$argon2id$")
    assert verify_password(LOGIN_PIN, stored.pin_hash)  # the login PIN is untouched
    [event] = _events(sessions, "identity.approval_pin_set")
    assert (event.aggregate_id, event.actor_name, event.details) == (WORKER, "Зингер", {"login": "zinger", "name": "Зингер"})
    assert APPROVAL not in str(event.details)


def test_the_login_pin_also_confirms_and_a_change_is_journalled_as_such(env):
    client, sessions = env
    assert _set(client).status_code == 200
    changed = _set(client, current=LOGIN_PIN, pin="7654321")
    same = _set(client, pin="7654321")

    assert changed.status_code == 200, changed.text
    assert verify_password("7654321", _credential(sessions).approval_pin_hash)
    assert len(_events(sessions, "identity.approval_pin_changed")) == 1
    assert same.status_code == 422 and same.json()["detail"] == "Новый разрешительный ПИН совпадает с текущим."


def test_it_must_differ_from_the_login_pin(env):
    client, sessions = env
    refused = _set(client, pin=LOGIN_PIN)

    assert refused.status_code == 422 and refused.json()["detail"] == "Разрешительный ПИН должен отличаться от PIN входа."
    assert _credential(sessions).approval_pin_hash is None


@pytest.mark.parametrize("pin", ["12345", "123456789", "12a456", "", "١٢٣٤٥٦"])
def test_the_policy_is_the_login_pins(env, pin):
    client, sessions = env
    refused = _set(client, pin=pin or " ")

    assert refused.status_code == 422 and refused.json()["detail"] == "Разрешительный ПИН — 6–8 цифр."
    assert _failures(sessions, "login:zinger") == 0  # the policy is checked before the current secret


def test_a_wrong_current_secret_is_a_failed_sign_in_and_a_series_locks(env):
    client, sessions = env
    wrong = _set(client, current="not-my-password")

    assert wrong.status_code == 422 and wrong.json()["detail"] == "Текущий пароль или PIN входа указан неверно."
    assert _failures(sessions, "login:zinger") == 1
    assert _events(sessions, "identity.login_failed")[0].details["method"] == "approval_pin_set"
    for _ in range(LOGIN_FAIL_LIMIT - 1):
        _set(client, current="000000")
    locked = _set(client)

    assert locked.status_code == 429
    assert _credential(sessions).approval_pin_hash is None
    assert _events(sessions, "identity.login_rate_limited")[0].details["method"] == "approval_pin_set"


def test_a_person_without_a_login_pin_confirms_with_the_password(env):
    client, sessions = env
    _as(_principal(NO_PIN, "Беспинов", "commercial"))

    assert _set(client, current=LOGIN_PIN).status_code == 422
    assert _set(client).status_code == 200
    assert _credential(sessions, NO_PIN).approval_pin_set_at is not None


def test_only_the_own_pin_is_reachable(env):
    """The person is the session's: a body naming someone else changes nothing for them."""
    client, sessions = env
    made = client.post("/api/auth/approval-pin", json={"current": PASSWORD, "pin": APPROVAL, "user_id": str(OWNER)})

    assert made.status_code == 200
    assert _credential(sessions, OWNER).approval_pin_hash is None and _credential(sessions).approval_pin_hash is not None


# ── «Сотрудники»: the administrator only resets ───────────────────────────────────────────────────────


def test_the_staff_list_shows_set_and_when_never_the_pin(env):
    client, _ = env
    _set(client)
    _as(ARTEMIY)
    users = {user["login"]: user for user in client.get("/api/auth/staff").json()["users"]}

    assert (users["zinger"]["approval_pin_set"], users["zinger"]["approval_pin_set_at"]) == (True, "2026-10-29T10:00:00+00:00")
    assert (users["artemiy"]["approval_pin_set"], users["artemiy"]["approval_pin_set_at"]) == (False, None)
    assert not any("hash" in key for key in users["zinger"])


def test_an_administrator_resets_it_journalled_and_the_person_sets_a_new_one(env):
    client, sessions = env
    _set(client)
    _as(ADMIN_P)
    reset = client.delete(f"/api/auth/staff/{WORKER}/approval-pin")
    again = client.delete(f"/api/auth/staff/{WORKER}/approval-pin")

    assert reset.status_code == 200 and reset.json() == {"reset": True, "set": False, "set_at": None}
    assert again.json()["reset"] is False
    stored = _credential(sessions)
    assert (stored.approval_pin_hash, stored.approval_pin_set_at) == (None, None)
    events = _events(sessions, "identity.approval_pin_reset")
    assert [(event.actor_name, event.details["was"]) for event in events] == [("Админ", "задан"), ("Админ", "не задан")]
    with pytest.raises(PinRefused):
        _confirm(sessions, APPROVAL)
    _as(ZINGER)
    assert _set(client).status_code == 200


def test_not_an_administrator_cannot_reset_and_an_admin_cannot_reset_a_superadmins(env):
    client, sessions = env
    _as(ARTEMIY)
    assert _set(client).status_code == 200
    _as(ZINGER)
    refused = client.delete(f"/api/auth/staff/{OWNER}/approval-pin")
    _as(ADMIN_P)
    admin = client.delete(f"/api/auth/staff/{OWNER}/approval-pin")
    _as(ARTEMIY)
    own = client.delete(f"/api/auth/staff/{OWNER}/approval-pin")

    assert refused.status_code == 403
    assert admin.status_code == 403 and "только суперадминистратор" in admin.json()["detail"]
    assert own.status_code == 200 and own.json()["reset"] is True
    assert len(_events(sessions, "identity.approval_pin_reset")) == 1


# ── Confirm: the generic permit ───────────────────────────────────────────────────────────────────────


def test_confirm_right_wrong_and_lockout(env):
    client, sessions = env
    _set(client)

    assert _confirm(sessions, APPROVAL).id == WORKER
    with pytest.raises(AuthenticationError, match="Неверный разрешительный ПИН"):
        _confirm(sessions, LOGIN_PIN)  # the login PIN is not the approval PIN
    assert _failures(sessions, "approval:zinger") == 1
    for _ in range(LOGIN_FAIL_LIMIT - 1):
        with pytest.raises(AuthenticationError):
            _confirm(sessions, "000000")
    with pytest.raises(AuthenticationRateLimited, match="Подождите 10 минут"):
        _confirm(sessions, APPROVAL)
    failed = _events(sessions, "identity.login_failed")
    assert len(failed) == LOGIN_FAIL_LIMIT and {event.details["method"] for event in failed} == {"noupd"}
    [locked] = _events(sessions, "identity.login_rate_limited")
    assert (locked.details["method"], locked.details["limited_by"]) == ("noupd", "login")
    # The login itself is not locked by approval failures (its own account counter).
    assert _failures(sessions, "login:zinger") == 0


def test_a_right_pin_clears_the_account_counter(env):
    client, sessions = env
    _set(client)
    with pytest.raises(AuthenticationError):
        _confirm(sessions, "000000")
    _confirm(sessions, APPROVAL)

    assert _failures(sessions, "approval:zinger") == 0


def test_confirm_without_a_pin_or_the_section_moves_no_counter(env):
    _, sessions = env
    with pytest.raises(PinRefused, match="не задан разрешительный ПИН — его задают в личном кабинете"):
        _confirm(sessions, APPROVAL)
    with pytest.raises(ApproverRefused):
        _confirm(sessions, APPROVAL, section="ship_no_upd")  # commercial without the section
    with pytest.raises(ApproverRefused):
        _confirm(sessions, APPROVAL, user_id=UUID(int=7))

    assert _failures(sessions, "approval:zinger") == 0
