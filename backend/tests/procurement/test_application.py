from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.procurement.application import (
    SaveProcurementFactsCommand,
    SendSupplierRequestCommand,
    RecordInvoiceCommand,
    MarkProcurementPaidCommand,
    TakeProcurementInWorkCommand,
    consume_procurement_requested,
    save_procurement_facts,
    send_supplier_request,
    record_invoice,
    mark_procurement_paid,
    take_procurement_in_work,
)
from app.modules.procurement.domain import (
    ProcurementFacts,
    ProcurementStatus,
    ProcurementValidationError,
)
from app.shared.events import OutboxMessage
from tests.factories import Actor


def requested_message(suffix: int = 1) -> OutboxMessage:
    return OutboxMessage(
        id=UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
        topic="procurement.requested",
        idempotency_key=f"request:{suffix}:procurement:v2",
        created_at=datetime(2026, 9, 15, 11, 0),
        payload={
            "source_request_id": f"10000000-0000-0000-0000-{suffix:012d}",
            "legacy_id": f"SM-20260915-{suffix:02d}",
            "request_date": "2026-09-15",
            "initiator": "Иванов",
            "department": "РНД",
            "task_type": "Закупка",
            "designation": f"RND.{suffix:03d}",
            "project_reference": None,
            "priority": "Очень срочно",
            "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
            "planned_date": "2026-09-25",
            "link": None,
            "item_type": "Комплектующие",
            "design_article": None,
            "category": "Общепром",
            "calculated_name": "Разъём",
            "calculated_quantity": "2",
            "calculated_unit": "шт",
            "calculated_price": None,
            "calculated_price_with_vat": None,
            "calculated_sum": None,
        },
    )


class FakeProcurementRepository:
    def __init__(self):
        self.items = {}

    def by_source_idempotency_key(self, key):
        return next(
            (item for item in self.items.values() if item.source_idempotency_key == key),
            None,
        )

    def get(self, line_id):
        return self.items.get(line_id)

    def add(self, line):
        self.items[line.id] = line


class FakeAppendOnlyRepository:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)


class FakeUnitOfWork:
    def __init__(self):
        self.procurement = FakeProcurementRepository()
        self.audit = FakeAppendOnlyRepository()
        self.outbox = FakeAppendOnlyRepository()
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        self.commits += 1


def test_requested_event_is_consumed_once_and_audited_atomically():
    uow = FakeUnitOfWork()
    message = requested_message()

    first = consume_procurement_requested(message, uow)
    retry = consume_procurement_requested(message, uow)

    assert retry == first
    assert len(uow.procurement.items) == 1
    assert [event.event_type for event in uow.audit.items] == ["procurement.created"]
    assert uow.audit.items[0].details == {
        "legacy_id": "SM-20260915-01",
        "source_event_id": str(message.id),
    }
    assert uow.commits == 1


def test_take_batch_is_atomic_and_same_buyer_retry_is_read_only():
    uow = FakeUnitOfWork()
    lines = [consume_procurement_requested(requested_message(i), uow) for i in (1, 2)]
    buyer = Actor(name="Петров", department="Закупки")
    command = TakeProcurementInWorkCommand(
        line_ids=tuple(item.id for item in lines),
        actor=buyer,
        now=datetime(2026, 9, 15, 11, 30),
    )

    first = take_procurement_in_work(command, uow)
    retry = take_procurement_in_work(command, uow)

    assert retry == first
    assert all(item.status is ProcurementStatus.IN_WORK for item in first)
    assert [event.event_type for event in uow.audit.items].count(
        "procurement.taken_in_work"
    ) == 2
    assert uow.commits == 3  # two consumes + first batch; retry is read-only


def test_take_batch_validates_every_line_before_writing():
    uow = FakeUnitOfWork()
    line = consume_procurement_requested(requested_message(), uow)
    unknown = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

    with pytest.raises(KeyError, match="Procurement line not found"):
        take_procurement_in_work(
            TakeProcurementInWorkCommand(
                line_ids=(line.id, unknown),
                actor=Actor(name="Петров", department="Закупки"),
                now=datetime(2026, 9, 15, 11, 30),
            ),
            uow,
        )

    assert uow.procurement.get(line.id).status is ProcurementStatus.NEW
    assert uow.commits == 1


def test_save_facts_moves_complete_line_to_ready_and_retry_is_read_only():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    line = consume_procurement_requested(requested_message(), uow)
    line = take_procurement_in_work(
        TakeProcurementInWorkCommand(
            line_ids=(line.id,),
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 30),
        ),
        uow,
    )[0]
    command = SaveProcurementFactsCommand(
        line_id=line.id,
        facts=ProcurementFacts(
            supplier="ООО Поставка",
            actual_quantity=Decimal("2"),
            actual_unit="шт",
        ),
        expected_version=line.version,
        actor=buyer,
        now=datetime(2026, 9, 15, 12, 0),
    )

    first = save_procurement_facts(command, uow)
    retry = save_procurement_facts(command, uow)

    assert retry == first
    assert first.status is ProcurementStatus.READY
    assert [event.event_type for event in uow.audit.items].count(
        "procurement.facts_saved"
    ) == 1
    assert uow.commits == 3


def test_empty_selection_is_rejected():
    with pytest.raises(ProcurementValidationError, match="Не выбрано"):
        take_procurement_in_work(
            TakeProcurementInWorkCommand(
                line_ids=(),
                actor=Actor(name="Петров", department="Закупки"),
                now=datetime(2026, 9, 15, 11, 30),
            ),
            FakeUnitOfWork(),
        )


def test_paid_line_emits_receiving_snapshot_once():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    line = consume_procurement_requested(requested_message(), uow)
    line = take_procurement_in_work(
        TakeProcurementInWorkCommand(
            line_ids=(line.id,), actor=buyer, now=datetime(2026, 9, 15, 11, 0)
        ),
        uow,
    )[0]
    line = save_procurement_facts(
        SaveProcurementFactsCommand(
            line_id=line.id,
            facts=ProcurementFacts(
                supplier="ООО Поставка", actual_quantity=Decimal("2"), actual_unit="шт"
            ),
            expected_version=line.version,
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 10),
        ),
        uow,
    )
    line = send_supplier_request(
        SendSupplierRequestCommand(
            line_id=line.id,
            expected_version=line.version,
            reference="REQ-1",
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 20),
        ),
        uow,
    )
    line = record_invoice(
        RecordInvoiceCommand(
            line_id=line.id,
            expected_version=line.version,
            invoice_url="https://files.test/invoice.pdf",
            expected_arrival=datetime(2026, 9, 25).date(),
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 30),
        ),
        uow,
    )
    line = mark_procurement_paid(
        MarkProcurementPaidCommand(
            line_id=line.id,
            expected_version=line.version,
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 40),
        ),
        uow,
    )

    assert line.status is ProcurementStatus.IN_TRANSIT
    assert len(uow.outbox.items) == 1
    assert uow.outbox.items[0].topic == "receiving.expected"
    assert uow.outbox.items[0].payload["warehouse"] == "ЦАР"
    assert uow.outbox.items[0].payload["article"] == (
        line.design_article or line.calculated_name
    )
