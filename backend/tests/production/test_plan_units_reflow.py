"""План по штукам операции и сдвиг по стрелкам схемы (G2-T2).

«N на изделие» на стрелке: ветка «2 на изделие» делает вдвое больше тиража — и в плане идёт вдвое дольше, а готова,
когда сдано вдвое больше тиража. Сдвиг по факту идёт по каждой стрелке с запаздываниями (первая партия входа, хвост не
обгоняет конец входа): опоздавший вход двигает и сборку, стоявшую внахлёст. Авторасчёт считает нахлёст по настоящему
коэффициенту монтажника, «▶ Начать» в бригаде ставит от «сейчас» только долю начавшего."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.main import app
from app.modules.production.application import AssignProductionCommand, LaunchProductionCommand, assign_production, launch_production
from app.modules.production.autoplan import operation_keys, plan
from app.modules.production.dispatch import DispatchCalendar, Progress, nodes_of, place, reflow, share_seconds
from app.modules.production.domain import (
    DEFAULT_SHIFT,
    OperationInput,
    OperationShare,
    OperatorSkill,
    ProductionOperation,
    ProductionTask,
    add_work_seconds,
    advance_clock,
    clock_between,
    link_inputs,
    rewind_clock,
)
from app.modules.production.scheduling import schedule_task
from app.shared.clock import moscow_now
from app.shared.http_auth import authenticated_principal
from tests.factories import Actor
from tests.production import test_autoplan as autoplan_fixtures
from tests.production.test_api import launch_from_card, production_api  # noqa: F401 — фикстура
from tests.production.test_dispatch import SECOND, add_second, as_operator, fresh, window
from tests.production.test_dispatch import at as moscow_at
from tests.production.test_network_schedule import _uow_factory

X = UUID("10000000-0000-0000-0000-000000000001")
Y = UUID("10000000-0000-0000-0000-000000000002")
QUANTITY = 10
CALENDAR = DispatchCalendar(frozenset(), DEFAULT_SHIFT, {})


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 21, hour, minute)  # понедельник


def _op(position: int, stage: str, unit: str = "315") -> ProductionOperation:
    # 315 с × 10 шт = 52,5 мин чистых = 1 ч на часах (смена 9:00–17:30, чистых 7 из 8 ч).
    return ProductionOperation.create(
        position=position, code=f"OP-{position}", stage=stage, description="",
        seconds_per_unit=Decimal(unit), setup_seconds=Decimal(0), quantity=QUANTITY,
    )


def _pair(per_unit: str = "1", batch: str | None = None, assembly_unit: str = "315") -> ProductionTask:
    """Нарезка → сборка: per_unit штук нарезки на одну сборку, batch — передаточная партия стрелки."""
    cut, assembly = link_inputs((_op(1, "Нарезка"), _op(2, "Сборка", assembly_unit)), {2: ((1, Decimal(per_unit)),)})
    if batch is not None:
        assembly = replace(assembly, inputs=(OperationInput(cut.id, Decimal(per_unit), Decimal(batch)),))
    return ProductionTask.create(
        number="ПЗ-0001", source_idempotency_key="units", source_fingerprint="fingerprint", project="708К.4",
        item="КБ1", name="Жгут", quantity=QUANTITY, fulfillment_request_id=None, deadline=None,
        launched_by="Мастер", operations=(cut, assembly), created_at=at(8),
    )


def _planned(task: ProductionTask, windows: dict[int, tuple[UUID, datetime, datetime]]) -> ProductionTask:
    return replace(task, operations=tuple(
        item.with_shares((OperationShare(windows[item.position][0], None, *windows[item.position][1:]),), "Мастер")
        if item.position in windows else item
        for item in task.operations
    ))


def _late(task: ProductionTask, cut_window: tuple[datetime, datetime], before: tuple[datetime, datetime]):
    """Нарезка теперь идёт в cut_window (по плану была before): куда сдвинулась остальная работа."""
    cut = task.operations[0]
    moved = replace(task, operations=(
        cut.with_shares((OperationShare(X, None, *cut_window),), "Мастер"), *task.operations[1:],
    ))
    moves = reflow(
        tasks=[moved], sources={(cut.id, None): before}, pull=False, now=at(9),
        calendar=CALENDAR, skills={}, progress=Progress.empty(),
    )
    return {move.operation.position: (move.start, move.finish) for move in moves.values()}


def test_two_per_product_branch_is_planned_at_double_time():
    task = _pair(per_unit="2")
    cut, assembly = task.operations

    assert task.operation_units(cut.id) == 20 and task.operation_units(assembly.id) == 10
    assert task.operation_units(cut.id, 5) == 10
    assert (cut.norm_seconds, assembly.norm_seconds) == (6300, 3150)
    # Доля: время на штуки нарезки для своих изделий — 5 изделий = 10 штук.
    assert share_seconds(task, cut, None, (X,), {}) == 6300
    assert share_seconds(task, cut, 5, (X,), {}) == 3150

    # Авторасчёт: нарезка 20 штук — 2 ч, сборка после неё.
    variant = schedule_task(
        task=task, transfer_batch=QUANTITY, team=(X, Y), skills={}, busy={}, now=at(9),
        holidays=frozenset(), max_crew=1,
    )
    windows = {item.position: (item.start, item.finish) for item in variant.operations}
    assert windows == {1: (at(9), at(11)), 2: (at(11), at(12))}

    # Экран мастера: доля нарезки встаёт на 2 ч.
    assert place(
        tasks=[task], task=task, operation=cut, operator_id=X, quantity=None, requested_start=at(9),
        calendar=CALENDAR, skills={}, progress=Progress.empty(),
    ) == (at(9), at(11))


def test_manual_assignment_plans_the_operation_pieces():
    engine, factory = _uow_factory()
    actor = Actor(name="Мастер", department="Производство")
    operations = link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(2)),)})
    task = launch_production(LaunchProductionCommand(
        idempotency_key="708К.4:КБ1:2-на-1", project="708К.4", item="КБ1", name="Жгут", quantity=QUANTITY,
        fulfillment_request_id=None, deadline=date(2026, 10, 20), operations=operations, actor=actor, now=at(9),
    ), factory())
    _, planned = assign_production(AssignProductionCommand(
        task_id=task.id, expected_version=task.version, operation_id=task.operations[0].id,
        operator_ids=(X,), requested_start=None, actor=actor, now=at(9), dry=True,
    ), factory())
    assert (planned.start, planned.finish) == (at(9), at(11))
    engine.dispose()


def test_autoplan_plans_the_operation_pieces():
    fixtures = autoplan_fixtures
    single = fixtures.task(1, (fixtures.operation(1, "CUT", setup="0"), fixtures.operation(2, "ASM", setup="0")))
    double = fixtures.task(2, link_inputs(
        (fixtures.operation(1, "CUT", setup="0"), fixtures.operation(2, "ASM", setup="0")), {2: ((1, Decimal(2)),)},
    ))
    for current, net in ((single, 3600), (double, 7200)):
        keys, _ = operation_keys(current, None)
        placed = {item.operation_id: item for item in plan(fixtures.data([current], keys)).placements}
        cut = placed[current.operations[0].id]
        # 36 с × 100 шт = 1 ч чистых; «2 на изделие» — 200 штук, 2 ч чистых.
        assert cut.finish == add_work_seconds(cut.start, net, frozenset(), DEFAULT_SHIFT)
        assert placed[current.operations[1].id].start >= cut.finish


def test_two_per_product_branch_is_done_at_double_quantity():
    task = _planned(_pair(per_unit="2"), {1: (X, at(9), at(11)), 2: (Y, at(11), at(12))})
    cut = task.operations[0]

    def fixed(handed_in: int) -> bool:
        return nodes_of([task], Progress({cut.id: Decimal(handed_in)}, {}))[(cut.id, None)].fixed

    # Сдано 10 штук — это половина нарезки на 10 изделий: работа ещё идёт, план её двигает.
    assert not fixed(10)
    assert fixed(20)


def test_late_input_moves_the_assembly_set_to_overlap_by_hand():
    # Мастер поставил сборку внахлёст на полчаса раньше конца нарезки (раньше такой сборки сдвиг не касался).
    task = _planned(_pair(), {1: (X, at(9), at(10)), 2: (Y, at(9, 30), at(10, 30))})
    # Нарезка кончится на час позже — сборка уходит на час, нахлёст в полчаса сохраняется.
    assert _late(task, (at(9), at(11)), (at(9), at(10))) == {2: (at(10, 30), at(11, 30))}


def test_late_input_moves_the_assembly_by_the_transfer_batch_lags():
    # Стрелка передаёт по одной штуке: сборка может идти с 9:06, мастер поставил её на 9:30.
    task = _planned(_pair(batch="1"), {1: (X, at(9), at(10)), 2: (Y, at(9, 30), at(10, 30))})
    # Нарезка до 10:20: последняя штука сборки (6 мин) всё равно после неё — сборка стоит.
    assert _late(task, (at(9), at(10, 20)), (at(9), at(10))) == {}
    # Нарезка до 11:00: хвост сборки обогнал бы её — сборка встаёт так, чтобы кончиться в 11:06.
    assert _late(task, (at(9), at(11)), (at(9), at(10))) == {2: (at(10, 6), at(11, 6))}


def test_slow_input_starts_the_overlap_later():
    # Нарезку делает X вдвое медленнее нормы: первая штука готова через 630 с чистых (12 мин), а не 315 (6 мин).
    task = _pair(batch="1", assembly_unit="945")
    skills = {
        "OP-1": {X: OperatorSkill(X, "OP-1", True, 3, Decimal("0.5"))},
        "OP-2": {Y: OperatorSkill(Y, "OP-2", True, 5, Decimal("1"))},
    }
    variant = schedule_task(
        task=task, transfer_batch=QUANTITY, team=(X, Y), skills=skills, busy={}, now=at(9),
        holidays=frozenset(), max_crew=1,
    )
    windows = {item.position: (item.start, item.finish) for item in variant.operations}
    assert windows[1] == (at(9), at(11))
    assert windows[2][0] == at(9, 12)


def test_clock_runs_back_and_forth_over_the_break():
    assert advance_clock(at(12, 50), 20 * 60, frozenset()) == at(13, 40)
    assert rewind_clock(at(13, 40), 20 * 60, frozenset()) == at(12, 50)
    assert rewind_clock(datetime(2026, 9, 22, 9, 10), 20 * 60, frozenset()) == at(17, 20)
    assert clock_between(at(12, 50), at(13, 40), frozenset()) == 20 * 60


def test_crew_member_start_anchors_only_his_share(production_api):
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: moscow_at(22, 9)
    add_second()
    task = launch_from_card(client, "crew-start-v1", "591K.3", "КБ1", 10, [("OP", "Обжим", 630, 0)]).json()["task"]
    crew = client.post(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id), str(SECOND)], "requested_start": "2026-09-22T09:00:00+03:00"},
    )
    assert crew.status_code == 200, crew.text
    task = crew.json()["task"]
    master = app.dependency_overrides[authenticated_principal]

    def start(who: UUID, minute: int) -> None:
        app.dependency_overrides[moscow_now] = lambda: moscow_at(22, 9, minute)
        as_operator(who)
        response = client.post(f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/work/start", json={})
        app.dependency_overrides[authenticated_principal] = master
        assert response.status_code == 200, response.text

    # Вдвоём — час. Первый взял в 9:30: от «сейчас» встаёт его доля, напарник — по плану.
    start(operator_id, 30)
    current = fresh(client, task["id"])
    assert window(current, 1, operator_id) == ("2026-09-22T09:30", "2026-09-22T10:30")
    assert window(current, 1, SECOND) == ("2026-09-22T09:00", "2026-09-22T10:00")
    # Второй взял в 9:45 — доля первого не переставляется.
    start(SECOND, 45)
    current = fresh(client, task["id"])
    assert window(current, 1, SECOND) == ("2026-09-22T09:45", "2026-09-22T10:45")
    assert window(current, 1, operator_id) == ("2026-09-22T09:30", "2026-09-22T10:30")
