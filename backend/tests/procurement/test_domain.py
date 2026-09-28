import dataclasses
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.procurement.domain import (
    MoneyEntry,
    ProcurementFacts,
    ProcurementStatus,
    ProcurementValidationError,
    create_backorder_line,
    create_line_from_requested,
    is_backorder_id,
    next_backorder_id,
)
from app.modules.procurement.money import EMPTY_MONEY, ExchangeRate, MoneyInput
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

    requested = ready.send_supplier_request("PO-001", date(2026, 9, 22), "REQ-2026-1", buyer).update_money(RUB_ENTRY, None, buyer)
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
        .send_supplier_request("PO-001", date(2026, 9, 22), "REQ-2026-1", buyer)
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


def paid_line(buyer: Actor, *, entry: MoneyEntry = RUB_ENTRY, rate=None):
    return (
        awaiting_invoice(buyer)
        .update_money(entry, rate, buyer)
        .record_invoice("https://files.test/invoice.pdf", date(2026, 9, 30), buyer)
        .mark_paid(buyer, datetime(2026, 9, 22, 12, 0), rate)
    )


def backorder_message(line, quantity: str) -> OutboxMessage:
    return OutboxMessage(
        id=UUID("00000000-0000-0000-0000-000000000901"),
        topic="procurement.backorder_requested",
        idempotency_key="receiving:rl-1:backorder",
        created_at=datetime(2026, 10, 1, 9, 0),
        payload={"procurement_line_id": str(line.id), "receiving_line_id": "rl-1", "backorder_quantity": quantity},
    )


def test_backorder_ids_follow_supplymap_suffix_rules():
    assert next_backorder_id("SM-20260915-01", []) == "SM-20260915-01-Д"
    assert next_backorder_id("SM-20260915-01", ["SM-20260915-01-Д"]) == "SM-20260915-01-Д2"
    assert next_backorder_id("SM-20260915-01", ["SM-20260915-01-Д", "SM-20260915-01-Д2"]) == "SM-20260915-01-Д3"
    assert next_backorder_id("SM-20260915-01-Д", []) == "SM-20260915-01-Д-Д"
    assert [is_backorder_id(value) for value in ("X-Д", "X-Д2", "X-Д-Д", "X", "X-Д-1")] == [
        True, True, True, False, False,
    ]


def test_backorder_line_keeps_price_payment_and_invoice_and_prices_the_remainder():
    buyer = Actor(name="Петров", department="Закупки")
    original = paid_line(buyer)

    backorder = create_backorder_line(original, backorder_message(original, "0.5"), "SM-20260915-01-Д")

    assert backorder.id != original.id
    assert (backorder.legacy_id, backorder.status, backorder.version) == (
        "SM-20260915-01-Д", ProcurementStatus.APPROVAL, 1,
    )
    assert (backorder.actual_quantity, backorder.calculated_quantity) == (Decimal("0.5"), Decimal("0.5"))
    assert backorder.calculated_sum == Decimal("500.00")
    assert backorder.expected_arrival is None
    assert (backorder.buyer, backorder.supplier, backorder.invoice_url, backorder.paid_at) == (
        original.buyer, original.supplier, original.invoice_url, original.paid_at,
    )
    assert (backorder.invoice_number, backorder.invoice_item_name) == ("СЧ-17", "Разъём SMA-001")
    assert (backorder.money.price, backorder.money.price_with_vat) == (Decimal("800"), Decimal("976.00"))
    assert backorder.money.sum == Decimal("488.00")
    assert original.money.sum == Decimal("1952.00")


def test_foreign_backorder_keeps_rate_and_reprices_source_sum():
    buyer = Actor(name="Петров", department="Закупки")
    usd = MoneyEntry(
        amounts=MoneyInput(currency="USD", vat_rate=Decimal("22"), sum=Decimal("20"), driver="sum"),
        invoice_item_name="SMA",
    )
    original = paid_line(buyer, entry=usd, rate=ExchangeRate(Decimal("90"), date(2026, 9, 22)))

    backorder = create_backorder_line(original, backorder_message(original, "1"), "X-Д")

    assert (backorder.money.exchange_rate, backorder.money.source_currency) == (Decimal("90"), "USD")
    assert (backorder.money.source_price_with_vat, backorder.money.source_sum) == (Decimal("10.00"), Decimal("10.00"))
    assert backorder.money.sum == Decimal("958.50")


@pytest.mark.parametrize("quantity", ["0", "-1", "abc", "NaN"])
def test_backorder_rejects_non_positive_or_garbage_remainder(quantity):
    buyer = Actor(name="Петров", department="Закупки")
    original = paid_line(buyer)

    with pytest.raises(ProcurementValidationError):
        create_backorder_line(original, backorder_message(original, quantity), "X-Д")


def test_backorder_is_confirmed_to_transit_or_cancelled_only_from_approval():
    buyer = Actor(name="Петров", department="Закупки")
    original = paid_line(buyer)
    backorder = create_backorder_line(original, backorder_message(original, "1"), "SM-20260915-01-Д")

    with pytest.raises(ProcurementValidationError, match="раньше дозавоза"):
        backorder.confirm_backorder(date(2026, 9, 30), buyer)
    shipped = backorder.confirm_backorder(date(2026, 10, 10), buyer)
    assert (shipped.status, shipped.expected_arrival, shipped.version) == (
        ProcurementStatus.IN_TRANSIT, date(2026, 10, 10), 2,
    )
    assert shipped.confirm_backorder(date(2026, 10, 10), buyer) is shipped
    with pytest.raises(ProcurementValidationError, match="только из «Согласование»"):
        shipped.cancel_backorder(buyer)

    cancelled = backorder.cancel_backorder(buyer)
    assert cancelled.status is ProcurementStatus.CANCELLED
    assert cancelled.cancel_backorder(buyer) is cancelled
    with pytest.raises(ProcurementValidationError, match="только у строки дозавоза"):
        original.confirm_backorder(date(2026, 10, 10), buyer)
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        backorder.cancel_backorder(Actor(name="Сидоров", department="Закупки"))


def test_approval_parks_any_unpaid_stage_and_resume_returns_to_the_stage_the_data_reached():
    buyer = Actor(name="Петров", department="Закупки")
    taken = create_line_from_requested(requested_message()).take_in_work(buyer)
    ready = taken.update_facts(
        ProcurementFacts(supplier="ООО Поставка", actual_quantity=Decimal("2"), actual_unit="шт"), buyer,
    )
    awaiting = awaiting_invoice(buyer)
    to_pay = awaiting.update_money(RUB_ENTRY, None, buyer).record_invoice(
        "https://files.test/invoice.pdf", date(2026, 9, 30), buyer,
    )

    for line, stage in (
        (taken, ProcurementStatus.IN_WORK),
        (ready, ProcurementStatus.READY),
        (awaiting, ProcurementStatus.AWAITING_INVOICE),
        (to_pay, ProcurementStatus.TO_PAY),
    ):
        parked = line.put_on_approval(buyer)
        assert (parked.status, parked.version) == (ProcurementStatus.APPROVAL, line.version + 1)
        assert parked.put_on_approval(buyer) is parked
        resumed = parked.resume_from_approval(buyer)
        assert (resumed.status, resumed.version) == (stage, line.version + 2)
        assert resumed.supplier_request_reference == line.supplier_request_reference
        assert resumed.money == line.money


def test_parked_line_is_frozen_until_approved():
    buyer = Actor(name="Петров", department="Закупки")
    parked = awaiting_invoice(buyer).put_on_approval(buyer)

    with pytest.raises(ProcurementValidationError, match="только на этапах"):
        parked.update_money(RUB_ENTRY, None, buyer)
    with pytest.raises(ProcurementValidationError, match="«Жду счет»"):
        parked.record_invoice("https://files.test/invoice.pdf", date(2026, 9, 30), buyer)
    with pytest.raises(ProcurementValidationError, match="только новую"):
        parked.take_in_work(buyer)
    with pytest.raises(ProcurementValidationError, match="не на согласовании"):
        awaiting_invoice(buyer).resume_from_approval(buyer)


def test_unclaimed_new_line_is_claimed_by_whoever_parks_or_cancels_it():
    buyer = Actor(name="Петров", department="Закупки")
    new = create_line_from_requested(requested_message())

    parked = new.put_on_approval(buyer)
    assert (parked.status, parked.buyer) == (ProcurementStatus.APPROVAL, buyer)
    assert parked.resume_from_approval(buyer).status is ProcurementStatus.IN_WORK

    cancelled = new.cancel(buyer)
    assert (cancelled.status, cancelled.buyer, cancelled.version) == (ProcurementStatus.CANCELLED, buyer, 2)


def test_cancel_is_terminal_and_reachable_from_any_unpaid_stage_including_approval():
    buyer = Actor(name="Петров", department="Закупки")
    awaiting = awaiting_invoice(buyer)

    cancelled = awaiting.cancel(buyer)
    assert cancelled.status is ProcurementStatus.CANCELLED
    assert cancelled.cancel(buyer) is cancelled
    assert awaiting.put_on_approval(buyer).cancel(buyer).status is ProcurementStatus.CANCELLED
    with pytest.raises(ProcurementValidationError, match="не на согласовании"):
        cancelled.resume_from_approval(buyer)
    with pytest.raises(ProcurementValidationError, match="только новую"):
        cancelled.take_in_work(buyer)
    with pytest.raises(ProcurementValidationError, match="отмена окончательная"):
        cancelled.put_on_approval(buyer)


def test_offline_moves_are_refused_after_payment_for_foreign_lines_and_on_backorders():
    buyer = Actor(name="Петров", department="Закупки")
    stranger = Actor(name="Сидоров", department="Закупки")
    paid = paid_line(buyer)

    with pytest.raises(ProcurementValidationError, match="передана в приёмку"):
        paid.put_on_approval(buyer)
    with pytest.raises(ProcurementValidationError, match="передана в приёмку"):
        paid.cancel(buyer)
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        awaiting_invoice(buyer).put_on_approval(stranger)
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        awaiting_invoice(buyer).cancel(stranger)
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        awaiting_invoice(buyer).put_on_approval(buyer).resume_from_approval(stranger)

    backorder = create_backorder_line(paid, backorder_message(paid, "1"), "SM-20260915-01-Д")
    for move in (backorder.put_on_approval, backorder.resume_from_approval, backorder.cancel):
        with pytest.raises(ProcurementValidationError, match="У дозавоза свои действия"):
            move(buyer)


# PR-5 — Invoices.gs:104-140: a second upload on a line with an invoice is a correction.
def test_invoice_replacement_keeps_payment_status_and_dates():
    buyer = Actor(name="Петров", department="Закупки")
    original = paid_line(buyer)

    replaced = original.replace_invoice(
        "https://files.test/invoice-v2.pdf", buyer, invoice_number=" СЧ-18 ", invoice_date=date(2026, 9, 25),
    )

    assert replaced.invoice_url == "https://files.test/invoice-v2.pdf"
    assert (replaced.invoice_number, replaced.invoice_date) == ("СЧ-18", date(2026, 9, 25))
    assert (replaced.status, replaced.paid_at, replaced.expected_arrival) == (
        original.status, original.paid_at, original.expected_arrival,
    )
    assert replaced.money == original.money
    assert replaced.version == original.version + 1


def test_invoice_replacement_leaves_number_and_date_when_the_file_gives_none():
    buyer = Actor(name="Петров", department="Закупки")
    original = paid_line(buyer)

    replaced = original.replace_invoice("https://files.test/invoice-v2.pdf", buyer, invoice_number="  ")

    assert (replaced.invoice_number, replaced.invoice_date) == (original.invoice_number, original.invoice_date)
    assert replaced.replace_invoice("https://files.test/invoice-v2.pdf", buyer) is replaced


def test_invoice_replacement_on_unpaid_line_does_not_advance_it():
    buyer = Actor(name="Петров", department="Закупки")
    to_pay = awaiting_invoice(buyer).update_money(RUB_ENTRY, None, buyer).record_invoice(
        "https://files.test/invoice.pdf", date(2026, 9, 30), buyer,
    )

    replaced = to_pay.replace_invoice("https://files.test/invoice-v2.pdf", buyer)

    assert (replaced.status, replaced.paid_at) == (ProcurementStatus.TO_PAY, None)


def test_backorder_invoice_is_replaced_without_an_expected_date():
    buyer = Actor(name="Петров", department="Закупки")
    original = paid_line(buyer)
    backorder = create_backorder_line(original, backorder_message(original, "0.5"), "X-Д")

    replaced = backorder.replace_invoice("https://files.test/invoice-v2.pdf", buyer)

    assert (replaced.status, replaced.invoice_url) == (ProcurementStatus.APPROVAL, "https://files.test/invoice-v2.pdf")


def test_invoice_replacement_is_refused_without_an_invoice_owner_money_or_url():
    buyer = Actor(name="Петров", department="Закупки")
    other = Actor(name="Сидоров", department="Закупки")
    line = paid_line(buyer)

    with pytest.raises(ProcurementValidationError, match="Счёта ещё нет"):
        awaiting_invoice(buyer).update_money(RUB_ENTRY, None, buyer).replace_invoice(
            "https://files.test/invoice-v2.pdf", buyer,
        )
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        line.replace_invoice("https://files.test/invoice-v2.pdf", other)
    with pytest.raises(ProcurementValidationError, match="Счёт обязателен"):
        line.replace_invoice("   ", buyer)
    with pytest.raises(ProcurementValidationError, match="сначала заполните: ВАЛЮТА"):
        _strip_money(line).replace_invoice("https://files.test/invoice-v2.pdf", buyer)


def test_cancelled_line_keeps_its_invoice():
    buyer = Actor(name="Петров", department="Закупки")
    original = paid_line(buyer)
    backorder = create_backorder_line(original, backorder_message(original, "0.5"), "X-Д")
    cancelled = backorder.cancel_backorder(buyer)

    with pytest.raises(ProcurementValidationError, match="отменённой строки"):
        cancelled.replace_invoice("https://files.test/invoice-v2.pdf", buyer)


def _strip_money(line):
    # A line that got its invoice before the money gate existed.
    return dataclasses.replace(line, money=EMPTY_MONEY)
