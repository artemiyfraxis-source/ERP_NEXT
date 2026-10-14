"""Остатки полуфабрикатов: сверх того, что ещё возьмут следующие этапы задания, — остаток; у выполненного или
отменённого задания остаток — всё, что лежит в коробках не финальной операции. Только чтение."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.production.application import (
    LaunchProductionCommand,
    SetProductionCancelledCommand,
    launch_production,
    set_production_cancelled,
)
from app.modules.production.domain import ProductionOperation, ProductionStatus, ProductionTask, link_inputs
from app.modules.production.infrastructure import ProductionBoxRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.leftovers import leftovers, needed, task_leftovers
from app.modules.production.traceability import BoxStatus, ProductionBox, ProductionOutput, format_box_number
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor

NOW = datetime(2026, 10, 13, 9, 0)
FITTER = UUID("40000000-0000-0000-0000-000000000002")
_sequence = iter(range(104000, 105000))


def operations(quantity: int, per_unit: Decimal = Decimal(1)) -> tuple[ProductionOperation, ...]:
    """Резка → опрессовка → сборка; на сборку идёт per_unit опрессованных проводов на одно изделие."""
    ops = tuple(ProductionOperation.create(
        position=position, code=code, stage=stage, description="", seconds_per_unit=Decimal(10),
        setup_seconds=Decimal(0), quantity=quantity,
    ) for position, code, stage in ((1, "CUT", "cutWire"), (2, "PRS", "prsTerm"), (3, "ASM", "Сборка")))
    return link_inputs(ops, {2: ((1, Decimal(1)),), 3: ((2, per_unit),)})


def task(quantity: int = 10, *, status: ProductionStatus = ProductionStatus.RUNNING, per_unit: Decimal = Decimal(1),
         number: str = "ПЗ-1", item: str = "КБ-1") -> ProductionTask:
    created = ProductionTask.create(
        number=number, source_idempotency_key=f"key-{number}", source_fingerprint="fp", project="708К", item=item,
        name="Жгут", quantity=quantity, fulfillment_request_id=None, deadline=None, launched_by="Мастер",
        operations=operations(quantity, per_unit), created_at=NOW,
    )
    return replace(created, status=status)


def box(owner: ProductionTask, position: int, good: int, consumed: int = 0) -> ProductionBox:
    operation = next(item for item in owner.operations if item.position == position)
    made = ProductionBox.submitted(
        task_id=owner.id, operation_id=operation.id, item=owner.item, operation_position=position,
        operator_id=FITTER, number=format_box_number(next(_sequence)), good=Decimal(good), actor="Монтажник", now=NOW,
    ).confirm("Мастер", NOW)
    if consumed:
        made = made.reserve(Decimal(consumed), item=owner.item, previous_position=position).consume(Decimal(consumed))
    return made


def output(owner: ProductionTask, position: int, good: int, defect: int = 0) -> ProductionOutput:
    operation = next(item for item in owner.operations if item.position == position)
    return ProductionOutput(
        id=uuid4(), session_id=None, task_id=owner.id, operation_id=operation.id, operator_id=FITTER,
        started_at=NOW, finished_at=NOW, pause_seconds=0, good_quantity=Decimal(good),
        defect_quantity=Decimal(defect), output_box_id=uuid4(),
    )


def test_running_task_without_surplus_keeps_pieces_for_the_next_stage():
    """Нарезано 10, опрессовано 6 (5 годных и 1 брак): 4 провода в коробке ещё возьмёт опрессовка — не остаток."""
    owner = task(10)
    boxes = [box(owner, 1, 10, consumed=6), box(owner, 2, 5)]
    outputs = [output(owner, 1, 10), output(owner, 2, 5, 1)]
    assert task_leftovers(owner, boxes, outputs) == ()
    assert needed(owner, owner.operations[0].id, {owner.operations[1].id: Decimal(6)}) == Decimal(4)


def test_running_task_with_more_pieces_than_the_next_stage_takes():
    """В коробке нарезки 12, а опрессовке до тиража осталось 4 — сверх них 2 провода остаток."""
    owner = task(10)
    boxes = [box(owner, 1, 12, consumed=6)]
    outputs = [output(owner, 2, 6)]
    [row] = task_leftovers(owner, boxes, outputs)
    assert (row.position, row.code, row.stage) == (1, "CUT", "Резка")
    assert (row.produced, row.consumed, row.in_boxes, row.needed, row.surplus) == (12, 6, 6, 4, 2)
    assert [item.available for item in row.boxes] == [Decimal(6)]


def test_cancelled_task_leaves_everything_in_its_semi_finished_boxes():
    """Отменили после резки и части опрессовки: всё, что лежит у не финальных этапов, — остаток."""
    owner = task(10, status=ProductionStatus.CANCELLED)
    boxes = [box(owner, 1, 10, consumed=7), box(owner, 2, 7, consumed=3), box(owner, 3, 3)]
    outputs = [output(owner, 1, 10), output(owner, 2, 7), output(owner, 3, 3)]
    rows = task_leftovers(owner, boxes, outputs)
    assert [(row.position, row.surplus, row.needed) for row in rows] == [(1, 3, 0), (2, 4, 0)]


def test_finished_goods_and_empty_boxes_are_not_leftovers():
    """Выполненное задание: изделия финального этапа идут в ОТК и на склад ГП, пустые коробки — не остаток."""
    owner = task(5, status=ProductionStatus.DONE)
    boxes = [box(owner, 1, 5, consumed=5), box(owner, 2, 5, consumed=5), box(owner, 3, 5)]
    assert task_leftovers(owner, boxes, [output(owner, 3, 5)]) == ()


def test_per_unit_need_counts_pieces_of_the_semi_finished():
    """Два опрессованных провода на изделие: на 3 оставшихся изделия нужно 6 — из 7 в коробке остаток 1."""
    owner = task(5, per_unit=Decimal(2))
    boxes = [box(owner, 2, 10, consumed=3)]
    outputs = [output(owner, 3, 2)]
    [row] = task_leftovers(owner, boxes, outputs)
    assert (row.position, row.in_boxes, row.needed, row.surplus) == (2, 7, 6, 1)


def test_leftovers_are_sorted_by_item_and_stage():
    first = task(4, status=ProductionStatus.CANCELLED, number="ПЗ-2", item="КБ-2")
    second = task(4, status=ProductionStatus.CANCELLED, number="ПЗ-1", item="КБ-1")
    boxes = [box(first, 2, 4), box(first, 1, 2), box(second, 1, 1)]
    assert [(row.item, row.position) for row in leftovers([first, second], boxes, [])] == [
        ("КБ-1", 1), ("КБ-2", 1), ("КБ-2", 2),
    ]


@pytest.fixture()
def client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Мастер", department="Производство"), email="master@example.test",
        roles=frozenset({"foreman"}), permissions={"production": "view"},
    )
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    delattr(app.state, "production_uow_factory")
    engine.dispose()


def test_api_lists_leftovers_of_a_cancelled_task_without_touching_boxes(client):
    http, sessions = client
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    master = Actor(name="Мастер", department="Производство")
    launched = launch_production(LaunchProductionCommand(
        idempotency_key="leftovers-1", project="708К", item="КБ-1", name="Жгут", quantity=10,
        fulfillment_request_id=None, deadline=None, operations=operations(10), actor=master, now=NOW,
    ), factory())
    running = launch_production(LaunchProductionCommand(
        idempotency_key="leftovers-2", project="708К", item="КБ-1", name="Жгут", quantity=10,
        fulfillment_request_id=None, deadline=None, operations=operations(10), actor=master, now=NOW,
    ), factory())
    with factory() as uow:
        uow.boxes.save(box(launched, 1, 10, consumed=4))
        uow.boxes.save(box(running, 1, 10))
        uow.commit()
    set_production_cancelled(SetProductionCancelledCommand(
        task_id=launched.id, expected_version=launched.version, cancelled=True, actor=master, now=NOW,
    ), factory())
    with sessions() as session:
        before = session.scalar(select(func.sum(ProductionBoxRow.consumed)))

    response = http.get("/api/production/leftovers")

    assert response.status_code == 200, response.text
    [row] = response.json()["leftovers"]
    assert row["task_number"] == launched.number and row["task_status"] == "Отменено"
    assert (row["item"], row["position"], row["code"], row["stage"]) == ("КБ-1", 1, "CUT", "Резка")
    assert (row["produced"], row["consumed"], row["in_boxes"], row["needed"], row["surplus"]) == ("10", "4", "6", "0", "6")
    assert [item["available"] for item in row["boxes"]] == ["6"]
    assert row["boxes"][0]["status"] == BoxStatus.CONFIRMED.value
    with sessions() as session:
        assert session.scalar(select(func.sum(ProductionBoxRow.consumed))) == before
