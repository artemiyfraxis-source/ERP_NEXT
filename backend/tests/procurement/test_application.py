from dataclasses import replace
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
    BuildSupplierRequestsCommand,
    RecordInvoiceCommand,
    MarkProcurementPaidCommand,
    TakeProcurementInWorkCommand,
    consume_procurement_requested,
    reconcile_procurement_rate,
    save_procurement_facts,
    save_procurement_money,
    build_supplier_requests,
    record_invoice,
    mark_procurement_paid,
    take_procurement_in_work,
)
from app.modules.procurement.domain import (
    MoneyEntry,
    ProcurementFacts,
    ProcurementStatus,
    ProcurementValidationError,
    designation_key,
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
        self.orders = {}
        self.documents = {}

    def by_source_idempotency_key(self, key):
        return next(
            (item for item in self.items.values() if item.source_idempotency_key == key),
            None,
        )

    def get(self, line_id):
        return self.items.get(line_id)

    def legacy_ids_with_prefix(self, prefix):
        return [item.legacy_id for item in self.items.values() if item.legacy_id.startswith(prefix)]

    def has_lines_for_request(self, source_request_id):
        return any(item.source_request_id == source_request_id for item in self.items.values())

    def revisions_for_designation(self, designation):
        key = designation_key(designation)
        return [item.revision for item in self.items.values() if designation_key(item.designation) == key]

    def add(self, line):
        self.items[line.id] = line

    def ready_for_request(self):
        return [item for item in self.items.values() if item.status is ProcurementStatus.READY]

    def order_numbers_with_prefix(self, prefix):
        return [number for number in self.orders if number.startswith(f"{prefix}-")]

    def permission_numbers_with_prefix(self, prefix):
        return [
            item.permission_number
            for item in self.items.values()
            if item.permission_number and item.permission_number.startswith(f"{prefix}-")
        ]

    def add_order(self, order):
        if order.order_number in self.orders:
            raise AssertionError(f"duplicate order number {order.order_number}")
        self.orders[order.order_number] = order

    def get_order(self, order_number):
        return self.orders.get(order_number)

    def add_order_document(self, document, content):
        self.documents[document.order_number] = (document, content)

    def get_order_document(self, order_number):
        return self.documents.get(order_number)


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


class FakeSuppliers:
    def __init__(self, suppliers=None):
        self.suppliers = dict(suppliers or {})

    def supplier_map(self):
        return self.suppliers


def handed_off(suffix: int = 1, buyer=None, **payload) -> OutboxMessage:
    """`procurement.requested` as «В закупку» sends it since 05.10: with the buyer who pressed it."""
    buyer = buyer or Actor(name="Петров", department="Закупки")
    base = requested_message(suffix)
    return replace(base, payload={
        **base.payload, "buyer_id": str(buyer.id), "buyer_name": buyer.name, "buyer_department": buyer.department,
        **payload,
    })


def test_handed_off_line_lands_in_work_for_the_buyer_with_facts_prefilled():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    message = handed_off(link="https://shop.test/item/1")

    line = consume_procurement_requested(message, uow, FakeSuppliers())

    assert (line.status, line.buyer, line.supplier) == (ProcurementStatus.IN_WORK, buyer, None)
    assert (line.actual_quantity, line.actual_unit) == (Decimal("2"), "шт")
    assert [(event.event_type, event.actor.name) for event in uow.audit.items] == [
        ("procurement.created", "ERP Integration"), ("procurement.taken_in_work", "Петров"),
    ]
    assert uow.audit.items[1].details == {
        "legacy_id": line.legacy_id, "from_status": "Новый", "to_status": "В работе",
    }
    (autofill,) = uow.outbox.items  # the page fill, queued in the same transaction (one commit)
    assert (autofill.topic, autofill.payload["line_id"], autofill.payload["version"]) == (
        "procurement.autofill_requested", str(line.id), str(line.version),
    )
    assert uow.commits == 1
    assert consume_procurement_requested(message, uow, FakeSuppliers()) == line  # replay is read-only
    assert len(uow.audit.items) == 2 and uow.commits == 1


def test_handed_off_line_with_a_known_supplier_is_ready_at_once():
    uow = FakeUnitOfWork()
    message = handed_off(link="https://www.chipdip.ru/product/1")

    line = consume_procurement_requested(message, uow, FakeSuppliers({"chipdip.ru": "ЧипДип"}))

    assert (line.status, line.supplier) == (ProcurementStatus.READY, "ЧипДип")
    assert uow.audit.items[1].details["to_status"] == "Готов к запросу"


def test_an_old_message_without_the_buyer_still_lands_new():
    uow = FakeUnitOfWork()

    line = consume_procurement_requested(requested_message(), uow)

    assert (line.status, line.buyer, line.actual_quantity, line.version) == (ProcurementStatus.NEW, None, None, 1)
    assert [event.event_type for event in uow.audit.items] == ["procurement.created"]
    assert uow.outbox.items == []


def test_ready_line_stays_editable_and_falls_back_to_in_work_when_the_supplier_is_cleared():
    uow = FakeUnitOfWork()
    buyer = Actor(name="Петров", department="Закупки")
    line = consume_procurement_requested(handed_off(), uow)
    ready = save_procurement_facts(
        SaveProcurementFactsCommand(line.id, ProcurementFacts(supplier="ООО А"), line.version, buyer,
                                    datetime(2026, 9, 15, 12, 0)),
        uow,
    )
    assert ready.status is ProcurementStatus.READY

    back = save_procurement_facts(
        SaveProcurementFactsCommand(ready.id, ProcurementFacts(supplier=""), ready.version, buyer,
                                    datetime(2026, 9, 15, 12, 5)),
        uow,
    )

    assert (back.status, back.supplier) == (ProcurementStatus.IN_WORK, None)
    assert uow.audit.items[-1].details["from_status"] == "Готов к запросу"
    assert uow.audit.items[-1].details["to_status"] == "В работе"


def transfer(suffix: int, attempt: int = 1, **payload) -> OutboxMessage:
    """The `procurement.requested` of request `suffix`; attempt > 1 is a retransfer (new event, same request)."""
    base = requested_message(suffix)
    return replace(
        base,
        id=UUID(f"00000000-0000-0000-{attempt:04d}-{suffix:012d}"),
        idempotency_key=f"request:{suffix}:procurement:v{attempt + 1}",
        payload={**base.payload, **payload},
    )


# PR-2 — Pipeline.gs:66-74: revisions are grouped by НАЗНАЧЕНИЕ under normHeader_ (spaces, case).
def test_retransfer_revision_counts_lines_whose_designation_differs_only_in_case_and_spaces():
    uow = FakeUnitOfWork()
    consume_procurement_requested(transfer(1, designation="RND.001"), uow)
    consume_procurement_requested(transfer(1, 2, designation="RND.001"), uow)
    consume_procurement_requested(transfer(2, designation=" rnd.001  "), uow)

    again = consume_procurement_requested(transfer(2, 2, designation=" rnd.001  "), uow)

    assert again.revision == "C"
    assert again.legacy_id == "SM-20260915-04"  # 02 is the retransfer of request 1, 03 request 2 itself
    assert sorted(item.revision for item in uow.procurement.items.values()) == ["A", "A", "B", "C"]


# PR-2 — SUPPLYMAP finds «already transferred» by System ID: a line holding the request's ID means a new ID.
def test_first_transfer_onto_an_id_taken_by_a_retransfer_line_gets_a_fresh_id_and_stays_revision_a():
    uow = FakeUnitOfWork()
    consume_procurement_requested(transfer(1), uow)
    retransferred = consume_procurement_requested(transfer(1, 2), uow)
    assert retransferred.legacy_id == "SM-20260915-02"  # the ID request 2 was given on its own sheet

    first = consume_procurement_requested(transfer(2), uow)

    assert (first.legacy_id, first.revision) == ("SM-20260915-03", "A")
    assert first.source_request_id == UUID("10000000-0000-0000-0000-000000000002")
    assert len({item.legacy_id for item in uow.procurement.items.values()}) == 3
    assert consume_procurement_requested(transfer(2), uow) == first  # replay is read-only


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
    line = build_supplier_requests(
        BuildSupplierRequestsCommand(actor=buyer, now=datetime(2026, 9, 15, 11, 20)),
        uow,
    ).lines[0]
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
    return build_supplier_requests(
        BuildSupplierRequestsCommand(actor=buyer, now=datetime(2026, 9, 15, 11, 20)),
        uow,
    ).lines[0]


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
