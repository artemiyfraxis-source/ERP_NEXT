from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.receiving.application import (
    InspectReceivingCommand,
    PlaceReceivingCommand,
    PoisonReceivingMessage,
    consume_receiving_expected,
    dispatch_next_receiving_expected,
    inspect_receiving,
    place_receiving,
    quarantine_overdue_waiting,
)
from app.modules.receiving.domain import InspectionInput, ReceivingStatus
from app.shared.events import OutboxMessage
from tests.factories import Actor
from uuid import UUID


class Repository:
    def __init__(self):
        self.items = {}

    def by_source_idempotency_key(self, key):
        return next((item for item in self.items.values() if item.source_idempotency_key == key), None)

    def get(self, line_id):
        return self.items.get(line_id)

    def queue(self, limit=200, after_id=None):
        items = list(self.items.values())
        if after_id is not None:
            position = next(index for index, item in enumerate(items) if item.id == after_id)
            items = items[position + 1 :]
        return items[:limit]

    def waiting_inspected_by(self, day, limit=200):
        return [
            item
            for item in self.items.values()
            if item.status is ReceivingStatus.WAITING and item.inspected_on and item.inspected_on <= day
        ][:limit]

    def all_grns(self):
        return [item.grn for item in self.items.values() if item.grn]

    def add(self, line):
        self.items[line.id] = line


class AppendOnly:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)


class Outbox(AppendOnly):
    def __init__(self):
        super().__init__()
        self.published = set()
        self.failures = []

    def by_idempotency_key(self, key):
        return next((item for item in self.items if item.idempotency_key == key), None)

    def next_unpublished(self, topic, now):
        del now
        return next(
            (
                item
                for item in self.items
                if item.topic == topic
                and item.id not in self.published
                and all(failure[0] != item.id for failure in self.failures)
            ),
            None,
        )

    def mark_published(self, message_id, published_at):
        del published_at
        self.published.add(message_id)

    def mark_failed(self, message_id, failed_at, error):
        self.failures.append((message_id, failed_at, error))


class Uow:
    def __init__(self):
        self.receiving = Repository()
        self.audit = AppendOnly()
        self.outbox = Outbox()
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1


def message():
    return OutboxMessage(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        topic="receiving.expected",
        idempotency_key="procurement:one:receiving:v1",
        created_at=datetime(2026, 9, 21, 10, 0),
        payload={
            "procurement_line_id": "20000000-0000-0000-0000-000000000001",
            "legacy_id": "SM-20260921-01",
            "warehouse": "ЦАР",
            "name": "Разъём",
            "item_type": "Комплектующие",
            "purpose": "FRX.001",
            "supplier": "ООО Поставка",
            "ordered_quantity": "10",
            "unit": "шт",
        },
    )


def inspection():
    return InspectionInput(
        fact_quantity=Decimal("10"),
        document_quantity=Decimal("10"),
        ordered_quantity=Decimal("10"),
        has_shipping_document=True,
        quality_document_required=False,
        quality_document_present=False,
        shipping_document_url="https://files.test/upd.pdf",
        expiry_date=date(2027, 1, 1),
        inspection_date=date(2026, 9, 21),
    )


def test_inspection_and_placement_emit_one_atomic_warehouse_event():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    actor = Actor(name="Приёмщик", department="ОТК")
    inspected = inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=inspection(),
            actor=actor,
            now=datetime(2026, 9, 21, 11, 0),
        ),
        uow,
    )
    placed = place_receiving(
        PlaceReceivingCommand(
            line_id=line.id,
            expected_version=inspected.version,
            storage_location="1F4",
            actor=actor,
            now=datetime(2026, 9, 21, 11, 5),
        ),
        uow,
    )

    assert inspected.status is ReceivingStatus.ACCEPTED
    assert placed.warehouse_event_emitted is True
    assert [item.topic for item in uow.outbox.items] == ["warehouse.received"]
    assert uow.outbox.items[0].payload["grn"] == "FRX.26.0000001"
    assert [event.event_type for event in uow.audit.items] == [
        "receiving.expected",
        "receiving.inspected",
        "receiving.placed",
        "receiving.sent_to_warehouse",
    ]
    assert uow.commits == 3


def test_expected_event_retry_is_read_only():
    uow = Uow()
    first = consume_receiving_expected(message(), uow)
    retry = consume_receiving_expected(message(), uow)

    assert retry == first
    assert uow.commits == 1


def test_invalid_decimal_event_is_quarantined_instead_of_blocking_worker():
    uow = Uow()
    invalid = replace(
        message(),
        payload={**message().payload, "ordered_quantity": "not-a-number"},
    )
    uow.outbox.add(invalid)

    with pytest.raises(PoisonReceivingMessage):
        dispatch_next_receiving_expected(datetime(2026, 9, 21, 10, 1), uow)

    assert uow.outbox.failures[0][0] == invalid.id
    assert uow.commits == 1


def test_overdue_waiting_is_quarantined_by_the_system_with_notify_targets():
    uow = Uow()
    line = consume_receiving_expected(message(), uow)
    actor = Actor(name="Приёмщик", department="ОТК")
    waiting = inspect_receiving(
        InspectReceivingCommand(
            line_id=line.id,
            expected_version=line.version,
            inspection=replace(
                inspection(),
                inspection_date=date(2026, 9, 24),
                manual_nonconformities=("Механическое повреждение / брак",),
                fact_quantity=Decimal("9"),
            ),
            actor=actor,
            now=datetime(2026, 9, 24, 11, 0),
        ),
        uow,
    )
    assert waiting.status is ReceivingStatus.WAITING
    commits = uow.commits

    assert quarantine_overdue_waiting(datetime(2026, 9, 25, 23, 0), uow) == []
    assert uow.commits == commits

    moved = quarantine_overdue_waiting(datetime(2026, 9, 28, 8, 0), uow)

    assert [item.id for item in moved] == [line.id]
    assert uow.receiving.get(line.id).status is ReceivingStatus.QUARANTINE
    assert uow.receiving.get(line.id).version == waiting.version + 1
    event = uow.audit.items[-1]
    assert event.event_type == "receiving.auto_quarantined"
    assert event.actor.department == "System"
    assert event.details["from_status"] == "Ожидание"
    assert event.details["to_status"] == "Карантин"
    assert event.details["waiting_since"] == "2026-09-24"
    assert event.details["notify"] == ["Технолог", "Снабжение"]
    assert uow.outbox.items == []
    assert uow.commits == commits + 1
    assert quarantine_overdue_waiting(datetime(2026, 9, 29, 8, 0), uow) == []
