from datetime import date, datetime
from decimal import Decimal

import pytest

from app.modules.receiving.domain import (
    InspectionInput,
    ReceivingStatus,
    ReceivingValidationError,
    grn_prefix_for,
    inspect_delivery,
    next_grn,
    create_line_from_expected,
)
from app.shared.events import OutboxMessage
from tests.factories import Actor
from uuid import UUID


def inspection(**overrides) -> InspectionInput:
    values = {
        "fact_quantity": Decimal("10"),
        "document_quantity": Decimal("10"),
        "ordered_quantity": Decimal("10"),
        "has_shipping_document": True,
        "quality_document_required": False,
        "quality_document_present": False,
        "expiry_date": date(2027, 1, 1),
        "inspection_date": date(2026, 9, 21),
        "manual_nonconformities": (),
        "disposition": None,
    }
    values.update(overrides)
    return InspectionInput(**values)


def test_clean_delivery_and_surplus_are_accepted_by_fact():
    clean = inspect_delivery(inspection())
    surplus = inspect_delivery(inspection(fact_quantity=Decimal("12")))

    assert clean.status is ReceivingStatus.ACCEPTED
    assert clean.verdict == "Годен"
    assert clean.accepted_quantity == Decimal("10")
    assert surplus.status is ReceivingStatus.ACCEPTED
    assert surplus.accepted_quantity == Decimal("12")
    assert surplus.nonconformities == ("Излишек (факт > УПД)",)


def test_wrong_item_or_expiry_is_terminal_return():
    wrong = inspect_delivery(
        inspection(manual_nonconformities=("Не тот товар (несоответствие счету)",))
    )
    expired = inspect_delivery(inspection(expiry_date=date(2026, 9, 20)))

    assert wrong.status is ReceivingStatus.RETURN
    assert expired.status is ReceivingStatus.RETURN
    assert wrong.verdict == expired.verdict == "Не годен"


def test_returned_line_cannot_be_reinspected():
    line = create_line_from_expected(expected_message())
    returned = line.inspect(
        inspection(manual_nonconformities=("Не тот товар (несоответствие счету)",)),
        Actor(name="Приёмщик", department="ОТК"),
        existing_grns=(),
    )

    with pytest.raises(ReceivingValidationError, match="закрыта"):
        returned.inspect(
            inspection(),
            Actor(name="Приёмщик", department="ОТК"),
            existing_grns=(returned.grn,),
        )


def test_documents_wait_and_confirmed_defect_goes_to_quarantine():
    documents = inspect_delivery(inspection(has_shipping_document=False))
    defect = inspect_delivery(
        inspection(
            manual_nonconformities=("Механическое повреждение / брак",),
            disposition="Изолятор",
        )
    )

    assert documents.status is ReceivingStatus.WAITING
    assert documents.missing_documents is True
    assert documents.nonconformity_act_required is False
    assert defect.status is ReceivingStatus.QUARANTINE
    assert defect.nonconformity_act_required is True


def test_shortage_creates_backorder_and_supplier_complaint():
    result = inspect_delivery(
        inspection(
            fact_quantity=Decimal("7"),
            document_quantity=Decimal("8"),
            ordered_quantity=Decimal("10"),
        )
    )

    assert result.status is ReceivingStatus.WAITING
    assert result.backorder_quantity == Decimal("2")
    assert result.complaint_required is True
    assert result.nonconformity_act_required is True


def test_invalid_quantities_are_rejected():
    with pytest.raises(ReceivingValidationError, match="Фактическое количество"):
        inspect_delivery(inspection(fact_quantity=Decimal("0")))


@pytest.mark.parametrize(
    ("purpose", "link", "goal", "expected"),
    [
        ("RND", "", "", "R&D"),
        ("FRX.001", "", "", "FRX"),
        ("Обеспечение производства", "", "591к.3", "PPM"),
        ("что угодно", "Давальческое сырьё", "", "CSM"),
        ("неизвестно", "", "", None),
    ],
)
def test_grn_prefix_matches_supplymap_rules(purpose, link, goal, expected):
    assert grn_prefix_for(purpose=purpose, link=link, goal=goal) == expected


def test_next_grn_uses_per_prefix_year_sequence_and_ignores_legacy_format():
    assert next_grn("FRX", 2026, ["FRX.26.0000002", "FRX.2026000009", "PPM.26.0000010"]) == (
        "FRX.26.0000003"
    )


def expected_message() -> OutboxMessage:
    return OutboxMessage(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        topic="receiving.expected",
        idempotency_key="procurement:one:receiving:v1",
        created_at=datetime(2026, 9, 21, 10, 0),
        payload={
            "procurement_line_id": "20000000-0000-0000-0000-000000000001",
            "legacy_id": "SM-20260921-01",
            "warehouse": "ЦАР",
            "article": "CON-001",
            "name": "Разъём",
            "item_type": "Комплектующие",
            "purpose": "FRX.001",
            "goal": "",
            "source_link": "",
            "supplier": "ООО Поставка",
            "ordered_quantity": "10",
            "unit": "шт",
            "quality_document_required": False,
        },
    )


def test_expected_line_is_inspected_numbered_and_placed_before_warehouse():
    line = create_line_from_expected(expected_message())
    inspected = line.inspect(
        inspection(),
        Actor(name="Приёмщик", department="ОТК"),
        existing_grns=("FRX.26.0000001",),
    )

    assert inspected.status is ReceivingStatus.ACCEPTED
    assert inspected.article == "CON-001"
    assert inspected.grn == "FRX.26.0000002"
    assert inspected.ready_for_warehouse is False
    placed = inspected.place("1F4")
    assert placed.ready_for_warehouse is True
    assert placed.mark_warehouse_event_emitted().warehouse_event_emitted is True


def test_manufacturer_series_is_recorded_during_inspection():
    line = create_line_from_expected(expected_message())

    inspected = line.inspect(
        inspection(series="LOT-2409"),
        Actor(name="Приёмщик", department="ОТК"),
        existing_grns=(),
    )

    assert inspected.series == "LOT-2409"


def test_unknown_purpose_requires_narrow_manual_grn_prefix():
    message = expected_message()
    line = create_line_from_expected(
        OutboxMessage(**{**message.__dict__, "payload": {**message.payload, "purpose": "Другое"}})
    )
    with pytest.raises(ReceivingValidationError, match="идентификатор ГРН"):
        line.inspect(
            inspection(), Actor(name="Приёмщик", department="ОТК"), existing_grns=()
        )
    assert line.inspect(
        inspection(),
        Actor(name="Приёмщик", department="ОТК"),
        existing_grns=(),
        manual_grn_prefix="PPM",
    ).grn == "PPM.26.0000001"
