from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.procurement.domain import (
    ProcurementFacts,
    ProcurementStatus,
    ProcurementValidationError,
    create_line_from_requested,
)
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

    requested = ready.send_supplier_request("REQ-2026-1", buyer)
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
