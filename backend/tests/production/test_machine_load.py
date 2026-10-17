"""Загрузка станков (чистые правила): занятости операций ЭТК со станком, единицы станка, перегруз дня и
предупреждение о занятом станке при ручной постановке."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.modules.production.domain import (
    DEFAULT_SHIFT,
    EtkOperation,
    OperationShare,
    ProductionOperation,
    ProductionTask,
    add_work_seconds,
)
from app.modules.production.machine_load import (
    conflict_text,
    conflicts,
    lay_out,
    machine_load,
    operation_occupancies,
    task_occupancies,
    task_steps,
)

ANNA = UUID("40000000-0000-0000-0000-00000000000a")
BORIS = UUID("40000000-0000-0000-0000-00000000000b")
MONDAY = datetime(2026, 10, 5, 9, 0)
NO_HOLIDAYS: frozenset[date] = frozenset()


def etk(*ops: dict) -> dict:
    """Выданная ЭТК: операция — код, этап, времена карты и, если есть, станок в «Оборудовании»."""
    return {
        "operations": [{
            "code": op["code"], "type": "",
            "card": {"tooling": [
                {"section": "Инструмент", "inventory_number": "ИН.001", "name": "Стриппер"},
                *([{"section": "Оборудование", "inventory_number": op["machine"], "name": op.get("name", "Станок")}] if op.get("machine") else []),
            ]},
        } for op in ops],
        "build_config": {"ops": [{
            "type": "", "stage": op["stage"], "opNumber": op["code"],
            "tPrep": op.get("prep", ""), "tOp": op.get("op", ""), "tMachine": op.get("machine_time", ""),
        } for op in ops]},
    }


def make_task(number: int, operations, quantity: int = 100) -> ProductionTask:
    return ProductionTask.create(
        number=f"ПЗ-{number:04d}", source_idempotency_key=f"key-{number}", source_fingerprint="fp",
        project="708К", item=f"КБ{number}", name="Жгут", quantity=quantity, fulfillment_request_id=None,
        deadline=None, launched_by="Мастер", operations=tuple(operations), created_at=MONDAY - timedelta(days=1),
    )


def single(position: int, code: str, stage: str) -> ProductionOperation:
    return ProductionOperation.create(
        position=position, code=code, stage=stage, description="", seconds_per_unit=Decimal(10),
        setup_seconds=Decimal(600), quantity=100,
    )


def placed(task: ProductionTask, *shares: OperationShare, position: int = 1) -> ProductionTask:
    return replace(task, operations=tuple(
        operation.with_shares(tuple(shares), "Мастер") if operation.position == position else operation
        for operation in task.operations
    ))


def at(hour: int, minute: int = 0, day: int = 5) -> datetime:
    return datetime(2026, 10, day, hour, minute)


def work(start: datetime, seconds: int) -> datetime:
    return add_work_seconds(start, seconds, NO_HOLIDAYS, DEFAULT_SHIFT)


CRIMP = {"code": "PRS", "stage": "Опрессовка терминалов", "prep": "1200", "op": "8", "machine_time": "6", "machine": "ОБ.027.1", "name": "Пресс"}


def crimp_task(number: int, *shares: OperationShare, quantity: int = 100):
    task = placed(make_task(number, [single(1, "PRS", "Опрессовка терминалов")], quantity), *shares)
    steps, warnings = task_steps(task, etk(CRIMP))
    assert warnings == ()
    return task, steps


# --- занятости операций ЭТК ------------------------------------------------------------------------------------


def test_semifinished_with_two_machines_occupies_each_in_etk_order_inside_the_share_window():
    pf = ProductionOperation.create(
        position=1, code="CUT", stage="ПФ 1", description="", seconds_per_unit=Decimal(20), setup_seconds=Decimal(1800),
        quantity=100, etk_ops=(EtkOperation(1, "CUT", "Резка"), EtkOperation(2, "HAND", "Скрутка"), EtkOperation(3, "PRS", "Опрессовка")),
    )
    task = placed(make_task(1, [pf]), OperationShare(ANNA, None, at(9), at(17, 30)))
    document = etk(
        {"code": "CUT", "stage": "Резка", "prep": "600", "op": "5", "machine_time": "3", "machine": "ОБ.010.1", "name": "Автомат резки"},
        {"code": "HAND", "stage": "Скрутка", "prep": "60", "op": "4"},
        {"code": "PRS", "stage": "Опрессовка", "prep": "1200", "op": "8", "machine_time": "6", "machine": "ОБ.027.2", "name": "Пресс"},
    )

    steps, warnings = task_steps(task, document)
    items = task_occupancies(task, steps, NO_HOLIDAYS, DEFAULT_SHIFT)

    assert warnings == ()
    assert [(item.machine, item.machine_label, item.etk_position, item.etk_stage) for item in items] == [
        ("ОБ.010", "ОБ.010 Автомат резки", 1, "Резка"), ("ОБ.027", "ОБ.027 Пресс", 3, "Опрессовка"),
    ]
    # Подготовка карты + машинное время × 100 шт, без экономии наладки; вторая — сразу за первой.
    assert items[0].start == at(9) and items[0].finish == work(at(9), 600 + 3 * 100)
    assert items[1].start == items[0].finish and items[1].finish == work(items[0].finish, 1200 + 6 * 100)
    assert all(item.operator_ids == (ANNA,) and item.task_number == "ПЗ-0001" for item in items)


def test_operation_without_machine_time_holds_the_machine_for_its_piece_time_and_without_any_time_not_at_all():
    task = placed(make_task(1, [single(1, "PRS", "Опрессовка"), single(2, "ASM", "Сборка")]), OperationShare(ANNA, None, at(9), at(17, 30)))
    hand_press = {"code": "PRS", "stage": "Опрессовка", "prep": "300", "op": "12", "machine": "ОБ.048.1"}
    no_time = {"code": "ASM", "stage": "Сборка", "machine": "ОБ.050.1"}

    steps, _ = task_steps(task, etk(hand_press, no_time))
    items = task_occupancies(task, steps, NO_HOLIDAYS, DEFAULT_SHIFT)

    assert [item.machine for item in items] == ["ОБ.048"]
    assert items[0].finish == work(at(9), 300 + 12 * 100)


def test_split_shares_occupy_the_machine_with_their_own_pieces_and_brigade_with_the_whole_task():
    split, steps = crimp_task(1, OperationShare(ANNA, 30, at(9), at(12)), OperationShare(BORIS, 70, at(13, 30), at(17, 30)))
    brigade, _ = crimp_task(2, OperationShare(ANNA, None, at(9, 0, 6), at(17, 30, 6)), OperationShare(BORIS, None, at(9, 0, 6), at(17, 30, 6)))

    items = task_occupancies(split, steps, NO_HOLIDAYS, DEFAULT_SHIFT)
    together = task_occupancies(brigade, task_steps(brigade, etk(CRIMP))[0], NO_HOLIDAYS, DEFAULT_SHIFT)

    assert [(item.operator_ids, item.start, item.finish) for item in items] == [
        ((ANNA,), at(9), work(at(9), 1200 + 6 * 30)), ((BORIS,), at(13, 30), work(at(13, 30), 1200 + 6 * 70)),
    ]
    assert [(item.operator_ids, item.finish) for item in together] == [((ANNA, BORIS), work(at(9, 0, 6), 1200 + 6 * 100))]


def test_steps_longer_than_the_window_are_squeezed_into_it_in_order():
    pf = ProductionOperation.create(
        position=1, code="CUT", stage="ПФ 1", description="", seconds_per_unit=Decimal(1), setup_seconds=Decimal(0),
        quantity=1000, etk_ops=(EtkOperation(1, "CUT", "Резка"), EtkOperation(2, "PRS", "Опрессовка")),
    )
    task = placed(make_task(1, [pf], quantity=1000), OperationShare(ANNA, None, at(9), at(10)))
    steps, _ = task_steps(task, etk(
        {"code": "CUT", "stage": "Резка", "machine_time": "30", "machine": "ОБ.010.1"},
        {"code": "PRS", "stage": "Опрессовка", "machine_time": "30", "machine": "ОБ.027.1"},
    ))

    items = task_occupancies(task, steps, NO_HOLIDAYS, DEFAULT_SHIFT)

    assert [item.machine for item in items] == ["ОБ.010", "ОБ.027"]
    assert items[0].start == at(9) and items[0].finish < items[1].finish <= at(10)
    assert items[1].start == items[0].finish


def test_code_that_left_the_techcard_is_not_counted_and_warns():
    task = placed(make_task(7, [single(1, "OLD", "Опрессовка")]), OperationShare(ANNA, None, at(9), at(12)))

    steps, warnings = task_steps(task, etk(CRIMP))
    missing, why = task_steps(task, None)

    assert steps == {} and warnings == ("ПЗ-0007 · этап 1: код OLD не совпадает с техкартой — станки этапа не учтены",)
    assert missing == {} and "выданная техкарта 708К КБ7 не найдена" in why[0]


def test_nothing_is_laid_out_in_an_empty_window():
    _, steps = crimp_task(1)
    assert lay_out(next(iter(steps.values())), at(18), at(19), Decimal(10), NO_HOLIDAYS, DEFAULT_SHIFT) == []


# --- загрузка по дням и единицы станка --------------------------------------------------------------------------


def test_day_load_is_busy_shift_seconds_over_all_units_and_two_at_once_overload_a_single_machine():
    first, steps = crimp_task(1, OperationShare(ANNA, None, at(9), at(17, 30)))
    second, _ = crimp_task(2, OperationShare(BORIS, None, at(9), at(17, 30)), quantity=4000)
    items = [*task_occupancies(first, steps, NO_HOLIDAYS, DEFAULT_SHIFT),
             *task_occupancies(second, task_steps(second, etk(CRIMP))[0], NO_HOLIDAYS, DEFAULT_SHIFT)]
    days = [date(2026, 10, 5), date(2026, 10, 10)]

    one = machine_load(items, {"ОБ.027": 1}, days, NO_HOLIDAYS, DEFAULT_SHIFT, None)[0]
    two = machine_load(items, {"ОБ.027": 2}, days, NO_HOLIDAYS, DEFAULT_SHIFT, None)[0]

    monday, saturday = one.days
    assert (one.machine, one.label, one.units, one.known) == ("ОБ.027", "ОБ.027 Пресс", 1, True)
    assert monday.capacity_seconds == 7 * 3600 and monday.peak == 2
    # Вторая задача — вся смена (1200 + 6 × 4000 с), с первой (1200 + 600 с) — больше смены одного станка.
    assert monday.busy_seconds > monday.capacity_seconds and one.over(monday) and monday.percent > 100
    assert two.days[0].capacity_seconds == 2 * 7 * 3600 and not two.over(two.days[0]) and two.days[0].percent < 100
    assert saturday.capacity_seconds == 0 and saturday.percent is None and saturday.busy_seconds == 0
    assert len(one.items) == 2
    assert machine_load(items, {}, days, NO_HOLIDAYS, DEFAULT_SHIFT, None)[0].known is False


# --- предупреждение при ручной постановке ----------------------------------------------------------------------


def test_overlap_on_a_single_machine_warns_and_the_next_window_does_not():
    busy, steps = crimp_task(1, OperationShare(ANNA, None, at(9), at(11)))
    existing = task_occupancies(busy, steps, NO_HOLIDAYS, DEFAULT_SHIFT)
    finish = existing[0].finish
    task, own = crimp_task(2)
    operation = task.operations[0]

    overlap = operation_occupancies(task, operation, own[operation.id], [((BORIS,), 10, at(9, 15), at(11))], NO_HOLIDAYS, DEFAULT_SHIFT)
    after = operation_occupancies(task, operation, own[operation.id], [((BORIS,), 10, finish, at(13))], NO_HOLIDAYS, DEFAULT_SHIFT)

    found = conflicts(overlap, existing, {"ОБ.027": 1})
    assert len(found) == 1 and found[0].busy == tuple(existing)
    assert conflict_text(found[0], {ANNA: "Анна"}) == (
        f"ОБ.027 занят 05.10 09:15–{finish:%H:%M}: ПЗ-0001 · КБ1, этап 1 «Опрессовка терминалов» 05.10 09:00–{finish:%H:%M} (Анна)"
    )
    assert conflicts(after, existing, {"ОБ.027": 1}) == ()


def test_machine_with_two_units_warns_only_when_both_are_taken():
    first, steps = crimp_task(1, OperationShare(ANNA, None, at(9), at(17, 30)))
    second, _ = crimp_task(2, OperationShare(BORIS, None, at(9), at(17, 30)))
    one_busy = task_occupancies(first, steps, NO_HOLIDAYS, DEFAULT_SHIFT)
    both_busy = [*one_busy, *task_occupancies(second, task_steps(second, etk(CRIMP))[0], NO_HOLIDAYS, DEFAULT_SHIFT)]
    task, own = crimp_task(3)
    operation = task.operations[0]
    candidate = operation_occupancies(task, operation, own[operation.id], [((), None, at(9), at(17, 30))], NO_HOLIDAYS, DEFAULT_SHIFT)

    assert conflicts(candidate, one_busy, {"ОБ.027": 2}) == ()
    found = conflicts(candidate, both_busy, {"ОБ.027": 2})
    assert len(found) == 1 and found[0].units == 2
    assert conflict_text(found[0], {}).startswith("ОБ.027 занят (заняты все 2 ед.) 05.10 09:00–")
