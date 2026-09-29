from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import (
    AssignProductionCommand,
    LaunchProductionCommand,
    assign_production,
    launch_production,
)
from app.modules.production.domain import ProductionOperation
from app.modules.production.execution import (
    BoxCommand,
    DecideDefectCommand,
    SessionCommand,
    StartWorkCommand,
    SubmitWorkCommand,
    cancel_work,
    confirm_box,
    decide_defect,
    start_work,
    submit_work,
)
from app.modules.production.infrastructure import (
    ProductionConsumptionRow,
    ProductionOutputRow,
    SqlAlchemyProductionUnitOfWork,
)
from app.modules.production.traceability import BoxStatus, DefectDisposition, DefectStatus, ProductionBox, WorkInput, WorkStatus, format_box_number
from app.shared.persistence import create_schema
from tests.factories import Actor


def execution_setup():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    operator_id = UUID("10000000-0000-0000-0000-000000000002")
    with sessions() as session:
        session.add(IdentityUserRow(
            id=operator_id, login="operator", email="operator@example.test", name="Оператор",
            department="Производство", role="staff", site="ЦАР", active=True,
        ))
        session.commit()
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    master = Actor(name="Мастер", department="Производство")
    operator = Actor(id=operator_id, name="Оператор", department="Производство")
    now = datetime(2026, 9, 22, 9, 0)
    operations = tuple(ProductionOperation.create(
        position=position, code=f"OP-{position:02d}", stage=stage,
        description="", seconds_per_unit=Decimal("10"),
        setup_seconds=Decimal("0"), quantity=10,
    ) for position, stage in ((1, "Подготовка"), (2, "Сборка")))
    task = launch_production(LaunchProductionCommand(
        idempotency_key="execution-v1", project="591K.3", item="КБ-1", name="Жгут",
        quantity=10, fulfillment_request_id=None, deadline=None,
        operations=operations, actor=master, now=now,
    ), factory())
    for operation in task.operations:
        task, _ = assign_production(AssignProductionCommand(
            task_id=task.id, expected_version=task.version, operation_id=operation.id,
            operator_ids=(operator_id,), requested_start=now,
            actor=master, now=now,
        ), factory())
    source = ProductionBox.create(
        task_id=task.id, operation_id=task.operations[0].id, item=task.item,
        operation_position=1, operator_id=operator_id, created_at=now,
    ).add_output(Decimal("10"))
    source = source.close(format_box_number(104233), operator.name, now).confirm(master.name, now)
    with factory() as uow:
        uow.boxes.save(source)
        uow.commit()
    return engine, sessions, factory, master, operator, task, source, now


def test_submission_closes_a_new_box_and_takes_pieces_from_the_input_box():
    engine, sessions, factory, master, operator, task, source, now = execution_setup()
    operation = task.operations[1]
    work = start_work(StartWorkCommand(
        task_id=task.id, operation_id=operation.id,
        box_numbers=(source.number,), actor=operator, now=now,
    ), factory())
    completed, output_box, output, defect = submit_work(SubmitWorkCommand(
        session_id=work.id, actor=operator, now=now + timedelta(minutes=10),
        good_quantity=Decimal("9"), defect_quantity=Decimal("1"),
        defect_character="Повреждение изоляции", defect_description="Надрез у контакта",
    ), factory())
    # prodWorkSubmit_: одна сдача — одна коробка, сразу «Закрыта оператором» с номером.
    assert output_box.status is BoxStatus.OPERATOR_CLOSED
    assert output_box.number == format_box_number(1)
    assert output_box.quantity == Decimal("9")
    confirmed = confirm_box(BoxCommand(output_box.id, output_box.version, master, now), factory())

    with factory() as uow:
        stored_source = uow.boxes.get(source.id)
        stored_output = uow.boxes.get(output_box.id)
    with sessions() as session:
        outputs = session.scalar(select(func.count()).select_from(ProductionOutputRow))
        edges = session.scalar(select(func.count()).select_from(ProductionConsumptionRow))

    assert completed.status is WorkStatus.COMPLETED
    assert output.good_quantity == Decimal("9")
    assert defect.number.startswith("Б-")
    assert stored_source.status is BoxStatus.CONSUMED
    assert stored_source.number == format_box_number(104233)
    assert stored_output.status is BoxStatus.CONFIRMED
    assert confirmed.number == format_box_number(1)
    assert outputs == 1
    assert edges == 1
    decided, decision = decide_defect(DecideDefectCommand(
        defect_id=defect.id, expected_version=defect.version,
        disposition=DefectDisposition.SCRAP, act_reference="Акт НС-17",
        actor=master, now=now,
    ), factory())
    assert decided.status is DefectStatus.DECIDED
    assert decision.disposition is DefectDisposition.SCRAP
    engine.dispose()


def test_starting_and_cancelling_work_reserves_nothing_in_input_boxes():
    engine, _sessions, factory, _master, operator, task, source, now = execution_setup()
    work = start_work(StartWorkCommand(
        task_id=task.id, operation_id=task.operations[1].id,
        box_numbers=(source.number,), actor=operator, now=now,
    ), factory())
    cancelled = cancel_work(SessionCommand(work.id, operator, now), factory())

    with factory() as uow:
        stored_source = uow.boxes.get(source.id)

    assert cancelled.status is WorkStatus.CANCELLED
    assert stored_source.reserved == Decimal("0")
    assert stored_source.available == Decimal("10")
    engine.dispose()
