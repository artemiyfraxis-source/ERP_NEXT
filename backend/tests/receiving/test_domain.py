from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.receiving.domain import (
    QUARANTINE_WORKDAYS,
    InspectionInput,
    ReceivingStatus,
    ReceivingValidationError,
    grn_prefix_for,
    inspect_delivery,
    next_grn,
    verdict_reference_date,
    create_line_from_expected,
    latest_overdue_inspection_date,
    quarantine_notify_targets,
    workdays_between,
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


def test_historical_receipt_judges_expiry_on_document_date():
    # Receiving.gs:685-693: old-but-good stock must not land in terminal «Возврат» when entered late.
    late = inspection(expiry_date=date(2026, 3, 1), inspection_date=date(2026, 9, 21))
    historical = inspect_delivery(
        inspection(
            expiry_date=date(2026, 3, 1),
            inspection_date=date(2026, 9, 21),
            verdict_date=date(2026, 2, 10),
        )
    )
    expired_then = inspect_delivery(
        inspection(
            expiry_date=date(2026, 2, 9),
            inspection_date=date(2026, 9, 21),
            verdict_date=date(2026, 2, 10),
        )
    )

    assert inspect_delivery(late).status is ReceivingStatus.RETURN
    assert historical.status is ReceivingStatus.ACCEPTED
    assert historical.verdict == "Годен"
    assert expired_then.status is ReceivingStatus.RETURN


def test_verdict_reference_date_falls_back_from_document_to_arrival_to_today():
    today = date(2026, 9, 21)
    upd = date(2026, 2, 10)
    arrival = date(2026, 2, 12)

    assert verdict_reference_date(today=today, historical=False, document_date=upd) == today
    assert verdict_reference_date(
        today=today, historical=True, document_date=upd, arrival_date=arrival
    ) == upd
    assert verdict_reference_date(today=today, historical=True, arrival_date=arrival) == arrival
    assert verdict_reference_date(today=today, historical=True) == today


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


@pytest.mark.parametrize("location", ["", "   ", "A-01"])
def test_receiving_places_only_on_a_rack_address_never_as_is(location):
    inspected = create_line_from_expected(expected_message()).inspect(
        inspection(), Actor(name="Приёмщик", department="ОТК"), existing_grns=(),
    )

    with pytest.raises(ReceivingValidationError, match="Место хранения"):
        inspected.place(location)
    assert inspected.ready_for_warehouse is False
    assert inspected.place("01f04").storage_location == "1F4"


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


def test_receiving_places_only_where_the_rack_layout_has_a_place():
    inspected = create_line_from_expected(expected_message()).inspect(
        inspection(), Actor(name="Приёмщик", department="ОТК"), existing_grns=(),
    )

    with pytest.raises(ReceivingValidationError, match="Места 18A1 нет: проверь номер стеллажа и полку."):
        inspected.place("18A1")
    assert inspected.place("17A1").storage_location == "17A1"


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 7, 2), date(2026, 7, 6), 2),  # чт → пн: пт, пн
        (date(2026, 7, 3), date(2026, 7, 6), 1),  # пт → пн
        (date(2026, 7, 3), date(2026, 7, 5), 0),  # пт → вс: выходные не считаются
        (date(2026, 7, 6), date(2026, 7, 10), 4),  # пн → пт той же недели
        (date(2026, 7, 6), date(2026, 7, 6), 0),
        (date(2026, 7, 10), date(2026, 7, 6), 0),  # обратный порядок
    ],
)
def test_workdays_between_matches_supplymap(start, end, expected):
    assert workdays_between(start, end) == expected


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 9, 23), date(2026, 9, 21)),  # ср → пн
        (date(2026, 9, 28), date(2026, 9, 24)),  # пн → чт
        (date(2026, 9, 27), date(2026, 9, 23)),  # вс → ср: пятница — единственный р.д. после чт
    ],
)
def test_latest_overdue_inspection_date_is_two_workdays_back(today, expected):
    assert latest_overdue_inspection_date(today) == expected
    assert workdays_between(expected, today) >= QUARANTINE_WORKDAYS
    assert workdays_between(expected + timedelta(days=1), today) < QUARANTINE_WORKDAYS


@pytest.mark.parametrize(
    ("nature", "expected"),
    [
        (("Механическое повреждение / брак",), ("Технолог",)),
        (("Недостача (факт < УПД)",), ("Снабжение",)),
        (("Механическое повреждение / брак", "Недовоз (УПД < заказ)"), ("Технолог", "Снабжение")),
        ((), ("Снабжение",)),
    ],
)
def test_quarantine_notify_targets_follow_the_nature(nature, expected):
    assert quarantine_notify_targets(nature) == expected


def waiting_line(inspected_on=date(2026, 9, 24)):
    line = create_line_from_expected(expected_message()).inspect(
        inspection(has_shipping_document=False, inspection_date=inspected_on),
        Actor(name="Приёмщик", department="ОТК"),
        existing_grns=(),
    )
    assert line.status is ReceivingStatus.WAITING
    assert line.inspected_on == inspected_on
    return line


def test_waiting_without_decision_goes_to_quarantine_after_two_workdays():
    line = waiting_line(date(2026, 9, 24))  # чт

    assert line.waiting_overdue(date(2026, 9, 25)) is False  # пт: 1 р.д.
    assert line.waiting_overdue(date(2026, 9, 27)) is False  # вс: всё ещё 1 р.д.
    assert line.waiting_overdue(date(2026, 9, 28)) is True  # пн: 2 р.д.
    with pytest.raises(ReceivingValidationError, match="Карантин"):
        line.quarantine_overdue(date(2026, 9, 25))
    quarantined = line.quarantine_overdue(date(2026, 9, 28))
    assert quarantined.status is ReceivingStatus.QUARANTINE
    assert quarantined.version == line.version + 1
    assert quarantined.grn == line.grn and quarantined.nonconformities == line.nonconformities


def test_timer_restarts_on_reinspection_and_ignores_other_statuses():
    line = waiting_line(date(2026, 9, 21))
    again = line.inspect(
        inspection(has_shipping_document=False, inspection_date=date(2026, 9, 24)),
        Actor(name="Приёмщик", department="ОТК"),
        existing_grns=(line.grn,),
    )
    assert again.waiting_overdue(date(2026, 9, 25)) is False
    assert again.waiting_overdue(date(2026, 9, 28)) is True

    accepted = create_line_from_expected(expected_message()).inspect(
        inspection(inspection_date=date(2026, 9, 1)),
        Actor(name="Приёмщик", department="ОТК"),
        existing_grns=(),
    )
    assert accepted.waiting_overdue(date(2026, 9, 28)) is False
    in_transit = create_line_from_expected(expected_message())
    assert in_transit.inspected_on is None
    assert in_transit.waiting_overdue(date(2026, 9, 28)) is False


def test_waiting_line_without_act_date_never_times_out():
    line = replace(waiting_line(), inspected_on=None)

    assert line.waiting_overdue(date(2027, 1, 1)) is False


def _inspected(**overrides):
    return create_line_from_expected(expected_message()).inspect(
        inspection(**overrides), Actor(name="Приёмщик", department="ОТК"), existing_grns=(),
    )


def test_new_vk_archives_inspected_line_and_opens_revision_in_transit():
    source = _inspected(
        document_quantity=Decimal("8"),
        fact_quantity=Decimal("8"),
        shipping_document_url="https://files.test/upd.pdf",
        series="LOT-1",
    )
    now = datetime(2026, 9, 22, 9, 0)

    archived, revision = source.open_vk_revision(existing_legacy_ids=(source.legacy_id,), now=now)

    assert archived.status is ReceivingStatus.VK_ARCHIVE
    assert archived.version == source.version + 1
    assert (archived.verdict, archived.grn, archived.nonconformities) == (
        source.verdict, source.grn, source.nonconformities,
    )
    assert revision.legacy_id == f"{source.legacy_id}-В2"
    assert revision.status is ReceivingStatus.IN_TRANSIT
    assert revision.version == 1
    assert revision.created_at == now
    assert revision.id != source.id
    assert revision.revision_of_id == source.id
    assert revision.revision_root_id == source.id
    assert revision.lineage_root_id == source.id
    # Order context and the delivery's УПД travel; acceptance fields start empty.
    assert (revision.procurement_line_id, revision.article, revision.ordered_quantity, revision.supplier) == (
        source.procurement_line_id, source.article, source.ordered_quantity, source.supplier,
    )
    assert revision.shipping_document_url == "https://files.test/upd.pdf"
    assert revision.series == "LOT-1"
    assert revision.verdict is None
    assert revision.grn is None
    assert revision.document_quantity is None
    assert revision.fact_quantity is None
    assert revision.accepted_quantity is None
    assert revision.backorder_quantity == Decimal("0")
    assert revision.nonconformities == ()
    assert revision.inspector is None
    assert revision.storage_location is None


def test_new_vk_revision_number_is_next_free_on_the_base_id():
    source = _inspected()
    _, second = source.open_vk_revision(
        existing_legacy_ids=(source.legacy_id, f"{source.legacy_id}-В2"), now=datetime(2026, 9, 22)
    )
    assert second.legacy_id == f"{source.legacy_id}-В3"

    reinspected = second.inspect(inspection(), Actor(name="Приёмщик", department="ОТК"), existing_grns=())
    _, third = reinspected.open_vk_revision(
        existing_legacy_ids=(source.legacy_id, f"{source.legacy_id}-В2", second.legacy_id),
        now=datetime(2026, 9, 23),
    )
    assert third.legacy_id == f"{source.legacy_id}-В4"
    assert third.revision_of_id == second.id
    assert third.revision_root_id == source.id


def test_new_vk_needs_a_held_act_and_a_line_not_yet_in_stock():
    line = create_line_from_expected(expected_message())
    with pytest.raises(ReceivingValidationError, match="ещё не проведён ВК"):
        line.open_vk_revision(existing_legacy_ids=(), now=datetime(2026, 9, 22))

    archived, _ = _inspected().open_vk_revision(existing_legacy_ids=(), now=datetime(2026, 9, 22))
    with pytest.raises(ReceivingValidationError, match="уже в архиве ВК"):
        archived.open_vk_revision(existing_legacy_ids=(), now=datetime(2026, 9, 22))

    in_stock = _inspected().place("1F4").mark_warehouse_event_emitted()
    with pytest.raises(ReceivingValidationError, match="передана на склад"):
        in_stock.open_vk_revision(existing_legacy_ids=(), now=datetime(2026, 9, 22))


def test_returned_line_can_get_a_new_vk_revision():
    returned = _inspected(expiry_date=date(2026, 1, 1))
    assert returned.status is ReceivingStatus.RETURN

    archived, revision = returned.open_vk_revision(existing_legacy_ids=(), now=datetime(2026, 9, 22))

    assert archived.status is ReceivingStatus.VK_ARCHIVE
    assert revision.status is ReceivingStatus.IN_TRANSIT


def test_archived_vk_line_is_closed_for_inspection_and_placement():
    archived, _ = _inspected().open_vk_revision(existing_legacy_ids=(), now=datetime(2026, 9, 22))

    with pytest.raises(ReceivingValidationError, match="Архив ВК"):
        archived.inspect(inspection(), Actor(name="Приёмщик", department="ОТК"), existing_grns=())
    with pytest.raises(ReceivingValidationError, match="только принятую"):
        archived.place("1F4")
    assert archived.ready_for_warehouse is False
