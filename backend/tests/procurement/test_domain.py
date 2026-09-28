from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.procurement.domain import (
    MoneyEntry,
    ProcurementFacts,
    ProcurementStatus,
    ProcurementValidationError,
    create_line_from_requested,
)
from app.modules.procurement.money import ExchangeRate, MoneyInput
from app.shared.events import OutboxMessage
from tests.factories import Actor


def requested_message() -> OutboxMessage:
    return OutboxMessage(
        id=UUID("00000000-0000-0000-0000-000000000101"),
        topic="procurement.requested",
        idempotency_key="request:00000000-0000-0000-0000-000000000001:procurement:v2",
        created_at=datetime(2026, 9, 15, 11, 0),
        payload={
            "source_request_id": "00000000-0000-0000-0000-000000000001",
            "legacy_id": "SM-20260915-01",
            "request_date": "2026-09-15",
            "initiator": "Иванов",
            "department": "РНД",
            "task_type": "Закупка",
            "designation": "RND.001",
            "project_reference": "591К.3",
            "priority": "Очень срочно",
            "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
            "planned_date": "2026-09-25",
            "link": "https://example.test/item",
            "item_type": "Комплектующие",
            "design_article": "SMA-001",
            "category": "Общепром",
            "calculated_name": "Разъём SMA",
            "calculated_quantity": "2.000000",
            "calculated_unit": "шт",
            "calculated_price": "800.00",
            "calculated_price_with_vat": "1000.00",
            "calculated_sum": "2000.00",
        },
    )


def test_requested_event_creates_procurement_owned_defaults():
    line = create_line_from_requested(requested_message())

    assert isinstance(line.id, UUID)
    assert line.source_event_id == requested_message().id
    assert line.status is ProcurementStatus.NEW
    assert line.revision == "A"
    assert line.buyer is None
    assert line.actual_quantity is None
    assert line.calculated_quantity == Decimal("2.000000")


def test_only_expected_topic_is_accepted():
    message = requested_message()
    wrong = OutboxMessage(
        id=message.id,
        topic="request.created",
        idempotency_key=message.idempotency_key,
        created_at=message.created_at,
        payload=message.payload,
    )

    with pytest.raises(ProcurementValidationError, match="topic"):
        create_line_from_requested(wrong)


def test_buyer_takes_new_line_in_work_and_retry_is_idempotent():
    buyer = Actor(name="Петров", department="Закупки")
    line = create_line_from_requested(requested_message())

    taken = line.take_in_work(buyer)
    retry = taken.take_in_work(buyer)

    assert taken.status is ProcurementStatus.IN_WORK
    assert taken.buyer == buyer
    assert retry == taken
    assert taken.version == 2


def test_other_buyer_cannot_capture_line_already_in_work():
    line = create_line_from_requested(requested_message()).take_in_work(
        Actor(name="Петров", department="Закупки")
    )

    with pytest.raises(ProcurementValidationError, match="другим закупщиком"):
        line.take_in_work(Actor(name="Сидоров", department="Закупки"))


def test_same_display_name_does_not_grant_ownership_to_another_identity():
    first = Actor(
        id=UUID("20000000-0000-0000-0000-000000000001"),
        name="Петров",
        department="Закупки",
    )
    second = Actor(
        id=UUID("20000000-0000-0000-0000-000000000002"),
        name="Петров",
        department="Закупки",
    )
    line = create_line_from_requested(requested_message()).take_in_work(first)

    with pytest.raises(ProcurementValidationError, match="другим закупщиком"):
        line.take_in_work(second)


def test_complete_facts_advance_line_to_ready():
    buyer = Actor(name="Петров", department="Закупки")
    line = create_line_from_requested(requested_message()).take_in_work(buyer)
    facts = ProcurementFacts(
        supplier="ООО Поставщик",
        site_name="SMA connector",
        link="https://supplier.test/item",
        design_article="SMA-001A",
        calculated_name="Разъём SMA, уточнённый",
        actual_quantity=Decimal("2"),
        actual_unit="шт",
    )

    ready = line.update_facts(facts, buyer)
    retry = ready.update_facts(facts, buyer)

    assert ready.status is ProcurementStatus.READY
    assert ready.supplier == "ООО Поставщик"
    assert ready.actual_quantity == Decimal("2")
    assert retry == ready


def test_incomplete_facts_remain_in_work_and_wrong_buyer_cannot_edit():
    buyer = Actor(name="Петров", department="Закупки")
    line = create_line_from_requested(requested_message()).take_in_work(buyer)
    incomplete = ProcurementFacts(
        actual_quantity=Decimal("2"),
        actual_unit="шт",
    )

    saved = line.update_facts(incomplete, buyer)
    assert saved.status is ProcurementStatus.IN_WORK

    with pytest.raises(ProcurementValidationError, match="свои строки"):
        saved.update_facts(
            incomplete,
            Actor(name="Сидоров", department="Закупки"),
        )


def test_explicit_empty_supplier_is_rejected_and_quantity_can_be_cleared():
    buyer = Actor(name="Петров", department="Закупки")
    line = create_line_from_requested(requested_message()).take_in_work(buyer)
    line = line.update_facts(
        ProcurementFacts(actual_quantity=Decimal("2"), actual_unit="шт"),
        buyer,
    )

    cleared = line.update_facts(ProcurementFacts(actual_quantity=None), buyer)

    assert cleared.actual_quantity is None
    with pytest.raises(ProcurementValidationError, match="Поставщик"):
        cleared.update_facts(ProcurementFacts(supplier=""), buyer)


def test_non_positive_actual_quantity_is_rejected():
    buyer = Actor(name="Петров", department="Закупки")
    line = create_line_from_requested(requested_message()).take_in_work(buyer)

    with pytest.raises(ProcurementValidationError, match="количество"):
        line.update_facts(
            ProcurementFacts(supplier="ООО", actual_quantity=Decimal("0"), actual_unit="шт"),
            buyer,
        )


def test_buyer_advances_ready_line_through_invoice_to_transit():
    buyer = Actor(name="Петров", department="Закупки")
    ready = (
        create_line_from_requested(requested_message())
        .take_in_work(buyer)
        .update_facts(
            ProcurementFacts(
                supplier="ООО Поставка",
                actual_quantity=Decimal("2"),
                actual_unit="шт",
            ),
            buyer,
        )
    )

    requested = ready.send_supplier_request("REQ-2026-1", buyer).update_money(RUB_ENTRY, None, buyer)
    invoiced = requested.record_invoice(
        "https://files.test/invoice.pdf",
        date(2026, 9, 30),
        buyer,
    )
    shipped = invoiced.mark_paid(buyer, datetime(2026, 9, 22, 12, 0))

    assert requested.status is ProcurementStatus.AWAITING_INVOICE
    assert invoiced.status is ProcurementStatus.TO_PAY
    assert shipped.status is ProcurementStatus.IN_TRANSIT
    assert shipped.expected_arrival == date(2026, 9, 30)


RUB_ENTRY = MoneyEntry(
    amounts=MoneyInput(currency="RUB", price=Decimal("800"), vat_rate=Decimal("22")),
    invoice_item_name="Разъём SMA-001",
    invoice_number="СЧ-17",
    invoice_date=date(2026, 9, 18),
)


def awaiting_invoice(buyer: Actor):
    return (
        create_line_from_requested(requested_message())
        .take_in_work(buyer)
        .update_facts(
            ProcurementFacts(supplier="ООО Поставка", actual_quantity=Decimal("2"), actual_unit="шт"),
            buyer,
        )
        .send_supplier_request("REQ-2026-1", buyer)
    )


def test_invoice_is_refused_until_fact_money_is_filled():
    buyer = Actor(name="Петров", department="Закупки")
    line = awaiting_invoice(buyer)

    with pytest.raises(ProcurementValidationError) as error:
        line.record_invoice("https://files.test/invoice.pdf", date(2026, 9, 30), buyer)

    assert str(error.value) == (
        "Нельзя загрузить счёт — сначала заполните: ВАЛЮТА, ЦЕНА (факт), ЦЕНА С НДС (факт), "
        "СУММА (факт), НАИМЕНОВАНИЕ (Счет)"
    )


def test_fact_money_is_derived_and_recorded_with_invoice_identity():
    buyer = Actor(name="Петров", department="Закупки")
    line = awaiting_invoice(buyer).update_money(RUB_ENTRY, None, buyer)

    assert line.money.currency == "RUB"
    assert (line.money.price, line.money.price_with_vat, line.money.sum) == (
        Decimal("800"), Decimal("976.00"), Decimal("1952.00"),
    )
    assert (line.invoice_item_name, line.invoice_number, line.invoice_date) == (
        "Разъём SMA-001", "СЧ-17", date(2026, 9, 18),
    )
    assert line.pay_missing(date(2026, 9, 30)) == []
    assert line.update_money(RUB_ENTRY, None, buyer) is line


def test_money_is_editable_only_by_owner_while_awaiting_invoice_or_payment():
    buyer = Actor(name="Петров", department="Закупки")
    other = Actor(name="Сидоров", department="Закупки")
    line = awaiting_invoice(buyer)

    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        line.update_money(RUB_ENTRY, None, other)
    in_work = create_line_from_requested(requested_message()).take_in_work(buyer)
    with pytest.raises(ProcurementValidationError, match="«Жду счет» и «В оплату»"):
        in_work.update_money(RUB_ENTRY, None, buyer)


def test_payment_is_refused_for_a_line_invoiced_without_money():
    buyer = Actor(name="Петров", department="Закупки")
    # A line that reached «В оплату» before the money gate existed.
    legacy = awaiting_invoice(buyer).update_money(RUB_ENTRY, None, buyer).record_invoice(
        "https://files.test/invoice.pdf", date(2026, 9, 30), buyer,
    )
    legacy = legacy.update_money(MoneyEntry(amounts=MoneyInput(currency=None)), None, buyer)

    with pytest.raises(ProcurementValidationError, match="Нельзя провести оплату — сначала заполните: ВАЛЮТА"):
        legacy.mark_paid(buyer, datetime(2026, 9, 22, 12, 0))
    paid = legacy.update_money(RUB_ENTRY, None, buyer).mark_paid(buyer, datetime(2026, 9, 22, 12, 0))
    assert paid.status is ProcurementStatus.IN_TRANSIT


def test_foreign_invoice_is_repriced_at_the_payment_day_rate():
    buyer = Actor(name="Петров", department="Закупки")
    usd = MoneyEntry(
        amounts=MoneyInput(currency="USD", price=Decimal("10"), vat_rate=Decimal("22")),
        invoice_item_name="SMA", invoice_date=date(2026, 9, 18),
    )
    invoiced = (
        awaiting_invoice(buyer)
        .update_money(usd, ExchangeRate(Decimal("80"), date(2026, 9, 18)), buyer)
        .record_invoice("https://files.test/invoice.pdf", date(2026, 9, 30), buyer)
    )
    assert invoiced.money.price == Decimal("852.00")

    with pytest.raises(ProcurementValidationError, match="Нет курса USD на день оплаты"):
        invoiced.mark_paid(buyer, datetime(2026, 9, 22, 12, 0))
    paid = invoiced.mark_paid(
        buyer, datetime(2026, 9, 22, 12, 0), ExchangeRate(Decimal("90"), date(2026, 9, 22)),
    )

    assert paid.money.price == Decimal("958.50")
    assert paid.money.sum == Decimal("2338.74")
    assert (paid.money.exchange_rate, paid.money.exchange_rate_date) == (Decimal("90"), date(2026, 9, 22))
    assert paid.money.source_price == Decimal("10")


def test_manual_rate_is_reconciled_with_cbr_rate_once():
    buyer = Actor(name="Петров", department="Закупки")
    usd = MoneyEntry(
        amounts=MoneyInput(currency="USD", price=Decimal("10"), vat_rate=Decimal("22")), invoice_item_name="SMA",
    )
    manual = awaiting_invoice(buyer).update_money(
        usd, ExchangeRate(Decimal("80"), date(2026, 9, 18), manual=True), buyer,
    )
    assert manual.money.exchange_rate_manual is True

    with pytest.raises(ProcurementValidationError, match="только с курсом ЦБ"):
        manual.reconcile_rate(ExchangeRate(Decimal("81"), date(2026, 9, 18), manual=True), buyer)
    reconciled = manual.reconcile_rate(ExchangeRate(Decimal("90"), date(2026, 9, 18)), buyer)

    assert reconciled.money.exchange_rate_manual is False
    assert reconciled.money.price == Decimal("958.50")
    assert reconciled.version == manual.version + 1
    with pytest.raises(ProcurementValidationError, match="сверять нечего"):
        reconciled.reconcile_rate(ExchangeRate(Decimal("90"), date(2026, 9, 18)), buyer)
