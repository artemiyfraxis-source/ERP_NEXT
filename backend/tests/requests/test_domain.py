from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.requests.domain import (
    RequestDraft,
    RequestStatus,
    ValidationError,
    create_request_batch,
    derive_priority,
    build_procurement_requested_snapshot,
    next_designation,
    next_legacy_id,
)
from tests.factories import Actor


def draft(**overrides) -> RequestDraft:
    values = {
        "purpose": "RND",
        "project_reference": "591К.3",
        "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
        "name": "Разъём SMA",
        "item_type": "Комплектующие",
        "quantity": Decimal("2"),
        "unit": "шт",
        "quality_document": "Нет",
        "procurement_deadline": date(2026, 9, 18),
        "planned_date": date(2026, 9, 25),
        "price_with_vat": Decimal("1000.50"),
    }
    values.update(overrides)
    return RequestDraft(**values)


def test_create_batch_preserves_legacy_rules_without_using_legacy_id_as_pk():
    requests = create_request_batch(
        [draft(), draft(name="Кабель")],
        actor=Actor(name="Иванов И.И.", department="РНД"),
        now=datetime(2026, 9, 15, 10, 30),
        existing_legacy_ids=["SM-20260915-01"],
        existing_designations=["RND.004"],
    )

    assert [item.legacy_id for item in requests] == ["SM-20260915-02", "SM-20260915-03"]
    assert all(isinstance(item.id, UUID) for item in requests)
    assert all(str(item.id) != item.legacy_id for item in requests)
    assert {item.designation for item in requests} == {"RND.005"}
    assert all(item.status is RequestStatus.NEW for item in requests)
    assert all(item.task_type == "Закупка" for item in requests)
    assert all(item.priority == "Очень срочно" for item in requests)
    assert requests[0].line_sum == Decimal("2001.00")


def test_batch_uses_one_designation_per_purpose_and_separate_numbers_for_other_purposes():
    requests = create_request_batch(
        [draft(purpose="RND"), draft(purpose="FRX"), draft(purpose="RND")],
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15),
        existing_legacy_ids=[],
        existing_designations=["RND.002", "FRX.007"],
    )

    assert [item.designation for item in requests] == ["RND.003", "FRX.008", "RND.003"]


@pytest.mark.parametrize(
    ("deadline", "expected"),
    [
        (date(2026, 9, 14), "Очень срочно"),
        (date(2026, 9, 18), "Очень срочно"),
        (date(2026, 9, 22), "Срочно"),
        (date(2026, 9, 29), "Не срочно"),
        (date(2026, 9, 30), "В свободное время"),
    ],
)
def test_priority_boundaries(deadline, expected):
    assert derive_priority(deadline, date(2026, 9, 15)) == expected


def test_category_rules_are_applied_server_side():
    requests = create_request_batch(
        [draft(category="ОП", quality_document="Нет"), draft(category="ИП", quality_document="")],
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15),
        existing_legacy_ids=[],
        existing_designations=[],
    )

    assert [item.quality_document for item in requests] == ["Да", "Да"]


@pytest.mark.parametrize(
    "changes",
    [
        {"purpose": ""},
        {"delivery_point": ""},
        {"name": ""},
        {"item_type": ""},
        {"quantity": Decimal("0")},
        {"unit": ""},
        {"quality_document": "Может быть"},  # empty is «Нет» for Общепром (RQ-1)
    ],
)
def test_invalid_request_is_rejected(changes):
    with pytest.raises(ValidationError):
        create_request_batch(
            [draft(**changes)],
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15),
            existing_legacy_ids=[],
            existing_designations=[],
        )


def test_unknown_manual_purpose_is_rejected_but_project_reference_is_independent():
    with pytest.raises(ValidationError, match="НАЗНАЧЕНИЕ"):
        create_request_batch(
            [draft(purpose="591К.3", project_reference="591К.3")],
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15),
            existing_legacy_ids=[],
            existing_designations=[],
        )


def test_unknown_delivery_point_is_rejected():
    with pytest.raises(ValidationError, match="ТОЧКА ОТГРУЗКИ"):
        create_request_batch(
            [draft(delivery_point="ЦАР")],
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15),
            existing_legacy_ids=[],
            existing_designations=[],
        )


def test_procurement_mapping_is_repeatable_and_does_not_copy_request_status_or_deadline():
    request = create_request_batch(
        [draft()],
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15),
        existing_legacy_ids=[],
        existing_designations=[],
    )[0].start_procurement(Actor(name="Петров", department="Закупки"))

    first = build_procurement_requested_snapshot(request, today=date(2026, 9, 15))
    retry = build_procurement_requested_snapshot(request, today=date(2026, 9, 15))

    assert first == retry
    assert first.idempotency_key == f"request:{request.id}:procurement:v{request.version}"
    assert first.calculated_name == request.name
    assert first.calculated_quantity == request.quantity


def test_handoff_requires_explicit_in_work_transition():
    request = create_request_batch(
        [draft()],
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15),
        existing_legacy_ids=[],
        existing_designations=[],
    )[0]

    with pytest.raises(ValidationError, match="В работе"):
        build_procurement_requested_snapshot(request, today=date(2026, 9, 15))


def test_legacy_sequence_and_designation_helpers():
    assert next_legacy_id(["noise", "SM-20260915-09"], date(2026, 9, 15)) == "SM-20260915-10"
    assert next_designation("48К.13", ["48К.13.009"]) == "48К.13.010"


@pytest.mark.parametrize(
    ("category", "given", "expected"),
    [("Общепром", "", "Нет"), ("Общепром", "Да", "Да"), ("ИП", "", "Да"), ("ОП", "Нет", "Да")],
)
def test_quality_document_rule_matches_schema_passport_rule(category, given, expected):
    (request,) = create_request_batch(
        [draft(category=category, quality_document=given)],
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15, 10, 30),
        existing_legacy_ids=[],
        existing_designations=[],
    )

    assert request.quality_document == expected


def test_batch_error_names_the_position():
    with pytest.raises(ValidationError, match="^Позиция 2: Не заполнены обязательные поля: ТИП$"):
        create_request_batch(
            [draft(), draft(item_type=" ")],
            actor=Actor(name="Иванов", department="РНД"),
            now=datetime(2026, 9, 15, 10, 30),
            existing_legacy_ids=[],
            existing_designations=[],
        )


def minimal_draft(**overrides) -> RequestDraft:
    values = {
        "purpose": "RND",
        "delivery_point": None,
        "name": "Болт М3",
        "item_type": None,
        "quantity": Decimal("10"),
        "unit": "шт",
        "quality_document": None,
        "procurement_deadline": None,
        "planned_date": None,
    }
    values.update(overrides)
    return RequestDraft(**values)


def create_one(item: RequestDraft):
    (request,) = create_request_batch(
        [item],
        actor=Actor(name="Иванов", department="РНД"),
        now=datetime(2026, 9, 15, 10, 30),
        existing_legacy_ids=[],
        existing_designations=[],
    )
    return request


def test_minimal_draft_gets_defaults():
    request = create_one(minimal_draft())

    assert request.quality_document == "Нет"
    assert request.delivery_point == "г. Москва, ул. 6-я Радиальная, д. 17"
    assert request.item_type == "Комплектующие"
    assert request.procurement_deadline == date(2026, 9, 22)
    assert request.planned_date == date(2026, 9, 29)
    assert request.priority == "Срочно"


def test_typed_values_beat_defaults():
    request = create_one(
        minimal_draft(
            delivery_point="г. Раменское, ул. 100-й Свирской Дивизии, д. 52",
            item_type="Инструмент",
            procurement_deadline=date(2026, 9, 16),
            planned_date=date(2026, 10, 1),
            quality_document="Да",
        )
    )

    assert request.quality_document == "Да"
    assert request.delivery_point.startswith("г. Раменское")
    assert request.item_type == "Инструмент"
    assert request.procurement_deadline == date(2026, 9, 16)
    assert request.planned_date == date(2026, 10, 1)


def test_quality_default_follows_category_rule():
    assert create_one(minimal_draft(category="ОП")).quality_document == "Да"
    assert create_one(minimal_draft(category="ИП")).quality_document == "Да"


@pytest.mark.parametrize("field", ["name", "unit"])
def test_name_and_unit_stay_required(field):
    with pytest.raises(ValidationError, match="Позиция 1:"):
        create_one(minimal_draft(**{field: " "}))
