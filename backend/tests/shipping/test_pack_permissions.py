"""Explicit KB ceilings never inherit the obsolete project-wide boolean."""

from datetime import datetime
from decimal import Decimal

import pytest

from app.modules.shipping.domain import MoveContext, PackPermission, ShippingValidationError
from app.main import app
from app.shared.http_auth import authenticated_principal
from tests.shipping.test_api import shipping_api  # noqa: F401
from tests.shipping.test_pack_decision_no_upd import _good, STOREKEEPER, PROJECT
from tests.identity.test_view_as import view_api, _login, _view, _csrf, ADMIN2_ID  # noqa: F401


NOW = datetime(2026, 11, 3, 10)


def test_absolute_cap_includes_sent_and_missing_permission_blocks_future_packing():
    empty = PackPermission("100К.1", "КБ1")
    assert empty.remaining(Decimal("7")) == 0
    allowed = empty.change(Decimal("12"), Decimal("7"), "Менеджер", NOW)
    assert allowed.remaining(Decimal("7")) == 5
    assert allowed.change(Decimal("12"), Decimal("7"), "Другой", NOW) is allowed
    assert allowed.change(Decimal("7"), Decimal("7"), "Менеджер", NOW).remaining(Decimal("7")) == 0


@pytest.mark.parametrize("quantity", ["-1", "NaN", "Infinity", "1.0000001", "1000000000000"])
def test_invalid_permission_quantity(quantity):
    with pytest.raises(ShippingValidationError):
        PackPermission("100К.1", "КБ1").change(Decimal(quantity), Decimal("0"), "Менеджер", NOW)


def test_reduction_cannot_erase_consumed_quantity():
    with pytest.raises(ShippingValidationError, match="ниже уже упакованного"):
        PackPermission("100К.1", "КБ1", Decimal("12"), 1).change(Decimal("6"), Decimal("7"), "Менеджер", NOW)


def grant(client, quantity="4", version=0, item_index="КБ1"):
    return client.post("/api/shipping/project-pack-permissions", json={
        "project": PROJECT, "lines": [{"item_index": item_index, "quantity": quantity, "version": version}],
    })


def pack(client, good, quantity="4"):
    return client.post("/api/shipping/project-pack", json={
        "project": PROJECT, "lines": [{"good_id": good["id"], "quantity": quantity}],
    })


def test_permission_is_explicit_atomic_versioned_and_not_a_stock_write(shipping_api):
    client = shipping_api
    good = _good(client)
    assert pack(client, good).status_code == 422
    allowed = grant(client)
    assert allowed.status_code == 200, allowed.text
    permission = allowed.json()["permissions"][0]
    assert (permission["quantity"], permission["remaining"], permission["consumed"], permission["version"]) == ("4", "4", "0", 1)
    assert grant(client, "5", 0).status_code == 409
    assert grant(client, "4", 1).json()["permissions"][0]["version"] == 1
    assert grant(client, "3", 1, "КБ999").status_code == 422
    assert pack(client, good, "5").status_code == 422
    picked = pack(client, good)
    assert picked.status_code == 200, picked.text
    assert picked.json()["shipment"]["lines"][0]["picked"] == "4"
    assert grant(client, "3", 1).status_code == 422
    assert pack(client, good, "1").status_code == 422
    view = client.get("/api/shipping/project-pack-permissions", params={"project": PROJECT}).json()["permissions"][0]
    assert (view["remaining"], view["physical_remaining"], view["consumed"]) == ("0", "2", "4")


def permission_view(client, item_index="КБ1"):
    rows = client.get("/api/shipping/project-pack-permissions", params={"project": PROJECT}).json()["permissions"]
    return next(row for row in rows if row["item_index"] == item_index)


def permission_events():
    from sqlalchemy import select
    from app.shared.persistence import AuditEventRow
    with app.state.shipping_uow_factory() as uow:
        return [row.details for row in uow.session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "shipping.pack_permission_changed"))]


def test_legacy_flag_does_not_bypass_cap_but_manager_shipment_grants_what_it_picks(shipping_api):
    """Договорено 29.10.2026: отправка менеджера — сама решение упаковывать. Набирая, она поднимает лимит КБ до
    набранного (журнал и уведомление — как у явного разрешения); старый флаг проекта (0162) ничего не разрешает."""
    from dataclasses import replace
    from app.modules.shipping.domain import ProjectAddressee
    client = shipping_api
    good = _good(client)
    with app.state.shipping_uow_factory() as uow:
        uow.addressees.add(replace(ProjectAddressee.empty(PROJECT), pack_decided_by="Менеджер", pack_decided_at=NOW))
        uow.commit()
    assert pack(client, good).status_code == 422
    response = client.post("/api/shipping/shipments", json={
        "customer": "Заказчик", "receiver": "Получатель", "lines": [{"good_id": good["id"], "quantity": "4"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["shipment"]["lines"][0]["picked"] == "4"
    view = permission_view(client)
    assert (view["quantity"], view["consumed"], view["remaining"], view["version"]) == ("4", "4", "0", 1)
    (event,) = permission_events()
    assert (event["before"], event["quantity"], event["shipment"]) == ("0", "4", response.json()["shipment"]["number"])
    assert client.post("/api/shipping/project-pack-decision", json={
        "project": PROJECT, "version": 0, "decided": True,
    }).status_code in {404, 405}


def manager_request(client, good, quantity):
    response = client.post("/api/shipping/shipments", json={
        "customer": "Заказчик", "receiver": "Получатель", "lines": [{"good_id": good["id"], "quantity": quantity}],
    })
    assert response.status_code == 200, response.text
    return response.json()["shipment"]


def accept(client, quantity):
    fresh = client.get("/api/shipping/goods").json()["goods"][0]
    response = client.post(f"/api/shipping/goods/{fresh['id']}/accept", json={"version": fresh["version"], "quantity": quantity})
    assert response.status_code == 200, response.text


def pick(client, shipment):
    return client.post(f"/api/shipping/shipments/{shipment['id']}/pick", json={"version": shipment["version"]})


def cancel(client, shipment_id):
    current = next(item for item in client.get("/api/shipping/shipments").json()["shipments"] if item["id"] == shipment_id)
    response = client.post(f"/api/shipping/shipments/{shipment_id}/cancel", json={"version": current["version"]})
    assert response.status_code == 200, response.text
    return response.json()["shipment"]


def test_manager_request_ahead_of_production_lets_the_warehouse_pack_it_later(shipping_api):
    """29.10.2026: заявка менеджера — решение упаковывать. Заведена вперёд производства — лимит КБ поднят на её
    количество сразу, кладовщик набирает её по мере сдачи цехом без отдельного разрешения; сверх заявки — нет."""
    from tests.shipping.test_pack_decision_no_upd import STOREKEEPER, _as
    client = shipping_api
    queued = _good(client, accepted="")
    made = manager_request(client, queued, "10")
    assert made["status"] == "Новая"
    view = permission_view(client)
    assert (view["quantity"], view["remaining"], view["version"]) == ("10", "10", 1)
    (event,) = permission_events()
    assert (event["before"], event["quantity"], event["shipment_id"]) == ("0", "10", made["id"])

    _as(STOREKEEPER)
    accept(client, "6")
    first = pick(client, made)
    assert first.status_code == 200, first.text
    assert first.json()["shipment"]["lines"][0]["picked"] == "6"
    accept(client, "5")
    second = pick(client, first.json()["shipment"])
    assert second.status_code == 200, second.text
    assert second.json()["shipment"]["lines"][0]["picked"] == "10"  # не больше заявки
    assert pack(client, queued, "1").status_code == 422  # остаток ГП 1, лимит выбран — сверх заявки нельзя
    assert permission_view(client)["remaining"] == "0"


def test_manager_request_beyond_stock_reserves_its_whole_quantity(shipping_api):
    client = shipping_api
    good = _good(client, accepted="6")
    made = manager_request(client, good, "10")
    assert made["lines"][0]["picked"] == "6"
    view = permission_view(client)
    assert (view["quantity"], view["consumed"], view["remaining"]) == ("10", "6", "4")


def test_cancel_gives_back_what_the_request_raised_and_never_used(shipping_api):
    client = shipping_api
    good = _good(client, accepted="6")
    assert grant(client, "4").status_code == 200
    made = manager_request(client, good, "10")  # набрано 6, лимит 4 → 10
    assert permission_view(client)["quantity"] == "10"
    cancel(client, made["id"])  # набранное вернулось на склад — поднятые 6 не использованы
    view = permission_view(client)
    assert (view["quantity"], view["consumed"], view["remaining"]) == ("4", "0", "4")
    assert [(event["before"], event["quantity"]) for event in permission_events()] == [("0", "4"), ("4", "10"), ("10", "4")]


def test_two_open_requests_both_fit_and_cancelling_one_keeps_the_other(shipping_api):
    client = shipping_api
    queued = _good(client, accepted="")
    first = manager_request(client, queued, "5")
    manager_request(client, queued, "3")
    assert permission_view(client)["quantity"] == "8"
    cancel(client, first["id"])
    assert permission_view(client)["quantity"] == "3"


def test_limit_is_checked_against_what_is_picked_not_what_is_ordered(shipping_api):
    """Заявка без решения менеджера (не менеджерская) на 10, на складе 2 и лимит 2 — набор 2 проходит; лимит сверх
    набираемого не нужен."""
    from uuid import UUID
    from app.modules.shipping.application import CreateShipmentCommand, RequestedGood, create_shipment
    from tests.shipping.test_pack_decision_no_upd import ADMIN, STOREKEEPER, _as
    client = shipping_api
    queued = _good(client, accepted="")
    created = create_shipment(CreateShipmentCommand(
        customer="Заказчик", receiver="Получатель", shipping_point="", contact="", phone="", terms=None, carrier="",
        deadline=None, upd_type=None, upd_number="", upd_date=None, upd_url="", note="",
        lines=(RequestedGood(UUID(queued["id"]), Decimal("10")),), actor=ADMIN.actor, now=NOW,
    ), app.state.shipping_uow_factory())
    made = {"id": str(created.id), "version": created.version}
    assert permission_view(client)["version"] == 0
    assert grant(client, "2").status_code == 200
    fresh = client.get("/api/shipping/goods").json()["goods"][0]
    _as(STOREKEEPER)
    assert client.post(f"/api/shipping/goods/{fresh['id']}/accept", json={"version": fresh["version"], "quantity": "2"}).status_code == 200
    picked = client.post(f"/api/shipping/shipments/{made['id']}/pick", json={"version": made["version"]})
    assert picked.status_code == 200, picked.text
    assert picked.json()["shipment"]["lines"][0]["picked"] == "2"
    assert permission_view(client)["quantity"] == "2"
    # Кладовщик лимит не поднимает: больше на складе — больше разрешённого — отказ.
    fresh = client.get("/api/shipping/goods").json()["goods"][0]
    assert client.post(f"/api/shipping/goods/{fresh['id']}/accept", json={"version": fresh["version"], "quantity": "3"}).status_code == 200
    again = client.post(f"/api/shipping/shipments/{made['id']}/pick", json={"version": picked.json()["shipment"]["version"]})
    assert again.status_code == 422 and "разрешено ещё 0" in again.json()["detail"]


def test_manager_project_ship_grants_and_storekeeper_project_ship_does_not(shipping_api):
    from tests.shipping.test_pack_decision_no_upd import STOREKEEPER, _as
    client = shipping_api
    good = _good(client, accepted="6")
    body = {"project": PROJECT, "addressee_version": 0, "ship": False,
            "lines": [{"good_id": good["id"], "version": good["version"], "quantity": "3"}]}
    _as(STOREKEEPER)
    refused = client.post("/api/shipping/project-ship", json=body, headers={"Idempotency-Key": "store-1"})
    assert refused.status_code == 422 and "разрешено ещё 0" in refused.json()["detail"]
    from tests.shipping.test_pack_decision_no_upd import ADMIN
    _as(ADMIN)
    made = client.post("/api/shipping/project-ship", json=body, headers={"Idempotency-Key": "manager-1"})
    assert made.status_code == 200, made.text
    assert made.json()["shipment"]["lines"][0]["picked"] == "3"
    view = permission_view(client)
    assert (view["quantity"], view["remaining"], view["version"]) == ("3", "0", 1)


def test_only_manager_can_grant_and_batch_is_all_or_nothing(shipping_api):
    client = shipping_api
    _good(client)
    response = client.post("/api/shipping/project-pack-permissions", json={
        "project": PROJECT, "lines": [
            {"item_index": "КБ1", "quantity": "4", "version": 0},
            {"item_index": "КБ404", "quantity": "4", "version": 0},
        ],
    })
    assert response.status_code == 422
    assert client.get("/api/shipping/project-pack-permissions", params={"project": PROJECT}).json()["permissions"][0]["version"] == 0
    app.dependency_overrides[authenticated_principal] = lambda: STOREKEEPER
    assert grant(client).status_code == 403


def test_cancel_releases_capacity_but_does_not_change_ceiling(shipping_api):
    client = shipping_api
    good = _good(client)
    assert grant(client).status_code == 200
    shipment = pack(client, good).json()["shipment"]
    cancelled = client.post(f"/api/shipping/shipments/{shipment['id']}/cancel", json={"version": shipment["version"]})
    assert cancelled.status_code == 200, cancelled.text
    permission = client.get("/api/shipping/project-pack-permissions", params={"project": PROJECT}).json()["permissions"][0]
    assert (permission["quantity"], permission["remaining"], permission["version"]) == ("4", "4", 1)
    assert pack(client, good).status_code == 200


def test_sent_is_consumed_once_and_missing_permission_has_consumed_baseline(shipping_api):
    from dataclasses import replace
    from uuid import UUID
    client = shipping_api
    good = _good(client)
    with app.state.shipping_uow_factory() as uow:
        stored = uow.goods.get(UUID(good["id"]))
        uow.goods.add(replace(stored, shipped=Decimal("4"), sent=Decimal("3"), version=stored.version + 1),
                      MoveContext("Тест", NOW))
        uow.commit()
    permission = client.get("/api/shipping/project-pack-permissions", params={"project": PROJECT}).json()["permissions"][0]
    assert (permission["quantity"], permission["consumed"], permission["remaining"]) == ("4", "4", "0")
    assert grant(client, "3").status_code == 422
    response = grant(client, "5")
    assert response.status_code == 200, response.text
    assert response.json()["permissions"][0]["remaining"] == "1"


def test_manager_create_raises_explicit_cap_openly_instead_of_truncating(shipping_api):
    client = shipping_api
    good = _good(client)
    assert grant(client, "2").status_code == 200
    response = client.post("/api/shipping/shipments", json={
        "customer": "Заказчик", "receiver": "Получатель", "lines": [{"good_id": good["id"], "quantity": "3"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["shipment"]["lines"][0]["picked"] == "3"
    view = permission_view(client)
    assert (view["quantity"], view["version"]) == ("3", 2)
    assert [(event["before"], event["quantity"]) for event in permission_events()] == [("0", "2"), ("2", "3")]
    assert pack(client, good, "1").status_code == 422  # склад сверх лимита не набирает


def test_concurrent_reduction_and_pick_share_one_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.modules.shipping.application import (
        PackPermissionChange, SetPackPermissionsCommand, set_pack_permissions,
        PackProjectCommand, RequestedGood, pack_project,
    )
    from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
    from app.shared.persistence import create_schema
    from tests.shipping.test_domain import good
    from tests.shipping.test_pack_decision_no_upd import ADMIN, DAY

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'packing.db'}", connect_args={"check_same_thread": False})
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    stored = good().accept(Decimal("6"), DAY, "ГП.TEST")
    with factory() as uow:
        uow.goods.add(stored, MoveContext("Тест", NOW))
        uow.commit()
    def change(quantity, version):
        return set_pack_permissions(SetPackPermissionsCommand(
            stored.project, (PackPermissionChange(stored.item_index, Decimal(quantity), version),), ADMIN.actor, NOW,
        ), factory(), lambda uow, project: {stored.item_index})
    change("4", 0)
    barrier = Barrier(2)
    def execute(action):
        barrier.wait(timeout=5)
        try:
            action()
            return "ok"
        except ShippingValidationError:
            return "rejected"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(execute, action) for action in (
                lambda: change("2", 1),
                lambda: pack_project(PackProjectCommand(
                    stored.project, (RequestedGood(stored.id, Decimal("4")),), ADMIN.actor, NOW,
                ), factory()),
            )]
            assert sorted(future.result(timeout=15) for future in futures) == ["ok", "rejected"]
        with factory() as uow:
            permission = uow.pack_permissions.get(stored.project, stored.item_index)
            assert uow.goods.get(stored.id).shipped <= permission.quantity
    finally:
        engine.dispose()


def test_service_token_cannot_grant(shipping_api):
    from dataclasses import replace
    from tests.shipping.test_pack_decision_no_upd import ADMIN
    app.dependency_overrides[authenticated_principal] = lambda: replace(ADMIN, service=True)
    assert grant(shipping_api).status_code == 403


def test_view_as_cannot_grant(view_api):
    from app.modules.identity.middleware import VIEW_ONLY_DETAIL
    admin = _login("admin")
    assert _view(admin, ADMIN2_ID).status_code == 200
    response = admin.post("/api/shipping/project-pack-permissions", json={}, headers=_csrf(admin))
    assert response.status_code == 403
    assert response.json()["detail"] == VIEW_ONLY_DETAIL


def test_long_existing_kb_index_can_receive_permission(shipping_api):
    index = "КБ" + "А" * 100
    _good(shipping_api, item_index=index)
    response = grant(shipping_api, item_index=index)
    assert response.status_code == 200, response.text
    assert response.json()["permissions"][0]["item_index"] == index


def test_confirmed_historical_import_consumes_capacity_without_raising_explicit_cap(shipping_api):
    from app.modules.shipping.application import ShipBeforeErpCommand, ship_before_erp
    from tests.shipping.test_pack_decision_no_upd import ADMIN
    client = shipping_api
    good = _good(client)
    assert grant(client, "2").status_code == 200
    ship_before_erp(ShipBeforeErpCommand(PROJECT, "КБ1", Decimal("4"), ADMIN.actor, NOW),
                    app.state.shipping_uow_factory())
    permission = client.get("/api/shipping/project-pack-permissions", params={"project": PROJECT}).json()["permissions"][0]
    assert (permission["quantity"], permission["consumed"], permission["remaining"], permission["version"]) == ("2", "4", "0", 1)
    assert pack(client, good, "1").status_code == 422
    assert grant(client, "3", 1).status_code == 422
    assert grant(client, "4", 1).json()["permissions"][0]["remaining"] == "0"


def test_cap_notification_is_specific_transactional_and_noop_safe(shipping_api, monkeypatch):
    from sqlalchemy import select
    from app.shared.persistence import OutboxMessageRow
    from app.modules.shipping import application
    client = shipping_api
    _good(client)
    def messages():
        with app.state.shipping_uow_factory() as uow:
            return list(uow.session.scalars(select(OutboxMessageRow.payload).where(
                OutboxMessageRow.idempotency_key.like("notify:shipping:pack-permissions:%"),
            )))
    assert grant(client, "4").status_code == 200
    assert grant(client, "4", 1).status_code == 200
    assert grant(client, "5", 0).status_code == 409
    assert len(messages()) == 1
    assert "КБ1: 4" in messages()[0]["body"]
    def broken(*args, **kwargs):
        raise RuntimeError("notification write failed")
    monkeypatch.setattr(application, "publish_notification", broken)
    with pytest.raises(RuntimeError, match="notification write failed"):
        grant(client, "6", 1)
    permission = client.get("/api/shipping/project-pack-permissions", params={"project": PROJECT}).json()["permissions"][0]
    assert (permission["quantity"], permission["version"]) == ("4", 1)
    assert len(messages()) == 1
