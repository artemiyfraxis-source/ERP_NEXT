from datetime import date, datetime, timezone

import pytest

from app.modules.production.domain import ProductionValidationError
from app.modules.production.plan import (
    PLAN_KINDS,
    KIT_STAGES,
    PlanEvent,
    PlanProjectSnapshot,
    KitReview,
    SupplyPosition,
    days_left,
    kit_key_from_purpose,
    kit_stage_key,
    normalize_project_number,
    project_from_purpose,
    project_stage,
    summarize_journal,
    summarize_kit_marks,
    summarize_supply,
    supply_bucket,
    validate_plan_note,
)
from app.modules.techcards.project_cards import ProjectTechcard
from app.workflows.production_plan import build_production_plan


def test_project_from_purpose_matches_current_supplymap_examples():
    assert project_from_purpose("708К.4 · КБ1 · жгут") == "708К.4"
    assert project_from_purpose("708К.4-КБ2 корпус") == "708К.4"
    assert project_from_purpose("591К.5.001") == "591К.5"
    assert project_from_purpose("735К.") == "735К.1"
    assert project_from_purpose("713К.2.2 КБ1") == "713К.2.2"
    assert project_from_purpose("") == ""


def test_supply_buckets_and_summary_match_current_supplymap():
    assert supply_bucket("Отменено", True) == ""
    assert supply_bucket("Готово", True) == "stock"
    assert supply_bucket("Закрыто", False) == "stock"
    assert supply_bucket("Карантин", True) == "transit"
    assert supply_bucket("Жду счет", True) == "buying"
    assert supply_bucket("Новый", False) == "request"

    summary = summarize_supply((
        SupplyPosition("708к.4", "stock", "01.10.2026", True),
        SupplyPosition("708К.4", "transit", "05.10.2026"),
        SupplyPosition("708К.4", "buying", "03.10.2026"),
        SupplyPosition("708К.4", "", "09.10.2026"),
    ))["708К.4"]
    assert (summary.total, summary.stock, summary.transit, summary.buying) == (3, 1, 1, 1)
    assert summary.customer_supplied_stock == 1
    assert summary.eta == "05.10.2026"


def test_journal_keeps_append_only_history_and_current_blocker():
    journal = summarize_journal((
        PlanEvent("708к.4", datetime(2026, 9, 1), "Анна", PLAN_KINDS["block"], "СТОП", "ждём разъёмы"),
        PlanEvent("708К.4", datetime(2026, 9, 2), "Анна", PLAN_KINDS["note"], text="звонила"),
    ))["708К.4"]
    assert journal.blocker and journal.blocker.type == "СТОП"
    assert journal.last and journal.last.text == "звонила"
    assert journal.events[0].text == "звонила"

    unblocked = summarize_journal((
        PlanEvent("708К.4", datetime(2026, 9, 1), "", PLAN_KINDS["block"], "СТОП", "x"),
        PlanEvent("708К.4", datetime(2026, 9, 2), "", PLAN_KINDS["unblock"]),
    ))["708К.4"]
    assert unblocked.blocker is None


def test_project_stage_uses_farthest_fact_per_kit_and_slowest_kit_for_project():
    first = kit_stage_key("708к.4", "КБ1")
    second = kit_stage_key("708К.4", "кб2")
    assert kit_key_from_purpose("708К.4-КБ2 корпус") == second
    stage = project_stage("708К.4", ("КБ1", "КБ2"), {
        "tc": {first, second}, "kit": {first, second}, "work": {first},
        "stock": {first}, "shipped": set(),
    })
    assert stage.status == "КБ набрана"
    assert [item.status for item in stage.kits] == ["КБ на складе", "КБ набрана"]
    assert project_stage("708К.4", ("КБ1",), {}, "Отменён").status == "Отменён"
    assert KIT_STAGES[-1] == "Отгружено"


def test_manual_kit_stage_and_latest_techcard_review_override_facts():
    events = (
        PlanEvent("708К.4", datetime(2026, 9, 22, 9), "Мастер", PLAN_KINDS["stage"], item_index="КБ1", value="В работе"),
        PlanEvent("708К.4", datetime(2026, 9, 22, 10), "ОТК", PLAN_KINDS["tcReview"], text="контакт 2", item_index="КБ1", value="с погрешностями"),
        PlanEvent("708К.4", datetime(2026, 9, 22, 11), "ОТК", PLAN_KINDS["tcReview"], item_index="КБ1", value="проверена"),
    )
    marks = summarize_kit_marks(events)
    stage = project_stage("708К.4", ("КБ1",), {"tc": set()}, marks=marks)

    assert stage.status == "В работе"
    assert stage.kits[0].manual is True
    assert stage.kits[0].review == KitReview("проверена", "", "ОТК", datetime(2026, 9, 22, 11))


def test_newer_techcard_invalidates_aware_review_when_card_time_is_naive():
    card_time = datetime(2026, 9, 22, 9)
    card = ProjectTechcard.create(
        project="708К.4", item_index="КБ1", product_name="Жгут",
        source_calculation="", deadline=None, now=card_time,
    ).rebuild({"operations": [{"code": "OP-1"}]}, card_time).issue(card_time)
    review = PlanEvent(
        "708К.4", datetime(2026, 9, 22, 8, tzinfo=timezone.utc), "ОТК",
        PLAN_KINDS["tcReview"], item_index="КБ1", value="проверена",
    )

    rows = build_production_plan(
        cards=(card,), tasks=(), fulfillment=(), goods=(), requests=(), procurement=(),
        projects=(PlanProjectSnapshot("708К.4"),), events=(review,), today=date(2026, 9, 22),
    )

    assert rows[0].stage.kits[0].review == KitReview(
        "не проверена", "ТК выдана", "генератор", card_time,
    )


def test_manual_techcard_project_starts_at_ready_without_fake_card_rows():
    assert project_stage(
        "714К.", (), {"tc_project": {normalize_project_number("714К.")}},
    ).status == "ТК готова"


def test_deadline_and_note_validation_match_current_supplymap():
    assert days_left("25.09.2026", datetime(2026, 9, 22, 15)) == 3
    assert days_left("20.09.2026", date(2026, 9, 22)) == -2
    assert days_left("", date(2026, 9, 22)) is None
    validate_plan_note({"project": "708К.4", "kind": "unblock"}, ["708К.4"])
    validate_plan_note({"project": "708К.4", "kind": "stage", "item_index": "КБ1", "value": "авто"}, ["708К.4"])

    cases = (
        ({"project": "708К.4", "kind": "block", "text": "x"}, "Выбери"),
        ({"project": "1К.1", "kind": "note", "text": "x"}, "Проект не найден"),
        ({"project": "708К.4", "kind": "deadline"}, "Укажи дату"),
        ({"project": "708К.4", "kind": "tcReview", "item_index": "КБ1", "value": "с погрешностями"}, "Напиши"),
    )
    for payload, message in cases:
        with pytest.raises(ProductionValidationError, match=message):
            validate_plan_note(payload, ["708К.4"])
