"""ОЖИД. ДАТА и ФАКТ. ДАТА между закупкой и приёмкой end to end on SQLite (OwnerTick.gs:949-1000):
ФАКТ. ДАТА строки — поздняя дата ВК среди её поставок (с партиями `-ПN`), ОЖИД. ДАТА закупщика уходит во все
открытые поставки строки — сразу при правке и ещё раз тиком, если копия промахнулась."""

from datetime import date, datetime, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.procurement.application import (
    SetExpectedArrivalCommand,
    set_procurement_expected_arrival,
    sync_receipt_stages,
)
from app.modules.procurement.domain import ProcurementStatus
from app.modules.receiving.application import (
    InspectReceivingCommand,
    dispatch_next_receiving_expected,
    inspect_receiving,
)
from app.modules.receiving.domain import InspectionInput, ReceivingStatus
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from app.workflows.receipt_stage import ReceivingStatusLookup
from tests.factories import Actor
from tests.procurement.test_backorder_flow import BUYER, NOW, RECEIVER, db, paid_procurement_line  # noqa: F401

TICK = datetime(2026, 9, 27, 12, 0)


def sync(db):
    return sync_receipt_stages(TICK, db.procurement(), ReceivingStatusLookup(db.receiving))


def procurement_line(db, line_id):
    with db.procurement() as uow:
        return uow.procurement.get(line_id)


def receiving_lines(db, procurement_line_id):
    with db.receiving() as uow:
        rows = uow.receiving.open_by_procurement_lines([procurement_line_id])
        closed = [
            line for line in uow.receiving.by_status(ReceivingStatus.ACCEPTED)
            if line.procurement_line_id == procurement_line_id
        ]
    return {line.legacy_id: line for line in [*rows, *closed]}


def split(db, line, *quantities):
    """Two manufacturer batches of one delivery: the line itself and `-П2`, both still «В пути»."""
    with db.receiving() as uow:
        current = uow.receiving.get(line.id)
        parts = current.split_into_batches(
            [Decimal(quantity) for quantity in quantities],
            existing_legacy_ids=uow.receiving.legacy_ids_starting_with(current.legacy_id),
            now=NOW,
        )
        for part in parts:
            uow.receiving.add(part)
        uow.commit()
    return parts


def inspect_on(db, line, quantity, day):
    return inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=InspectionInput(
                fact_quantity=Decimal(quantity),
                document_quantity=Decimal(quantity),
                ordered_quantity=Decimal("0"),
                has_shipping_document=True,
                quality_document_required=False,
                quality_document_present=False,
                shipping_document_url="https://files.test/upd.pdf",
                expiry_date=date(2027, 1, 1),
                inspection_date=day,
            ),
            actor=RECEIVER,
            now=datetime.combine(day, time(12, 0)),
        ),
        db.receiving(),
    )


def audit(db, event_type):
    with db.sessions() as session:
        return session.scalars(select(AuditEventRow.details).where(AuditEventRow.event_type == event_type)).all()


def test_actual_arrival_is_the_latest_inspection_among_the_batches(db):
    _, line = paid_procurement_line(db)
    delivery = dispatch_next_receiving_expected(NOW, db.receiving())
    first, second = split(db, delivery, "6", "4")
    assert sync(db) == 0
    assert procurement_line(db, line.id).actual_arrival is None

    inspect_on(db, first, "6", date(2026, 9, 24))
    assert sync(db) == 1
    current = procurement_line(db, line.id)
    assert (current.status, current.actual_arrival) == (ProcurementStatus.IN_TRANSIT, date(2026, 9, 24))
    assert sync(db) == 0

    inspect_on(db, second, "4", date(2026, 9, 26))
    assert sync(db) == 1
    current = procurement_line(db, line.id)
    assert (current.status, current.actual_arrival) == (ProcurementStatus.DONE, date(2026, 9, 26))
    assert current.version == line.version + 2  # one step per tick, the stage and the date together
    assert audit(db, "procurement.actual_arrival") == [
        {"legacy_id": line.legacy_id, "actual_arrival": "2026-09-24"},
        {"legacy_id": line.legacy_id, "actual_arrival": "2026-09-26"},
    ]
    assert audit(db, "procurement.receipt_stage") == [
        {"legacy_id": line.legacy_id, "from_status": "В пути", "to_status": "Готово"},
    ]


def test_buyer_date_goes_to_every_open_batch_and_skips_the_accepted_one(db):
    _, line = paid_procurement_line(db)
    delivery = dispatch_next_receiving_expected(NOW, db.receiving())
    first, second, third = split(db, delivery, "4", "3", "3")
    inspect_on(db, first, "4", date(2026, 9, 24))
    assert sync(db) == 1  # ФАКТ. ДАТА of the first batch; the line stays «В пути»
    line = procurement_line(db, line.id)

    moved = set_procurement_expected_arrival(
        SetExpectedArrivalCommand(line.id, line.version, date(2026, 10, 2), BUYER, NOW),
        db.procurement(),
        ReceivingStatusLookup(db.receiving),
    )
    assert moved.expected_arrival == date(2026, 10, 2)
    deliveries = receiving_lines(db, line.id)
    assert {legacy_id: item.expected_arrival for legacy_id, item in deliveries.items()} == {
        first.legacy_id: date(2026, 9, 25),  # accepted: keeps the date it was received against
        second.legacy_id: date(2026, 10, 2),
        third.legacy_id: date(2026, 10, 2),
    }
    assert second.legacy_id.endswith("-П2") and third.legacy_id.endswith("-П3")
    copied = audit(db, "receiving.expected_arrival_copied")
    assert sorted(item["legacy_id"] for item in copied) == [second.legacy_id, third.legacy_id]
    assert {item["previous_expected_arrival"] for item in copied} == {"2026-09-25"}
    assert sync(db) == 0  # nothing left for the tick to repair


def test_tick_repairs_a_delivery_created_after_the_date_changed(db):
    _, line = paid_procurement_line(db)
    # The buyer moves the date while receiving.expected (snapshot with the old date) still waits in the outbox.
    moved = set_procurement_expected_arrival(
        SetExpectedArrivalCommand(line.id, line.version, date(2026, 10, 5), BUYER, NOW),
        db.procurement(),
        ReceivingStatusLookup(db.receiving),
    )
    delivery = dispatch_next_receiving_expected(NOW, db.receiving())
    assert delivery.expected_arrival == date(2026, 9, 25)

    assert sync(db) == 1
    (repaired,) = receiving_lines(db, line.id).values()
    assert repaired.expected_arrival == moved.expected_arrival == date(2026, 10, 5)
    assert sync(db) == 0


def test_expected_date_is_closed_once_the_line_is_done(db):
    _, line = paid_procurement_line(db)
    delivery = dispatch_next_receiving_expected(NOW, db.receiving())
    inspect_on(db, delivery, "10", date(2026, 9, 24))
    sync(db)
    done = procurement_line(db, line.id)
    assert done.status is ProcurementStatus.DONE
    with pytest.raises(ValueError, match="от «Жду счет» до «Карантин»"):
        set_procurement_expected_arrival(
            SetExpectedArrivalCommand(done.id, done.version, date(2026, 10, 2), BUYER, NOW),
            db.procurement(),
            ReceivingStatusLookup(db.receiving),
        )


@pytest.fixture()
def client(db):
    app.state.procurement_uow_factory = db.procurement
    app.state.receiving_uow_factory = db.receiving
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 9, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.procurement_uow_factory
    del app.state.receiving_uow_factory


def test_sheet_fields_over_http(db, client):
    _, line = paid_procurement_line(db)
    dispatch_next_receiving_expected(NOW, db.receiving())

    response = client.patch(f"/api/procurement/{line.id}/note", json={"version": line.version, "note": " ждём УПД "})
    assert response.status_code == 200, response.text
    body = response.json()["line"]
    assert body["note"] == "ждём УПД"
    assert body["actual_arrival"] is None
    assert {"calculated_price", "calculated_price_with_vat", "calculated_sum"} <= body.keys()

    stale = client.patch(f"/api/procurement/{line.id}/note", json={"version": line.version, "note": "другое"})
    assert stale.status_code == 409

    moved = client.patch(
        f"/api/procurement/{line.id}/expected-arrival",
        json={"version": body["version"], "expected_arrival": "2026-10-04"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["line"]["expected_arrival"] == "2026-10-04"
    (delivery,) = receiving_lines(db, line.id).values()
    assert delivery.expected_arrival == date(2026, 10, 4)

    too_early = client.patch(
        f"/api/procurement/{line.id}/expected-arrival",
        json={"version": moved.json()["line"]["version"], "expected_arrival": "2026-09-01"},
    )
    assert too_early.status_code == 422
    assert "раньше заказа" in too_early.json()["detail"]
