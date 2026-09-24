from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.fulfillment.infrastructure import FulfillmentLineRow, FulfillmentRequestRow
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import AssignProductionCommand, LaunchProductionCommand, assign_production, launch_production
from app.modules.production.domain import ProductionOperation
from app.modules.production.execution import HandoffBoxCommand, StartWorkCommand, SubmitWorkCommand, handoff_box, start_work, submit_work
from app.modules.production.infrastructure import ProductionMaterialConsumptionRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.quality import InspectBoxCommand, inspect_box
from app.modules.production.traceability import BoxStatus, MaterialInput, ProductionBox, format_box_number
from app.modules.shipping.domain import FinishedGood
from app.shared.persistence import create_schema
from tests.factories import Actor


def stage_three_setup(*, fulfillment: bool):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    operator_id = UUID("10000000-0000-0000-0000-000000000002")
    request_id = uuid4() if fulfillment else None
    line_id = uuid4() if fulfillment else None
    now = datetime(2026, 9, 23, 9, 0)
    with sessions() as session:
        session.add(IdentityUserRow(
            id=operator_id, login="operator", email="operator@example.test", name="Оператор",
            department="Производство", role="staff", site="ЦАР", active=True,
        ))
        if request_id and line_id:
            session.add(FulfillmentRequestRow(
                id=request_id, number="REQ.26.0001", source_idempotency_key="kit-v1",
                source_fingerprint="f" * 64, version=1, created_at=now,
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
    master = Actor(name="Мастер", department="Производство")
    operator = Actor(id=operator_id, name="Оператор", department="Производство")
    operation = ProductionOperation.create(
        position=1, code="OP-01", stage="Сборка", description="",
        seconds_per_unit=Decimal("10"), setup_seconds=Decimal("0"), quantity=10,
    )
    task = launch_production(LaunchProductionCommand(
        idempotency_key=f"task-{request_id}", project="591K.3", item="591K.3.КБ1",
        name="Жгут", quantity=10, fulfillment_request_id=request_id, deadline=None,
        operations=(operation,), actor=master, now=now,
    ), factory())
    task, _ = assign_production(AssignProductionCommand(
        task_id=task.id, expected_version=task.version, operation_id=operation.id,
        operator_ids=(operator_id,), requested_start=now, actor=master, now=now,
    ), factory())
    return engine, sessions, factory, master, operator, task, line_id, now


def test_first_operation_consumes_issued_grn_and_package_into_output_box():
    engine, sessions, factory, _master, operator, task, line_id, now = stage_three_setup(fulfillment=True)
    work = start_work(StartWorkCommand(
        task_id=task.id, operation_id=task.operations[0].id, inputs=(), actor=operator, now=now,
        materials=(MaterialInput(line_id, Decimal("3")),),
    ), factory())
    _completed, box, _output, _defect = submit_work(SubmitWorkCommand(
        session_id=work.id, actor=operator, now=now,
        good_quantity=Decimal("2"), defect_quantity=Decimal("0"),
    ), factory())

    with factory() as uow:
        source = uow.materials.source(task.fulfillment_request_id, line_id)
    with sessions() as session:
        edges = session.scalar(select(func.count()).select_from(ProductionMaterialConsumptionRow))

    assert box.quantity == Decimal("2")
    assert source.grn == "PPM.26.0000001"
    assert source.package == "П1"
    assert source.consumed == Decimal("3")
    assert source.available == Decimal("7")
    assert edges == 1
    engine.dispose()


def test_last_confirmed_box_is_handed_to_matching_finished_good_grn_once():
    engine, _sessions, factory, master, operator, task, _line_id, now = stage_three_setup(fulfillment=False)
    box = ProductionBox.create(
        task_id=task.id, operation_id=task.operations[0].id, item=task.item,
        operation_position=1, operator_id=operator.id, created_at=now,
    ).add_output(Decimal("5"))
    box = box.close(format_box_number(1), operator.name, now).confirm(master.name, now)
    good = FinishedGood.create(
        project="591K.3", item_index="КБ1", customer="Заказчик",
        article="", name="Жгут", ordered=Decimal("10"), unit="шт",
    )
    with factory() as uow:
        uow.boxes.save(box)
        uow.goods.add(good)
        uow.commit()

    box, _inspection, _serials = inspect_box(InspectBoxCommand(
        box_id=box.id, expected_version=box.version, accepted=5, rejected=0,
        defect_character="", defect_description="", instrument_ids=(),
        actor=master, now=now,
    ), factory())

    handed_box, accepted, receipt = handoff_box(HandoffBoxCommand(
        box_id=box.id, expected_version=box.version, actor=master,
        now=now, finished_good_id=good.id,
    ), factory())

    assert handed_box.status is BoxStatus.CONSUMED
    assert accepted.accepted == Decimal("5")
    assert accepted.grn == "ГП.26.0000001"
    assert receipt.grn == accepted.grn
    assert receipt.source_box_id == box.id
    engine.dispose()
