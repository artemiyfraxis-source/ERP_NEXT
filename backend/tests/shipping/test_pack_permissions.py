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


def test_legacy_flag_and_manager_create_cannot_bypass_explicit_cap(shipping_api):
    from app.modules.shipping.domain import ProjectAddressee
    client = shipping_api
    good = _good(client)
    with app.state.shipping_uow_factory() as uow:
        uow.addressees.add(ProjectAddressee.empty(PROJECT).decide_pack(True, "Менеджер", NOW))
        uow.commit()
    assert pack(client, good).status_code == 422
    response = client.post("/api/shipping/shipments", json={
        "customer": "Заказчик", "receiver": "Получатель", "lines": [{"good_id": good["id"], "quantity": "4"}],
    })
    assert response.status_code == 422, response.text
    assert client.post("/api/shipping/project-pack-decision", json={
        "project": PROJECT, "version": 0, "decided": True,
    }).status_code == 422


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


def test_explicit_cap_is_not_silently_truncated_on_manager_create(shipping_api):
    client = shipping_api
    good = _good(client)
    assert grant(client, "2").status_code == 200
    response = client.post("/api/shipping/shipments", json={
        "customer": "Заказчик", "receiver": "Получатель", "lines": [{"good_id": good["id"], "quantity": "3"}],
    })
    assert response.status_code == 422
    assert "разрешено ещё 2" in response.json()["detail"]


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
