"""Откат тестовых нажатий «за сотрудника» на проде (app/workflows/test_rollback.py, владелец 13.10.2026)."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.fulfillment.domain import FulfillmentStatus
from app.modules.fulfillment.infrastructure import FulfillmentLineRow, FulfillmentRequestRow
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import (
    AssignProductionCommand,
    LaunchProductionCommand,
    SetProductionCancelledCommand,
    assign_production,
    launch_production,
    set_production_cancelled,
)
from app.modules.production.domain import ProductionOperation
from app.modules.production.downtime_infrastructure import ProductionDowntimeRow
from app.modules.production.execution import (
    BoxCommand,
    SessionCommand,
    StartWorkCommand,
    SubmitWorkCommand,
    confirm_box,
    pause_work,
    resume_work,
    start_work,
    submit_work,
)
from app.modules.production.infrastructure import (
    ProductionBoxRow,
    ProductionDefectRow,
    ProductionMaterialConsumptionRow,
    ProductionOperationOperatorRow,
    ProductionOutputRow,
    ProductionPlanEventRow,
    ProductionTaskRow,
    ProductionWorkSessionRow,
    ProductionWorkTimeLogRow,
    SqlAlchemyProductionUnitOfWork,
)
from app.modules.production.traceability import BoxStatus, MaterialInput
from app.shared.clock import MOSCOW
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.test_rollback import RollbackError, parse_moment, run

OWNER = Actor(UUID("20000000-0000-0000-0000-000000000001"), "Артемий Васильев", "Администрация")
ELINA = Actor(UUID("20000000-0000-0000-0000-000000000002"), "Богородская Элина", "Производство")
MATE = Actor(UUID("20000000-0000-0000-0000-000000000003"), "Напарник", "Производство")
MASTER = Actor(UUID("20000000-0000-0000-0000-000000000004"), "Мастер", "Производство")
SINCE = datetime(2026, 9, 23, 10, 0, tzinfo=MOSCOW)
UNTIL = datetime(2026, 9, 23, 18, 0, tzinfo=MOSCOW)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 23, hour, minute)


class Shop:
    """Цех: задание из двух этапов (1 — с комплекта, 2 — из коробок этапа 1), на обоих Элина и Напарник."""

    def __init__(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        create_schema(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)
        self.request_id, self.line_id = uuid4(), uuid4()
        with self.sessions() as session:
            session.add_all([
                IdentityUserRow(id=OWNER.id, login="artemiy", email="owner@example.test", name=OWNER.name, department=OWNER.department, role="admin", site="", active=True),
                IdentityUserRow(id=ELINA.id, login="elina", email="elina@example.test", name=ELINA.name, department=ELINA.department, role="staff", site="ЦАР", active=True, shop_worker=True),
                IdentityUserRow(id=MATE.id, login="mate", email="mate@example.test", name=MATE.name, department=MATE.department, role="staff", site="ЦАР", active=True, shop_worker=True),
                IdentityUserRow(id=MASTER.id, login="master", email="master@example.test", name=MASTER.name, department=MASTER.department, role="master", site="ЦАР", active=True, site_master=True),
            ])
            session.add(FulfillmentRequestRow(
                id=self.request_id, number="REQ.26.0007", source_idempotency_key="kit-180", source_fingerprint="f" * 64,
                version=1, created_at=at(8), requester="Инженер", purpose="180К.11", deadline=None,
                status=FulfillmentStatus.ISSUED.value, handout_location=None, return_location=None,
            ))
            session.add(FulfillmentLineRow(
                id=self.line_id, request_id=self.request_id, position=0, requirement_id=self.line_id,
                article="WIRE-1", name="Провод", unit="м", requirement=Decimal("10"), reserved=Decimal("0"),
                issued=Decimal("10"), returned_good=Decimal("0"), defective_returned=Decimal("0"),
                defective_missing=Decimal("0"), lot_id=uuid4(), lot_warehouse="01", lot_grn="PPM.26.0000001",
                lot_package="П1", lot_storage_location="A-01",
            ))
            session.commit()
        self.factory = lambda: SqlAlchemyProductionUnitOfWork(self.sessions)
        operations = tuple(ProductionOperation.create(
            position=position, code=code, stage=stage, description="", seconds_per_unit=Decimal("10"),
            setup_seconds=Decimal("0"), quantity=10,
        ) for position, code, stage in ((1, "CUT_WIRE+2crimp", "Автомат резка + опрессовка"), (2, "ASSEMBLY", "Сборка")))
        task = launch_production(LaunchProductionCommand(
            idempotency_key="pz-0007", project="180К.11", item="КБ1", name="Жгут", quantity=10,
            fulfillment_request_id=self.request_id, deadline=None, operations=operations, actor=MASTER, now=at(9),
        ), self.factory())
        for operation in task.operations:
            task, _ = assign_production(AssignProductionCommand(
                task_id=task.id, expected_version=task.version, operation_id=operation.id,
                operator_ids=(ELINA.id, MATE.id), requested_start=at(9), actor=MASTER, now=at(9),
            ), self.factory())
        self.task = task
        self.cut, self.assembly = task.operations

    # Нажатия: `by` — кто нажал, `worker` — за кого (None — сам).
    def start(self, operation, by, worker=None, *, when, boxes=(), material=None):
        return start_work(StartWorkCommand(
            task_id=self.task.id, operation_id=operation.id, box_numbers=tuple(boxes), actor=by, now=when,
            materials=(MaterialInput(self.line_id, Decimal(material)),) if material else (), performer=worker,
        ), self.factory())

    def pause(self, work, by, worker=None, *, when):
        return pause_work(SessionCommand(work.id, by, when, performer=worker), self.factory())

    def resume(self, work, by, worker=None, *, when):
        return resume_work(SessionCommand(work.id, by, when, performer=worker), self.factory())

    def submit(self, work, by, worker=None, *, when, good, defect="0"):
        return submit_work(SubmitWorkCommand(
            session_id=work.id, actor=by, now=when, good_quantity=Decimal(good), defect_quantity=Decimal(defect),
            defect_character="Обрыв" if Decimal(defect) else "", defect_description="Порван провод" if Decimal(defect) else "",
            performer=worker,
        ), self.factory())[1]

    def confirm(self, box, by, *, when):
        return confirm_box(BoxCommand(box.id, box.version, by, when), self.factory())

    def rows(self, model, *where):
        with self.sessions() as session:
            return list(session.scalars(select(model).where(*where)))

    def share(self, operation, operator_id):
        with self.sessions() as session:
            return session.get(ProductionOperationOperatorRow, (operation.id, operator_id))

    def rollback(self, *, apply, actor="owner@example.test", employee="Богородская", now=at(18)):
        return run(self.sessions, since=SINCE, until=UNTIL, actor=actor, employee=employee, apply=apply, now=now)


@pytest.fixture
def shop():
    shop = Shop()
    yield shop
    shop.engine.dispose()


def _real_work_before_the_test(shop):
    """Напарник до окна сдал 4 шт с комплекта; мастер подтвердил коробку."""
    work = shop.start(shop.cut, MATE, when=at(9, 10), material="2")
    box = shop.submit(work, MATE, when=at(9, 40), good="4")
    return shop.confirm(box, MASTER, when=at(9, 45))


def test_rollback_removes_only_the_owner_test_for_the_employee(shop):
    real_box = _real_work_before_the_test(shop)
    # Тест: владелец за Элину — начал (с комплектом), пауза, продолжил, сдал с браком; потом этап 2 из коробки
    # Напарника; простой; всё вперемешку с настоящей работой Напарника.
    work = shop.start(shop.cut, OWNER, ELINA, when=at(10, 5), material="3")
    shop.pause(work, OWNER, ELINA, when=at(10, 10))
    shop.resume(work, OWNER, ELINA, when=at(10, 15))
    real = shop.start(shop.cut, MATE, when=at(10, 20), material="1")
    shop.pause(real, MATE, when=at(10, 25))
    test_box = shop.submit(work, OWNER, ELINA, when=at(10, 30), good="3", defect="1")
    second = shop.start(shop.assembly, OWNER, ELINA, when=at(10, 35), boxes=(real_box.number,))
    shop.submit(second, OWNER, ELINA, when=at(10, 50), good="2")
    with shop.sessions() as session:
        session.add(ProductionDowntimeRow(
            id=uuid4(), task_id=shop.task.id, operation_id=shop.cut.id, operator_id=ELINA.id, operator_name=ELINA.name,
            reason="no_material", comment="", started_at=at(10, 40), started_by_id=OWNER.id, started_by_name=OWNER.name,
            project="180К.11", task_number=shop.task.number, item="КБ1", stage="", ended_at=at(10, 45),
            ended_by_id=OWNER.id, ended_by_name=OWNER.name,
        ))
        session.commit()
    assert shop.share(shop.cut, ELINA.id).started_at is not None
    version = shop.rows(ProductionTaskRow)[0].version

    text, code = shop.rollback(apply=False)

    assert code == 0, text
    assert "Будет отменено" in text and "Проверка без записи" in text
    assert shop.task.number in text and "Богородская Элина" in text
    assert f"коробка {test_box.number}" in text and f"Входная коробка {real_box.number}: вернуть 2 шт" in text
    assert "Комплект: WIRE-1 Провод — расход 3 м" in text and "Простой" in text
    assert len(shop.rows(ProductionWorkSessionRow)) == 4  # проверка ничего не пишет

    text, code = shop.rollback(apply=True)

    assert code == 0, text
    assert "Откачено" in text
    # Работа Элины (тест) ушла целиком; работа Напарника — до окна и в окне — осталась.
    sessions = shop.rows(ProductionWorkSessionRow)
    assert {row.operator_id for row in sessions} == {MATE.id} and len(sessions) == 2
    assert {row.operator_id for row in shop.rows(ProductionWorkTimeLogRow)} == {MATE.id}
    assert len(shop.rows(ProductionWorkTimeLogRow)) == 4
    assert [row.operator_id for row in shop.rows(ProductionOutputRow)] == [MATE.id]
    boxes = shop.rows(ProductionBoxRow)
    assert [row.number for row in boxes] == [real_box.number]
    assert boxes[0].consumed == 0 and boxes[0].status == BoxStatus.CONFIRMED.value
    assert shop.rows(ProductionDefectRow) == []
    assert [row.quantity for row in shop.rows(ProductionMaterialConsumptionRow)] == [Decimal("2")]
    assert shop.rows(ProductionDowntimeRow) == []
    # «Взял» Элины снят на обоих этапах, у Напарника — остался; версия задания выросла (экраны перечитают).
    assert shop.share(shop.cut, ELINA.id).started_at is None
    assert shop.share(shop.assembly, ELINA.id).started_at is None
    assert shop.share(shop.cut, MATE.id).started_at is not None
    assert shop.rows(ProductionTaskRow)[0].version > version
    event = shop.rows(AuditEventRow, AuditEventRow.event_type == "test_rollback")
    assert len(event) == 1 and event[0].details["employee"] == ELINA.name
    assert any(test_box.number in line for line in event[0].details["lines"])
    assert event[0].details["audit_ids"]
    # Повтор ничего не откатывает дважды.
    again, code = shop.rollback(apply=True)
    assert code == 0 and "Откатывать нечего" in again
    assert len(shop.rows(AuditEventRow, AuditEventRow.event_type == "test_rollback")) == 1


def test_a_later_submission_by_someone_else_on_the_same_operation_refuses(shop):
    work = shop.start(shop.cut, OWNER, ELINA, when=at(10, 5))
    shop.submit(work, OWNER, ELINA, when=at(10, 30), good="3")
    real = shop.start(shop.cut, MATE, when=at(10, 35))
    shop.submit(real, MATE, when=at(10, 45), good="5")

    text, code = shop.rollback(apply=True)

    assert code == 1
    assert "ОТКАЗ" in text and "сдал(а) Напарник" in text
    assert len(shop.rows(ProductionWorkSessionRow)) == 2  # ничего не тронуто
    assert len(shop.rows(ProductionBoxRow)) == 2
    assert shop.rows(AuditEventRow, AuditEventRow.event_type == "test_rollback") == []


def test_a_test_box_confirmed_and_taken_by_others_refuses(shop):
    work = shop.start(shop.cut, OWNER, ELINA, when=at(10, 5))
    box = shop.submit(work, OWNER, ELINA, when=at(10, 30), good="4")
    box = shop.confirm(box, MASTER, when=at(10, 40))
    taken = shop.start(shop.assembly, MATE, when=at(10, 45), boxes=(box.number,))
    shop.submit(taken, MATE, when=at(11), good="2")

    text, code = shop.rollback(apply=False)

    assert code == 1
    assert "Мастер подтвердил(а) её" in text
    assert "уже взята в работу: Напарник" in text
    assert len(shop.rows(ProductionBoxRow)) == 2


def test_a_session_started_before_the_window_is_not_cut_in_half(shop):
    work = shop.start(shop.cut, ELINA, when=at(9, 30))  # сама Элина, до окна
    shop.pause(work, OWNER, ELINA, when=at(10, 10))  # тестирующий нажал паузу в её работе

    text, code = shop.rollback(apply=True)

    assert code == 1 and "до окна" in text
    assert len(shop.rows(ProductionWorkTimeLogRow)) == 2


def test_the_employee_pressing_in_the_test_work_refuses(shop):
    work = shop.start(shop.cut, OWNER, ELINA, when=at(10, 5))
    shop.pause(work, ELINA, when=at(10, 20))  # Элина сама продолжила тестовую работу

    text, code = shop.rollback(apply=False)

    assert code == 1 and "потом нажал(а) Богородская Элина" in text


def test_without_actor_the_testers_are_whoever_acted_for_the_employee(shop):
    work = shop.start(shop.cut, OWNER, ELINA, when=at(10, 5))
    own = shop.start(shop.cut, MATE, when=at(10, 6))  # сам за себя — не тест

    text, code = shop.rollback(apply=True, actor=None, employee="elina")

    assert code == 0, text
    assert "нажимал(и) Артемий Васильев" in text
    assert [row.id for row in shop.rows(ProductionWorkSessionRow)] == [own.id]
    assert work.id not in {row.session_id for row in shop.rows(ProductionWorkTimeLogRow)}


def test_task_status_changed_by_the_tester_goes_back_from_the_audit(shop):
    task = shop.rows(ProductionTaskRow)[0]
    set_production_cancelled(SetProductionCancelledCommand(shop.task.id, task.version, True, OWNER, at(10, 30)), shop.factory())

    text, code = shop.rollback(apply=True, employee=None)

    assert code == 0, text
    assert "«Отменено» → снова «Запущено»" in text
    assert shop.rows(ProductionTaskRow)[0].status == "Запущено"


def test_a_status_changed_again_by_someone_else_is_not_reverted(shop):
    task = shop.rows(ProductionTaskRow)[0]
    cancelled = set_production_cancelled(SetProductionCancelledCommand(shop.task.id, task.version, True, OWNER, at(10, 30)), shop.factory())
    set_production_cancelled(SetProductionCancelledCommand(shop.task.id, cancelled.version, False, MASTER, at(11)), shop.factory())

    text, code = shop.rollback(apply=False, employee=None)

    assert code == 1 and "Мастер вернул(а) отменённое задание" in text


def test_bad_parameters_get_a_russian_message(shop):
    assert shop.rollback(apply=False, actor=None, employee=None)[1] == 2
    text, code = shop.rollback(apply=False, actor="никто")
    assert code == 2 and "не найден" in text
    assert parse_moment("13.10.2026 16:00") == datetime(2026, 10, 13, 16, 0, tzinfo=MOSCOW)
    with pytest.raises(RollbackError):
        parse_moment("вчера")


def test_a_manual_stage_change_by_the_tester_is_taken_back(shop):
    def journal(session, sequence, who, when, value, before):
        row = ProductionPlanEventRow(
            id=uuid4(), project="180К.11", sequence=sequence, occurred_at=when, who=who, kind="Этап КБ", blocker="",
            text=f"{before} → {value}", deadline=None, item_index="КБ1", value=value, field="", from_value=before, reason="",
        )
        session.add(row)
        return row

    with shop.sessions() as session:
        journal(session, 1, MASTER.name, at(9), "В работе", "Распланировано")
        row = journal(session, 2, OWNER.name, at(10, 30), "ОТК", "В работе")
        session.add(AuditEventRow(
            id=uuid4(), aggregate_id=row.id, event_type="production.stage_changed", actor_id=OWNER.id,
            actor_name=OWNER.name, actor_department="", occurred_at=at(10, 30),
            details={"project": "180К.11", "value": "ОТК", "changes": ["КБ1: В работе → ОТК"]},
        ))
        session.commit()

    text, code = shop.rollback(apply=True, employee=None)

    assert code == 0, text
    assert "Этап КБ 180К.11" in text
    assert [(row.who, row.value) for row in shop.rows(ProductionPlanEventRow)] == [(MASTER.name, "В работе")]
