"""«№ изделия» в надписях меток по КД: номера изделий задания — сквозь задания одной позиции проекта."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from random import Random
from uuid import uuid4

from app.main import app
from app.modules.production.domain import MarkEvent, ProductionStatus, has_numbered_marks, link_inputs, mark_serial_range
from tests.production.test_api import production_api  # noqa: F401 — фикстура API цеха
from tests.production.test_my_readiness import _as_operator, _etk_url, _launch
from tests.production.test_network_execution import _op


def _chain():
    return link_inputs((_op(1, "Нарезка"), _op(2, "Маркировка")), {2: ((1, Decimal(1)),)})


def test_first_task_numbers_its_items_from_one(production_api):  # noqa: F811
    _client, operator_id = production_api
    task = _launch(app.state.production_uow_factory, operator_id, "serial-1", _chain())
    assert mark_serial_range(task, [task]) == (1, 5)
    assert mark_serial_range(task, []) == (1, 5)


def test_the_next_task_of_the_same_item_continues_and_other_items_or_later_tasks_do_not_count(production_api):  # noqa: F811
    _client, operator_id = production_api
    factory = app.state.production_uow_factory
    first = _launch(factory, operator_id, "serial-a", _chain())
    other_item = _launch(factory, operator_id, "serial-b", _chain(), item="591K.3.КБ2")
    second = _launch(factory, operator_id, "serial-c", _chain())
    later = _launch(factory, operator_id, "serial-d", _chain())
    order = [first, other_item, second, later]

    assert mark_serial_range(first, order) == (1, 5)
    assert mark_serial_range(other_item, order) == (1, 5)
    assert mark_serial_range(second, order) == (6, 10)
    assert mark_serial_range(later, order) == (11, 15)
    # Отменённое задание номера не отдаёт: напечатанные надписи следующих не сдвигаются.
    cancelled = [replace(first, status=ProductionStatus.CANCELLED), other_item, second, later]
    assert mark_serial_range(second, cancelled) == (6, 10)
    # Позиция сравнивается без регистра и пробелов по краям.
    assert mark_serial_range(replace(later, item=" 591k.3.кб1 "), order) == (11, 15)


def test_operation_etk_returns_the_task_serial_range_and_the_card_is_untouched(production_api):  # noqa: F811
    from tests.production.test_api import issue_card

    client, operator_id = production_api
    factory = app.state.production_uow_factory
    first = _launch(factory, operator_id, "serial-etk-1", _chain())
    second = _launch(factory, operator_id, "serial-etk-2", _chain())
    issue_card("591K.3", "КБ1", [("OP-01", "Нарезка", "10", "0"), ("OP-02", "Маркировка", "10", "0")])
    _as_operator(operator_id)

    one = client.get(_etk_url(first))
    two = client.get(_etk_url(second))

    assert one.status_code == 200, one.text
    assert one.json()["serial"] == {"first": 1, "last": 5, "task": first.number}
    assert two.json()["serial"] == {"first": 6, "last": 10, "task": second.number}
    assert one.json()["card"] == two.json()["card"]


# --- Освобождение номеров отменённого без работы (владелец 22.10: «освобождать») ----------------------------------


def _runs(production_api, *quantities, at=None):  # noqa: F811
    """Задания позиции 591K.3.КБ1 подряд: номера ПЗ и время запуска растут (``at`` — одно время на все)."""
    _client, operator_id = production_api
    base = _launch(app.state.production_uow_factory, operator_id, "serial-base", _chain())
    return [
        replace(base, id=uuid4(), number=f"ПЗ-{index + 1:04d}", quantity=quantity,
                created_at=at or base.created_at + timedelta(minutes=index))
        for index, quantity in enumerate(quantities)
    ]


def _released(task, after, at=None):
    return MarkEvent(task.id, at or after.created_at + timedelta(seconds=30), True, after.number)


def _restored(task, after):
    return MarkEvent(task.id, after.created_at + timedelta(seconds=40), False, after.number)


def test_a_released_run_is_numbered_again_from_one(production_api):  # noqa: F811
    first, again = _runs(production_api, 10, 10)
    events = [_released(first, first)]

    assert mark_serial_range(first, [first, again], events) is None
    assert mark_serial_range(again, [first, again], events) == (1, 10)
    # Без освобождения (отменено после работы) — номера за ним, следующее продолжает.
    assert mark_serial_range(again, [replace(first, status=ProductionStatus.CANCELLED), again]) == (11, 20)


def test_same_clock_launches_and_releases_keep_their_order_by_the_latest_launch(production_api):  # noqa: F811
    first, second, third = _runs(production_api, 10, 10, 10, at=datetime(2026, 10, 22, 9, 0))
    # Первое освобождено, когда уже было второе; третье запущено после — время у всех одно.
    events = [_released(first, second, at=first.created_at)]
    tasks = [first, second, third]

    assert mark_serial_range(second, tasks, events) == (11, 20)  # выданное раньше не сдвигается
    assert mark_serial_range(third, tasks, events) == (1, 10)


def test_a_live_range_never_moves_a_bigger_run_skips_the_gap_and_a_restore_takes_the_next_free(production_api):  # noqa: F811
    first, second, third, fourth = _runs(production_api, 10, 10, 15, 10)
    tasks = [first, second, third, fourth]
    events = [_released(first, second)]

    assert mark_serial_range(second, tasks, events) == (11, 20)
    assert mark_serial_range(third, tasks, events) == (21, 35)  # 15 в свободные 1–10 не влезают
    assert mark_serial_range(fourth, tasks, events) == (1, 10)
    # Отмену первого сняли после четвёртого: его 1–10 заняты — первый свободный диапазон.
    restored = [*events, _restored(first, fourth)]
    assert mark_serial_range(first, tasks, restored) == (36, 45)
    assert mark_serial_range(fourth, tasks, restored) == (1, 10)


def test_an_event_without_a_known_launch_is_placed_by_its_time(production_api):  # noqa: F811
    first, second = _runs(production_api, 10, 10)
    events = [MarkEvent(first.id, first.created_at + timedelta(seconds=30), True)]

    assert mark_serial_range(second, [first, second], events) == (1, 10)


def test_random_launches_and_releases_never_overlap_or_move_a_held_range(production_api):  # noqa: F811
    [base] = _runs(production_api, 1)
    for seed in range(40):
        rng = Random(seed)
        tasks: list = []
        events: list = []
        seen: dict = {}
        for step in range(25):
            live = [task for task in tasks if mark_serial_range(task, tasks, events) is not None]
            if live and rng.random() < 0.35:
                gone = rng.choice(live)
                latest = tasks[-1]
                at = latest.created_at + timedelta(seconds=rng.choice((0, 1)))
                events.append(MarkEvent(gone.id, at, True, latest.number))
            else:
                last = tasks[-1].created_at if tasks else base.created_at
                at = last + timedelta(minutes=rng.choice((0, 1, 1)))
                tasks.append(replace(base, id=uuid4(), number=f"ПЗ-{len(tasks) + 1:04d}",
                                     quantity=rng.randint(1, 12), created_at=at))
            ranges = {task.id: mark_serial_range(task, tasks, events) for task in tasks}
            held = sorted(value for value in ranges.values() if value is not None)
            assert all(left[1] < right[0] for left, right in zip(held, held[1:])), (seed, step, held)
            for task_id, value in ranges.items():
                if value is not None:
                    assert seen.setdefault(task_id, value) == value, (seed, step)


def test_numbered_marks_are_found_in_the_etk_document():
    assert has_numbered_marks({"operations": [{"fields": {"marks": [{"text": "КБ1-{№:3}"}]}}]})
    assert has_numbered_marks({"operations": [{}, {"fields": {"marks": [{"text": "X1"}, {"text": "{№}"}]}}]})
    assert not has_numbered_marks({"operations": [{"fields": {"marks": [{"text": "КБ1-{N}"}, {"text": "№ 1"}]}}]})
    assert not has_numbered_marks(None) and not has_numbered_marks({"operations": None})
