"""План задания (flow.py): три режима передачи партии, люди на этапе, «первые k раньше» с порогами 5 / 15 %."""

from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.production.domain import FlowMode, ProductionValidationError
from app.modules.production.flow import (
    EARLY_LIMIT,
    EARLY_NOTICE,
    LEVEL_LIMIT,
    LEVEL_NOTICE,
    LEVEL_OK,
    MAX_BATCHES,
    FlowStep,
    plan_flow,
)


def _id(number: int) -> UUID:
    return UUID(int=number)


def chain(*times, setup=0, people=None):
    """Цепочка этапов 1→2→…: время на изделие одним человеком, подготовка одна на всех."""
    people = people or [1] * len(times)
    return tuple(
        FlowStep(_id(index), index, (_id(index - 1),) if index > 1 else (), Decimal(setup), Decimal(seconds), Decimal(count))
        for index, (seconds, count) in enumerate(zip(times, people), start=1)
    )


def test_three_modes_match_the_textbook_cycle_of_a_batch():
    # n = 4, p = 1, t = 2, 1, 3: последовательно nΣt = 24; параллельно pΣt + (n − p)·tmax = 6 + 9 = 15;
    # параллельно-последовательно nΣt − (n − p)·Σmin(tᵢ, tᵢ₊₁) = 24 − 3·(1 + 1) = 18.
    steps = chain(2, 1, 3)

    assert plan_flow(steps, 4, FlowMode.SEQUENTIAL, transfer_batch=1).finish == 24
    assert plan_flow(steps, 4, FlowMode.PARALLEL, transfer_batch=1).finish == 15
    assert plan_flow(steps, 4, FlowMode.MIXED, transfer_batch=1).finish == 18


def test_sequential_ignores_the_transfer_batch_and_moves_the_whole_run():
    plan = plan_flow(chain(2, 1, 3), 4, FlowMode.SEQUENTIAL, transfer_batch=1)

    assert plan.transfer_batch == 4
    assert plan.first_quantity == 4 and plan.first_ready == plan.finish == 24
    assert [(item.start, item.finish) for item in plan.steps] == [(0, 8), (8, 12), (12, 24)]


def test_parallel_stage_waits_for_a_slower_input_and_mixed_starts_later_without_gaps():
    # Второй этап быстрее первого: параллельно он берёт каждую партию сразу и ждёт следующую,
    # параллельно-последовательно начинает позже, чтобы дальше работать подряд. Конец у обоих один.
    steps = chain(3, 1)
    parallel = plan_flow(steps, 4, FlowMode.PARALLEL, transfer_batch=1)
    mixed = plan_flow(steps, 4, FlowMode.MIXED, transfer_batch=1)

    assert parallel.steps[1].start == 3 and parallel.steps[1].wait == 6
    assert mixed.steps[1].start == 9 and mixed.steps[1].wait == 0
    assert parallel.finish == mixed.finish == 13
    # Первые изделия параллельно выходят раньше: первая партия не ждёт хвоста.
    assert parallel.first_ready == 4 < mixed.first_ready == 10


def test_n_people_on_a_stage_divide_its_time_and_setup_is_counted_once():
    one = plan_flow(chain(60, setup=300), 10, FlowMode.SEQUENTIAL)
    three = plan_flow(chain(60, setup=300, people=[3]), 10, FlowMode.SEQUENTIAL)

    assert one.finish == 300 + 600
    assert three.finish == 300 + 200


def test_branches_run_side_by_side_and_the_assembly_waits_for_the_later_one():
    # Две ветки (резка 2 с/шт, пайка 5 с/шт) сходятся на сборке (1 с/шт).
    steps = (
        FlowStep(_id(1), 1, (), Decimal(0), Decimal(2)),
        FlowStep(_id(2), 2, (), Decimal(0), Decimal(5)),
        FlowStep(_id(3), 3, (_id(1), _id(2)), Decimal(0), Decimal(1)),
    )
    plan = plan_flow(steps, 10, FlowMode.SEQUENTIAL)

    assert {item.position: (item.start, item.finish) for item in plan.steps} == {1: (0, 20), 2: (0, 50), 3: (50, 60)}
    assert plan_flow(steps, 10, FlowMode.PARALLEL, transfer_batch=1).finish == 51


def test_early_quantity_brings_the_first_pieces_forward():
    # Последовательно 100 шт по трём этапам по 10 с: всё — 3000 с, первые изделия — тоже 3000.
    plan = plan_flow(chain(10, 10, 10), 100, FlowMode.SEQUENTIAL, early_quantity=5)

    assert plan.baseline_finish == 3000
    assert plan.first_quantity == 5 and plan.first_ready == 150
    assert plan.baseline_early_ready == 3000
    # Отдельная партия тех же людей последовательно даже сокращает цикл; 5 % тиража — в пределах нормы.
    assert plan.finish == 2900 and plan.delay < 0
    assert plan.share == Decimal("0.05") and plan.level == LEVEL_OK
    assert not plan.warnings


def test_early_quantity_thresholds_five_and_fifteen_percent_of_the_run():
    assert (EARLY_NOTICE, EARLY_LIMIT) == (Decimal("0.05"), Decimal("0.15"))
    steps = chain(10, 10, 10)
    notice = plan_flow(steps, 100, FlowMode.SEQUENTIAL, early_quantity=10)
    limit = plan_flow(steps, 100, FlowMode.SEQUENTIAL, early_quantity=30)
    edge = plan_flow(steps, 100, FlowMode.SEQUENTIAL, early_quantity=15)

    assert notice.level == LEVEL_NOTICE
    assert "10 % тиража (больше 5 %)" in notice.warnings[0]
    assert limit.level == LEVEL_LIMIT
    assert "30 % тиража (больше 15 %): лучше запустить 30 шт отдельным заданием" in limit.warnings[0]
    assert edge.level == LEVEL_NOTICE  # ровно 15 % — ещё предупреждение


def test_early_lot_that_moves_the_finish_is_reported():
    # Параллельно партиями по 4: отдельные 5 шт ломают партии (4 + 1, затем 4 + 1) — конец позже на 4 с из 78.
    plan = plan_flow(chain(7, 1, 3), 10, FlowMode.PARALLEL, transfer_batch=4, early_quantity=5)

    assert (plan.baseline_finish, plan.finish) == (78, 82)
    assert any("сдвигает конец задания на 5 %" in warning for warning in plan.warnings)

def test_early_quantity_that_gains_nothing_says_so():
    # Партиями по 5 шт первые 5 и так выходят с первой партией финального этапа.
    plan = plan_flow(chain(10, 10, 10), 100, FlowMode.PARALLEL, transfer_batch=5, early_quantity=5)

    assert plan.first_ready >= plan.baseline_early_ready
    assert any("ничего не дают" in warning for warning in plan.warnings)


def test_bad_inputs_are_refused():
    with pytest.raises(ProductionValidationError):
        plan_flow(chain(1, 1), 10, FlowMode.MIXED, early_quantity=10)
    with pytest.raises(ProductionValidationError):
        plan_flow((), 10, FlowMode.MIXED)
    looped = (
        FlowStep(_id(1), 1, (_id(2),), Decimal(0), Decimal(1)),
        FlowStep(_id(2), 2, (_id(1),), Decimal(0), Decimal(1)),
    )
    with pytest.raises(ProductionValidationError, match="цикл"):
        plan_flow(looped, 10, FlowMode.MIXED)


def test_a_too_fine_batch_is_coarsened_with_a_warning():
    plan = plan_flow(chain(1, 1), MAX_BATCHES * 3, FlowMode.PARALLEL, transfer_batch=1)

    assert plan.transfer_batch == 3
    assert "укрупнена до 3 шт" in plan.warnings[0]
