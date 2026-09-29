"""Вход по PIN и «Режим админа» (SUPPLYMAP Portal.gs portalLogin / portalRevealPin_ / portalLoginBlocked_ /
portalToggleAdmin, Staff.gs portalStaffGet `pinSet` / portalStaffSave `pin`)."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.application import set_pin_reveal
from app.modules.identity.domain import (
    ADMIN_MODE_EMAIL,
    ROLE_DEFAULTS,
    ROLE_SEED,
    IdentityUser,
    IdentityValidationError,
    hash_password,
    hash_pin,
    new_pin,
    validate_pin,
)
from app.modules.identity.infrastructure import (
    IdentityCredentialRow,
    IdentityUserRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_role_matrix,
)
from app.modules.requests.api import moscow_now
from app.shared.persistence import AuditEventRow, create_schema


NOW = datetime(2026, 9, 29, 10, 0)
ADMIN_ID = UUID("40000000-0000-0000-0000-000000000001")
BUYER_ID = UUID("40000000-0000-0000-0000-000000000002")
NEWBIE_ID = UUID("40000000-0000-0000-0000-000000000003")
VICTOR_ID = UUID("40000000-0000-0000-0000-000000000004")
GONE_ID = UUID("40000000-0000-0000-0000-000000000005")


# ── Domain ─────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pin", ["123456", "1234567", "12345678"])
def test_pin_policy_accepts_six_to_eight_digits(pin):
    assert validate_pin(pin) == pin


@pytest.mark.parametrize("pin", ["", "12345", "123456789", "12a456", "１２３４５６"])
def test_pin_policy_rejects_the_rest(pin):
    with pytest.raises(IdentityValidationError, match="PIN — 6–8 цифр."):
        validate_pin(pin)


def test_new_pin_is_six_digits_without_leading_zero():
    for _ in range(200):
        pin = new_pin()
        assert len(pin) == 6 and pin.isdigit() and pin[0] != "0"


def test_pin_is_stored_as_argon2id():
    assert hash_pin("123456").startswith("$argon2id$")


# ── HTTP ───────────────────────────────────────────────────────────────────────────────────────────


def _user(user_id: UUID, login: str, role: str, *, email: str | None = None, active: bool = True) -> IdentityUser:
    return IdentityUser(
        id=user_id, login=login, email=email or f"{login}@example.test", name=login.title(), department="Цех",
        role=role, site=None, active=active,
    )


@pytest.fixture()
def pin_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        provision_user(session, _user(ADMIN_ID, "admin", "admin"), hash_password("admin-password"),
                       role_access=ROLE_DEFAULTS["admin"], pin_hash=hash_pin("99887766"))
        provision_user(session, _user(BUYER_ID, "buyer", "buyer"), hash_password("buyer-password"),
                       role_access=ROLE_SEED["buyer"], pin_hash=hash_pin("123456"))
        provision_user(session, _user(NEWBIE_ID, "newbie", "staff"), hash_password("newbie-password"))
        provision_user(session, _user(VICTOR_ID, "victor", "pm", email=ADMIN_MODE_EMAIL),
                       hash_password("victor-password"), pin_hash=hash_pin("24681357"))
        provision_user(session, _user(GONE_ID, "gone", "staff", active=False), hash_password("gone-password"),
                       pin_hash=hash_pin("111111"))
        seed_role_matrix(session)
        session.commit()
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.state.cookie_secure = False
    clock = {"now": NOW}
    app.state.identity_clock = lambda: clock["now"]
    app.dependency_overrides[moscow_now] = lambda: clock["now"]
    yield sessions, clock
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("identity_uow_factory", "cookie_secure", "identity_clock"):
        delattr(app.state, name)
    engine.dispose()


def _pin(client: TestClient, who: str | UUID, pin: str):
    return client.post("/api/auth/pin-login", json={"who": str(who), "pin": pin})


def _signed_in(who: str | UUID, pin: str) -> TestClient:
    client = TestClient(app)
    response = _pin(client, who, pin)
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = client.cookies.get("erp_csrf")
    return client


def _events(sessions, event_type: str) -> list[AuditEventRow]:
    with sessions() as session:
        return list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == event_type)))


def test_login_list_has_active_staff_without_emails(pin_api):
    body = TestClient(app).get("/api/auth/login-staff").json()
    assert [row["name"] for row in body["staff"]] == ["Admin", "Buyer", "Newbie", "Victor"]
    assert set(body["staff"][0]) == {"id", "name", "department"}
    assert "gone" not in str(body)


def test_pin_login_by_picked_employee_opens_a_session(pin_api):
    sessions, _ = pin_api
    client = _signed_in(BUYER_ID, "123456")
    me = client.get("/api/auth/me").json()
    assert me["user"]["id"] == str(BUYER_ID)
    assert me["roles"] == ["buyer"] and me["role_label"] == "Специалист по снабжению"
    assert me["can_admin_mode"] is False and me["admin_mode"] is False
    [event] = _events(sessions, "identity.login_succeeded")
    assert event.details["method"] == "pin"
    with sessions() as session:
        assert session.get(IdentityUserRow, BUYER_ID).seen_at is not None


def test_pin_login_by_email_is_the_fallback(pin_api):
    assert _pin(TestClient(app), "Buyer@Example.test", "123456").status_code == 200


def test_wrong_pin_and_unknown_employee_answer_alike(pin_api):
    sessions, _ = pin_api
    client = TestClient(app)
    wrong = _pin(client, BUYER_ID, "654321")
    unknown = _pin(client, "40000000-0000-0000-0000-00000000ffff", "654321")
    inactive = _pin(client, GONE_ID, "111111")
    assert wrong.status_code == unknown.status_code == inactive.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == inactive.json()["detail"] == "Неверный сотрудник или PIN."
    failed = _events(sessions, "identity.login_failed")
    assert len(failed) == 3 and all(event.details["method"] == "pin" for event in failed)


def test_pin_not_assigned_is_said_and_not_counted(pin_api):
    sessions, _ = pin_api
    response = _pin(TestClient(app), NEWBIE_ID, "123456")
    assert response.status_code == 422
    assert response.json()["detail"] == "PIN ещё не назначен — обратись к администратору."
    assert _events(sessions, "identity.login_failed") == []


def test_pin_attempts_share_the_id4_account_limit(pin_api):
    client = TestClient(app)
    for _ in range(10):
        assert _pin(client, BUYER_ID, "000000").status_code == 401
    blocked = _pin(client, BUYER_ID, "123456")
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Слишком много попыток. Подождите 10 минут."
    # the same account counter as the login+password path
    assert client.post("/api/auth/login", json={"login": "buyer", "password": "buyer-password"}).status_code == 429


def test_pin_attempts_count_toward_the_global_limit(pin_api):
    client = TestClient(app)
    for index in range(30):
        _pin(client, f"nobody{index}@example.test", "000000")
    blocked = _pin(client, BUYER_ID, "123456")
    assert blocked.status_code == 429
    assert "Вход временно закрыт" in blocked.json()["detail"]


# ── «Узнать PIN» при первом входе (portalRevealPin_) ────────────────────────────────────────────────


def _reveal_on(sessions):
    set_pin_reveal(True, SqlAlchemyIdentityUnitOfWork(sessions))


def test_empty_pin_with_the_mode_off_asks_for_the_pin(pin_api):
    response = _pin(TestClient(app), NEWBIE_ID, "")
    assert response.status_code == 422 and response.json()["detail"] == "Введи PIN."


def test_empty_pin_reveals_a_new_pin_once_to_a_newcomer(pin_api):
    sessions, _ = pin_api
    _reveal_on(sessions)
    client = TestClient(app)
    revealed = _pin(client, NEWBIE_ID, "").json()
    assert revealed["reveal"] is True and revealed["name"] == "Newbie"
    assert len(revealed["pin"]) == 6 and revealed["pin"][0] != "0"
    assert "erp_session" not in client.cookies  # no session yet: the person signs in with it
    [event] = _events(sessions, "identity.pin_revealed")
    assert event.aggregate_id == NEWBIE_ID
    # Until the first sign-in the door stays open (each ask issues a new PIN, the old one stops working).
    again = _pin(client, NEWBIE_ID, "").json()
    assert _pin(client, NEWBIE_ID, revealed["pin"]).status_code == 401
    assert _pin(client, NEWBIE_ID, again["pin"]).status_code == 200
    closed = _pin(TestClient(app), NEWBIE_ID, "")
    assert closed.status_code == 422
    assert closed.json()["detail"] == "Введи PIN. Пароль ты уже получал — если забыл, обратись к администратору."


def test_reveal_is_never_given_to_an_administrator(pin_api):
    sessions, _ = pin_api
    _reveal_on(sessions)
    response = _pin(TestClient(app), ADMIN_ID, "")
    assert response.status_code == 422 and response.json()["detail"] == "Введи PIN."
    assert _events(sessions, "identity.pin_revealed") == []


def test_reveal_does_not_bypass_the_unknown_employee_check(pin_api):
    sessions, _ = pin_api
    _reveal_on(sessions)
    response = _pin(TestClient(app), "stranger@example.test", "")
    assert response.status_code == 401
    assert len(_events(sessions, "identity.login_failed")) == 1


def test_password_sign_in_counts_as_the_first_sign_in(pin_api):
    sessions, _ = pin_api
    _reveal_on(sessions)
    TestClient(app).post("/api/auth/login", json={"login": "newbie", "password": "newbie-password"})
    assert _pin(TestClient(app), NEWBIE_ID, "").status_code == 422


# ── «Сотрудники»: PIN назначает админ, видит статус ─────────────────────────────────────────────────


def test_staff_screen_shows_pin_status_and_sets_a_pin(pin_api):
    sessions, _ = pin_api
    admin = _signed_in(ADMIN_ID, "99887766")
    rows = {row["login"]: row for row in admin.get("/api/auth/staff").json()["users"]}
    assert rows["buyer"]["pin_set"] is True and rows["newbie"]["pin_set"] is False
    assert "pin_hash" not in str(rows)

    bad = admin.put(f"/api/auth/staff/{NEWBIE_ID}", json={
        "name": "Newbie", "department": "Цех", "role": "staff", "site": "", "active": True, "pin": "1234",
    })
    assert bad.status_code == 422 and bad.json()["detail"] == "PIN — 6–8 цифр."

    saved = admin.put(f"/api/auth/staff/{NEWBIE_ID}", json={
        "name": "Newbie", "department": "Цех", "role": "staff", "site": "", "active": True, "pin": "7654321",
    })
    assert saved.status_code == 200 and saved.json()["user"]["pin_set"] is True
    [event] = [e for e in _events(sessions, "identity.user_updated") if e.aggregate_id == NEWBIE_ID]
    assert event.details["pin"] == "назначен" and "7654321" not in str(event.details)
    assert _pin(TestClient(app), NEWBIE_ID, "7654321").status_code == 200


def test_admin_creates_an_employee_with_a_pin_and_no_password(pin_api):
    sessions, _ = pin_api
    admin = _signed_in(ADMIN_ID, "99887766")
    created = admin.post("/api/auth/staff", json={
        "email": "kladov@example.test", "name": "Кладовщиков", "role": "receiver", "pin": "13572468",
    })
    assert created.status_code == 201, created.text
    assert created.json()["user"]["pin_set"] is True
    user_id = created.json()["user"]["id"]
    assert _pin(TestClient(app), user_id, "13572468").status_code == 200
    with sessions() as session:
        credential = session.get(IdentityCredentialRow, UUID(user_id))
        assert credential.pin_hash.startswith("$argon2id$")


# ── «Режим админа» (portalToggleAdmin) ─────────────────────────────────────────────────────────────


def test_admin_mode_is_only_for_the_god_mode_account(pin_api):
    buyer = _signed_in(BUYER_ID, "123456")
    response = buyer.post("/api/auth/admin-mode", json={"on": True})
    assert response.status_code == 403
    assert response.json()["detail"] == "Режим админа недоступен для этого аккаунта."


def test_admin_mode_lifts_the_role_to_admin_and_back(pin_api):
    sessions, _ = pin_api
    victor = _signed_in(VICTOR_ID, "24681357")
    me = victor.get("/api/auth/me").json()
    assert me["roles"] == ["pm"] and me["can_admin_mode"] is True and me["admin_mode"] is False
    assert victor.get("/api/auth/staff").status_code == 403

    on = victor.post("/api/auth/admin-mode", json={"on": True}).json()
    assert on["roles"] == ["admin"] and on["admin_mode"] is True
    assert on["role_label"] == "Технолог · режим"
    assert on["permissions"]["staffdir"] == "edit"
    assert victor.get("/api/auth/me").json()["admin_mode"] is True
    assert victor.get("/api/auth/staff").status_code == 200  # portalRealAdmin_: the admin mode counts

    off = victor.post("/api/auth/admin-mode", json={"on": False}).json()
    assert off["roles"] == ["pm"] and off["admin_mode"] is False and off["role_label"] == "Менеджер по проектам"
    assert victor.get("/api/auth/staff").status_code == 403
    events = [e.details["event"] for e in _events(sessions, "identity.admin_mode_on")
              + _events(sessions, "identity.admin_mode_off")]
    assert events == ["Вход в режим админа", "Выход из режима админа"]
    with sessions() as session:
        assert session.get(IdentityUserRow, VICTOR_ID).role == "pm"  # the stored role never changes


def test_admin_mode_belongs_to_the_session_and_expires_after_six_hours(pin_api):
    _, clock = pin_api
    victor = _signed_in(VICTOR_ID, "24681357")
    victor.post("/api/auth/admin-mode", json={"on": True})
    other = _signed_in(VICTOR_ID, "24681357")
    assert other.get("/api/auth/me").json()["admin_mode"] is False
    clock["now"] = NOW + timedelta(hours=6, minutes=1)
    assert victor.get("/api/auth/me").json()["admin_mode"] is False


# ── Migration ──────────────────────────────────────────────────────────────────────────────────────


def test_migration_marks_everyone_who_already_signed_in_as_seen(tmp_path):
    database = tmp_path / "identity.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    command.upgrade(config, "0061_procurement_traceability")
    with sqlite3.connect(database) as connection:
        for user_id, login in ((BUYER_ID, "buyer"), (NEWBIE_ID, "newbie")):
            connection.execute(
                "INSERT INTO identity_users (id, login, email, name, department, role, site, active)"
                " VALUES (?, ?, ?, ?, 'Цех', 'staff', NULL, 1)",
                (user_id.hex, login, f"{login}@example.test", login),
            )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO audit_events (id, aggregate_id, event_type, actor_id, actor_name, actor_department,"
            " occurred_at, details) VALUES (?, ?, 'identity.login_succeeded', ?, 'Buyer', 'Цех',"
            " '2026-09-20 08:00:00', '{}')",
            ("50000000000000000000000000000001", BUYER_ID.hex, BUYER_ID.hex),
        )
    command.upgrade(config, "0062_identity_pin_admin_mode")
    with sqlite3.connect(database) as connection:
        seen = dict(connection.execute("SELECT login, seen_at FROM identity_users").fetchall())
    assert seen["buyer"] is not None and seen["newbie"] is None
    command.downgrade(config, "0061_procurement_traceability")
