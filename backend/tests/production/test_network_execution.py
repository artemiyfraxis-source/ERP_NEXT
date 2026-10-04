"""ARCH-TRACK-2 B2: исполнение идёт по сетевому графику задания, а не по «предыдущему номеру».

Коробки — с любого этапа-входа операции; сборка начинается только с коробками каждого входа; расход входа —
штуки выхода × штук на одну (per_unit ребра); комплект — первой исходной операции (решение 5); ОТК и склад ГП —
только выход финальной операции. Цепочка (все задания до графа) идёт как раньше — это держат старые тесты цеха."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.fulfillment.infrastructure import FulfillmentLineRow, FulfillmentRequestRow
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import (
    AssignProductionCommand,
    LaunchProductionCommand,
    assign_production,
    launch_production,
)
from app.modules.production.domain import ProductionOperation, ProductionTask, ProductionValidationError, link_inputs
from app.modules.production.execution import (
    BoxCommand,
    CorrectBoxCommand,
    HandoffBoxCommand,
    ReturnBoxCommand,
    SessionCommand,
    StartWorkCommand,
    SubmitWorkCommand,
    cancel_work,
    confirm_box,
    correct_box,
    handoff_box,
    material_operation,
    operation_quantity,
    return_box,
    start_work,
    submit_work,
)
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.quality import InspectBoxCommand, inspect_box
from app.modules.production.traceability import (
    BoxStatus,
    InputStage,
    MaterialInput,
    ProductionBox,
    WorkInput,
    WorkStatus,
    correction_moves,
    format_box_number,
    take_inputs,
)
from app.modules.shipping.domain import FinishedGood
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.production.test_api import production_api  # noqa: F401 — фикстура API цеха

NOW = datetime(2026, 10, 5, 9, 0)
OPERATOR_ID = UUID("10000000-0000-0000-0000-000000000002")
MASTER = Actor(name="Мастер", department="Производство")
OPERATOR = Actor(id=OPERATOR_ID, name="Оператор", department="Производство")


def _op(position: int, stage: str) -> ProductionOperation:
    return ProductionOperation.create(
        position=position, code=f"OP-{position:02d}", stage=stage, description="",
        seconds_per_unit=Decimal("10"), setup_seconds=Decimal("0"), quantity=5,
    )


def merge_operations() -> tuple[ProductionOperation, ...]:
    """Две ветки сходятся в сборку: нарезка А (2 шт на сборку) и нарезка Б (1 шт) → сборка → маркировка."""
    return link_inputs(
        (_op(1, "Нарезка А"), _op(2, "Нарезка Б"), _op(3, "Сборка"), _op(4, "Маркировка")),
        {3: ((1, Decimal(2)), (2, Decimal(1))), 4: ((3, Decimal(1)),)},
    )


@dataclass
class Shop:
    engine: object
    factory: object
    task: ProductionTask
    request_id: UUID
    line_id: UUID

    def operation(self, position: int) -> ProductionOperation:
        return next(item for item in self.task.operations if item.position == position)

    def box(self, box: ProductionBox) -> ProductionBox:
        with self.factory() as uow:
            return uow.boxes.get(box.id)


def network_shop(operations: tuple[ProductionOperation, ...] | None = None) -> Shop:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    request_id, line_id = uuid4(), uuid4()
    with sessions() as session:
        session.add(IdentityUserRow(
            id=OPERATOR_ID, login="operator", email="operator@example.test", name="Оператор",
            department="Производство", role="staff", site="ЦАР", active=True,
        ))
        session.add(FulfillmentRequestRow(
            id=request_id, number="REQ.26.0001", source_idempotency_key="kit-network",
            source_fingerprint="f" * 64, version=1, created_at=NOW,
            requester="Инженер", purpose="591K.3", deadline=None,
            status=FulfillmentStatus.ISSUED.value, handout_location=None, return_location=None,
        ))
        session.add(FulfillmentLineRow(
            id=line_id, request_id=request_id, position=0, requirement_id=line_id,
            article="WIRE-1", name="Провод", unit="м", requirement=Decimal("10"),
            reserved=Decimal("0"), issued=Decimal("10"), returned_good=Decimal("0"),
            defective_returned=Decimal("0"), defective_missing=Decimal("0"),
            lot_id=uuid4(), lot_warehouse="01", lot_grn="PPM.26.0000001",
            lot_package="П1", lot_storage_location="A-01",
        ))
        session.commit()
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    task = launch_production(LaunchProductionCommand(
        idempotency_key="591K.3:КБ1:сеть", project="591K.3", item="591K.3.КБ1", name="Жгут",
        quantity=5, fulfillment_request_id=request_id, deadline=None,
        operations=operations or merge_operations(), actor=MASTER, now=NOW,
    ), factory())
    for operation in task.operations:
        task, _ = assign_production(AssignProductionCommand(
            task_id=task.id, expected_version=task.version, operation_id=operation.id,
            operator_ids=(OPERATOR_ID,), requested_start=NOW, actor=MASTER, now=NOW,
        ), factory())
    return Shop(engine, factory, task, request_id, line_id)


def _start(shop: Shop, position: int, *boxes: ProductionBox, materials: tuple[MaterialInput, ...] = ()):
    return start_work(StartWorkCommand(
        task_id=shop.task.id, operation_id=shop.operation(position).id,
        box_numbers=tuple(box.number for box in boxes), actor=OPERATOR, now=NOW, materials=materials,
    ), shop.factory())


def _submit(shop: Shop, work, good: str, defect: str = "0"):
    _completed, box, output, _defect = submit_work(SubmitWorkCommand(
        session_id=work.id, actor=OPERATOR, now=NOW + timedelta(minutes=5),
        good_quantity=Decimal(good), defect_quantity=Decimal(defect),
        defect_character="Обрыв" if Decimal(defect) else "", defect_description="Нет контакта" if Decimal(defect) else "",
    ), shop.factory())
    return box, output


def _made(shop: Shop, position: int, good: str, *boxes: ProductionBox, materials: tuple[MaterialInput, ...] = ()) -> ProductionBox:
    """Операция сделана и коробка её выхода подтверждена мастером."""
    box, _output = _submit(shop, _start(shop, position, *boxes, materials=materials), good)
    return confirm_box(BoxCommand(box.id, box.version, MASTER, NOW), shop.factory())


def test_both_branches_start_without_boxes_and_the_kit_goes_to_the_first_source_operation():
    shop = network_shop()
    kit = (MaterialInput(shop.line_id, Decimal("4")),)

    assert material_operation(shop.task) == shop.operation(1)
    with pytest.raises(ProductionValidationError, match="Складские партии берёт только первая операция"):
        _start(shop, 2, materials=kit)
    cut_a = _made(shop, 1, "10", materials=kit)
    with pytest.raises(ProductionValidationError, match="Этап 2 — начало ветки: коробки других этапов он не берёт"):
        _start(shop, 2, cut_a)
    cut_b = _made(shop, 2, "5")

    with shop.factory() as uow:
        source = uow.materials.source(shop.request_id, shop.line_id)
    assert (cut_a.quantity, cut_b.quantity) == (Decimal(10), Decimal(5))
    assert source.consumed == Decimal(4)
    shop.engine.dispose()


def test_branch_with_two_per_assembly_makes_twice_the_run():
    shop = network_shop()

    assert [operation_quantity(shop.task, item) for item in shop.task.operations] == [
        Decimal(10), Decimal(5), Decimal(5), Decimal(5),
    ]
    _made(shop, 1, "6")
    work = _start(shop, 1)
    with pytest.raises(ProductionValidationError, match=r"уже сдано 6 из 10, осталось 4\."):
        _submit(shop, work, "5")
    cancel_work(SessionCommand(work.id, OPERATOR, NOW), shop.factory())
    with pytest.raises(ProductionValidationError, match=r"уже сдано 0 из 5, осталось 5\."):
        _submit(shop, _start(shop, 2), "6")
    shop.engine.dispose()


def test_assembly_is_refused_until_boxes_of_every_input_are_given():
    shop = network_shop()
    cut_a = _made(shop, 1, "10")
    cut_b = _made(shop, 2, "5")

    with pytest.raises(ProductionValidationError, match=r"Введи номера входных коробок с этапов 1, 2\."):
        _start(shop, 3)
    with pytest.raises(ProductionValidationError, match=r"Нет входных коробок с этапа 2 «Нарезка Б»: операция собирается"):
        _start(shop, 3, cut_a)
    with pytest.raises(ProductionValidationError, match=r"Нет входных коробок с этапа 1 «Нарезка А»"):
        _start(shop, 3, cut_b)
    with pytest.raises(ProductionValidationError, match=r"с этапа 1, а на вход нужен этап 3\."):
        _start(shop, 4, cut_a)

    work = _start(shop, 3, cut_b, cut_a)
    assert work.status is WorkStatus.ACTIVE
    shop.engine.dispose()


def test_assembly_takes_per_unit_pieces_from_each_input_and_refuses_a_short_branch_whole():
    shop = network_shop()
    cut_a = _made(shop, 1, "6")
    cut_b = _made(shop, 2, "5")
    work = _start(shop, 3, cut_a, cut_b)

    with pytest.raises(ProductionValidationError, match=r"С этапа 1 «Нарезка А» во входных коробках 6 шт, а на 4 шт нужно 8 \(2 на одну\)\."):
        _submit(shop, work, "4")
    assert (shop.box(cut_a).consumed, shop.box(cut_b).consumed) == (Decimal(0), Decimal(0))

    assembly, _output = _submit(shop, work, "2", defect="1")

    assert assembly.quantity == Decimal(2)
    assert (shop.box(cut_a).consumed, shop.box(cut_a).status) == (Decimal(6), BoxStatus.CONSUMED)
    assert (shop.box(cut_b).consumed, shop.box(cut_b).available) == (Decimal(3), Decimal(2))
    with shop.factory() as uow:
        _nodes, edges = uow.genealogy.search(assembly.number)
    assert {(edge.source, edge.quantity) for edge in edges if edge.target == f"box:{assembly.id}"} == {
        (f"box:{cut_a.id}", Decimal(6)), (f"box:{cut_b.id}", Decimal(3)),
    }
    shop.engine.dispose()


def test_correcting_an_assembly_box_moves_each_input_by_its_per_unit():
    shop = network_shop()
    cut_a = _made(shop, 1, "10")
    cut_b = _made(shop, 2, "5")
    assembly, output = _submit(shop, _start(shop, 3, cut_a, cut_b), "3")

    returned = return_box(ReturnBoxCommand(
        box_id=assembly.id, expected_version=assembly.version, actor=MASTER, now=NOW, reason="Пересчитать",
    ), shop.factory())
    down, down_output, _defect = correct_box(CorrectBoxCommand(
        box_id=returned.id, expected_version=returned.version, actor=OPERATOR, now=NOW,
        output_id=output.id, good_quantity=Decimal(1), defect_quantity=Decimal(0),
    ), shop.factory())
    assert (shop.box(cut_a).consumed, shop.box(cut_b).consumed) == (Decimal(2), Decimal(1))

    returned = return_box(ReturnBoxCommand(
        box_id=down.id, expected_version=down.version, actor=MASTER, now=NOW, reason="Пересчитать",
    ), shop.factory())
    correct_box(CorrectBoxCommand(
        box_id=returned.id, expected_version=returned.version, actor=OPERATOR, now=NOW,
        output_id=down_output.id, good_quantity=Decimal(4), defect_quantity=Decimal(0),
    ), shop.factory())
    assert (shop.box(cut_a).consumed, shop.box(cut_b).consumed) == (Decimal(8), Decimal(4))
    shop.engine.dispose()


def test_qc_and_finished_goods_take_only_the_final_operation_even_when_it_is_not_the_last_number():
    # Номера — порядок показа: сборка (этап 2) собирает нарезку А (этап 1) и нарезку Б (этап 3) и она же — финал.
    operations = link_inputs(
        (_op(1, "Нарезка А"), _op(2, "Сборка"), _op(3, "Нарезка Б")),
        {2: ((1, Decimal(1)), (3, Decimal(1)))},
    )
    shop = network_shop(operations)
    assert shop.task.final_operation == shop.operation(2)
    good = FinishedGood.create(
        project="591K.3", item_index="КБ1", customer="Заказчик",
        article="", name="Жгут", ordered=Decimal("10"), unit="шт",
    )
    with shop.factory() as uow:
        uow.goods.add(good)
        uow.commit()
    cut_a = _made(shop, 1, "5")
    cut_b = _made(shop, 3, "5")

    with pytest.raises(ProductionValidationError, match="ОТК принимает только выход последней операции"):
        inspect_box(InspectBoxCommand(
            box_id=cut_b.id, expected_version=cut_b.version, accepted=5, rejected=0,
            defect_character="", defect_description="", instrument_ids=(), actor=MASTER, now=NOW,
        ), shop.factory())
    with pytest.raises(ProductionValidationError, match="На склад ГП сдаётся только последняя операция маршрута"):
        handoff_box(HandoffBoxCommand(
            box_id=cut_b.id, expected_version=cut_b.version, actor=MASTER, now=NOW, finished_good_id=good.id,
        ), shop.factory())

    assembly = _made(shop, 2, "5", cut_a, cut_b)
    inspected, _inspection, _serials = inspect_box(InspectBoxCommand(
        box_id=assembly.id, expected_version=assembly.version, accepted=5, rejected=0,
        defect_character="", defect_description="", instrument_ids=(), actor=MASTER, now=NOW,
    ), shop.factory())
    handed, accepted, _receipt = handoff_box(HandoffBoxCommand(
        box_id=inspected.id, expected_version=inspected.version, actor=MASTER, now=NOW, finished_good_id=good.id,
    ), shop.factory())

    assert handed.status is BoxStatus.CONSUMED
    assert accepted.accepted == Decimal(5)
    shop.engine.dispose()


def test_api_offers_boxes_of_every_input_and_marks_sources_inputs_and_the_final(production_api):  # noqa: F811
    client, operator_id = production_api
    factory = app.state.production_uow_factory
    task = launch_production(LaunchProductionCommand(
        idempotency_key="591K.3:КБ1:сеть-api", project="591K.3", item="591K.3.КБ1", name="Жгут",
        quantity=5, fulfillment_request_id=None, deadline=None,
        operations=merge_operations(), actor=MASTER, now=NOW,
    ), factory())
    for operation in task.operations:
        task, _ = assign_production(AssignProductionCommand(
            task_id=task.id, expected_version=task.version, operation_id=operation.id,
            operator_ids=(operator_id,), requested_start=None, actor=MASTER, now=NOW,
        ), factory())
    by_position = {item.position: item for item in task.operations}
    boxes = []
    for position, quantity, sequence in ((1, "10", 104233), (2, "5", 104234)):
        box = ProductionBox.create(
            task_id=task.id, operation_id=by_position[position].id, item=task.item,
            operation_position=position, operator_id=operator_id, created_at=NOW,
        ).add_output(Decimal(quantity)).close(format_box_number(sequence), "Оператор", NOW).confirm("Мастер", NOW)
        boxes.append(box)
    with factory() as uow:
        for box in boxes:
            uow.boxes.save(box)
        uow.commit()
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"staff"}), permissions={"production": "view"},
    )

    assembly_inputs = client.get(f"/api/production/tasks/{task.id}/operations/{by_position[3].id}/inputs")
    marking_inputs = client.get(f"/api/production/tasks/{task.id}/operations/{by_position[4].id}/inputs")
    mine = client.get("/api/production/my")

    assert assembly_inputs.status_code == 200, assembly_inputs.text
    assert sorted(box["number"] for box in assembly_inputs.json()["boxes"]) == [box.number for box in boxes]
    assert marking_inputs.json()["boxes"] == []
    rows = {row["operations"][0]["position"]: row["operations"][0] for row in mine.json()["tasks"]}
    assert {position: row["first"] for position, row in rows.items()} == {1: True, 2: True, 3: False, 4: False}
    assert {position: row["final"] for position, row in rows.items()} == {1: False, 2: False, 3: False, 4: True}
    assert {position: row["required_quantity"] for position, row in rows.items()} == {1: "10", 2: "5", 3: "5", 4: "5"}
    assert rows[3]["inputs"] == [
        {"operation_id": str(by_position[1].id), "position": 1, "per_unit": "2", "transfer_batch": None},
        {"operation_id": str(by_position[2].id), "position": 2, "per_unit": "1", "transfer_batch": None},
    ]


def _confirmed(position: int, quantity: str, sequence: int) -> ProductionBox:
    return ProductionBox.create(
        task_id=uuid4(), operation_id=uuid4(), item="КБ1", operation_position=position,
        operator_id=uuid4(), created_at=NOW,
    ).add_output(Decimal(quantity)).close(format_box_number(sequence), "Оператор", NOW).confirm("Мастер", NOW)


def test_box_of_a_stage_outside_the_inputs_names_every_allowed_stage():
    box = _confirmed(3, "5", 104233)

    assert box.input_error(item="КБ1", previous_position=(1, 2)) == (
        "Коробка 104233-2 — с этапа 3, а на вход нужны этапы 1, 2."
    )
    assert box.input_error(item="КБ1", previous_position=(2, 3)) == ""
    with pytest.raises(ProductionValidationError, match="не к предыдущему этапу"):
        box.reserve(Decimal(1), item="КБ1", previous_position=(1, 2))


def test_take_and_correction_moves_scale_each_input_by_its_per_unit():
    first, second = _confirmed(1, "10", 104233), _confirmed(2, "4", 104234)
    stages = (InputStage(uuid4(), 1, "Нарезка А", Decimal(2)), InputStage(uuid4(), 2, "Нарезка Б", Decimal(1)))

    assert take_inputs(stages, (second, first), Decimal(3)) == (
        WorkInput(first.id, Decimal(6)), WorkInput(second.id, Decimal(3)),
    )
    taken = (WorkInput(first.id, Decimal(6)), WorkInput(second.id, Decimal(3)))
    known = {first.id: first, second.id: second}
    assert correction_moves(stages, taken, known, Decimal(-1)) == (
        WorkInput(first.id, Decimal(2)), WorkInput(second.id, Decimal(1)),
    )
    assert correction_moves(stages, taken, known, Decimal(2)) == (
        WorkInput(first.id, Decimal(4)), WorkInput(second.id, Decimal(2)),
    )
