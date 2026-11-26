"""Fulfillment must recheck whole-place quality and the CSM project on every stock path."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, delete

from app.main import app
from app.modules.fulfillment.application import (
    HoldKitReserveCommand, hold_kit_reserve,
)
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.production.infrastructure import ProductionPlanProjectRow
from app.modules.warehouse.domain import WarehouseValidationError
from app.modules.warehouse.infrastructure import StockLotRow, WriteOffEntryRow
from tests.warehouse.test_receipts import NOW, _accept, _receipt, stand  # noqa: F401
from tests.warehouse.test_grn_acceptance import csm_context, permit_quality, quality


@pytest.fixture
def shop(stand):
    client, sessions = stand
    actor = permit_quality()
    sentinel = object()
    previous = getattr(app.state, "fulfillment_uow_factory", sentinel)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = factory
    yield client, sessions, factory, actor
    if previous is sentinel:
        del app.state.fulfillment_uow_factory
    else:
        app.state.fulfillment_uow_factory = previous


def kit(client, lot=None, *, project="591К.3", kb="КБ1", waiting=False):
    body = {"purpose": f"{project} · {kb} · Жгут", "deadline": "2026-11-30", "lines": [
        {"article": "CON-001", "name": "Контакт", "unit": "шт", "requirement": "2"}]}
    if waiting:
        body["lines"].append({"article": "B", "name": "B", "unit": "шт", "requirement": "1"})
    response = client.post("/api/issues", headers={"Idempotency-Key": str(uuid4())}, json=body)
    assert response.status_code == 200, response.text
    request = response.json()["request"]
    if lot:
        bound = bind(client, request, lot)
        assert bound.status_code == 200, bound.text
        request = bound.json()["request"]
    return request


def bind(client, request, lot):
    return client.post(f"/api/issues/{request['id']}/lines/{request['lines'][0]['id']}/bind",
                       json={"version": request["version"], "lot_id": lot["id"]})


def stock(client, sessions, *, project="591К.3", category="PPM", released=True):
    values = {"grn_category": category}
    if category == "CSM":
        values["grn_context"] = csm_context(sessions, project)
    response = _accept(client, _receipt(client)["id"], **values)
    assert response.status_code == 200, response.text
    lot = response.json()["lot"]
    if released:
        response = quality(client, lot)
        assert response.status_code == 200, response.text
        lot = response.json()["lot"]
    return lot


@pytest.mark.parametrize("path", ["single", "partial", "batch"])
def test_hold_after_reservation_blocks_every_issue_path_atomically(shop, path):
    client, sessions, factory, _ = shop
    lot = stock(client, sessions)
    request = kit(client, lot, waiting=path == "partial")
    if path == "single":
        ready = client.post(f"/api/issues/{request['id']}/ready",
                            json={"version": request["version"], "handout_location": "1B3"})
        assert ready.status_code == 200, ready.text
        request = ready.json()["request"]
    second = None
    if path == "batch":
        other = stock(client, sessions)
        second = kit(client, other, kb="КБ2")
    with factory() as uow:
        current = uow.warehouse.get(UUID(lot["id"]))
    held = quality(client, {**lot, "version": current.version}, "QTN")
    assert held.status_code == 200, held.text
    before = {}
    with sessions() as session:
        before = {row.id: (row.reserved, row.written_off) for row in session.scalars(select(StockLotRow))}
    if path == "batch":
        response = client.post("/api/issues/issue-batch", json={"requests": [
            {"id": row["id"], "version": row["version"]} for row in (second, request)]})
    else:
        action = "issue" if path == "single" else "issue-picked"
        response = client.post(f"/api/issues/{request['id']}/{action}", json={"version": request["version"]})
    assert response.status_code == 422, response.text
    assert "ОТК" in response.text
    with sessions() as session:
        assert {row.id: (row.reserved, row.written_off) for row in session.scalars(select(StockLotRow))} == before
        assert session.query(WriteOffEntryRow).count() == 0
    with factory() as uow:
        assert len(uow.fulfillment.list()) == (2 if second else 1)


def test_reservation_and_techcard_reserve_refuse_iqh(shop):
    client, sessions, factory, actor = shop
    lot = stock(client, sessions, released=False)
    request = kit(client)
    assert bind(client, request, lot).status_code == 422
    with factory() as uow, pytest.raises(WarehouseValidationError, match="ОТК"):
        hold_kit_reserve(HoldKitReserveCommand(project="591К.3", item_index="КБ1", article="CON-001",
            lot_id=UUID(lot["id"]), quantity=Decimal(2), unit="шт", actor=actor, now=NOW), uow)


def test_csm_same_article_two_owners_cannot_cross_projects_and_return_keeps_identity(shop):
    client, sessions, factory, _ = shop
    first = stock(client, sessions, category="CSM")
    second = stock(client, sessions, category="CSM", project="592К.1")
    request = kit(client)
    refused = bind(client, request, second)
    assert refused.status_code == 422 and "другого проекта" in refused.text
    bound = bind(client, request, first)
    assert bound.status_code == 200, bound.text
    request = bound.json()["request"]
    issued = client.post("/api/issues/issue-batch", json={"requests": [
        {"id": request["id"], "version": request["version"]}]})
    assert issued.status_code == 200, issued.text
    request = issued.json()["requests"][0]
    returned = client.post(f"/api/issues/{request['id']}/returned",
                          json={"version": request["version"], "return_location": "Возвраты"})
    assert returned.status_code == 200, returned.text
    request = returned.json()["request"]
    settled = client.post(f"/api/issues/{request['id']}/complete-return", json={"version": request["version"],
        "lines": [{"line_id": request["lines"][0]["id"], "returned_good": "1", "defective_returned": "0",
                   "defective_missing": "0", "storage_location": "2A2"}]})
    assert settled.status_code == 200, settled.text
    with factory() as uow:
        current = uow.warehouse.get(UUID(first["id"]))
        untouched = uow.warehouse.get(UUID(second["id"]))
    assert current.balance == 9 and untouched.balance == 10
    assert str(current.material_owner_id) == first["material_owner_id"]
    assert current.csm_project_id == "591К.3" and str(current.grn_id) == first["grn_id"]
    moved = client.post(f"/api/stock/batches/{current.id}/move", json={"version": current.version, "warehouse": "ЖУК"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["lot"]["csm_project_id"] == first["csm_project_id"]
    # Refreshing the imported project snapshot must not erase or prevent the accepted stable key.
    with sessions.begin() as session:
        session.execute(delete(ProductionPlanProjectRow))
    with factory() as uow:
        assert uow.warehouse.get(current.id).csm_project_id == "591К.3"


def test_full_techcard_reserve_takeover_advances_stock_version_and_checks_hold(shop):
    client, sessions, factory, actor = shop
    lot = stock(client, sessions)
    with factory() as uow:
        hold_kit_reserve(HoldKitReserveCommand(project="591К.3", item_index="КБ1", article="CON-001",
            lot_id=UUID(lot["id"]), quantity=Decimal(2), unit="шт", actor=actor, now=NOW), uow)
        uow.commit()
    with factory() as uow:
        before = uow.warehouse.get(UUID(lot["id"]))
    request = kit(client, lot)
    with factory() as uow:
        after = uow.warehouse.get(UUID(lot["id"]))
    assert before.reserved == after.reserved == 2 and after.version == before.version + 1
    assert request["lines"][0]["reserved"] == "2"
