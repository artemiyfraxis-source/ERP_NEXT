from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.domain import ProductionValidationError
from app.modules.warehouse.application import ReturnWriteOffCommand, return_writeoff
from app.modules.warehouse.domain import StockLot
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


def test_from_stock_commits_both_sides_and_existing_return_remains_available(production_api):
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
    assert entries[0].purpose == entries[0].reason == "Принадлежности СИ СИ.001"
    assert sorted(name for name, _ in events) == ["production.instrument_registered", "production.instrument_registered", "warehouse.written_off"]
    returned = return_writeoff(ReturnWriteOffCommand(
        lot_id=lot.id, expected_version=updated.version, quantity=Decimal(3), source=entries[0].purpose,
        reason="Возврат принадлежности", actor=Actor(id=MASTER_ID, name="ОТК", department="ОТК"), now=NOW,
    ), SqlAlchemyWarehouseUnitOfWork(sessions))
    assert returned.balance == Decimal(10)


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
