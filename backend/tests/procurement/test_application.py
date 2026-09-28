from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.procurement.application import (
    ManualRateRequired,
    RateUnavailable,
    ReconcileProcurementRateCommand,
    SaveProcurementFactsCommand,
    SaveProcurementMoneyCommand,
    SendSupplierRequestCommand,
    RecordInvoiceCommand,
    MarkProcurementPaidCommand,
    TakeProcurementInWorkCommand,
    consume_procurement_requested,
    reconcile_procurement_rate,
    save_procurement_facts,
    save_procurement_money,
    send_supplier_request,
    record_invoice,
    mark_procurement_paid,
    take_procurement_in_work,
)
from app.modules.procurement.domain import (
    MoneyEntry,
    ProcurementFacts,
    ProcurementStatus,
    ProcurementValidationError,
)
from app.modules.procurement.money import MoneyInput
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
    line = save_procurement_money(
        SaveProcurementMoneyCommand(
            line_id=line.id,
            expected_version=line.version,
            entry=MoneyEntry(
                amounts=MoneyInput(currency="RUB", price=Decimal("100"), vat_rate=Decimal("22")),
                invoice_item_name="Разъём",
            ),
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 25),
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


class FakeRates:
    def __init__(self, rates=None):
        self.rates = dict(rates or {})
        self.calls = []

    def rate_to_rub(self, currency, day):
        self.calls.append((currency, day))
        return self.rates.get((currency, day))


def line_awaiting_invoice(uow, buyer):
    line = consume_procurement_requested(requested_message(), uow)
    line = take_procurement_in_work(
        TakeProcurementInWorkCommand(line_ids=(line.id,), actor=buyer, now=datetime(2026, 9, 15, 11, 0)),
        uow,
    )[0]
    line = save_procurement_facts(
        SaveProcurementFactsCommand(
            line_id=line.id,
            facts=ProcurementFacts(supplier="ООО Поставка", actual_quantity=Decimal("2"), actual_unit="шт"),
            expected_version=line.version,
            actor=buyer,
            now=datetime(2026, 9, 15, 11, 10),
        ),
        uow,
    )
    return send_supplier_request(
        SendSupplierRequestCommand(
            line_id=line.id, expected_version=line.version, reference="REQ-1", actor=buyer,
            now=datetime(2026, 9, 15, 11, 20),
        ),
        uow,
    )


USD_ENTRY = MoneyEntry(
    amounts=MoneyInput(currency="USD", price=Decimal("10"), vat_rate=Decimal("22")),
    invoice_item_name="Разъём",
    invoice_date=datetime(2026, 9, 18).date(),
)


def save_usd(uow, line, buyer, rates, manual_rate=None, now=datetime(2026, 9, 20, 10, 0)):
    return save_procurement_money(
        SaveProcurementMoneyCommand(
            line_id=line.id, expected_version=line.version, entry=USD_ENTRY, actor=buyer, now=now,
            manual_rate=manual_rate,
        ),
        uow,
        rates,
    )


def test_foreign_money_uses_cbr_rate_of_the_invoice_date_and_is_audited():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    rates = FakeRates({("USD", datetime(2026, 9, 18).date()): Decimal("90")})
    line = save_usd(uow, line_awaiting_invoice(uow, buyer), buyer, rates)

    assert rates.calls == [("USD", datetime(2026, 9, 18).date())]
    assert (line.money.price, line.money.exchange_rate, line.money.exchange_rate_manual) == (
        Decimal("958.50"), Decimal("90"), False,
    )
    audit = uow.audit.items[-1]
    assert audit.event_type == "procurement.money_saved"
    assert audit.details["exchange_rate_source"] == "cbr"
    assert audit.details["source_price"] == "10"

    again = save_usd(uow, line, buyer, rates, now=datetime(2026, 9, 21, 10, 0))
    assert again == line
    assert len(rates.calls) == 1  # stored CBR rate of that day is reused, the sum does not drift


def test_cbr_outage_asks_for_manual_rate_and_writes_nothing():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    line = line_awaiting_invoice(uow, buyer)
    commits = uow.commits

    with pytest.raises(ManualRateRequired, match="ЦБ не отдал курс USD на 18.09.2026 — введите курс вручную"):
        save_usd(uow, line, buyer, FakeRates())

    assert uow.commits == commits
    assert uow.procurement.get(line.id) == line


def test_manual_rate_is_marked_then_reconciled_on_next_save_when_cbr_answers():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    manual = save_usd(uow, line_awaiting_invoice(uow, buyer), buyer, FakeRates(), manual_rate=Decimal("80"))
    assert (manual.money.price, manual.money.exchange_rate_manual) == (Decimal("852.00"), True)
    assert uow.audit.items[-1].details["exchange_rate_source"] == "manual"

    still_down = save_usd(uow, manual, buyer, FakeRates())
    assert still_down == manual  # the earlier manual rate stays, nobody is asked twice

    rates = FakeRates({("USD", datetime(2026, 9, 18).date()): Decimal("90")})
    reconciled = save_usd(uow, manual, buyer, rates)
    assert (reconciled.money.price, reconciled.money.exchange_rate_manual) == (Decimal("958.50"), False)
    details = uow.audit.items[-1].details
    assert (details["previous_exchange_rate"], details["exchange_rate"]) == ("80", "90")


def test_foreign_line_is_paid_at_payment_day_rate_or_manual_rate():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    rates = FakeRates({("USD", datetime(2026, 9, 18).date()): Decimal("90")})
    line = save_usd(uow, line_awaiting_invoice(uow, buyer), buyer, rates)
    line = record_invoice(
        RecordInvoiceCommand(
            line_id=line.id, expected_version=line.version, invoice_url="https://files.test/invoice.pdf",
            expected_arrival=datetime(2026, 9, 30).date(), actor=buyer, now=datetime(2026, 9, 20, 11, 0),
        ),
        uow,
    )
    pay = MarkProcurementPaidCommand(
        line_id=line.id, expected_version=line.version, actor=buyer, now=datetime(2026, 9, 22, 12, 0),
    )

    with pytest.raises(ManualRateRequired, match="22.09.2026"):
        mark_procurement_paid(pay, uow, rates)
    paid = mark_procurement_paid(
        MarkProcurementPaidCommand(**{**pay.__dict__, "manual_rate": Decimal("95")}), uow, rates,
    )

    assert paid.status is ProcurementStatus.IN_TRANSIT
    assert (paid.money.exchange_rate, paid.money.exchange_rate_manual) == (Decimal("95"), True)
    assert paid.money.exchange_rate_date == datetime(2026, 9, 22).date()
    assert uow.audit.items[-1].details["exchange_rate_source"] == "manual"

    reconcile = ReconcileProcurementRateCommand(
        line_id=paid.id, expected_version=paid.version, actor=buyer, now=datetime(2026, 9, 23, 9, 0),
    )
    with pytest.raises(RateUnavailable, match="сверьте позже"):
        reconcile_procurement_rate(reconcile, uow, rates)
    rates.rates[("USD", datetime(2026, 9, 22).date())] = Decimal("92")
    reconciled = reconcile_procurement_rate(reconcile, uow, rates)

    assert (reconciled.money.exchange_rate, reconciled.money.exchange_rate_manual) == (Decimal("92"), False)
    assert reconciled.money.price == Decimal("979.80")
    assert reconciled.status is ProcurementStatus.IN_TRANSIT
    assert uow.audit.items[-1].event_type == "procurement.rate_reconciled"
