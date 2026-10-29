from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

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
    work_days_left,
    kit_key_from_purpose,
    kit_stage_key,
    normalize_project_number,
    project_from_purpose,
    project_stage,
    project_terminal,
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
    # Куплено — оплаченные позиции (в пути и на складе), давальческие не в счёт.
    assert summary.purchased == 1
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
    # 29.10: «ТК готова» / «КБ набрана» — отметки, не этапы: КБ2 без работы — «Новый» с обеими отметками.
    assert stage.status == "Новый"
    assert [item.status for item in stage.kits] == ["Принято складом", "Новый"]
    assert [(item.tc, item.kit) for item in stage.kits] == [(True, True), (True, True)]
    assert (stage.tc, stage.kit) == (True, True)
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


def test_kit_stage_carries_techcard_and_kit_facts_under_a_manual_stage():
    first = kit_stage_key("708К.4", "КБ1")
    marks = summarize_kit_marks((
        PlanEvent("708К.4", datetime(2026, 9, 28, 9), "Мастер", PLAN_KINDS["stage"], item_index="КБ2", value="В работе"),
    ))
    stage = project_stage("708К.4", ("КБ1", "КБ2"), {"tc": {first}, "kit": {first}}, marks=marks)

    # КБ2 «В работе» вручную: работа начата — «КБ набрана» да (правило листа), ТК — нет.
    assert [(item.status, item.manual, item.tc, item.kit) for item in stage.kits] == [
        ("Новый", False, True, True),
        ("В работе", True, False, True),
    ]
    assert (stage.tc, stage.kit) == (False, True)
    # A manual-techcard project counts «ТК выдана» for every KB without a card.
    manual_tc = project_stage("714К.", ("КБ1",), {"tc_project": {normalize_project_number("714К.")}})
    assert (manual_tc.kits[0].status, manual_tc.kits[0].tc, manual_tc.kits[0].kit) == ("Новый", True, False)


def test_stage_mark_without_kb_is_the_stage_of_a_project_without_kits():
    def marks(*values):
        return summarize_kit_marks(tuple(
            PlanEvent("70К.26.13", datetime(2026, 9, 28, 9, index), "план цеха", PLAN_KINDS["stage"], value=value)
            for index, value in enumerate(values)
        ))

    shipped = project_stage("70К.26.13", (), {}, marks=marks("Отгружено"))
    assert (shipped.status, shipped.manual, shipped.kits) == ("Отгружено", True, ())
    # «авто» removes the project-level mark; the project falls back to the derived stage.
    assert project_stage("70К.26.13", (), {}, marks=marks("В работе", "авто")) == project_stage("70К.26.13", (), {})
    # A terminal registry status wins over the mark, as over any derived stage.
    assert project_stage("70К.26.13", (), {}, "Отменён", marks("В работе")).status == "Отменён"
    # A project with KBs ignores a project-level mark: its stage stays the slowest KB.
    with_kits = project_stage("70К.26.13", ("КБ1",), {}, marks=marks("Отгружено"))
    assert (with_kits.status, with_kits.manual) == ("Новый", False)
    # A manual-techcard project without cards also takes the mark over its «ТК готова» start.
    manual_tc = project_stage(
        "714К.", (), {"tc_project": {normalize_project_number("714К.")}},
        marks=summarize_kit_marks((PlanEvent("714К.", datetime(2026, 9, 28), "", PLAN_KINDS["stage"], value="В работе"),)),
    )
    assert manual_tc.status == "В работе"


def test_plan_shows_registry_project_without_kits_by_its_journal_stage_and_blocker():
    events = (
        # Старая запись журнала со словом «КБ на складе» читается как «Принято складом».
        PlanEvent("70К.26.13", datetime(2026, 9, 28, 9), "план цеха", PLAN_KINDS["stage"], value="КБ на складе"),
        # Старый блокер ХОЛД (журнал неизменяем) читается как «СТОП» (29.10).
        PlanEvent("70К.26.13", datetime(2026, 9, 28, 10), "план цеха", PLAN_KINDS["block"], "ХОЛД", "ждём оплату"),
    )
    rows = build_production_plan(
        cards=(), tasks=(), fulfillment=(), goods=(), requests=(), procurement=(),
        projects=(PlanProjectSnapshot("70К.26.13", customer="Заказчик"),), events=events,
        today=date(2026, 9, 28),
    )

    assert [(row.project, row.stage.status, row.stage.manual) for row in rows] == [("70К.26.13", "Принято складом", True)]
    assert rows[0].journal.blocker and rows[0].journal.blocker.type == "СТОП"
    assert rows[0].group == "hold"


def test_manual_techcard_project_is_new_with_the_techcard_marker_without_fake_card_rows():
    stage = project_stage("714К.", (), {"tc_project": {normalize_project_number("714К.")}})
    assert (stage.status, stage.kits, stage.tc, stage.kit) == ("Новый", (), True, False)


def test_deadline_and_note_validation_match_current_supplymap():
    assert days_left("25.09.2026", datetime(2026, 9, 22, 15)) == 3
    assert days_left("20.09.2026", date(2026, 9, 22)) == -2
    assert days_left("", date(2026, 9, 22)) is None
    validate_plan_note({"project": "708К.4", "kind": "unblock"}, ["708К.4"])
    validate_plan_note({"project": "708К.4", "kind": "stage", "item_index": "КБ1", "value": "авто"}, ["708К.4"])
    # A stage mark with an empty KB is the stage of the whole project.
    validate_plan_note({"project": "708К.4", "kind": "stage", "item_index": " ", "value": "Отгружено"}, ["708К.4"])
    validate_plan_note({"project": "708К.4", "kind": "stage", "value": "авто"}, ["708К.4"])

    cases = (
        ({"project": "708К.4", "kind": "block", "text": "x"}, "Выбери"),
        ({"project": "1К.1", "kind": "note", "text": "x"}, "Проект не найден"),
        ({"project": "708К.4", "kind": "deadline"}, "Укажи дату"),
        ({"project": "708К.4", "kind": "tcReview", "item_index": "КБ1", "value": "с погрешностями"}, "Напиши"),
        ({"project": "708К.4", "kind": "stage", "item_index": "корпус", "value": "В работе"}, "Укажи КБ"),
        ({"project": "708К.4", "kind": "stage", "value": "Готово"}, "Неизвестный этап"),
        ({"project": "708К.4", "kind": "tcReview", "value": "проверена"}, "Укажи КБ"),
    )
    for payload, message in cases:
        with pytest.raises(ProductionValidationError, match=message):
            validate_plan_note(payload, ["708К.4"])


def _status(value):
    return SimpleNamespace(value=value)


def test_supply_eta_takes_procurement_plan_date_before_request_date():
    # ProdPlan.gs planSupplyPositions_: Закупки ОЖИД. || Закупки ПЛАН. || заявка (PP-3).
    planned, expected, bare = uuid4(), uuid4(), uuid4()
    requests = [
        SimpleNamespace(id=request_id, project_reference="708К.4", purpose="", link=None,
                        status=_status("Новый"), planned_date=date(2026, 10, 1))
        for request_id in (planned, expected, bare)
    ]
    procurement = [
        SimpleNamespace(source_request_id=planned, status=_status("Жду счет"),
                        expected_arrival=None, planned_date=date(2026, 10, 9)),
        SimpleNamespace(source_request_id=expected, status=_status("Жду счет"),
                        expected_arrival=date(2026, 10, 5), planned_date=date(2026, 10, 20)),
    ]

    rows = build_production_plan(
        cards=(), tasks=(), fulfillment=(), goods=(), requests=requests, procurement=procurement,
        projects=(PlanProjectSnapshot("708К.4"),),
        events=(PlanEvent("708К.4", datetime(2026, 9, 28), "", PLAN_KINDS["note"], text="есть"),),
        today=date(2026, 9, 28),
    )

    assert rows[0].supply.eta == "09.10.2026"
    assert (rows[0].supply.buying, rows[0].supply.request) == (2, 1)


def test_a_broken_supply_column_leaves_the_plan_standing():
    # ProdPlan.gs prodPlanData_ `safe` (PP-5): the column is empty, the project row stays.
    broken = [SimpleNamespace(id=uuid4())]  # no status/purpose: summarising supply raises

    rows = build_production_plan(
        cards=(), tasks=(), fulfillment=(), goods=(), requests=broken, procurement=(),
        projects=(PlanProjectSnapshot("708К.4", terminal=True, status="Закрыт"),), events=(PlanEvent("708К.4", datetime(2026, 9, 22, 8, tzinfo=timezone.utc), "Мастер", PLAN_KINDS["note"], text="заметка"),),
        today=date(2026, 9, 28),
    )

    assert [(row.project, row.supply, row.terminal) for row in rows] == [("708К.4", None, True)]


def test_plan_rows_come_only_from_the_project_registry():
    card_time = datetime(2026, 9, 22, 9)
    orphan = ProjectTechcard.create(
        project="999К.1", item_index="КБ1", product_name="Жгут",
        source_calculation="", deadline=None, now=card_time,
    )

    rows = build_production_plan(
        cards=(orphan,), tasks=(), fulfillment=(), goods=(), requests=(), procurement=(),
        projects=(PlanProjectSnapshot("708К.4"), PlanProjectSnapshot("710К.1")), events=(PlanEvent("708К.4", datetime(2026, 9, 22, 8, tzinfo=timezone.utc), "Мастер", PLAN_KINDS["note"], text="заметка"),), today=date(2026, 9, 22),
    )

    # Карточка без строки реестра и голая строка реестра (710К.1) в план не попадают (PP-1).
    assert [row.project for row in rows] == ["708К.4"]


def test_shipment_overrides_a_manual_stage_and_the_journal_closes_or_reopens_a_project():
    marks = summarize_kit_marks((
        PlanEvent("708К.4", datetime(2026, 9, 22, 9), "Мастер", PLAN_KINDS["stage"], item_index="КБ1", value="В работе"),
    ))
    shipped = project_stage("708К.4", ("КБ1",), {"shipped": {"708К.4|КБ1"}}, marks=marks)
    assert (shipped.status, shipped.kits[0].manual) == ("Отгружено", False)

    snapshot = PlanProjectSnapshot("708К.4", status="Отменён", terminal=True)
    assert project_terminal(snapshot, None) == "Отменён"
    reopened = summarize_journal((
        PlanEvent("708К.4", datetime(2026, 9, 22, 9), "РП", PLAN_KINDS["status"], value="авто"),
    ))["708К.4"]
    assert project_terminal(snapshot, reopened) == ""
    closed = summarize_journal((
        PlanEvent("708К.4", datetime(2026, 9, 22, 9), "РП", PLAN_KINDS["status"], value="Слился"),
    ))["708К.4"]
    # «Слился» — слово прежнего списка (до 12.10): читается как «Отменён».
    assert project_terminal(None, closed) == "Отменён"
    with pytest.raises(ProductionValidationError, match="статус"):
        validate_plan_note({"project": "708К.4", "kind": "status", "value": "Готово"}, ("708К.4",))


def test_work_days_left_skips_weekends_and_shop_holidays():
    # Ср 14.10.2026 → пт 16.10: чт и пт; → пн 19.10: ещё пн, выходные не в счёт.
    assert work_days_left(date(2026, 10, 16), date(2026, 10, 14)) == 2
    assert work_days_left(date(2026, 10, 19), datetime(2026, 10, 14, 18)) == 3
    assert work_days_left(date(2026, 10, 14), date(2026, 10, 14)) == 0
    # Праздник 04.11 (ср) из календаря цеха не считается.
    assert work_days_left(date(2026, 11, 6), date(2026, 11, 2), holidays={date(2026, 11, 4)}) == 3
    # Просрочка: рабочие дни после дедлайна по сегодня, со знаком минус.
    assert work_days_left(date(2026, 10, 9), date(2026, 10, 14)) == -3
    # Шестидневка: суббота рабочая.
    assert work_days_left(date(2026, 10, 19), date(2026, 10, 14), weekdays=(0, 1, 2, 3, 4, 5)) == 4
    assert work_days_left("", date(2026, 10, 14)) is None
