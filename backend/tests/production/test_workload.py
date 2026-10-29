"""«Загрузка»: часы по ЭТК, источник «сделано», мощность с праздниками и отпусками, порядок прогноза, «не успевает»."""

from datetime import date
from decimal import Decimal

from app.modules.production.domain import WorkShift
from app.modules.production.launchable import BlockInput, RouteStep
from app.modules.production.workload import (
    SHOP,
    SOURCE_SHIPPED,
    SOURCE_SHOP,
    SOURCE_STOCK,
    UNASSIGNED,
    AbsenceSpan,
    Done,
    KbLoad,
    ProjectLoad,
    Staff,
    bottlenecks,
    capacity_days,
    demand,
    done_of,
    forecast,
    kb_site,
    operation_kind,
    status_of,
    unit_hours,
    weeks,
)

D = Decimal
MONDAY = date(2026, 11, 2)  # понедельник
SHIFT = WorkShift()  # пн–пт, 7 ч чистого времени


def step(position, code, stage, seconds, **extra):
    return RouteStep(position=position, code=code, stage=stage, description=stage,
                     seconds_per_unit=D(seconds), setup_seconds=D(0), **extra)


def kb(project="708К", index="КБ1", run=10, done=D(0), site="ЦАР", per_unit=None):
    return KbLoad(project, index, "Жгут", run, Done(done, SOURCE_SHOP if done else ""), site, per_unit or {"Обжим": D(1)})


def project(name, deadline=None, kbs=(), opened_on=None):
    return ProjectLoad(name, "", deadline, opened_on, tuple(kbs))


def test_operation_kind_by_card_code_then_by_stage_name():
    assert operation_kind("PRS_TERMINAL_hand", "что угодно") == "Обжим"
    assert operation_kind("CUT_WIRE+1crimp", "Автомат резка + опрессовка") == "Резка"
    assert operation_kind("SDR_COAX_pin", "") == "Пайка"
    assert operation_kind("HEAT_TUT", "") == "Термоусадка"
    assert operation_kind("", "Опрессовка втулки экрана") == "Обжим"
    assert operation_kind("", "Резка с обжимом") == "Резка"
    assert operation_kind("", "Усадка ТУТ") == "Термоусадка"
    assert operation_kind("", "Контроль КСВН") == "Контроль"
    assert operation_kind("", "Разделка коаксиала") == "Зачистка"
    assert operation_kind("ASM", "Монтаж в корпус") == "Монтаж"
    assert operation_kind("", "Свивка") == "Прочее"


def test_unit_hours_sum_norms_by_kind_and_multiply_pieces_per_product():
    chain = [step(1, "PRS", "Опрессовка", 1800), step(2, "", "Опрессовка", 1800), step(3, "", "Пайка разъёма", 3600)]
    assert unit_hours(chain) == {"Обжим": D(1), "Пайка": D(1)}
    # Схема ПФ: ПФ-1 (резка) идёт по 2 штуки на изделие — его норма считается дважды.
    blocks = [
        step(1, "CUT_WIRE", "Резка", 900, block="b1", block_name="Резка", manual=True, block_number=1),
        step(2, "INS_x", "Монтаж", 1800, block="b2", block_name="Сборка", final=True, manual=True, block_number=2,
             inputs=(BlockInput("b1", D(2)),)),
    ]
    assert unit_hours(blocks) == {"Резка": D("0.5"), "Монтаж": D("0.5")}


def test_done_takes_the_largest_fact_and_names_its_source():
    assert done_of(D(0), D(0), D(0)) == Done(D(0), "")
    assert done_of(D(5), D(0), D(0)) == Done(D(5), SOURCE_SHOP)
    assert done_of(D(5), D(8), D(3)) == Done(D(8), SOURCE_STOCK)
    assert done_of(D(5), D(8), D(9)) == Done(D(9), SOURCE_SHIPPED)
    # Равенство — первый по порядку: сдано цехом, принято складом, отгружено.
    assert done_of(D(8), D(8), D(8)).source == SOURCE_SHOP


def test_remaining_hours_are_norm_times_run_minus_done_never_negative():
    item = kb(run=10, done=D(4), per_unit={"Обжим": D("0.5"), "Пайка": D("0.25")})
    assert item.remaining == 6 and item.by_kind == {"Обжим": D(3), "Пайка": D("1.5")} and item.hours == D("4.5")
    assert kb(run=10, done=D(12)).hours == 0
    assert kb(run=None).hours == 0


def test_kb_site_from_handover_then_assigned_fitters_else_unassigned():
    assert kb_site(["ЖУК"], ["ЦАР"]) == ("ЖУК", "задание отдано в ЖУК")
    assert kb_site(["", ""], ["ЦАР", "ЖУК", "ЦАР"])[0] == "ЦАР"
    assert kb_site([], [""])[0] == "ЦАР"  # монтажник без площадки — ЦАР
    assert kb_site([], []) == (UNASSIGNED, "нет заданий с исполнителями")


def test_capacity_counts_present_fitters_on_working_days_without_holidays_and_leave():
    staff = [Staff(1, "ЦАР"), Staff(2, "ЦАР"), Staff(3, "ЖУК"), Staff(4, "")]
    holidays = frozenset({date(2026, 11, 4)})
    absences = [AbsenceSpan(2, date(2026, 11, 3), date(2026, 11, 3))]

    days = capacity_days(MONDAY, 7, SHIFT, holidays, staff, absences)

    assert [day.capacity["ЦАР"] for day in days] == [D(21), D(14), D(0), D(21), D(21), D(0), D(0)]
    assert [day.capacity["ЖУК"] for day in days] == [D(7), D(7), D(0), D(7), D(7), D(0), D(0)]
    assert [day.working for day in days] == [True, True, False, True, True, False, False]


def test_forecast_fills_by_deadline_then_plan_order_and_flags_late():
    days = capacity_days(MONDAY, 21, SHIFT, frozenset(), [Staff(1, "ЦАР")], [])  # 7 ч в рабочий день
    # Срок один — раньше открытый идёт первым: 14 ч → пн+вт.
    early = project("701К", date(2026, 11, 6), [kb("701К", run=14)], opened_on=date(2026, 1, 1))
    # Ещё 28 ч → ср, чт, пт и пн 09.11 — к сроку 06.11 не успеть.
    late = project("702К", date(2026, 11, 6), [kb("702К", run=28)], opened_on=date(2026, 2, 1))
    free = project("703К", None, [kb("703К", run=7)])  # без срока — после всех: вт 10.11
    nothing = project("704К", date(2026, 11, 3), [kb("704К", run=0)])  # нет часов — не в прогнозе

    plan = forecast([free, late, early, nothing], days)

    assert plan.forecasts["701К"].ready == date(2026, 11, 3) and plan.forecasts["701К"].status == "в срок"
    assert plan.forecasts["702К"].ready == date(2026, 11, 9) and plan.forecasts["702К"].status == "не успевает"
    assert plan.forecasts["702К"].slack_days == -3
    assert plan.forecasts["703К"].ready == date(2026, 11, 10) and plan.forecasts["703К"].status == "без срока"
    assert "704К" not in plan.forecasts


def test_unassigned_work_takes_any_free_site_and_site_work_only_its_own():
    days = capacity_days(MONDAY, 5, SHIFT, frozenset(), [Staff(1, "ЦАР"), Staff(2, "ЖУК")], [])
    zhuk = project("701К", date(2026, 11, 2), [kb("701К", run=14, site="ЖУК")])
    loose = project("702К", date(2026, 11, 3), [kb("702К", run=7, site=UNASSIGNED)])

    plan = forecast([zhuk, loose], days)

    # ЖУК занят 701К пн и вт; «не распределено» берёт ЦАР в понедельник.
    assert plan.forecasts["701К"].ready == date(2026, 11, 3) and plan.forecasts["701К"].status == "не успевает"
    assert plan.forecasts["702К"].ready == date(2026, 11, 2) and plan.forecasts["702К"].status == "риск"
    assert plan.planned[0] == {"ЖУК": {"Обжим": D(7)}, "ЦАР": {"Обжим": D(7)}}


def test_work_that_does_not_fit_the_horizon_has_no_ready_date():
    days = capacity_days(MONDAY, 5, SHIFT, frozenset(), [Staff(1, "ЦАР")], [])
    plan = forecast([project("701К", date(2026, 12, 1), [kb("701К", run=100, site="ЖУК")])], days)
    assert plan.forecasts["701К"].ready is None and plan.forecasts["701К"].status == "не успевает"


def test_status_risk_inside_three_days_of_deadline():
    assert status_of(date(2026, 11, 2), date(2026, 11, 5)) == ("в срок", 3)
    assert status_of(date(2026, 11, 3), date(2026, 11, 5)) == ("риск", 2)
    assert status_of(None, None) == ("без срока", None)


def test_demand_spreads_deadline_hours_over_working_days_and_marks_bottlenecks():
    days = capacity_days(MONDAY, 14, SHIFT, frozenset(), [Staff(1, "ЦАР")], [])  # 35 ч в неделю
    due = project("701К", date(2026, 11, 6), [kb("701К", run=50, per_unit={"Пайка": D("0.8"), "Обжим": D("0.2")})])
    overdue = project("702К", date(2026, 10, 1), [kb("702К", run=5, site=UNASSIGNED)])
    no_deadline = project("703К", None, [kb("703К", run=100)])
    projects = [due, overdue, no_deadline]

    needed = demand(projects, days)
    plan = forecast(projects, days)
    rows = weeks(days, plan, needed, 2)

    assert needed[0]["ЦАР"] == {"Пайка": D(8), "Обжим": D(2)} and needed[0][UNASSIGNED] == {"Обжим": D(5)}
    assert needed[5] == {} and needed[7] == {}  # суббота пустая; срок прошёл — дальше не нужно
    first = rows[0]
    assert first.start == MONDAY and first.capacity["ЦАР"] == D(35) and first.capacity[SHOP] == D(35)
    assert sum(first.demand["ЦАР"].values()) == D(50) and sum(first.demand[SHOP].values()) == D(55)
    assert first.planned["ЦАР"] == D(35) and rows[1].demand == {}
    found = bottlenecks(rows)
    assert [(item.site, item.start) for item in found] == [("ЦАР", MONDAY), (SHOP, MONDAY)]
    assert found[0].kinds[0] == ("Пайка", D(40)) and found[0].capacity == D(35)
