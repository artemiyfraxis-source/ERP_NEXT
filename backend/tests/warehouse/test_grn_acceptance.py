"""GRN-B acceptance transactions, whole-place decisions and HTTP authorization."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.main import app
from app.modules.production.infrastructure import ProductionPlanProjectRow
from app.modules.warehouse import receipts_application
from app.modules.warehouse.application import (
    AddManualStockCommand, ManualStockItem, PoisonWarehouseMessage, add_manual_stock, dispatch_next_warehouse_received,
)
from app.modules.warehouse.domain import WarehouseValidationError
from app.modules.warehouse.grn_registry import GrnCounterRow, GrnRegistryRow, MaterialOwnerRow
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork, StockLotRow
from app.modules.warehouse.label_jobs import LabelJobRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.shared.events import OutboxMessage
from tests.factories import Actor
from tests.warehouse.test_receipts import LINE, NOW, _accept, _receipt, stand  # noqa: F401


def permit_quality():
    actor = Actor(name="Инспектор ОТК", department="ОТК")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="qc@example.test", roles=frozenset(),
        permissions={"quality": "edit", "warehouse": "edit", "fulfillment": "edit", "handout": "edit"})
    return actor


def quality(client, lot, state="RELEASED", **values):
    return client.post(f"/api/stock/batches/{lot['id']}/quality", json={
        "version": lot["version"], "quality_version": lot["quality_version"],
        "state": state, "basis": "Проверено всё место по акту ВК №1", **values})


def csm_context(sessions, project="591К.3", **changes):
    owner = uuid4()
    with sessions.begin() as session:
        session.add(MaterialOwnerRow(id=owner, display_name="Заказчик", legal_identity=f"TEST:{owner}"))
        if session.get(ProductionPlanProjectRow, project) is None:
            session.add(ProductionPlanProjectRow(project=project, customer="Текст заказчика не является ключом",
                status="", terminal=False, imported_at=NOW))
    return {"owner_id": str(owner), "customer_order": project, "transfer_document": "Акт передачи №1", **changes}


def test_new_line_requires_category_and_operation_identity(stand):
    client, _ = stand
    receipt = _receipt(client)
    for payload in ({k: v for k, v in LINE.items() if k != "grn_category"}, LINE):
        assert client.post(f"/api/stock/receipts/{receipt['id']}/accept", json=payload).status_code == 422


@pytest.mark.parametrize("changes", [
    {"grn_category": "GOS", "grn_context": {"purpose": "Уборка"}},
    {"grn_context": {"purpose": "Другой контекст"}}, {"quantity": "11"}, {"labels": 2},
])
def test_manual_retry_is_one_identity_lot_job_and_changed_body_conflicts(stand, changes):
    client, sessions = stand
    receipt, key = _receipt(client), str(uuid4())
    first = _accept(client, receipt["id"], acceptance_id=key)
    assert first.status_code == 200, first.text
    assert _accept(client, receipt["id"], acceptance_id=key).json() == first.json()
    assert _accept(client, receipt["id"], acceptance_id=key, **changes).status_code == 409
    other = _receipt(client)
    assert _accept(client, other["id"], acceptance_id=key).status_code == 409
    with sessions() as session:
        assert session.query(StockLotRow).count() == session.query(GrnRegistryRow).count() == 1
        assert session.query(LabelJobRow).count() == 1
        assert session.scalar(select(GrnCounterRow.last_serial)) == 1


def test_retry_finds_relocated_place_and_does_not_reprint(stand):
    client, sessions = stand
    receipt, key = _receipt(client), str(uuid4())
    first = _accept(client, receipt["id"], acceptance_id=key).json()
    lot = first["lot"]
    moved = client.post(f"/api/stock/batches/{lot['id']}/move",
                        json={"version": lot["version"], "warehouse": "ЖУК", "storage_location": "2A2"})
    assert moved.status_code == 200
    retry = _accept(client, receipt["id"], acceptance_id=key)
    assert retry.status_code == 200, retry.text
    assert retry.json()["lot"]["warehouse"] == "ЖУК"
    assert retry.json()["label_job_id"] == first["label_job_id"]
    with sessions() as session:
        assert session.query(LabelJobRow).count() == 1


def test_hold_is_persisted_before_label_render_and_print_db_error_does_not_cost_stock(stand, monkeypatch):
    client, sessions = stand
    receipt = _receipt(client)
    seen = []
    def fail_job(session, **_kwargs):
        lot = session.scalars(select(StockLotRow)).one()
        seen.append((lot.quality_state, lot.grn_category))
        session.execute(insert(GrnCounterRow).values(category="PPM", full_year=2026, last_serial=-1))
    monkeypatch.setattr(receipts_application, "add_job", fail_job)
    response = _accept(client, receipt["id"])
    assert response.status_code == 200, response.text
    assert seen == [("IQH", "PPM")]
    assert response.json()["label_error"] and response.json()["label_job_id"] is None
    with sessions() as session:
        assert session.query(GrnRegistryRow).count() == session.query(StockLotRow).count() == 1


def test_failed_acceptance_rolls_back_identity_counter_stock_and_job(stand, monkeypatch):
    client, sessions = stand
    receipt = _receipt(client)
    original = SqlAlchemyWarehouseUnitOfWork.commit
    def fail(_uow):
        raise WarehouseValidationError("Прервано до фиксации")
    monkeypatch.setattr(SqlAlchemyWarehouseUnitOfWork, "commit", fail)
    response = _accept(client, receipt["id"])
    assert response.status_code == 422
    with sessions() as session:
        for model in (GrnRegistryRow, GrnCounterRow, StockLotRow, LabelJobRow):
            assert session.query(model).count() == 0
    monkeypatch.setattr(SqlAlchemyWarehouseUnitOfWork, "commit", original)
    assert _accept(client, receipt["id"]).json()["lot"]["grn"] == "PPM.26.000001"


@pytest.mark.parametrize("category,context", [("GOS", {"purpose": "Уборка"}), ("PAM", {"equipment": "ОБ.1"}),
    ("FRX", {"production_order": "ORDER-1", "acceptance_document": "ACT-1"})])
def test_nonrequired_routes_record_basis_without_shipping_papers(stand, category, context):
    client, _ = stand
    receipt = _receipt(client, upd_number="", invoice_number="")
    response = _accept(client, receipt["id"], grn_category=category, grn_context=context)
    assert response.status_code == 200, response.text
    lot = response.json()["lot"]
    assert lot["quality_state"] == "NOT_REQUIRED" and "ВК не требуется" in lot["quality_basis"]
    assert lot["quality_decided_by"] and lot["quality_decided_at"]
    issued = client.post(f"/api/stock/batches/{lot['id']}/writeoff",
                         json={"version": lot["version"], "quantity": "1", "purpose": "Цех"})
    assert issued.status_code == 200, issued.text


def test_quality_requires_permission_whole_place_basis_and_both_versions(stand):
    client, sessions = stand
    lot = _accept(client, _receipt(client)["id"]).json()["lot"]
    assert quality(client, lot).status_code == 403
    actor = permit_quality()
    assert quality(client, lot, quantity="1").status_code == 422
    assert quality(client, lot, basis=" ").status_code == 422
    assert quality(client, lot, state="NOT_REQUIRED").status_code == 422
    assert quality(client, lot, quality_version=2).status_code == 409
    response = quality(client, lot)
    assert response.status_code == 200, response.text
    released = response.json()["lot"]
    assert released["version"] == lot["version"] + 1 and released["quality_version"] == 2
    assert released["quality_decided_by"] == str(actor.id)
    assert quality(client, lot).status_code == 409
    with sessions() as session:
        event = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "warehouse.row_edited")).one()
        assert event.details["scope"] == "whole_place" and event.details["balance"] == "10.000000"


@pytest.mark.parametrize("expiry", [None, "2020-01-01"])
def test_release_cannot_make_unknown_required_or_expired_stock_issuable(stand, expiry):
    client, _ = stand
    lot = _accept(client, _receipt(client)["id"], expiry_required=True, expiry_date=expiry).json()["lot"]
    permit_quality()
    assert quality(client, lot).status_code == 422


def test_quality_update_wins_against_a_stale_reservation(stand):
    client, sessions = stand
    lot = _accept(client, _receipt(client)["id"]).json()["lot"]
    actor = permit_quality()
    released = quality(client, lot).json()["lot"]
    with SqlAlchemyWarehouseUnitOfWork(sessions) as stale:
        old = stale.warehouse.get(UUID(lot["id"]))
        held = quality(client, released, "QTN")
        assert held.status_code == 200
        with pytest.raises(StaleDataError):
            stale.warehouse.add(old.reserve(1, on_date=NOW.date()))
            stale.commit()
    with sessions() as session:
        stored = session.get(StockLotRow, UUID(lot["id"]))
        assert stored.quality_state == "QTN" and stored.reserved == 0


@pytest.mark.parametrize("failure", ["owner", "project", "document", "mismatch"])
def test_csm_acceptance_validates_owner_project_and_transfer_document(stand, failure):
    client, sessions = stand
    context = csm_context(sessions)
    if failure == "owner":
        context["owner_id"] = str(uuid4())
    elif failure == "project":
        context["customer_order"] = "Не проект"
    elif failure == "document":
        context.pop("transfer_document")
    else:
        context["project"] = "999К.1"
    response = _accept(client, _receipt(client)["id"], grn_category="CSM", grn_context=context)
    assert response.status_code == 422, response.text
    with sessions() as session:
        assert session.query(GrnRegistryRow).count() == session.query(StockLotRow).count() == 0


def test_mixed_quick_entry_allocates_per_category_and_retries_atomically(stand):
    client, sessions = stand
    payload = {"operation_id": str(uuid4()), "items": [
        {"article": "A", "quantity": "2", "storage_location": "2A1", "grn_category": "PPM"},
        {"article": "A", "quantity": "2", "storage_location": "2A2", "grn_category": "GOS",
         "grn_context": {"purpose": "Уборка"}}]}
    first = client.post("/api/stock/batches", json=payload)
    assert first.status_code == 200, first.text
    assert [lot["grn"] for lot in first.json()["lots"]] == ["PPM.26.000001", "GOS.26.000001"]
    assert client.post("/api/stock/batches", json=payload).json() == first.json()
    payload["items"][1]["quantity"] = "3"
    assert client.post("/api/stock/batches", json=payload).status_code == 409
    with sessions() as session:
        assert session.query(GrnRegistryRow).count() == session.query(StockLotRow).count() == 2


@pytest.mark.parametrize("all_invalid", [True, False])
def test_quick_retry_cannot_replace_the_valid_subset(stand, all_invalid):
    client, sessions = stand
    row = {"article": "A", "quantity": "1", "storage_location": "2A1", "grn_category": "PPM"}
    payload = {"operation_id": str(uuid4()), "items": [row, {**row, "quantity": "0"}]}
    assert client.post("/api/stock/batches", json=payload).json()["added"] == 1
    payload["items"] = [{**row, "quantity": "0"}, {**row, "quantity": "0" if all_invalid else "1"}]
    assert client.post("/api/stock/batches", json=payload).status_code == 409
    with sessions() as session:
        assert session.query(StockLotRow).count() == 1


def test_owner_registry_normalizes_legal_identity_and_returns_stable_owner(stand):
    client, _ = stand
    body = {"display_name": "  Заказчик  1 ", "legal_identity": " inn: 7707083893 "}
    first = client.post("/api/stock/material-owners", json=body)
    assert first.status_code == 200, first.text
    owner = first.json()["owner"]
    assert owner["legal_identity"] == "ИНН:7707083893" and owner["display_name"] == "Заказчик 1"
    body["legal_identity"] = "ИНН:7707083893"
    assert client.post("/api/stock/material-owners", json=body).json()["owner"] == owner
    assert client.get("/api/stock/material-owners").json()["owners"] == [owner]
    body["legal_identity"] = "ИНН:123"
    assert client.post("/api/stock/material-owners", json=body).status_code == 422


def test_nonfinite_quantity_is_a_row_error_not_server_error(stand):
    client, _ = stand
    payload = ('{"operation_id":"' + str(uuid4()) + '","items":[{"article":"A","quantity":1e999,'
               '"storage_location":"2A1","grn_category":"PPM"}]}')
    result = client.post("/api/stock/batches", content=payload, headers={"Content-Type": "application/json"})
    assert result.status_code == 200, result.text
    assert result.json()["added"] == 0 and len(result.json()["errors"]) == 1


def test_identical_quick_commands_concurrently_commit_one_result(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'quick.db'}", connect_args={"timeout": 30})
    create_schema(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    command = AddManualStockCommand(operation_id=uuid4(), actor=Actor(name="Склад", department="Склад"), now=NOW, items=(ManualStockItem(
        warehouse="ЦАР", article="A", name="", item_type="", purpose="", unit="шт", quantity="2",
        storage_location="2A1", grn_category="PPM"),))
    barrier = Barrier(2)
    def run():
        barrier.wait(timeout=10)
        return add_manual_stock(command, SqlAlchemyWarehouseUnitOfWork(sessions))
    try:
        with ThreadPoolExecutor(2) as pool:
            results = [future.result(timeout=40) for future in [pool.submit(run), pool.submit(run)]]
        assert results[0].lots[0].id == results[1].lots[0].id
        with sessions() as session:
            assert session.query(StockLotRow).count() == session.query(GrnRegistryRow).count() == 1
            assert session.scalar(select(GrnCounterRow.last_serial)) == 1
    finally:
        engine.dispose()


def test_csm_retry_survives_project_snapshot_refresh(stand):
    client, sessions = stand
    context = csm_context(sessions)
    receipt, key = _receipt(client), str(uuid4())
    first = _accept(client, receipt["id"], acceptance_id=key, grn_category="CSM", grn_context=context)
    assert first.status_code == 200, first.text
    with sessions.begin() as session:
        session.execute(delete(ProductionPlanProjectRow))
    assert _accept(client, receipt["id"], acceptance_id=key, grn_category="CSM", grn_context=context).json() == first.json()


def test_received_replay_after_transfer_preserves_one_place_and_never_tops_up_released_stock(stand):
    client, sessions = stand
    lot = _accept(client, _receipt(client)["id"]).json()["lot"]
    permit_quality()
    lot = quality(client, lot).json()["lot"]
    payload = {key: lot[key] for key in (
        "grn", "grn_id", "grn_category", "grn_context", "grn_contract_version", "quality_state", "quality_basis",
        "quality_decided_by", "quality_decided_at", "quality_version", "expiry_required", "warehouse", "package",
        "name", "article", "item_type", "purpose", "unit", "storage_location")}
    payload.update(quantity="10", receiving_line_id=str(uuid4()), receipt_id=lot["source_receiving_ids"][0])
    moved = client.post(f"/api/stock/batches/{lot['id']}/move", json={"version": lot["version"], "warehouse": "ЖУК"})
    assert moved.status_code == 200, moved.text
    def enqueue(values):
        with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
            uow.outbox.add(OutboxMessage(id=uuid4(), topic="warehouse.received", idempotency_key=str(uuid4()),
                                       created_at=NOW, payload=values))
            uow.commit()
    enqueue(payload)
    found = dispatch_next_warehouse_received(NOW, SqlAlchemyWarehouseUnitOfWork(sessions))
    assert str(found.id) == lot["id"] and found.warehouse == "ЖУК" and found.inbound == 10
    enqueue({**payload, "receipt_id": str(uuid4())})
    with pytest.raises(PoisonWarehouseMessage, match="отдельного ГРН"):
        dispatch_next_warehouse_received(NOW, SqlAlchemyWarehouseUnitOfWork(sessions))
    with sessions() as session:
        stored = session.query(StockLotRow).one()
        assert stored.inbound == 10 and stored.quality_state == "RELEASED"


def test_receiving_csm_payload_cannot_change_the_registry_order(stand):
    client, sessions = stand
    context = csm_context(sessions)
    lot = _accept(client, _receipt(client)["id"], grn_category="CSM", grn_context=context).json()["lot"]
    payload = {key: lot[key] for key in (
        "grn", "grn_id", "grn_category", "grn_context", "grn_contract_version", "material_owner_id",
        "csm_project_id", "quality_state", "quality_version", "expiry_required", "warehouse", "package",
        "name", "article", "item_type", "purpose", "unit", "storage_location")}
    payload.update(csm_project_id="592К.1", quantity="10", receiving_line_id=str(uuid4()))
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        uow.outbox.add(OutboxMessage(id=uuid4(), topic="warehouse.received", idempotency_key=str(uuid4()),
                                   created_at=NOW, payload=payload))
        uow.commit()
    with pytest.raises(PoisonWarehouseMessage, match="владелец и заказ"):
        dispatch_next_warehouse_received(NOW, SqlAlchemyWarehouseUnitOfWork(sessions))
    with sessions() as session:
        assert session.query(StockLotRow).one().csm_project_id == context["customer_order"]


def test_receiving_can_read_material_owners_but_cannot_create_them(stand):
    client, sessions = stand
    csm_context(sessions)
    actor = Actor(name="Receiver", department="Receiving")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="receiver@example.test", roles=frozenset(), permissions={"receiving":"view"})
    response = client.get("/api/stock/material-owners")
    assert response.status_code == 200 and len(response.json()["owners"]) == 1
    assert client.post("/api/stock/material-owners", json={"display_name":"Owner","legal_identity":"TEST:1"}).status_code == 403


def _journal(sessions):
    from app.modules.warehouse.infrastructure import WriteOffEntryRow
    with sessions() as session:
        return [(row.reason, row.purpose) for row in session.scalars(select(WriteOffEntryRow).order_by(WriteOffEntryRow.occurred_at))]


def test_disposal_is_decided_by_the_reason_with_a_fixed_destination_never_by_the_purpose_text(stand):
    client, sessions = stand
    lot = _accept(client, _receipt(client)["id"]).json()["lot"]
    assert lot["quality_state"] == "IQH"
    url = f"/api/stock/batches/{lot['id']}/writeoff"
    # An undecided (IQH) place cannot leave at all, whatever the reason.
    assert client.post(url, json={"version": lot["version"], "quantity": "1", "reason": "Брак"}).status_code == 422
    permit_quality()
    held = quality(client, lot, state="QTN")
    assert held.status_code == 200, held.text
    version = held.json()["lot"]["version"]
    # An issue (any reason that is not scrap/loss/inventory) of a quarantined place stays blocked, «Цех» or not.
    for purpose in ("Цех", "Иванов", "591К.3", "Станок"):
        assert client.post(url, json={"version": version, "quantity": "1", "purpose": purpose,
                                      "reason": "Выдача в производство"}).status_code == 422
    # «Брак производителя» is a note, not a reason: it is an issue as well.
    assert client.post(url, json={"version": version, "quantity": "1", "purpose": "Цех",
                                  "reason": "Брак производителя"}).status_code == 422
    # Scrap: the server fixes the destination; whatever purpose text came with it is not used.
    for reason, purpose, fixed in (("Брак", "Цех", "Утилизация — брак"), ("Порча", "Иванов", "Утилизация — порча"),
                                   ("Инвентаризация", "26.11 инвентаризация", "Инвентаризация")):
        done = client.post(url, json={"version": version, "quantity": "1", "purpose": purpose, "reason": reason})
        assert done.status_code == 200, done.text
        version = done.json()["lot"]["version"]
        assert _journal(sessions)[-1] == (reason, fixed)



def test_customer_material_is_issued_only_to_the_project_it_was_received_for(stand):
    client, sessions = stand
    context = csm_context(sessions)
    lot = _accept(client, _receipt(client)["id"], grn_category="CSM", grn_context=context).json()["lot"]
    permit_quality()
    released = quality(client, lot)
    assert released.status_code == 200, released.text
    version = released.json()["lot"]["version"]
    url = f"/api/stock/batches/{lot['id']}/writeoff"
    base = {"version": version, "quantity": "1"}
    # every non-disposal write-off is an issue and must name the lot's own project, whatever the reason says
    for reason in ("Выдача в производство", "Брак производителя", ""):
        for purpose in ("999К.9", "Цех", "Иванов"):
            assert client.post(url, json={**base, "purpose": purpose, "reason": reason}).status_code == 422
    done = client.post(url, json={**base, "purpose": "591К.3 · КБ1", "reason": "Выдача в производство"})
    assert done.status_code == 200, done.text
    # scrap of customer material cannot be pointed at another project: the destination is the fixed one
    scrapped = client.post(url, json={"version": done.json()["lot"]["version"], "quantity": "1",
                                      "purpose": "999К.9", "reason": "Брак"})
    assert scrapped.status_code == 200, scrapped.text
    assert _journal(sessions)[-1] == ("Брак", "Утилизация — брак")
