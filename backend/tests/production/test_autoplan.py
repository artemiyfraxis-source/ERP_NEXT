"""Автоплан (чистые правила): ключ наладки, цена переналадки, расчёт с партиями, станками и дедлайнами."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.modules.production.application import _busy
from app.modules.production.autoplan import (
    ADJUSTMENT_SECONDS,
    AutoplanInput,
    SetupKey,
    batches,
    changeover_seconds,
    equipment_units,
    inventory_prefix,
    operation_keys,
    plan,
    setup_key,
)
from app.modules.production.domain import (
    DEFAULT_SHIFT,
    BusyInterval,
    EtkOperation,
    OperationShare,
    OperatorSkill,
    PlanResult,
    ProductionOperation,
    ProductionTask,
)
from app.modules.production.machine_load import machine_load, task_occupancies, task_steps
from tests.production.test_machine_load import etk

ANNA = UUID("20000000-0000-0000-0000-00000000000a")
BORIS = UUID("20000000-0000-0000-0000-00000000000b")
VERA = UUID("20000000-0000-0000-0000-00000000000c")
MONDAY = datetime(2026, 10, 5, 9, 0)
PERIOD_END = datetime(2026, 10, 10)


def crimp_key(terminal: str = "T-1", height: str = "1,14 - 1,24", machine: str = "ОБ.027") -> SetupKey:
    return setup_key(
        code="PRS", stage="Опрессовка терминалов",
        built={"type": "prsTerm", "fields": {"applicator": "A19", "crimpHeight": height},
               "card": {"tooling": [{"section": "Оборудование", "inventory_number": f"{machine}.X", "name": "Пресс"}]}},
        config_op={"type": "prsTerm", "ep": {"termArt": terminal}}, config={},
    )


def operation(position: int, code: str, *, unit: str = "36", setup: str = "1800") -> ProductionOperation:
    return ProductionOperation.create(
        position=position, code=code, stage=code, description="",
        seconds_per_unit=Decimal(unit), setup_seconds=Decimal(setup), quantity=100,
    )


def task(number: int, operations, *, deadline: date | None = date(2026, 10, 30), quantity: int = 100) -> ProductionTask:
    return ProductionTask.create(
        number=f"ПЗ-{number:04d}", source_idempotency_key=f"key-{number}", source_fingerprint="fp",
        project="708К", item=f"КБ{number}", name="Жгут", quantity=quantity, fulfillment_request_id=None,
        deadline=deadline, launched_by="Мастер", operations=tuple(operations), created_at=MONDAY - timedelta(days=1),
    )


def data(tasks, keys, *, team=(ANNA, BORIS, VERA), skills=None, busy=None, capacity=None, free=None, start=MONDAY):
    operations = [item for current in tasks for item in current.operations]
    return AutoplanInput(
        tasks=tuple(tasks),
        free=frozenset(free if free is not None else (item.id for item in operations if not item.operator_ids)),
        keys=keys, team=tuple(team), skills=skills or {}, busy=busy or {}, capacity=capacity or {},
        start=start, end=PERIOD_END, holidays=frozenset(), shift=DEFAULT_SHIFT,
    )


def by_operation(result):
    return {item.operation_id: item for item in result.placements}


# --- ключ наладки -----------------------------------------------------------------------------------------------


def test_inventory_prefix_groups_units_of_one_machine():
    assert inventory_prefix("ОБ.027.X") == "ОБ.027" == inventory_prefix("ОБ.027.2") == inventory_prefix("OБ.027")
    assert equipment_units(["ОБ.027.1", "ОБ.027.2", "ОБ.027.2", "ОБ.048", "без номера"]) == {"ОБ.027": 2, "ОБ.048": 1}


def test_same_applicator_and_terminal_is_one_family_crimp_height_is_the_adjustment():
    first, second = crimp_key(height="1,14 - 1,24"), crimp_key(height="0,80 - 0,90")

    assert first.machine == "ОБ.027" and first.limited
    assert first.family == second.family and first.exact != second.exact
    assert changeover_seconds(first, crimp_key(height="1,14 - 1,24"), Decimal(1800)) == 0
    assert changeover_seconds(first, second, Decimal(1800)) == ADJUSTMENT_SECONDS
    assert changeover_seconds(first, second, Decimal(300)) == 300
    assert changeover_seconds(first, crimp_key(terminal="T-2"), Decimal(1800)) == 1800
    assert changeover_seconds(None, first, Decimal(1800)) == 1800
    assert "терминал T-1" in first.label and "высота обжима 1,14 - 1,24 мм" in first.label


def test_coax_same_cable_and_connector_with_other_l1_l3_is_the_same_family():
    def coax(l1: str, l2: str) -> SetupKey:
        return setup_key(
            code="COAX_STRIP", stage="coaxStrip", config_op={"type": "coaxStrip", "side": "A"}, config={},
            built={"type": "coaxStrip", "fields": {"coax": {"cable": "RG316", "connector": "SMA-M", "l1": l1, "l2": l2, "l3": "2"}}},
        )

    first, second = coax("6", "4"), coax("7", "4,5")
    other_cable = setup_key(
        code="COAX_STRIP", stage="coaxStrip", config_op={"type": "coaxStrip"}, config={},
        built={"fields": {"coax": {"cable": "RG58", "connector": "SMA-M", "l1": "6", "l2": "4", "l3": "2"}}},
    )

    assert first.family == second.family and first.exact != second.exact
    assert changeover_seconds(first, second, Decimal(900)) == min(900, ADJUSTMENT_SECONDS)
    assert other_cable.family != first.family
    assert first.label.startswith("Разделка коаксиала: кабель RG316, разъём SMA-M")


def test_cut_length_is_not_part_of_the_key_but_strip_lengths_are():
    def cut(length: str, strip_a: str) -> SetupKey:
        return setup_key(
            code="CUT", stage="cutWireStrip", config_op={"type": "cutWireStrip"}, config={},
            built={"fields": {"lengthKd": f"{length}мм", "wires": [{"art": "НВ-4 0,35", "length": length}],
                              "strip": {"a": True, "b": True, "lenA": strip_a, "lenB": "4"}}},
        )

    assert cut("120", "5").exact == cut("300", "5").exact
    assert cut("120", "5").family == cut("120", "7").family and cut("120", "5").exact != cut("120", "7").exact


def test_soldering_family_is_code_and_tooling_parameters():
    def solder(parameters: str) -> SetupKey:
        return setup_key(
            code="SOLDER", stage="Пайка разъёма", config_op={"type": "solderConn"}, config={},
            built={"card": {"tooling": [{"section": "Инструмент", "name": "Паяльник", "parameters": parameters}]}},
        )

    assert solder("жало 1,2 мм, 350 °C").family == solder("жало 1,2 мм, 350 °C").family
    assert solder("жало 1,2 мм, 350 °C").family != solder("жало 2,4 мм, 380 °C").family


def test_keys_fall_back_to_the_operation_code_when_the_card_does_not_match():
    current = task(1, [operation(1, "PRS"), operation(2, "ASM")])
    document = {
        "operations": [{"code": "PRS", "type": "prsTerm", "fields": {"applicator": "A19"}}, {"code": "OTHER"}],
        "build_config": {"ops": [{"type": "prsTerm", "opNumber": "PRS", "ep": {"termArt": "T-1"}}, {"opNumber": "OTHER"}]},
    }

    keys, warnings = operation_keys(current, document)
    missing, missing_warnings = operation_keys(current, None)

    assert keys[current.operations[0].id].family[1:] == ("A19", "T-1")
    assert keys[current.operations[1].id].family == ("ASM",)
    assert warnings == ("ПЗ-0001 · этап 2: код ASM не совпадает с техкартой (OTHER) — наладка по коду операции",)
    assert all(key.family == (item.code,) for item, key in zip(current.operations, (missing[item.id] for item in current.operations)))
    assert "техкарта 708К КБ1 не найдена" in missing_warnings[0]


# --- расчёт ---------------------------------------------------------------------------------------------------


def test_one_terminal_across_three_variants_goes_to_one_fitter_with_one_setup():
    tasks = [task(number, [operation(1, "PRS")]) for number in (1, 2, 3)]
    keys = {current.operations[0].id: crimp_key() for current in tasks}

    result = plan(data(tasks, keys, capacity={"ОБ.027": 3}))

    assert len(result.placements) == 3 and not result.unplaced
    assert {item.operator_id for item in result.placements} == {ANNA}
    assert [item.changeover_seconds for item in result.placements] == [1800, 0, 0]
    assert (result.changeovers, result.changeovers_naive, result.saved_seconds) == (1, 3, 3600)
    assert [item.pulled for item in result.placements] == [False, True, True]
    batch = batches(result.placements)[ANNA]
    assert len(batch) == 1 and len(batch[0].operations) == 3
    # Без партий тот же расчёт раздаёт варианты трём монтажникам, каждый налаживает пресс заново.
    naive = plan(data(tasks, keys, capacity={"ОБ.027": 3}), batching=False)
    assert len({item.operator_id for item in naive.placements}) == 3


def test_same_applicator_different_crimp_height_costs_a_short_adjustment():
    tasks = [task(1, [operation(1, "PRS")]), task(2, [operation(1, "PRS")])]
    keys = {tasks[0].operations[0].id: crimp_key(height="1,14 - 1,24"), tasks[1].operations[0].id: crimp_key(height="0,80 - 0,90")}

    result = plan(data(tasks, keys, team=(ANNA,)))

    assert [item.changeover_seconds for item in result.placements] == [1800, ADJUSTMENT_SECONDS]
    assert result.saved_seconds == 1800 - ADJUSTMENT_SECONDS


def test_two_presses_never_crimp_more_than_two_at_a_time():
    tasks = [task(number, [operation(1, "PRS")]) for number in (1, 2, 3)]
    keys = {current.operations[0].id: crimp_key(terminal=f"T-{index}") for index, current in enumerate(tasks)}

    result = plan(data(tasks, keys, capacity={"ОБ.027": 2}))

    windows = [(item.start, item.finish) for item in result.placements]
    assert len(windows) == 3 and len({item.operator_id for item in result.placements}) >= 2
    for moment in sorted({value for window in windows for value in window}):
        assert sum(1 for start, finish in windows if start <= moment < finish) <= 2
    # Один пресс (нет в БД.ОБ — считается один): опрессовки идут по очереди.
    single = plan(data(tasks, keys))
    ordered = sorted((item.start, item.finish) for item in single.placements)
    assert all(earlier[1] <= later[0] for earlier, later in zip(ordered, ordered[1:]))


def test_operations_without_a_machine_are_not_limited():
    tasks = [task(number, [operation(1, "ASM")]) for number in (1, 2, 3)]
    keys = {current.operations[0].id: setup_key(code="ASM", stage="Сборка", built={"connector_article": f"X{number}"}, config_op={}, config={})
            for number, current in enumerate(tasks)}

    result = plan(data(tasks, keys))

    assert {item.start for item in result.placements} == {MONDAY}


def test_absent_fitter_is_skipped():
    current = task(1, [operation(1, "PRS")])
    absent = {ANNA: (BusyInterval("Отпуск", datetime(2026, 10, 5), datetime(2026, 10, 8)),)}

    result = plan(data([current], {current.operations[0].id: crimp_key()}, team=(ANNA, BORIS), busy=absent))

    assert result.placements[0].operator_id == BORIS and result.placements[0].start == MONDAY


def test_competences_limit_who_gets_the_operation():
    current = task(1, [operation(1, "PRS")])
    skills = {"PRS": {VERA: OperatorSkill(VERA, "PRS", True, 5, Decimal(1)), ANNA: OperatorSkill(ANNA, "PRS", False, 0, Decimal(1))}}

    result = plan(data([current], {current.operations[0].id: crimp_key()}, skills=skills))
    nobody = plan(data([current], {current.operations[0].id: crimp_key()}, skills=skills, team=(ANNA,)))

    assert result.placements[0].operator_id == VERA
    assert nobody.unplaced[0].reason.startswith("Нет монтажников ЦАР с допуском к операции PRS")


def test_route_precedence_and_blocked_successors():
    current = task(1, [operation(1, "CUT", unit="72"), operation(2, "PRS")])
    keys = {current.operations[0].id: setup_key(code="CUT", stage="Резка", built={}, config_op={}, config={}),
            current.operations[1].id: crimp_key()}

    result = by_operation(plan(data([current], keys)))
    first, second = result[current.operations[0].id], result[current.operations[1].id]

    assert second.start >= first.finish
    skills = {"CUT": {VERA: OperatorSkill(VERA, "CUT", True, 5, Decimal(1))}}
    blocked = plan(data([current], keys, skills=skills, team=(ANNA,)))
    assert [item.reason.split(" —")[0] for item in blocked.unplaced] == [
        "Нет монтажников ЦАР с допуском к операции CUT", "Ждёт этап 1 «CUT»",
    ]


def test_earlier_deadline_first_and_batching_never_pushes_it_past_the_deadline():
    # ПЗ-0001 (срок — сегодня): опрессовка 1 ч, потом сборка 3 ч у той же Анны. ПЗ-0002 (срок через неделю): та же
    # наладка на 5 ч. Подтянуть ПЗ-0002 сразу после опрессовки ПЗ-0001 — сборка ПЗ-0001 уедет на завтра: нельзя.
    urgent = task(1, [operation(1, "PRS", unit="30", setup="600"), operation(2, "ASM", unit="96", setup="600")], deadline=date(2026, 10, 5))
    later = task(2, [operation(1, "PRS", unit="174", setup="600")], deadline=date(2026, 10, 12))
    keys = {urgent.operations[0].id: crimp_key(), later.operations[0].id: crimp_key(),
            urgent.operations[1].id: setup_key(code="ASM", stage="Сборка", built={}, config_op={}, config={})}

    result = plan(data([later, urgent], keys, team=(ANNA,)))
    placed = by_operation(result)

    assert placed[urgent.operations[0].id].start == MONDAY
    assert placed[urgent.operations[1].id].finish.date() == date(2026, 10, 5)
    assert placed[later.operations[0].id].start >= placed[urgent.operations[1].id].finish
    assert not placed[later.operations[0].id].pulled
    # Наладка пресса Анны держится через сборку: ПЗ-0002 всё равно идёт без переналадки.
    assert placed[later.operations[0].id].changeover_seconds == 0
    assert not any("не успевает" in item for item in result.warnings)


def test_batching_pulls_a_later_task_when_the_deadline_allows():
    urgent = task(1, [operation(1, "PRS", unit="30", setup="600"), operation(2, "ASM", unit="96", setup="600")], deadline=date(2026, 10, 7))
    later = task(2, [operation(1, "PRS", unit="174", setup="600")], deadline=date(2026, 10, 12))
    keys = {urgent.operations[0].id: crimp_key(), later.operations[0].id: crimp_key(),
            urgent.operations[1].id: setup_key(code="ASM", stage="Сборка", built={}, config_op={}, config={})}

    placed = by_operation(plan(data([later, urgent], keys, team=(ANNA,))))

    assert placed[later.operations[0].id].pulled
    assert placed[later.operations[0].id].start == placed[urgent.operations[0].id].finish
    assert placed[urgent.operations[1].id].start >= placed[later.operations[0].id].finish


def test_existing_assignments_are_locked_and_occupy_the_fitter():
    first = operation(1, "PRS")
    current = task(1, [first, operation(2, "ASM")])
    assigned = current.assign(first.id, (ANNA,), PlanResult(MONDAY, MONDAY + timedelta(hours=3), ()), "Мастер")
    other = task(2, [operation(1, "ASM")])
    keys = {item.id: setup_key(code=item.code, stage=item.code, built={}, config_op={}, config={})
            for item in (*assigned.operations, *other.operations)}

    # Занятость монтажников по назначенной работе собирает слой приложения (application._busy).
    result = plan(data([assigned, other], keys, team=(ANNA,), busy=_busy([assigned, other], UUID(int=0), UUID(int=0), MONDAY)))
    placed = by_operation(result)

    assert assigned.operations[0].id not in placed
    assert placed[assigned.operations[1].id].start >= MONDAY + timedelta(hours=3)
    assert all(not (item.start < MONDAY + timedelta(hours=3)) for item in result.placements)


def test_the_same_input_gives_the_same_plan():
    tasks = [task(number, [operation(1, "PRS"), operation(2, "ASM")]) for number in range(1, 6)]
    keys = {}
    for index, current in enumerate(tasks):
        keys[current.operations[0].id] = crimp_key(terminal=f"T-{index % 2}", height=f"{index}")
        keys[current.operations[1].id] = setup_key(code="ASM", stage="Сборка", built={}, config_op={}, config={})

    first = plan(data(tasks, keys, capacity={"ОБ.027": 2}))
    second = plan(data(list(reversed(tasks)), keys, capacity={"ОБ.027": 2}))

    assert first == second


def test_a_period_too_short_leaves_operations_unplaced():
    current = task(1, [operation(1, "PRS")])

    result = plan(data([current], {current.operations[0].id: crimp_key()}, start=datetime(2026, 10, 10, 9, 0)))

    assert result.unplaced[0].reason == "Не помещается в период до 09.10.2026"


def test_a_realistic_week_is_planned_quickly():
    import time as clock

    tasks = []
    keys = {}
    for number in range(1, 31):
        current = task(number, [operation(1, "CUT", unit="10"), operation(2, "PRS", unit="12"), operation(3, "ASM", unit="20")],
                       deadline=date(2026, 10, 5) + timedelta(days=number % 7), quantity=40)
        tasks.append(current)
        keys[current.operations[0].id] = setup_key(code="CUT", stage="Резка", built={"fields": {"wires": [{"art": f"W{number % 3}"}]}},
                                                   config_op={"type": "cutWire"}, config={})
        keys[current.operations[1].id] = crimp_key(terminal=f"T-{number % 4}", height=f"{number % 2}")
        keys[current.operations[2].id] = setup_key(code="ASM", stage="Сборка", built={}, config_op={}, config={})
    team = tuple(UUID(int=index + 1) for index in range(9))

    began = clock.perf_counter()
    result = plan(data(tasks, keys, team=team, capacity={"ОБ.027": 2}))
    elapsed = clock.perf_counter() - began

    assert len(result.placements) + len(result.unplaced) == 90
    assert result.changeovers < result.changeovers_naive
    assert elapsed < 20, elapsed


# --- станки по операциям ЭТК (загрузка станков, 16.10) ---------------------------------------------------------

# ПФ из двух операций ЭТК на двух станках: резка 600 + 3 × 100 с на ОБ.010, опрессовка 1200 + 6 × 100 с на ОБ.027.
PF_CARD = etk(
    {"code": "CUT", "stage": "Резка", "prep": "600", "op": "5", "machine_time": "3", "machine": "ОБ.010.1", "name": "Автомат резки"},
    {"code": "PRS", "stage": "Опрессовка", "prep": "1200", "op": "8", "machine_time": "6", "machine": "ОБ.027.1", "name": "Пресс"},
)


def semifinished(position: int = 1) -> ProductionOperation:
    return ProductionOperation.create(
        position=position, code="CUT", stage="ПФ 1", description="", seconds_per_unit=Decimal(20),
        setup_seconds=Decimal(1800), quantity=100,
        etk_ops=(EtkOperation(1, "CUT", "Резка"), EtkOperation(2, "PRS", "Опрессовка")),
    )


def with_steps(tasks, document, **options) -> AutoplanInput:
    """Вход автоплана со станками по шагам ЭТК; наладка у каждого задания своя (не налаживается вместе)."""
    keys = {item.id: SetupKey(current.number, (current.number,), (current.number,), current.number)
            for current in tasks for item in current.operations}
    steps = {}
    for current in tasks:
        own, warnings = task_steps(current, document)
        assert warnings == ()
        steps.update(own)
    return replace(data(tasks, keys, **options), steps=steps)


def machine_days(tasks, windows, document):
    """Загрузка станков (тот же расчёт, что сетка мастера) по окнам плана: (станок, день) → пик и перегруз."""
    placed_tasks = [
        replace(current, operations=tuple(
            item.with_shares((OperationShare(windows[item.id][0], None, windows[item.id][1], windows[item.id][2]),), "Мастер")
            for item in current.operations
        )) for current in tasks
    ]
    occupancies = [
        occupancy for current in placed_tasks
        for occupancy in task_occupancies(current, task_steps(current, document)[0], frozenset(), DEFAULT_SHIFT)
    ]
    days = sorted({occupancy.start.date() for occupancy in occupancies})
    return {
        (row.machine, day.day): (day.peak, row.over(day))
        for row in machine_load(occupancies, {}, days, frozenset(), DEFAULT_SHIFT, None) for day in row.days
    }


def test_two_semifinished_on_one_machine_take_turns_per_etk_step_not_for_the_whole_window():
    tasks = [task(number, [semifinished()]) for number in (1, 2)]

    result = plan(with_steps(tasks, PF_CARD))

    first, second = (by_operation(result)[current.operations[0].id] for current in tasks)
    # Монтажников хватает, но резка и пресс в одном экземпляре: второй ПФ ждёт, пока первый освободит станки, —
    # не весь первый ПФ (ручная работа идёт параллельно на другом монтажнике).
    assert first.start == MONDAY and first.operator_id != second.operator_id
    assert MONDAY < second.start < first.finish
    load = machine_days(tasks, {item.operation_id: (item.operator_id, item.start, item.finish) for item in result.placements}, PF_CARD)
    assert set(load) == {("ОБ.010", MONDAY.date()), ("ОБ.027", MONDAY.date())}
    assert all(peak == 1 and not over for peak, over in load.values())
    # Без шагов ЭТК (старый вход) станки ключа не учтены — оба ПФ сразу, станки перегружены.
    keys = {current.operations[0].id: SetupKey(current.number, (current.number,), (current.number,), current.number) for current in tasks}
    blind = plan(data(tasks, keys))
    assert {item.start for item in blind.placements} == {MONDAY}
    blind_load = machine_days(tasks, {item.operation_id: (item.operator_id, item.start, item.finish) for item in blind.placements}, PF_CARD)
    assert all(over for _, over in blind_load.values())
    # Двух единиц каждого станка хватает на два ПФ сразу.
    both = plan(with_steps(tasks, PF_CARD, capacity={"ОБ.010": 2, "ОБ.027": 2}))
    assert {item.start for item in both.placements} == {MONDAY}


def test_machine_without_any_time_and_steps_without_a_machine_do_not_hold_anything():
    card = etk(
        {"code": "CUT", "stage": "Резка", "prep": "600", "op": "5"},  # только инструмент — станка нет
        {"code": "PRS", "stage": "Опрессовка", "machine": "ОБ.050.1", "name": "Ручной пресс"},  # станок без времён
    )
    tasks = [task(number, [semifinished()]) for number in (1, 2)]

    result = plan(with_steps(tasks, card))

    assert {item.start for item in result.placements} == {MONDAY}
