"""Receiving → procurement stage (OwnerTick.gs syncPurchaseStatusFromWaitlist_) end to end on SQLite."""

from dataclasses import replace
from datetime import datetime

from sqlalchemy import select

from app.modules.procurement.application import sync_receipt_stages
from app.modules.procurement.domain import ProcurementStatus, stage_from_receipts
from app.modules.receiving.application import dispatch_next_receiving_expected
from app.modules.receiving.domain import ReceivingStatus
from app.shared.persistence import AuditEventRow
from app.workflows.receipt_stage import ReceivingStatusLookup
from tests.procurement.test_backorder_flow import NOW, db, inspect, paid_procurement_line  # noqa: F401

TICK = datetime(2026, 9, 24, 12, 5)


def sync(db):
    return sync_receipt_stages(TICK, db.procurement(), ReceivingStatusLookup(db.receiving))


def procurement_status(db, line_id):
    with db.procurement() as uow:
        return uow.procurement.get(line_id).status


def test_partitions_resolve_to_the_least_advanced_mapped_stage():
    assert stage_from_receipts([]) is None
    assert stage_from_receipts(["Возврат", "Архив ВК"]) is None
    assert stage_from_receipts(["Принято", "Архив ВК"]) is ProcurementStatus.DONE
    assert stage_from_receipts(["Принято", "В пути"]) is ProcurementStatus.IN_TRANSIT
    assert stage_from_receipts(["Принято", "Ожидание"]) is ProcurementStatus.QUARANTINE
    assert stage_from_receipts([" Карантин ", "Принято"]) is ProcurementStatus.QUARANTINE


def test_receipt_moves_a_paid_line_forward_only(db):
    _, line = paid_procurement_line(db)
    assert line.status is ProcurementStatus.IN_TRANSIT
    assert sync(db) == 0  # nothing expected yet

    expected = dispatch_next_receiving_expected(NOW, db.receiving())
    assert sync(db) == 0  # «В пути» in receiving = «В пути» in procurement
    accepted = inspect(db, expected, "10")
    assert accepted.status is ReceivingStatus.ACCEPTED

    assert sync(db) == 1
    assert procurement_status(db, line.id) is ProcurementStatus.DONE
    assert sync(db) == 0
    with db.sessions() as session:
        events = session.scalars(
            select(AuditEventRow.details).where(AuditEventRow.event_type == "procurement.receipt_stage")
        ).all()
    assert events == [{"legacy_id": line.legacy_id, "from_status": "В пути", "to_status": "Готово"}]


def test_a_nonconformity_is_quarantine_and_an_offline_line_is_left_alone(db):
    _, line = paid_procurement_line(db)
    expected = dispatch_next_receiving_expected(NOW, db.receiving())
    # A missing УПД still holds the line in «Ожидание» (a shortfall no longer does, owner 05.10.2026).
    waiting = inspect(db, expected, "10", shipping_document_url=None, has_shipping_document=False)
    assert waiting.status is ReceivingStatus.WAITING

    assert sync(db) == 1
    assert procurement_status(db, line.id) is ProcurementStatus.QUARANTINE

    # The buyer parks the line («Согласование»): receiving no longer moves it.
    with db.procurement() as uow:
        current = uow.procurement.get(line.id)
        uow.procurement.add(replace(current, status=ProcurementStatus.APPROVAL, version=current.version + 1))
        uow.commit()
    inspect(db, waiting, "10")
    assert sync(db) == 0
    assert procurement_status(db, line.id) is ProcurementStatus.APPROVAL


def test_done_is_never_rolled_back(db):
    _, line = paid_procurement_line(db)
    done = replace(line, status=ProcurementStatus.DONE)
    assert done.advance_by_receipt(ProcurementStatus.IN_TRANSIT) is done
    quarantine = replace(line, status=ProcurementStatus.QUARANTINE)
    assert quarantine.advance_by_receipt(ProcurementStatus.IN_TRANSIT) is quarantine
    moved = quarantine.advance_by_receipt(ProcurementStatus.DONE)
    assert (moved.status, moved.version) == (ProcurementStatus.DONE, line.version + 1)
