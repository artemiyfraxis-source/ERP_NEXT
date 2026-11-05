from datetime import UTC, datetime, timedelta
from dataclasses import replace
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.equipment_setup.application import ConcurrentSetupUpdate, SetupCommandReceipt
from app.modules.equipment_setup.domain import IssuedOperationRef, RecipeVersionRef, SetupStatus, SetupTask
from app.modules.equipment_setup.infrastructure import SetupCommandRow, SetupTaskRow, SetupTransitionRow, SqlAlchemySetupCommands, SqlAlchemySetupTasks, SqlAlchemySetupTransitions, SqlAlchemySetupUnitOfWork
from app.modules.equipment_setup.recipe_infrastructure import RecipeVersionRow, SqlAlchemyRecipes
from app.modules.equipment_setup.recipes import ManualRecipeVersion, RecipeField
from app.shared.persistence import AuditEventRow, OutboxMessageRow


NOW = datetime(2026, 11, 3, 8, tzinfo=UTC)


@pytest.fixture
def engine():
    value = create_engine("sqlite://")
    for table in (RecipeVersionRow, SetupTaskRow, SetupCommandRow, SetupTransitionRow, AuditEventRow, OutboxMessageRow):
        table.__table__.create(value)
    yield value
    value.dispose()


def task(session):
    recipe = ManualRecipeVersion(uuid4(), 1, "ОБ.027.2", "Рецепт", (), (RecipeField("Поле", "12"),), "Панель", uuid4(), NOW)
    SqlAlchemyRecipes(session).append(recipe, expected_version=0)
    return SetupTask.create(operation=IssuedOperationRef(uuid4(), 1, uuid4(), uuid4(), 1), machine_id=recipe.machine_id,
        technician_id=uuid4(), recipe=RecipeVersionRef(recipe.recipe_id, 1), planned_start=NOW,
        planned_finish=NOW + timedelta(hours=1), now=NOW)


def test_task_round_trip_and_compare_and_swap(engine):
    with Session(engine) as session:
        store = SqlAlchemySetupTasks(session)
        first = task(session)
        store.save(first, expected_version=0)
        assert store.get(first.id) == first
        changed, transition = first.transition(SetupStatus.IN_PROGRESS, actor_id=first.technician_id, now=NOW)
        SqlAlchemySetupTransitions(session).add(transition)
        store.save(changed, expected_version=1)
        assert store.get(first.id) == changed
        with pytest.raises(ConcurrentSetupUpdate):
            store.save(changed, expected_version=1)
        assert session.get(SetupTransitionRow, (first.id, 2)).snapshot["status"] == "InProgress"


def test_receipt_preserves_command_result_not_latest_task(engine):
    with Session(engine) as session:
        first = task(session)
        store = SqlAlchemySetupTasks(session)
        store.save(first, expected_version=0)
        commands = SqlAlchemySetupCommands(session)
        assert commands.claim("key") is None
        commands.add(SetupCommandReceipt("key", "a" * 64, first))
        changed, _ = first.transition(SetupStatus.IN_PROGRESS, actor_id=first.technician_id, now=NOW)
        store.save(changed, expected_version=1)
        assert commands.claim("key").result == first
        assert store.get(first.id) == changed


def test_task_list_filters_before_stable_pagination(engine):
    with Session(engine) as session:
        store = SqlAlchemySetupTasks(session)
        first = task(session)
        later = replace(task(session), technician_id=first.technician_id,
            created_at=NOW + timedelta(minutes=1), updated_at=NOW + timedelta(minutes=1))
        other = replace(task(session), machine_id="ОБ.027.3")
        for value in (first, later, other):
            store.save(value, expected_version=0)
        session.commit()
        session.expire_all()
        assert store.list(technician_id=first.technician_id, limit=1) == (later,)
        assert store.list(technician_id=first.technician_id, limit=1, offset=1) == (first,)
        assert store.list(machine_ids=("ОБ.027.3",)) == (other,)
        assert store.list(machine_ids=()) == ()
        assert store.list(technician_id=uuid4()) == ()


def test_uow_rolls_back_every_uncommitted_write(engine):
    factory = sessionmaker(engine)
    uow = SqlAlchemySetupUnitOfWork(factory, context_factory=lambda session: object(),
        reservations_factory=lambda session: SimpleNamespace(acquire=lambda: None))
    with pytest.raises(RuntimeError):
        with uow:
            first = task(uow.session)
            uow.tasks.save(first, expected_version=0)
            uow.commands.add(SetupCommandReceipt("key", "a" * 64, first))
            raise RuntimeError("failure after resources")
    with factory() as session:
        assert list(session.scalars(select(SetupTaskRow))) == []
        assert list(session.scalars(select(RecipeVersionRow))) == []
        assert list(session.scalars(select(SetupCommandRow))) == []
