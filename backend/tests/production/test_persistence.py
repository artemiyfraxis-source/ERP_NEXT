from dataclasses import replace
from datetime import date, datetime, timedelta
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
from tests.production.test_autoplan_api import client  # noqa: F401 — HTTP fixture
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


def _watch_batch_locks(monkeypatch, engine, expected):
    from app.modules.production import infrastructure
    from app.shared.advisory_lock import lock_project_run

    calls = []
    lock_task = infrastructure.SqlAlchemyProductionRepository.lock

    def locked(session, project):
        calls.append(project)
        lock_project_run(session, project)

    def task_locked(repository, task_id):
        assert calls[:len(expected)] == expected
        return lock_task(repository, task_id)

    @event.listens_for(engine, "before_cursor_execute")
    def before_write(connection, cursor, statement, parameters, context, executemany):
        if context.isinsert or context.isupdate or context.isdelete:
            assert calls[:len(expected)] == expected

    monkeypatch.setattr(infrastructure, "lock_project_run", locked)
    monkeypatch.setattr(infrastructure.SqlAlchemyProductionRepository, "lock", task_locked)
    return calls


@pytest.mark.parametrize("batch", ["autoplan", "board"])
@pytest.mark.parametrize("sources", ["existing", "future", "mixed"])
def test_batch_locks_projects_before_task_rows_and_writes(client, monkeypatch, batch, sources):
    from app.main import app
    from tests.production.test_autoplan_api import ANNA, NOW, apply, issue, preview
    from tests.production.test_production_board import assign

    http, sessions = client
    items = []
    # UUID and input order are B, A; project-name order must still be A, B.
    for position, project in enumerate(("592К.1", "591К.3"), 1):
        if sources == "future" or sources == "mixed" and position == 2:
            card = issue("КБ1", project=project)
            items.append({"techcard_id": str(card.id), "techcard_version": card.version, "position": 1})
        else:
            task = replace(_task(project, "КБ1", 10, number=f"ПЗ-SEED-{position}"),
                           id=UUID(int=position), created_at=NOW)
            with app.state.production_uow_factory() as uow:
                uow.production.add(task)
                uow.commit()
            items.append({"task_id": str(task.id), "task_version": task.version, "position": 1})
    planned = preview(http) if batch == "autoplan" else None
    calls = _watch_batch_locks(monkeypatch, sessions.kw["bind"], ["591К.3", "592К.1"])

    response = apply(http, planned) if batch == "autoplan" else assign(http, ANNA, items)

    assert response.status_code == 200, response.text
    assert calls[:2] == ["591К.3", "592К.1"]


def test_save_moves_locks_old_and_new_projects_before_audit_and_task_writes(project_lock_setup, monkeypatch):
    from app.modules.production.application import _save_moves
    from app.modules.production.dispatch import Move

    sessions, _, engine = project_lock_setup
    task_b = _task("592К.1", "КБ1", 10, number="ПЗ-B")
    task_a = _task("591K.3", "КБ1", 10, number="ПЗ-A")
    with SqlAlchemyProductionUnitOfWork(sessions) as uow:
        for task in (task_b, task_a):
            uow.production.add(task)
        uow.commit()
    moved = replace(task_b, project="593К.1")
    calls = _watch_batch_locks(monkeypatch, engine, ["591К.3", "592К.1", "593К.1"])
    with SqlAlchemyProductionUnitOfWork(sessions) as uow:
        tasks = {moved.id: moved, task_a.id: task_a}
        operation = task_a.operations[0]
        move = Move(task_a, operation, (), task_a.created_at, task_a.created_at + timedelta(minutes=10))
        _save_moves(uow, tasks, {moved.id}, {(operation.id, None): move},
                    Actor(name="Мастер", department="Производство"), task_a.created_at, "test")
        uow.commit()
    assert calls[:3] == ["591К.3", "592К.1", "593К.1"]


def test_share_assignment_locks_projects_of_shifted_tasks_before_initial_task(client, monkeypatch):
    from app.main import app
    from tests.production.test_autoplan_api import ANNA, NOW

    http, sessions = client
    task_b = _task("592К.1", "КБ1", 10, number="ПЗ-B")
    task_a = _task("591К.3", "КБ1", 10, number="ПЗ-A")
    task_a = task_a.apply_schedule((
        (task_a.operations[0].id, (ANNA,), NOW, NOW + timedelta(minutes=10)),
        (task_a.operations[1].id, (), None, None),
    ), "Мастер")
    with app.state.production_uow_factory() as uow:
        for task in (task_b, task_a):
            uow.production.add(task)
        uow.commit()
    calls = _watch_batch_locks(monkeypatch, sessions.kw["bind"], ["591К.3", "592К.1"])

    response = http.post(f"/api/production/tasks/{task_b.id}/operations/{task_b.operations[0].id}/share", json={
        "version": task_b.version, "operator_id": str(ANNA), "requested_start": NOW.isoformat(),
    })

    assert response.status_code == 200, response.text
    assert calls[:2] == ["591К.3", "592К.1"]
    with app.state.production_uow_factory() as uow:
        assert uow.production.get(task_a.id).version > task_a.version


@pytest.mark.parametrize("method", ["lock", "add", "delete", "lock_projects"])
def test_batch_rejects_new_project_before_taking_an_out_of_order_lock(project_lock_setup, method):
    from app.modules.production.application import ConcurrentProductionUpdate
    from app.modules.production.infrastructure import ProductionTaskRow, SqlAlchemyProductionRepository
    from sqlalchemy import update

    sessions, calls, _ = project_lock_setup
    task = _task("592К.1", "КБ1", 10, number="ПЗ-B")
    with sessions() as session:
        repository = SqlAlchemyProductionRepository(session)
        repository.add(task)
        session.commit()
        repository.lock_projects((task.id,))
        # Model a row moved by another writer between project discovery and the later reread.
        session.execute(update(ProductionTaskRow).where(ProductionTaskRow.id == task.id).values(project="591К.3"))
        calls.clear()
        argument = replace(task, project="591К.3") if method == "add" else (task.id,) if method == "lock_projects" else task.id
        with pytest.raises(ConcurrentProductionUpdate):
            getattr(repository, method)(argument)
        assert calls == []
        session.rollback()
        repository.lock_projects(projects=("591К.3",))
        assert calls == [(session, "591К.3")]


def test_reflow_scope_cannot_grow_after_initial_project_locks(project_lock_setup):
    from app.modules.production.application import ConcurrentProductionUpdate, lock_reflow_projects

    sessions, calls, _ = project_lock_setup
    task_a = _task("591К.3", "КБ1", 10, number="ПЗ-A")
    task_b = _task("592К.1", "КБ1", 10, number="ПЗ-B")
    with SqlAlchemyProductionUnitOfWork(sessions) as uow:
        for task in (task_a, task_b):
            uow.production.add(task)
        uow.commit()
    with SqlAlchemyProductionUnitOfWork(sessions) as uow:
        lock_reflow_projects(uow, (task_b.id,), task_b.created_at)
        calls.clear()
        with pytest.raises(ConcurrentProductionUpdate):
            uow.production.lock_projects((task_b.id, task_a.id))
        assert calls == []


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
