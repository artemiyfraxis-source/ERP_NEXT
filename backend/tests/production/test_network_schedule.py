"""ARCH-TRACK-2 B3: сетевой график цеха — ветки идут параллельно на разных людях, сборка ждёт самую позднюю ветку,
сдвиг плана идёт по графу входов до финальной операции. Цепочка планируется как раньше (test_scheduling, test_dispatch)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import (
    AssignProductionCommand,
    LaunchProductionCommand,
    assign_production,
    launch_production,
)
from app.modules.production.dispatch import DispatchCalendar, Progress, place, reflow
from app.modules.production.domain import (
    DEFAULT_SHIFT,
    OperationInput,
    OperationShare,
    ProductionOperation,
    ProductionTask,
    link_inputs,
)
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.scheduling import (
    network_order,
    schedule_task,
    schedule_variants,
    upstream_ids,
    waited_input,
)
from app.shared.persistence import create_schema
from tests.factories import Actor

X = UUID("10000000-0000-0000-0000-000000000001")
Y = UUID("10000000-0000-0000-0000-000000000002")
Z = UUID("10000000-0000-0000-0000-000000000003")
QUANTITY = 10
# Смена 9:00–17:30, обед 13:00–13:30, чистых 7 из 8 ч: 315 с × 10 шт = 52,5 мин чистых = 1 ч на часах.
HOUR = "315"


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 21, hour, minute)  # понедельник


def _op(position: int, stage: str, unit: str = HOUR) -> ProductionOperation:
    return ProductionOperation.create(
        position=position, code=f"OP-{position}", stage=stage, description="",
        seconds_per_unit=Decimal(unit), setup_seconds=Decimal(0), quantity=QUANTITY,
    )


def _task(operations: tuple[ProductionOperation, ...], edges: dict | None = None) -> ProductionTask:
    linked = link_inputs(operations, edges) if edges is not None else operations
    return ProductionTask.create(
        number="ПЗ-0001", source_idempotency_key="network", source_fingerprint="fingerprint", project="708К.4",
        item="КБ1", name="Жгут", quantity=QUANTITY, fulfillment_request_id=None, deadline=None,
        launched_by="Мастер", operations=linked, created_at=at(8),
    )


def _merge(long_branch: str = "472.5") -> ProductionTask:
    """Ветка А: нарезка → опрессовка (по 1 ч); ветка Б: нарезка 3 ч (2 на сборку: 472,5 с × 20 шт); обе → сборка →
    маркировка (по 1 ч)."""
    return _task(
        (_op(1, "Нарезка А"), _op(2, "Опрессовка А"), _op(3, "Нарезка Б", long_branch), _op(4, "Сборка"),
         _op(5, "Маркировка")),
        {2: ((1, Decimal(1)),), 4: ((2, Decimal(1)), (3, Decimal(2))), 5: ((4, Decimal(1)),)},
    )


def _schedule(task: ProductionTask, batch: int = QUANTITY, team=(X, Y), **kwargs):
    return schedule_task(
        task=task, transfer_batch=batch, team=team, skills={}, busy={}, now=at(9),
        holidays=frozenset(), max_crew=1, **kwargs,
    )


def _windows(variant) -> dict[int, tuple[datetime, datetime]]:
    return {item.position: (item.start, item.finish) for item in variant.operations}


def test_branches_run_in_parallel_and_the_assembly_waits_for_the_later_branch():
    task = _merge()
    variant = _schedule(task)
    plan = {item.position: item for item in variant.operations}
    windows = _windows(variant)

    # Ветки одновременно на разных людях: нарезка Б не ждёт ветку А.
    assert windows[1] == (at(9), at(10)) and windows[2] == (at(10), at(11))
    assert windows[3] == (at(9), at(12))
    assert plan[3].operator_ids != plan[1].operator_ids
    assert windows[3][0] < windows[1][1]
    # Сборка — после самой поздней ветки (нарезка Б, 12:00), маркировка — после сборки, через обед.
    assert windows[4] == (at(12), at(13))
    assert windows[4][0] == max(windows[2][1], windows[3][1])
    assert windows[5] == (at(13, 30), at(14, 30))
    assert (variant.start, variant.finish) == (at(9), at(14, 30))
    assert variant.first_ready == at(14, 30)  # партия целиком: первые изделия — вместе со всеми
    assert variant.warnings == ()
    # Тот же маршрут цепочкой по номерам (без схемы) идёт дольше: нарезка Б ждёт опрессовку А.
    chain = _schedule(_task(tuple(replace(item, inputs=()) for item in task.operations)))
    assert _windows(chain)[3][0] == at(11)
    assert chain.finish > variant.finish


def test_transfer_batch_lets_the_assembly_overlap_the_later_branch_but_not_outrun_it():
    variant = _schedule(_merge(), batch=1, team=(X, Y, Z))
    windows = _windows(variant)

    # Передаточная партия 1: опрессовка А идёт за нарезкой А после первой штуки, как в цепочке.
    assert windows[2][0] == at(9, 6)
    assert windows[3] == (at(9), at(12))
    # Сборка стартует по готовности поздней ветки (её хвост не обгонит нарезку Б), а не ранней.
    assert windows[2][1] < windows[4][0] < windows[3][1]
    assert windows[4] == (at(11, 6), at(12, 6))
    assert windows[4][1] > windows[3][1]


def test_batch_on_the_edge_overrides_the_variant_batch():
    first, second = _op(1, "Нарезка"), _op(2, "Опрессовка")
    whole = _schedule(_task((first, second)))
    assert _windows(whole)[2] == (at(10), at(11))

    edge = replace(second, inputs=(OperationInput(first.id, Decimal(1), Decimal(1)),))
    by_piece = _schedule(_task((first, edge)))
    # Ребро передаёт по одной штуке: опрессовка идёт внахлёст, хотя вариант задания — партия целиком.
    assert _windows(by_piece)[2] == (at(9, 6), at(10, 6))
    assert by_piece.transfer_batch == QUANTITY


def test_assembly_set_by_hand_too_early_is_flagged_against_the_branch_that_is_not_ready():
    task = _merge()
    assembly = task.operations[3]
    fixed = replace(task, operations=tuple(
        item.with_shares((OperationShare(X, None, at(11, 30), at(12, 30)),), "Мастер") if item.id == assembly.id else item
        for item in task.operations
    ))
    variant = _schedule(fixed)

    # Ветка А готова к 11:00, нарезка Б — только к 12:00: предупреждение — по ней одной.
    assert variant.warnings == ("Этап 4 назначен раньше готовности деталей с этапа 3",)
    assert _windows(variant)[4] == (at(11, 30), at(12, 30))
    assert _windows(variant)[5][0] == at(12, 30)


def test_variants_keep_the_whole_batch_on_a_network():
    variants = schedule_variants(
        task=_merge(), team=(X, Y, Z), skills={}, busy={}, now=at(9), holidays=frozenset(), max_crew=1,
        replan=False,
    )
    assert variants[0].transfer_batch == QUANTITY
    assert all(len(item.operations) == 5 for item in variants)
    assert min(item.first_ready for item in variants) < variants[0].first_ready


def test_order_follows_inputs_even_when_numbers_do_not():
    # Сборка под номером 2 берёт этапы 1 и 3: планируется после этапа 3.
    task = _task(
        (_op(1, "Нарезка А"), _op(2, "Сборка"), _op(3, "Нарезка Б"), _op(4, "Маркировка")),
        {2: ((1, Decimal(1)), (3, Decimal(1))), 4: ((2, Decimal(1)),)},
    )
    assert [item.position for item in network_order(task.operations)] == [1, 3, 2, 4]
    assert upstream_ids(task.operations, task.operations[3].id) == {item.id for item in task.operations[:3]}
    windows = _windows(_schedule(task))
    assert windows[2][0] >= max(windows[1][1], windows[3][1])
    assert [item.position for item in _schedule(task).operations] == [1, 2, 3, 4]


def test_waited_input_is_the_latest_planned_branch():
    task = _merge()
    one, two, three, assembly, _ = task.operations
    planned = replace(task, operations=(
        one.with_shares((OperationShare(X, None, at(9), at(10)),), "Мастер"),
        two,  # опрессовка А без плана — ветка А представлена нарезкой А
        three.with_shares((OperationShare(Y, None, at(9), at(9, 30)),), "Мастер"),
        assembly, task.operations[4],
    ))
    assert waited_input(planned, assembly.id).position == 1
    assert waited_input(planned, one.id) is None


CALENDAR = DispatchCalendar(frozenset(), DEFAULT_SHIFT, {})


def _planned(task: ProductionTask, windows: dict[int, tuple[UUID, datetime, datetime]]) -> ProductionTask:
    return replace(task, operations=tuple(
        item.with_shares((OperationShare(windows[item.position][0], None, *windows[item.position][1:]),), "Мастер")
        if item.position in windows else item
        for item in task.operations
    ))


def _four() -> ProductionTask:
    """Ветки А и Б → сборка → маркировка (сборка и маркировка по 1 ч)."""
    return _task(
        (_op(1, "Нарезка А"), _op(2, "Нарезка Б"), _op(3, "Сборка"), _op(4, "Маркировка")),
        {3: ((1, Decimal(1)), (2, Decimal(1))), 4: ((3, Decimal(1)),)},
    )


def _reflow(task: ProductionTask, changed: int, before: tuple[datetime, datetime], pull: bool, now: datetime):
    operation = next(item for item in task.operations if item.position == changed)
    moves = reflow(
        tasks=[task], sources={(operation.id, None): before}, pull=pull, now=now,
        calendar=CALENDAR, skills={}, progress=Progress.empty(),
    )
    return {move.operation.position: (move.start, move.finish) for move in moves.values()}


def test_delayed_branch_pushes_the_assembly_and_the_final_operation():
    plan = {1: (X, at(9), at(9, 30)), 2: (Y, at(9), at(10)), 3: (Z, at(10), at(11)), 4: (Z, at(11), at(12))}

    # Ветка А задержалась до 9:50 — сборка всё равно ждёт ветку Б до 10:00: ничего не двигается.
    late = _planned(_four(), {**plan, 1: (X, at(9), at(9, 50))})
    assert _reflow(late, 1, (at(9), at(9, 30)), pull=False, now=at(9)) == {}

    # До 10:20 — сборка встаёт за ней, маркировка за сборкой; ветка Б на месте.
    later = _planned(_four(), {**plan, 1: (X, at(9), at(10, 20))})
    assert _reflow(later, 1, (at(9), at(9, 30)), pull=False, now=at(9)) == {
        3: (at(10, 20), at(11, 20)),
        4: (at(11, 20), at(12, 20)),
    }


def test_early_branch_pulls_the_assembly_only_to_the_other_branch():
    plan = {1: (X, at(9), at(10, 30)), 2: (Y, at(9), at(10)), 3: (Z, at(11), at(12)), 4: (Z, at(12), at(13))}
    # Ветка Б по плану до 11:00 кончилась в 10:00 — сборка подтягивается, но не раньше ветки А (10:30).
    early = _planned(_four(), plan)
    assert _reflow(early, 2, (at(9), at(11)), pull=True, now=at(10)) == {
        3: (at(10, 30), at(11, 30)),
        4: (at(11, 30), at(12, 30)),
    }


def test_installer_places_a_parallel_branch_freely_but_the_assembly_after_his_branch():
    task = _planned(_four(), {1: (X, at(10), at(11))})
    branch, assembly = task.operations[1], task.operations[2]

    def placed(target: ProductionTask, operation: ProductionOperation) -> tuple[datetime, datetime]:
        return place(
            tasks=[target], task=target, operation=operation, operator_id=X, quantity=None,
            requested_start=at(9), calendar=CALENDAR, skills={}, progress=Progress.empty(),
        )

    # Нарезка Б — параллельная ветка: встаёт с 9:00 до нарезки А того же монтажника.
    assert placed(task, branch) == (at(9), at(10))
    # Цепочкой по номерам этап 2 ждал бы этап 1.
    chain = _planned(_task(tuple(replace(item, inputs=()) for item in _four().operations)), {1: (X, at(10), at(11))})
    assert placed(chain, chain.operations[1]) == (at(11), at(12))
    # Сборка берёт нарезку А — не раньше её конца.
    assert placed(task, assembly) == (at(11), at(12))


def _uow_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with sessions() as session:
        for operator_id, login in ((X, "first"), (Y, "second")):
            session.add(IdentityUserRow(
                id=operator_id, login=login, email=f"{login}@example.test", name=login,
                department="Производство", role="staff", site="ЦАР", active=True, shop_worker=True,
            ))
        session.commit()
    return engine, (lambda: SqlAlchemyProductionUnitOfWork(sessions))


def test_manual_assignment_waits_only_for_what_feeds_the_operation():
    engine, factory = _uow_factory()
    actor = Actor(name="Мастер", department="Производство")
    operations = link_inputs(
        (_op(1, "Нарезка А"), _op(2, "Нарезка Б", "630"), _op(3, "Сборка"), _op(4, "Маркировка")),
        {3: ((1, Decimal(1)), (2, Decimal(1))), 4: ((3, Decimal(1)),)},
    )
    task = launch_production(LaunchProductionCommand(
        idempotency_key="708К.4:КБ1:сеть", project="708К.4", item="КБ1", name="Жгут", quantity=QUANTITY,
        fulfillment_request_id=None, deadline=date(2026, 10, 20), operations=operations, actor=actor, now=at(9),
    ), factory())

    def assign(current: ProductionTask, position: int, operator_id: UUID, dry: bool = False):
        return assign_production(AssignProductionCommand(
            task_id=current.id, expected_version=current.version, operation_id=current.operations[position - 1].id,
            operator_ids=(operator_id,), requested_start=None, actor=actor, now=at(9), dry=dry,
        ), factory())

    task, plan = assign(task, 1, X)
    assert (plan.start, plan.finish) == (at(9), at(10))
    # Нарезка Б не ждёт нарезку А: другая ветка, другой монтажник — с 9:00.
    task, plan = assign(task, 2, Y)
    assert (plan.start, plan.finish) == (at(9), at(11))
    # Сборка — после обеих веток: X свободен с 10:00, но нарезка Б кончается в 11:00.
    _, plan = assign(task, 3, X, dry=True)
    assert (plan.start, plan.finish) == (at(11), at(12))
    engine.dispose()
