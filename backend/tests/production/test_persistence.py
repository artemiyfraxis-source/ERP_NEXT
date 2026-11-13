from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.workflows.test_production_intake import _task


@pytest.fixture
def project_lock_setup(monkeypatch):
    from app.modules.production import infrastructure
    from app.shared.advisory_lock import lock_project_run

    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    calls = []

    def locked(session, project):
        calls.append((session, project))
        lock_project_run(session, project)

    # Spy at the repository boundary; SQLite must continue to use the real no-op helper.
    monkeypatch.setattr(infrastructure, "lock_project_run", locked)
    try:
        yield sessions, calls, engine
    finally:
        engine.dispose()


def test_task_creation_locks_project_before_resource_or_database_writes(project_lock_setup):
    from app.modules.production.infrastructure import SqlAlchemyProductionRepository

    sessions, calls, engine = project_lock_setup
    task = _task("591K.3", "КБ1", 10, number="ПЗ-LOCK")
    with sessions() as session:
        class Resources:
            def record_task(self, task):
                assert calls == [(session, "591К.3")]

        @event.listens_for(engine, "before_cursor_execute")
        def assert_locked(connection, cursor, statement, parameters, context, executemany):
            if context.isinsert or context.isupdate or context.isdelete:
                assert calls == [(session, "591К.3")]

        SqlAlchemyProductionRepository(session, Resources()).add(task)
        session.commit()
        assert calls == [(session, "591К.3")]


@pytest.mark.parametrize("cancelled", [True, False])
def test_cancel_and_restore_take_project_lock(project_lock_setup, cancelled):
    sessions, calls, engine = project_lock_setup
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    task = _task("591К.3", "КБ1", 10, number="ПЗ-LOCK")
    if not cancelled:
        task = task.set_cancelled(True)
    with factory() as uow:
        uow.production.add(task)
        uow.commit()
    calls.clear()

    @event.listens_for(engine, "before_cursor_execute")
    def assert_locked(connection, cursor, statement, parameters, context, executemany):
        if context.isinsert or context.isupdate or context.isdelete:
            assert len(calls) == 1 and calls[0][1] == "591К.3"

    updated = set_production_cancelled(SetProductionCancelledCommand(
        task.id, task.version, cancelled, Actor(name="Мастер", department="Производство"), task.created_at,
    ), factory())
    assert len(calls) == 1 and calls[0][1] == "591К.3"
    with factory() as uow:
        assert uow.production.get(task.id) == updated


def test_task_move_locks_old_and_new_projects_in_order(project_lock_setup):
    from app.modules.production.infrastructure import SqlAlchemyProductionRepository

    sessions, calls, _ = project_lock_setup
    task = _task("592К.1", "КБ1", 10, number="ПЗ-LOCK")
    with sessions() as session:
        repository = SqlAlchemyProductionRepository(session)
        repository.add(task)
        session.commit()
        calls.clear()
        repository.add(replace(task, project="591K.3", item="КБ2"))
        assert calls == [(session, "591К.3"), (session, "592К.1")]
        session.commit()


def test_task_row_lock_takes_project_lock_first(project_lock_setup, monkeypatch):
    from app.modules.production.infrastructure import SqlAlchemyProductionRepository

    sessions, calls, _ = project_lock_setup
    task = _task("591К.3", "КБ1", 10, number="ПЗ-LOCK")
    with sessions() as session:
        repository = SqlAlchemyProductionRepository(session)
        repository.add(task)
        session.commit()
        calls.clear()
        scalar = session.scalar

        def assert_project_locked(statement, *args, **kwargs):
            assert calls == [(session, "591К.3")]
            return scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", assert_project_locked)
        repository.lock(task.id)
        assert calls == [(session, "591К.3")]


def test_task_deletion_locks_project_before_database_writes(project_lock_setup):
    from app.modules.production.infrastructure import SqlAlchemyProductionRepository

    sessions, calls, engine = project_lock_setup
    task = _task("591К.3", "КБ1", 10, number="ПЗ-LOCK")
    with sessions() as session:
        repository = SqlAlchemyProductionRepository(session)
        repository.add(task)
        session.commit()
        calls.clear()

        @event.listens_for(engine, "before_cursor_execute")
        def assert_locked(connection, cursor, statement, parameters, context, executemany):
            if context.isinsert or context.isupdate or context.isdelete:
                assert calls == [(session, "591К.3")]

        repository.delete(task.id)
        session.commit()
        assert calls == [(session, "591К.3")]
        assert repository.get(task.id) is None


def test_task_route_assignment_and_calendar_persist_together():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    operator_id = UUID("10000000-0000-0000-0000-000000000001")
    with sessions() as session:
        session.add(IdentityUserRow(
            id=operator_id, login="operator", email="operator@example.test", name="Оператор",
            department="Производство", role="staff", site="ЦАР", active=True, shop_worker=True,
        ))
        session.commit()
    actor = Actor(name="Мастер", department="Производство")
    now = datetime(2026, 9, 22, 9, 0)
    created = launch_production(LaunchProductionCommand(
        idempotency_key="project:591K.3:KB-1:v1", project="591K.3", item="591K.3.КБ1",
        name="Жгут", quantity=10, fulfillment_request_id=None, deadline=date(2026, 10, 1),
        operations=(ProductionOperation.create(
            position=1, code="OP-01", stage="Разделка", description="Подготовить кабель",
            seconds_per_unit=Decimal("60"), setup_seconds=Decimal("120"), quantity=10,
        ),), actor=actor, now=now,
    ), factory())
    assigned, _ = assign_production(AssignProductionCommand(
        task_id=created.id, expected_version=created.version,
        operation_id=created.operations[0].id, operator_ids=(operator_id,),
        requested_start=None, actor=actor, now=now,
    ), factory())

    with factory() as uow:
        stored = uow.production.get(created.id)

    assert stored == assigned
    assert stored.number == "ПЗ-0001"
    assert stored.operations[0].operator_ids == (operator_id,)
    assert stored.operations[0].planned_finish == datetime(2026, 9, 22, 9, 14)
    engine.dispose()
