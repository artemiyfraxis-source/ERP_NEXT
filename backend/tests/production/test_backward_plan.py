"""План от даты отгрузки и первая поставка (чистые правила): как можно позже к сроку, где не успеваем, фронт Парето
первой партии."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.modules.production.autoplan import fallback_key
from app.modules.production.backward_plan import backward_plan, due_moment
from app.modules.production.domain import DEFAULT_SHIFT, BusyInterval, OperationShare, OperatorSkill, clock_between
from app.modules.production.first_delivery import DeliveryVariant, Lot, first_delivery_variants, pareto
from tests.production.test_autoplan import (
    ANNA,
    BORIS,
    MONDAY,
    PF_CARD,
    VERA,
    data,
    machine_days,
    operation,
    semifinished,
    task,
    with_steps,
)

FRIDAY = date(2026, 10, 9)


def keys_of(*tasks):
    return {item.id: fallback_key(item.code, item.stage) for current in tasks for item in current.operations}


def by_position(result, current):
    return {current.operation(slot.operation_id).position: slot for slot in result.slots if slot.task_id == current.id}


# --- от даты отгрузки -------------------------------------------------------------------------------------------


def test_due_is_the_end_of_the_shift_on_the_ship_day_and_skips_the_weekend():
    assert due_moment(FRIDAY, None, frozenset(), DEFAULT_SHIFT) == datetime(2026, 10, 9, 17, 30)
    # Отгрузка в субботу — готово к концу пятницы; праздник в пятницу — к концу четверга.
    assert due_moment(date(2026, 10, 10), None, frozenset(), DEFAULT_SHIFT) == datetime(2026, 10, 9, 17, 30)
    assert due_moment(FRIDAY, None, frozenset({FRIDAY}), DEFAULT_SHIFT) == datetime(2026, 10, 8, 17, 30)


def test_fits_operations_end_as_late_as_possible_chain_back_from_the_ship_date():
    current = task(1, [operation(1, "PRS"), operation(2, "ASM")])

    result = backward_plan(data([current], keys_of(current)), FRIDAY)

    slots = by_position(result, current)
    assert result.fits and not result.unplaced
    # Каждая операция — 1,5 ч нормы = 103 мин смены; последняя кончается к концу смены в день отгрузки.
    assert slots[2].finish == datetime(2026, 10, 9, 17, 30) and slots[2].start == datetime(2026, 10, 9, 15, 47)
    assert slots[1].finish == slots[2].start and slots[1].start == datetime(2026, 10, 9, 14, 4)
    assert result.start_by == slots[1].start
    assert result.tasks[0].start == slots[1].start and result.tasks[0].finish == slots[2].finish
    assert result.slack_seconds > 0


def test_does_not_fit_shows_the_late_operations_and_the_shortage():
    current = task(1, [operation(1, "PRS"), operation(2, "ASM")], quantity=1000)

    result = backward_plan(data([current], keys_of(current), team=(ANNA,)), date(2026, 10, 5))

    slots = by_position(result, current)
    assert not result.fits
    # 10,5 ч нормы на операцию = 12 ч смены: обе сразу не влезают в один день.
    # Вход кончается к началу потребителя (13:00 и 13:30 — один момент смены: между ними обед).
    assert slots[1].late(MONDAY) and clock_between(slots[1].finish, slots[2].start, frozenset(), DEFAULT_SHIFT) == 0
    assert result.start_by < MONDAY and result.slack_seconds < 0
    assert result.tasks[0].slack_seconds == result.slack_seconds
    # Опоздание — рабочее время смены, которого не хватает: две операции по 12 ч против одного дня 8 ч.
    assert -result.slack_seconds == 2 * 12 * 3600 - 8 * 3600


def test_busy_time_and_absences_push_the_window_earlier():
    current = task(1, [operation(1, "PRS")])
    busy = {ANNA: (BusyInterval("Отпуск", datetime(2026, 10, 9, 15, 0), datetime(2026, 10, 9, 18, 0)),)}

    result = backward_plan(data([current], keys_of(current), team=(ANNA,), busy=busy), FRIDAY)

    assert result.slots[0].finish == datetime(2026, 10, 9, 15, 0)


def test_the_latest_fitter_wins_and_admission_is_respected():
    current = task(1, [operation(1, "PRS")])
    busy = {ANNA: (BusyInterval("Занят", datetime(2026, 10, 9, 9, 0), datetime(2026, 10, 9, 18, 0)),)}
    skills = {"PRS": {ANNA: OperatorSkill(ANNA, "PRS", True, 3, Decimal(1))}}

    free_boris = backward_plan(data([current], keys_of(current), team=(ANNA, BORIS), busy=busy), FRIDAY)
    only_anna = backward_plan(data([current], keys_of(current), team=(ANNA, BORIS), busy=busy, skills=skills), FRIDAY)
    nobody = backward_plan(data([current], keys_of(current), team=(BORIS,), skills=skills), FRIDAY)

    assert free_boris.slots[0].operator_id == BORIS and free_boris.slots[0].finish == datetime(2026, 10, 9, 17, 30)
    assert only_anna.slots[0].operator_id == ANNA and only_anna.slots[0].finish == datetime(2026, 10, 8, 17, 30)
    assert not nobody.fits and "Нет монтажников с допуском к операции PRS" in nobody.unplaced[0].reason


def test_a_manually_assigned_consumer_holds_its_start_and_bounds_the_input():
    first, second = operation(1, "PRS"), operation(2, "ASM")
    current = task(1, [first, second])
    second = replace(current.operations[1], operator_ids=(BORIS,), planned_start=datetime(2026, 10, 8, 9, 0),
                     planned_finish=datetime(2026, 10, 8, 11, 0), assigned_by="Мастер")
    current = replace(current, operations=(current.operations[0], second))

    result = backward_plan(data([current], keys_of(current), free={current.operations[0].id}), FRIDAY)

    assert [slot.operation_id for slot in result.slots] == [current.operations[0].id]
    assert result.slots[0].finish == datetime(2026, 10, 7, 17, 30)


def test_backward_plan_writes_nothing_into_the_tasks():
    current = task(1, [operation(1, "PRS")])
    before = repr(current)

    backward_plan(data([current], keys_of(current)), FRIDAY)

    assert repr(current) == before and current.operations[0].planned_start is None


# --- первая поставка ----------------------------------------------------------------------------------------------


def test_first_delivery_offers_a_whole_quick_kb_first_then_growing_sets():
    small = task(1, [operation(1, "CUT")], quantity=10)
    big = task(2, [operation(1, "PRS")], quantity=100)

    variants = first_delivery_variants(data([small, big], keys_of(small, big), team=(ANNA,)))

    assert [item.kind for item in variants] == ["items", "share", "share", "share", "full"]
    first = variants[0]
    # Быстрее всего — целая маленькая КБ: одна наладка и 10 штук; её доля — по работе штук (10 из 110 изделий).
    assert first.lots == (Lot(small.id, 10, 10),)
    assert first.ready == MONDAY + timedelta(minutes=42)
    assert first.share == Decimal(360) / Decimal(3960)
    assert variants[1].lots == (Lot(small.id, 1, 10), Lot(big.id, 10, 100)) and variants[1].share == Decimal("0.1")
    assert variants[-1].share == 1
    # Фронт Парето: каждый следующий вариант позже и везёт больше.
    assert all(a.ready < b.ready and a.share < b.share for a, b in zip(variants, variants[1:]))


def test_first_delivery_with_two_fitters_sets_beat_a_whole_kb():
    small = task(1, [operation(1, "CUT")], quantity=10)
    big = task(2, [operation(1, "PRS")], quantity=100)

    variants = first_delivery_variants(data([small, big], keys_of(small, big), team=(ANNA, BORIS)))

    # Двое делают 10 % обеих КБ параллельно не позже, чем целую маленькую — целая КБ доминируема и уходит.
    assert variants[0].kind == "share" and variants[0].share == Decimal("0.1")
    assert "items" not in {item.kind for item in variants}


def test_pareto_drops_dominated_variants():
    def variant(hours: int, share: str) -> DeliveryVariant:
        return DeliveryVariant("share", (), MONDAY + timedelta(hours=hours), Decimal(share))

    kept = pareto([variant(5, "0.5"), variant(3, "0.2"), variant(4, "0.1"), variant(3, "0.3"), variant(8, "1")])

    assert [(item.ready - MONDAY, item.share) for item in kept] == [
        (timedelta(hours=3), Decimal("0.3")), (timedelta(hours=5), Decimal("0.5")), (timedelta(hours=8), Decimal(1)),
    ]


def test_first_delivery_of_nothing_is_empty():
    current = task(1, [operation(1, "PRS")])

    assert first_delivery_variants(data([current], keys_of(current), free=())) == ()


# --- станки по операциям ЭТК ------------------------------------------------------------------------------------


def test_two_semifinished_on_one_machine_the_other_moves_earlier_until_the_machines_are_free():
    tasks = [task(number, [semifinished()]) for number in (1, 2)]

    result = backward_plan(with_steps(tasks, PF_CARD, team=(ANNA, BORIS)), FRIDAY)

    later, earlier = by_position(result, tasks[1])[1], by_position(result, tasks[0])[1]
    assert result.fits and not result.warnings
    assert later.finish == datetime(2026, 10, 9, 17, 30) and earlier.operator_id != later.operator_id
    # Уходит раньше ровно настолько, чтобы освободить станки, а не на весь соседний ПФ.
    assert later.start < earlier.finish < later.finish
    load = machine_days(tasks, {slot.operation_id: (slot.operator_id, slot.start, slot.finish) for slot in result.slots}, PF_CARD)
    assert load and all(peak == 1 and not over for peak, over in load.values())
    # Без шагов ЭТК станки не ограничивают: оба кончаются к концу смены.
    blind = backward_plan(data(tasks, keys_of(*tasks), team=(ANNA, BORIS)), FRIDAY)
    assert {slot.finish for slot in blind.slots} == {datetime(2026, 10, 9, 17, 30)}


def test_machine_busy_the_whole_ship_day_pushes_the_semifinished_before_now_and_warns():
    busy_day = task(9, [semifinished()], quantity=4000)
    busy_day = replace(busy_day, operations=(busy_day.operations[0].with_shares(
        (OperationShare(VERA, None, MONDAY, datetime(2026, 10, 5, 17, 30)),), "Мастер",
    ),))
    current = task(1, [semifinished()])

    result = backward_plan(with_steps([busy_day, current], PF_CARD, team=(ANNA,)), date(2026, 10, 5))

    slot = by_position(result, current)[1]
    assert not result.fits and slot.late(MONDAY)
    assert result.warnings == (
        "Станок ОБ.027 Пресс занят 05.10: ПЗ-0001 · этап 1 «ПФ 1» уходит раньше «сейчас» — к отгрузке не успевает",
    )
