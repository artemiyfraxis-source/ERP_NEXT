from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.application import ConcurrentProductionUpdate
from app.modules.production.domain import ProductionValidationError
from app.modules.warehouse.application import ReturnWriteOffCommand, return_writeoff
from app.modules.warehouse.domain import StockLot, WarehouseValidationError
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401
from tests.production.test_instruments_api import _as, create
from tests.workflows.test_coax_vswr_api import NOW

URL = "/api/production/instruments/from-stock"


def setup(client):
    _as({"quality": "edit", "production": "view"})
    parent = create(client)
    sessions = app.state.production_uow_factory().session_factory
    lot = StockLot.create(warehouse="ЦАР", grn="ГРН-777", package="", name="Переходник SMA", item_type="Переходник",
                          purpose="ОТК", unit="шт", quantity=Decimal(10), storage_location="", source_receiving_ids=("receipt",),
                          location_as_is=True, manufacturer="Завод")
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        uow.warehouse.add(lot)
        uow.commit()
    return parent, lot, sessions


def snapshot(sessions, lot_id):
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        return (uow.warehouse.get(lot_id), list(uow.writeoffs.for_lot(lot_id)),
                [(row.event_type, row.details) for row in uow.session.scalars(select(AuditEventRow).order_by(AuditEventRow.id))])


def test_from_stock_and_return_commit_both_sides_and_block_warehouse_undo(production_api):
    client, _ = production_api
    parent, lot, sessions = setup(client)
    result = client.post(URL, json={"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": lot.version, "quantity": 3})
    assert result.status_code == 200, result.text
    item = result.json()["instrument"]
    assert item["kind"] == "Принадлежность" and item["parent_id"] == parent["id"]
    assert item["inventory_number"] == "СИ.002" and item["name"] == lot.name and item["quantity"] == 3
    assert item["storage_location"] == "ОТК" and item["warehouse_grn"] == lot.grn and item["manufacturer"] == lot.manufacturer
    assert item["verification_number"] is None and item["status"] == "Действует"
    updated, entries, events = snapshot(sessions, lot.id)
    assert updated.balance == Decimal(7) and updated.version == result.json()["lot_version"]
    assert len(entries) == 1 and entries[0].quantity == Decimal(3)
    assert item["warehouse_writeoff_id"] == str(entries[0].id)
    assert entries[0].purpose == entries[0].reason == "Принадлежности СИ СИ.001"
    assert sorted(name for name, _ in events) == ["production.instrument_registered", "production.instrument_registered", "warehouse.written_off"]
    for source in (entries[0].purpose, "Другое назначение"):
        with pytest.raises(WarehouseValidationError, match="Это принадлежность СИ"):
            return_writeoff(ReturnWriteOffCommand(
                lot_id=lot.id, expected_version=updated.version, quantity=Decimal(3), source=source,
                reason="Возврат принадлежности", actor=Actor(id=MASTER_ID, name="ОТК", department="ОТК"), now=NOW,
            ), SqlAlchemyWarehouseUnitOfWork(sessions))
    response = client.post(f"/api/production/instruments/{item['id']}/return-to-stock", json={"version": item["version"]})
    assert response.status_code == 200, response.text
    returned = response.json()["instrument"]
    assert returned == {**item, "version": item["version"] + 1, "status": "Не используется"}
    final, journal, events = snapshot(sessions, lot.id)
    assert final.balance == Decimal(10) and final.version == updated.version + 1
    assert journal[0] in entries or journal[1] in entries
    returns = [entry for entry in journal if entry.kind == "return"]
    assert len(returns) == 1 and returns[0].quantity == Decimal(3)
    assert returns[0].source_key == f"instrument-return:{item['warehouse_writeoff_id']}"
    assert sorted(name for name, _ in events) == sorted([
        "production.instrument_registered", "production.instrument_registered", "warehouse.written_off",
        "warehouse.writeoff_returned", "production.instrument_returned_to_stock",
    ])
    before = snapshot(sessions, lot.id)
    repeated = client.post(f"/api/production/instruments/{item['id']}/return-to-stock", json={"version": returned["version"]})
    assert repeated.status_code == 422 and "уже возвращена" in repeated.json()["detail"]
    assert snapshot(sessions, lot.id) == before


@pytest.mark.parametrize("case,status", [("version", 409), ("balance", 422), ("parent", 422), ("duplicate", 422), ("fraction", 422), ("negative", 422), ("accessory_parent", 422)])
def test_from_stock_failure_rolls_back_lot_journal_audit_and_instrument(production_api, case, status):
    client, _ = production_api
    parent, lot, sessions = setup(client)
    body = {"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": lot.version, "quantity": 2}
    if case == "version": body["lot_version"] += 1
    if case == "balance": body["quantity"] = 11
    if case == "parent": body["parent_id"] = str(uuid4())
    if case == "duplicate": body["inventory_number"] = parent["inventory_number"]
    if case == "fraction": body["quantity"] = 1.5
    if case == "negative": body["quantity"] = -1
    if case == "accessory_parent":
        body["parent_id"] = create(client, "СИ.003", kind="Принадлежность", status="Действует", parent_id=parent["id"])["id"]
    before = snapshot(sessions, lot.id)
    instruments_before = client.get("/api/production/instruments").json()
    response = client.post(URL, json=body)
    assert response.status_code == status, response.text
    assert snapshot(sessions, lot.id) == before
    assert client.get("/api/production/instruments").json() == instruments_before


def test_from_stock_final_failure_rolls_back_flushed_writes(production_api, monkeypatch):
    from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository

    client, _ = production_api
    parent, lot, sessions = setup(client)
    before = snapshot(sessions, lot.id)
    original = SqlAlchemyInstrumentRepository.save
    def fail_after_save(repository, instrument):
        original(repository, instrument)
        repository.session.flush()
        raise ProductionValidationError("Ошибка записи СИ")
    monkeypatch.setattr(SqlAlchemyInstrumentRepository, "save", fail_after_save)
    result = client.post(URL, json={"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": lot.version, "quantity": 2})
    assert result.status_code == 422
    assert snapshot(sessions, lot.id) == before
    assert client.get("/api/production/instruments").json()["instruments"] == [parent]


def test_from_stock_accepts_overrides_and_requires_quality_edit(production_api):
    client, _ = production_api
    parent, lot, sessions = setup(client)
    body = {"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": lot.version, "quantity": 1,
            "inventory_number": "СИ.010", "name": "Адаптер", "doc_designation": "SMA", "storage_location": "Лаборатория"}
    _as({"production": "edit"})
    assert client.post(URL, json=body).status_code == 403
    _as({"quality": "edit", "production": "view"})
    response = client.post(URL, json=body)
    assert response.status_code == 200, response.text
    item = response.json()["instrument"]
    assert all(item[key] == body[key] for key in ("inventory_number", "name", "doc_designation", "storage_location"))


@pytest.mark.parametrize("case,status", [("version", 409), ("manual", 422), ("missing", 404), ("forbidden", 403)])
def test_return_failure_leaves_both_sides_unchanged(production_api, case, status):
    client, _ = production_api
    parent, lot, sessions = setup(client)
    item = client.post(URL, json={"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": lot.version, "quantity": 2}).json()["instrument"]
    version = item["version"]
    if case == "version": version += 1
    if case == "manual": item = create(client, "СИ.003", kind="Принадлежность", status="Действует", parent_id=parent["id"])
    if case == "missing": item = {**item, "id": str(uuid4())}
    before = snapshot(sessions, lot.id)
    records = client.get("/api/production/instruments").json()
    if case == "forbidden": _as({"production": "edit", "warehouse": "edit"})
    response = client.post(f"/api/production/instruments/{item['id']}/return-to-stock", json={"version": version})
    assert response.status_code == status, response.text
    assert snapshot(sessions, lot.id) == before
    assert client.get("/api/production/instruments").json() == records


@pytest.mark.parametrize("error,status", [(ProductionValidationError, 422), (ConcurrentProductionUpdate, 409)])
def test_return_final_failure_rolls_back_stock_journal_and_audit(production_api, monkeypatch, error, status):
    from app.modules.production.infrastructure import SqlAlchemyInstrumentRepository

    client, _ = production_api
    parent, lot, sessions = setup(client)
    item = client.post(URL, json={"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": lot.version, "quantity": 2}).json()["instrument"]
    before = snapshot(sessions, lot.id)
    records = client.get("/api/production/instruments").json()
    original = SqlAlchemyInstrumentRepository.save
    def fail_after_save(repository, instrument):
        original(repository, instrument)
        repository.session.flush()
        raise error("Ошибка записи СИ")
    monkeypatch.setattr(SqlAlchemyInstrumentRepository, "save", fail_after_save)
    response = client.post(f"/api/production/instruments/{item['id']}/return-to-stock", json={"version": item["version"]})
    assert response.status_code == status, response.text
    assert snapshot(sessions, lot.id) == before
    assert client.get("/api/production/instruments").json() == records


def test_each_accessory_returns_its_original_writeoff_once_even_after_reactivation(production_api):
    client, _ = production_api
    parent, lot, sessions = setup(client)
    items = []
    version = lot.version
    for quantity in (2, 3):
        response = client.post(URL, json={"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": version, "quantity": quantity})
        assert response.status_code == 200, response.text
        items.append(response.json()["instrument"])
        version = response.json()["lot_version"]
    assert items[0]["warehouse_writeoff_id"] != items[1]["warehouse_writeoff_id"]
    for item in reversed(items):
        response = client.post(f"/api/production/instruments/{item['id']}/return-to-stock", json={"version": item["version"]})
        assert response.status_code == 200, response.text
    assert snapshot(sessions, lot.id)[0].balance == Decimal(10)
    item = items[0]
    reactivated = client.patch(f"/api/production/instruments/{item['id']}", json={"version": 2, "status": "Действует"}).json()["instrument"]
    before = snapshot(sessions, lot.id)
    response = client.post(f"/api/production/instruments/{item['id']}/return-to-stock", json={"version": reactivated["version"]})
    assert response.status_code == 422
    assert snapshot(sessions, lot.id) == before


def test_warehouse_http_return_flag_and_undo_cannot_bypass_workflow(production_api, monkeypatch):
    client, _ = production_api
    parent, lot, sessions = setup(client)
    monkeypatch.setattr(app.state, "warehouse_uow_factory", lambda: SqlAlchemyWarehouseUnitOfWork(sessions), raising=False)
    _as({"quality": "edit", "production": "view", "warehouse": "edit"})
    item = client.post(URL, json={"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": lot.version, "quantity": 2}).json()["instrument"]
    written = snapshot(sessions, lot.id)[0]
    before = snapshot(sessions, lot.id)
    response = client.post(f"/api/stock/batches/{lot.id}/return", json={
        "version": written.version, "quantity": "2", "source": "Другое назначение",
        "instrument_writeoff_id": item["warehouse_writeoff_id"],
    })
    assert response.status_code == 422, response.text
    assert "Это принадлежность СИ" in response.json()["detail"]
    assert snapshot(sessions, lot.id) == before
    for returned in (False, True):
        if returned:
            response = client.post(f"/api/production/instruments/{item['id']}/return-to-stock", json={"version": item["version"]})
            assert response.status_code == 200, response.text
        before = snapshot(sessions, lot.id)
        records = client.get("/api/production/instruments").json()
        preview = client.get(f"/api/stock/batches/{lot.id}/undo")
        assert preview.status_code == 200, preview.text
        assert preview.json()["undo"] is None
        assert "Это принадлежность СИ" in preview.json()["reason"]
        response = client.post(f"/api/stock/batches/{lot.id}/undo", json={"version": before[0].version})
        assert response.status_code == 422, response.text
        assert "Это принадлежность СИ" in response.json()["detail"]
        assert snapshot(sessions, lot.id) == before
        assert client.get("/api/production/instruments").json() == records


def test_stock_linked_accessory_quantity_kind_and_grn_change_only_through_return(production_api):
    client, _ = production_api
    parent, lot, sessions = setup(client)
    response = client.post(URL, json={"parent_id": parent["id"], "lot_id": str(lot.id), "lot_version": lot.version, "quantity": 3})
    assert response.status_code == 200, response.text
    item = response.json()["instrument"]
    before = snapshot(sessions, lot.id)
    for change in ({"quantity": 7}, {"kind": "Средство измерений"}, {"warehouse_grn": "OLD.26.0000001"}):
        response = client.patch(f"/api/production/instruments/{item['id']}", json={"version": item["version"], **change})
        assert response.status_code == 422, (change, response.text)
        assert "возвратом на склад" in response.json()["detail"]
    same = client.patch(f"/api/production/instruments/{item['id']}", json={"version": item["version"], "quantity": 3, "storage_location": "ОТК, шкаф 2"})
    assert same.status_code == 200, same.text
    assert same.json()["instrument"]["quantity"] == 3
    assert snapshot(sessions, lot.id)[0] == before[0]