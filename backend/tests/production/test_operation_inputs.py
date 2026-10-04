"""ARCH-TRACK-2 B1: явный граф входов операций задания (сетевой график вместо неявного «предыдущего этапа»)."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import (
    AssignProductionCommand,
    LaunchProductionCommand,
    assign_production,
    launch_production,
)
from app.modules.production.domain import (
    OperationInput,
    ProductionOperation,
    ProductionTask,
    ProductionValidationError,
    final_operation,
    link_inputs,
)
from app.modules.production.infrastructure import ProductionOperationInputRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.launchable import RouteStep, launch_operations, route_inputs
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.production.test_api import launch_from_card, production_api  # noqa: F401 — фикстура API цеха

NOW = datetime(2026, 10, 4, 9, 0)
ACTOR = Actor(name="Мастер", department="Производство")


def _op(position: int, stage: str = "Этап", stage_key: str | None = None) -> ProductionOperation:
    return ProductionOperation.create(
        position=position, code=f"OP-{position:02d}", stage=f"{stage} {position}", description="",
        seconds_per_unit=Decimal("10"), setup_seconds=Decimal("0"), quantity=5, stage_key=stage_key,
    )


def _task(operations: tuple[ProductionOperation, ...]) -> ProductionTask:
    return ProductionTask.create(
        number="ПЗ-0001", source_idempotency_key="key", source_fingerprint="fingerprint", project="708К.4",
        item="КБ1", name="Жгут", quantity=5, fulfillment_request_id=None, deadline=None, launched_by="Мастер",
        operations=operations, created_at=NOW,
    )


def _merge() -> tuple[ProductionOperation, ...]:
    """Две ветки сходятся в сборку: нарезка А (2 шт на изделие) и нарезка Б (1 шт) → сборка → маркировка."""
    return link_inputs(
        (_op(1, "Нарезка А"), _op(2, "Нарезка Б"), _op(3, "Сборка"), _op(4, "Маркировка")),
        {3: ((2, Decimal(1)), (1, Decimal(2))), 4: ((3, Decimal(1)),)},
    )


def test_task_without_inputs_runs_todays_chain_and_ends_at_the_last_operation():
    task = _task((_op(3), _op(1), _op(2)))

    first, second, third = task.operations
    assert first.inputs == ()
    assert second.inputs == (OperationInput(first.id),)
    assert third.inputs == (OperationInput(second.id, Decimal(1), None),)
    assert task.final_operation == third


def test_single_operation_task_has_no_inputs_and_is_its_own_final():
    task = _task((_op(1),))

    assert task.operations[0].inputs == ()
    assert task.final_operation == task.operations[0]


def test_branches_merge_into_assembly_with_per_unit_on_each_edge():
    task = _task(_merge())

    cut_a, cut_b, assembly, marking = task.operations
    assert cut_a.inputs == cut_b.inputs == ()
    # Входы — по возрастанию номера операции-входа, сколько штук на одну сборку — на ребре.
    assert assembly.inputs == (OperationInput(cut_a.id, Decimal(2)), OperationInput(cut_b.id, Decimal(1)))
    assert marking.inputs == (OperationInput(assembly.id),)
    assert task.final_operation == marking
    assert final_operation(task.operations) == marking


def test_transfer_batch_is_kept_on_the_edge_and_defaults_to_the_task_batch():
    edge = OperationInput(uuid4(), 3, Decimal("50"))

    assert (edge.per_unit, edge.transfer_batch) == (Decimal(3), Decimal(50))
    assert OperationInput(uuid4()).transfer_batch is None


@pytest.mark.parametrize("per_unit, batch", [(0, None), (Decimal("-1"), None), (1, 0), (Decimal("NaN"), None)])
def test_edge_rejects_non_positive_quantities(per_unit, batch):
    with pytest.raises(ProductionValidationError):
        OperationInput(uuid4(), per_unit, batch)


def test_cycle_is_rejected():
    first, second, third = _op(1), _op(2), _op(3)
    looped = (
        first,
        replace(second, inputs=(OperationInput(third.id),)),
        replace(third, inputs=(OperationInput(second.id),)),
    )
    with pytest.raises(ProductionValidationError, match="цикл"):
        _task(looped)


def test_two_final_operations_are_rejected():
    operations = link_inputs((_op(1), _op(2), _op(3)), {2: ((1, Decimal(1)),)})
    with pytest.raises(ProductionValidationError, match="одна финальная"):
        _task(operations)


@pytest.mark.parametrize("target", ["self", "foreign", "twice"])
def test_inputs_must_be_other_operations_of_the_same_task_once(target):
    first, second = _op(1), _op(2)
    inputs = {
        "self": (OperationInput(second.id),),
        "foreign": (OperationInput(uuid4()),),
        "twice": (OperationInput(first.id), OperationInput(first.id, Decimal(2))),
    }[target]
    with pytest.raises(ProductionValidationError):
        _task((first, replace(second, inputs=inputs)))


def test_link_inputs_rejects_unknown_positions():
    with pytest.raises(ProductionValidationError):
        link_inputs((_op(1), _op(2)), {2: ((7, Decimal(1)),)})
    with pytest.raises(ProductionValidationError):
        link_inputs((_op(1), _op(2)), {9: ((1, Decimal(1)),)})


def _step(position: int, block: str | None = None, inputs: tuple = ()) -> object:
    base = dict(
        position=position, code=f"OP-{position:02d}", stage=f"Этап {position}", description="",
        seconds_per_unit=Decimal("10"), setup_seconds=Decimal("0"),
    )
    if block is None:
        return RouteStep(**base)
    return _SchemeStep(**base, block=block, inputs=inputs)


@dataclass(frozen=True)
class _SchemeStep:
    """Шаг маршрута со схемой полуфабрикатов (поля block / inputs добавляет A2 в launchable.RouteStep)."""
    position: int
    code: str
    stage: str
    description: str
    seconds_per_unit: Decimal
    setup_seconds: Decimal
    block: str = ""
    inputs: tuple = ()


def _block_input(block: str, per_unit: str = "1") -> SimpleNamespace:
    return SimpleNamespace(block=block, per_unit=Decimal(per_unit))


def test_route_without_scheme_gives_the_position_chain():
    route = (_step(1), _step(2), _step(3))

    assert route_inputs(route) == {2: ((1, Decimal(1)),), 3: ((2, Decimal(1)),)}
    operations = launch_operations(route, 5)
    assert [operation.stage_key for operation in operations] == [None, None, None]
    assert [len(operation.inputs) for operation in operations] == [0, 1, 1]


def test_route_with_scheme_chains_inside_a_block_and_joins_blocks_by_their_last_step():
    # Блок «a» — нарезка (шаг 1); блок «b» — нарезка и опрессовка (шаги 2, 3); блок «c» — сборка из 2×a и 1×b
    # (шаги 4, 5).
    route = (
        _step(1, "a"),
        _step(2, "b"), _step(3, "b"),
        _step(4, "c", (_block_input("a", "2"), _block_input("b"))), _step(5, "c", (_block_input("a", "2"), _block_input("b"))),
    )

    assert route_inputs(route) == {
        3: ((2, Decimal(1)),),
        4: ((1, Decimal(2)), (3, Decimal(1))),
        5: ((4, Decimal(1)),),
    }
    operations = launch_operations(route, 5)
    task = _task(operations)
    by_position = {operation.position: operation for operation in task.operations}
    assert [operation.stage_key for operation in task.operations] == ["a", "b", "b", "c", "c"]
    assert by_position[4].inputs == (
        OperationInput(by_position[1].id, Decimal(2)), OperationInput(by_position[3].id, Decimal(1)),
    )
    assert task.final_operation == by_position[5]


def test_route_with_unknown_predecessor_block_is_rejected():
    with pytest.raises(ProductionValidationError, match="«x»"):
        route_inputs((_step(1, "a"), _step(2, "b", (_block_input("x"),))))


def _uow_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    operator_id = UUID("10000000-0000-0000-0000-000000000001")
    with sessions() as session:
        session.add(IdentityUserRow(
            id=operator_id, login="operator", email="operator@example.test", name="Оператор",
            department="Производство", role="staff", site="ЦАР", active=True, shop_worker=True,
        ))
        session.commit()
    return engine, sessions, (lambda: SqlAlchemyProductionUnitOfWork(sessions)), operator_id


def _edges(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(ProductionOperationInputRow))


def test_graph_stage_keys_and_edge_quantities_persist_and_survive_assignment():
    engine, sessions, factory, operator_id = _uow_factory()
    operations = _merge()
    operations = tuple(
        replace(operation, stage_key=key, inputs=tuple(
            OperationInput(item.input_operation_id, item.per_unit, Decimal("20") if operation.position == 3 else None)
            for item in operation.inputs
        ))
        for operation, key in zip(operations, ("a", "b", "c", "c"))
    )
    created = launch_production(LaunchProductionCommand(
        idempotency_key="708К.4:КБ1:граф", project="708К.4", item="КБ1", name="Жгут", quantity=5,
        fulfillment_request_id=None, deadline=date(2026, 10, 20), operations=operations, actor=ACTOR, now=NOW,
    ), factory())
    with factory() as uow:
        stored = uow.production.get(created.id)

    assert stored == created
    assert [operation.stage_key for operation in stored.operations] == ["a", "b", "c", "c"]
    assert stored.operations[2].inputs[0].transfer_batch == Decimal(20)
    assert stored.final_operation.position == 4
    assert _edges(sessions) == 3

    assigned, _ = assign_production(AssignProductionCommand(
        task_id=created.id, expected_version=created.version, operation_id=created.operations[0].id,
        operator_ids=(operator_id,), requested_start=None, actor=ACTOR, now=NOW,
    ), factory())
    with factory() as uow:
        reloaded = uow.production.get(created.id)
    assert reloaded == assigned
    assert [operation.inputs for operation in reloaded.operations] == [operation.inputs for operation in created.operations]
    assert _edges(sessions) == 3

    with factory() as uow:
        uow.production.delete(created.id)
        uow.commit()
    assert _edges(sessions) == 0
    engine.dispose()


def test_launch_from_issued_card_writes_the_chain_inputs(production_api):  # noqa: F811
    client, _ = production_api
    response = launch_from_card(
        client, "graph-708k4-kb2", "708К.4", "КБ2", 4,
        [("OP-01", "Нарезка", 10, 0), ("OP-02", "Опрессовка", 20, 0), ("OP-03", "Монтаж", 30, 0)],
    )
    assert response.status_code == 200, response.text

    with app.state.production_uow_factory() as uow:
        task = uow.production.get(UUID(response.json()["task"]["id"]))
    first, second, third = task.operations
    assert [operation.inputs for operation in task.operations] == [
        (), (OperationInput(first.id),), (OperationInput(second.id),),
    ]
    assert task.final_operation == third
