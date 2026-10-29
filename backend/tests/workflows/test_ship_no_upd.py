"""«Отгрузить без УПД» по PIN (владелец 29.10.2026, workflows/ship_no_upd.py): разрешает действующий сотрудник с разделом
«Отгрузка без УПД — разрешение» на правку своим разрешительным ПИН (не PIN входа — владелец 29.10); неверный ПИН —
неудачная попытка под пределами входа по PIN."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.application import LOGIN_FAIL_LIMIT
from app.modules.identity.domain import LEVEL_SECTION_LABELS, AccessMode, IdentityUser, hash_password, hash_pin
from app.modules.identity.infrastructure import (
    IdentityCredentialRow, IdentityLoginThrottleRow, SqlAlchemyIdentityUnitOfWork, provision_user, seed_level_matrix,
)
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.ship_no_upd import NO_UPD_SECTION
from tests.factories import Actor

PIN = "482913"  # разрешительный ПИН
LOGIN_PIN = "135790"  # PIN входа — разрешения не даёт
PROJECT = "591К.3"
APPROVER = UUID("71000000-0000-0000-0000-000000000001")  # Зингер: раздел выдан лично
OWNER = UUID("71000000-0000-0000-0000-000000000002")  # суперадминистратор: раздел у полного уровня сам
STRANGER = UUID("71000000-0000-0000-0000-000000000003")  # кладовщик без раздела
GONE = UUID("71000000-0000-0000-0000-000000000004")  # раздел есть, но уволен
MANAGER = Principal(
    actor=Actor(id=UUID("10000000-0000-0000-0000-000000000002"), name="Ульяна", department="Продажи"),
    email="pm@example.test", roles=frozenset({"pm"}), permissions={"shipping": "edit"},
)
STOREKEEPER = Principal(
    actor=Actor(id=UUID("10000000-0000-0000-0000-000000000005"), name="Алексей", department="Склад"),
    email="sklad@example.test", roles=frozenset({"storekeeper"}), permissions={"shipping_goods": "edit", "shipping": "view"},
)


def _user(user_id: UUID, login: str, name: str, level: str, active: bool = True) -> IdentityUser:
    return IdentityUser(
        id=user_id, login=login, email=f"{login}@example.test", name=name, department="Офис", role="staff", site=None,
        active=active, access_level=level,
    )


@pytest.fixture()
def env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    password, pin, approval = hash_password("approver-password-1"), hash_pin(LOGIN_PIN), hash_pin(PIN)
    grant = {NO_UPD_SECTION: AccessMode.EDIT}
    with sessions() as session:
        for user, personal in (
            (_user(APPROVER, "zinger", "Зингер", "commercial"), grant),
            (_user(OWNER, "artemiy", "Артемий", "superadmin"), {}),
            (_user(STRANGER, "aleksey", "Алексей", "storekeeper"), {}),
            (_user(GONE, "kasatochkin", "Касаточкин", "commercial", active=False), grant),
        ):
            provision_user(session, user, password, pin_hash=pin, level_access=personal)
        session.flush()
        for user_id in (APPROVER, OWNER, STRANGER, GONE):
            credential = session.get(IdentityCredentialRow, user_id)
            credential.approval_pin_hash, credential.approval_pin_set_at = approval, datetime(2026, 10, 29, 7, 0)
        seed_level_matrix(session)
        session.commit()
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    previous_identity = getattr(app.state, "identity_uow_factory", None)
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: MANAGER
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 10, 29, 10, 0)
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.shipping_uow_factory
    if previous_identity is None:
        del app.state.identity_uow_factory
    else:
        app.state.identity_uow_factory = previous_identity
    engine.dispose()


def _as(principal: Principal) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: principal


def _without_upd(client) -> dict:
    """Менеджер завела отправку «Без УПД» самовывозом (её отправка ставит решение «упаковывать»): всё есть, кроме УПД."""
    good = client.post("/api/shipping/goods", json={
        "project": PROJECT, "item_index": "КБ1", "customer": "Заказчик", "name": "Жгут", "ordered": "6", "unit": "шт",
    }).json()["good"]
    _as(STOREKEEPER)
    good = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "6"}).json()["good"]
    _as(MANAGER)
    made = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "phone": "+7 900", "terms": "САМОВЫВОЗ", "upd_type": "БЕЗ УПД",
        "lines": [{"good_id": good["id"], "quantity": "6"}],
    })
    assert made.status_code == 200, made.text
    _as(STOREKEEPER)
    return made.json()["shipment"]


def _approve(client, shipment: dict, approver: UUID = APPROVER, pin: str = PIN):
    return client.post(f"/api/shipping/shipments/{shipment['id']}/approve-no-upd", json={
        "version": shipment["version"], "approver_id": str(approver), "pin": pin,
    })


def _failures(sessions, key: str) -> int:
    with sessions() as session:
        row = session.get(IdentityLoginThrottleRow, key)
        return row.failures if row is not None else 0


def test_the_section_exists_and_the_approvers_are_the_active_holders(env):
    client, _ = env
    approvers = client.get("/api/shipping/no-upd-approvers")

    assert LEVEL_SECTION_LABELS[NO_UPD_SECTION] == "Отгрузка без УПД — разрешение"
    assert approvers.status_code == 200
    assert approvers.json()["approvers"] == [
        {"id": str(OWNER), "name": "Артемий"}, {"id": str(APPROVER), "name": "Зингер"},
    ]


def test_right_pin_lets_the_shipment_go_without_a_upd(env):
    client, sessions = env
    shipment = _without_upd(client)
    refused = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})
    approved = _approve(client, shipment)
    body = approved.json()["shipment"]
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": body["version"]})

    assert shipment["missing_ship"] == ["разрешение на отгрузку без УПД"] and shipment["ready_to_ship"] is False
    assert refused.status_code == 422
    assert approved.status_code == 200, approved.text
    assert (body["no_upd_approved_by"], body["no_upd_approved_at"]) == ("Зингер", "2026-10-29T10:00:00")
    assert (body["missing_ship"], body["ready_to_ship"]) == ([], True)
    assert shipped.status_code == 200, shipped.text
    # Уехала без УПД — «Отгружена», не «Закрыта», пока УПД не внесут.
    assert (shipped.json()["shipment"]["status"], shipped.json()["closed"]) == ("Отгружена", False)
    assert "№ УПД" in shipped.json()["why"]
    with sessions() as session:
        event = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "shipping.no_upd_approved")).one()
    assert (event.details["approver_id"], event.details["approver"], event.actor_name) == (str(APPROVER), "Зингер", "Алексей")
    assert _failures(sessions, "approval:zinger") == 0


def test_a_full_level_approves_without_a_grant(env):
    client, _ = env
    shipment = _without_upd(client)
    approved = _approve(client, shipment, OWNER)

    assert approved.status_code == 200 and approved.json()["shipment"]["no_upd_approved_by"] == "Артемий"


@pytest.mark.parametrize("who", [STRANGER, GONE, UUID("71000000-0000-0000-0000-0000000000ff")])
def test_a_stranger_an_inactive_or_unknown_person_cannot_permit(env, who):
    client, sessions = env
    shipment = _without_upd(client)
    refused = _approve(client, shipment, who)

    assert refused.status_code == 403 and refused.json()["detail"] == "Этот сотрудник не может дать такое разрешение."
    assert client.get("/api/shipping/shipments").json()["shipments"][0]["no_upd_approved_by"] == ""
    # До PIN не дошло — счётчики входа не тронуты.
    assert _failures(sessions, "approval:aleksey") == 0 and _failures(sessions, "approval:kasatochkin") == 0


def test_a_wrong_pin_is_a_failed_login_and_a_series_locks_even_the_right_pin(env):
    client, sessions = env
    shipment = _without_upd(client)
    wrong = _approve(client, shipment, pin="000000")

    assert wrong.status_code == 403 and wrong.json()["detail"] == "Неверный разрешительный ПИН."
    assert _failures(sessions, "approval:zinger") == 1
    for _ in range(LOGIN_FAIL_LIMIT - 1):
        assert _approve(client, shipment, pin="000000").status_code == 403
    locked = _approve(client, shipment)

    assert _failures(sessions, "approval:zinger") == LOGIN_FAIL_LIMIT
    assert locked.status_code == 429 and "Подождите 10 минут" in locked.json()["detail"]
    assert client.get("/api/shipping/shipments").json()["shipments"][0]["no_upd_approved_by"] == ""
    with sessions() as session:
        failed = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "identity.login_failed")).all()
    assert len(failed) == LOGIN_FAIL_LIMIT and failed[0].details["method"] == "noupd"


def test_a_stale_form_or_a_viewer_spends_no_pin_attempt(env):
    client, sessions = env
    shipment = _without_upd(client)
    stale = _approve(client, {**shipment, "version": shipment["version"] - 1}, pin="000000")
    _as(Principal(
        actor=Actor(id=UUID("10000000-0000-0000-0000-000000000009"), name="Гость", department="ERP"),
        email="view@example.test", roles=frozenset({"pm"}), permissions={"shipping": "view", "shipping_goods": "view"},
    ))
    viewer = _approve(client, shipment, pin="000000")

    assert stale.status_code == 409 and viewer.status_code == 403
    assert _failures(sessions, "approval:zinger") == 0


def test_the_permit_is_refused_once_given(env):
    client, _ = env
    shipment = _without_upd(client)
    first = _approve(client, shipment).json()["shipment"]
    again = _approve(client, first, OWNER)

    assert again.status_code == 422 and "уже разрешил Зингер" in again.json()["detail"]


def test_the_login_pin_no_longer_permits(env):
    """Владелец 29.10: «ПИН того же, что для входа — нет»: PIN входа — неверный разрешительный ПИН (неудачная попытка)."""
    client, sessions = env
    shipment = _without_upd(client)
    refused = _approve(client, shipment, pin=LOGIN_PIN)

    assert refused.status_code == 403 and refused.json()["detail"] == "Неверный разрешительный ПИН."
    assert _failures(sessions, "approval:zinger") == 1
    assert client.get("/api/shipping/shipments").json()["shipments"][0]["no_upd_approved_by"] == ""


def test_without_an_approval_pin_the_person_is_told_where_to_set_it(env):
    client, sessions = env
    with sessions() as session:
        credential = session.get(IdentityCredentialRow, APPROVER)
        credential.approval_pin_hash = credential.approval_pin_set_at = None
        session.commit()
    shipment = _without_upd(client)
    refused = _approve(client, shipment)

    assert refused.status_code == 403
    assert refused.json()["detail"] == "У сотрудника не задан разрешительный ПИН — его задают в личном кабинете."
    assert _failures(sessions, "approval:zinger") == 0
