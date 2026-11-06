"""Transactional behavior only; SQLite does not prove PostgreSQL concurrent exclusion."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.modules.production.resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceOwner, ResourceWindow
from app.modules.production.resource_infrastructure import ActiveResourceRow, ResourceGenerationRow, ResourceReservationRow, SqlAlchemyResourceClaims
from app.modules.references.infrastructure import EquipmentModelRow, TechOperationRecordRow  # the units machine claims name


NOW = datetime(2026, 11, 3, 8, tzinfo=UTC)
MACHINE = ResourceKey(ResourceKind.MACHINE, str(uuid4()))  # a БД.ОБ unit id
PERSON = ResourceKey(ResourceKind.PERSON, str(uuid4()))
WINDOW = ResourceWindow(NOW, NOW + timedelta(hours=1))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for row in (EquipmentModelRow, TechOperationRecordRow, ResourceGenerationRow, ResourceReservationRow, ActiveResourceRow):
        row.__table__.create(engine)
    with Session(engine) as session:
        session.add(ResourceGenerationRow(id=1, version=0))
        session.commit()
        session.execute(text("BEGIN IMMEDIATE"))
        yield session
    engine.dispose()


def owner():
    return ResourceOwner("setup", uuid4())


def test_plan_overlap_and_boundary(db):
    store = SqlAlchemyResourceClaims(db)
    store.replace_plan(owner(), (MACHINE,), WINDOW)
    with pytest.raises(ResourceConflict):
        store.replace_plan(owner(), (MACHINE,), WINDOW)
    store.replace_plan(owner(), (MACHINE,), ResourceWindow(WINDOW.finish, WINDOW.finish + timedelta(hours=1)))
    assert len(list(db.scalars(select(ResourceReservationRow)))) == 2


def test_active_claim_blocks_after_scheduled_finish(db):
    store = SqlAlchemyResourceClaims(db)
    first, second = owner(), owner()
    store.replace_plan(first, (MACHINE,), WINDOW)
    store.replace_plan(second, (MACHINE,), ResourceWindow(WINDOW.finish, WINDOW.finish + timedelta(hours=1)))
    store.activate(first, uuid4(), (MACHINE,), NOW)
    with pytest.raises(ResourceConflict):
        store.activate(second, uuid4(), (MACHINE,), WINDOW.finish)


def test_replan_removal_does_not_release_active(db):
    store = SqlAlchemyResourceClaims(db)
    first, execution = owner(), uuid4()
    store.replace_plan(first, (MACHINE,), WINDOW)
    assert db.get(ResourceReservationRow, (first.kind, first.id, "machine", MACHINE.key)).machine_id == MACHINE.unit_id
    store.activate(first, execution, (MACHINE,), NOW)
    store.release_plan(first)
    active = db.get(ActiveResourceRow, ("machine", MACHINE.key))
    assert active.execution_id == execution and active.machine_id == MACHINE.unit_id  # the unit, as a FK


def test_release_person_independently_from_machine_and_exact_execution(db):
    store = SqlAlchemyResourceClaims(db)
    first, execution = owner(), uuid4()
    store.replace_plan(first, (MACHINE, PERSON), WINDOW)
    store.activate(first, execution, (MACHINE, PERSON), NOW)
    store.release_active(first, uuid4(), kinds=(ResourceKind.PERSON,))
    assert db.get(ActiveResourceRow, ("person", PERSON.key)) is not None
    store.release_active(first, execution, kinds=(ResourceKind.PERSON,))
    assert db.get(ActiveResourceRow, ("person", PERSON.key)) is None
    assert db.get(ActiveResourceRow, ("machine", MACHINE.key)) is not None


def test_same_assignment_second_session_cannot_reenter(db):
    store = SqlAlchemyResourceClaims(db)
    first, execution = owner(), uuid4()
    store.replace_plan(first, (MACHINE,), WINDOW)
    store.activate(first, execution, (MACHINE,), NOW)
    generation = store.generation()
    store.activate(first, execution, (MACHINE,), NOW)
    assert store.generation() == generation
    with pytest.raises(ResourceConflict):
        store.activate(first, uuid4(), (MACHINE,), NOW)


def test_atomic_acquisition_does_not_partially_add_person(db):
    store = SqlAlchemyResourceClaims(db)
    first, second = owner(), owner()
    store.replace_plan(first, (MACHINE,), WINDOW)
    store.replace_plan(second, (MACHINE, PERSON), ResourceWindow(WINDOW.finish, WINDOW.finish + timedelta(hours=1)))
    store.activate(first, uuid4(), (MACHINE,), NOW)
    with pytest.raises(ResourceConflict):
        store.activate(second, uuid4(), (PERSON, MACHINE), WINDOW.finish)
    assert db.get(ActiveResourceRow, ("person", PERSON.key)) is None


def test_rollback_discards_reservation_and_generation(db):
    store = SqlAlchemyResourceClaims(db)
    store.replace_plan(owner(), (MACHINE,), WINDOW)
    assert store.generation() == 1
    db.rollback()
    assert store.generation() == 0
    assert list(db.scalars(select(ResourceReservationRow))) == []


def test_uninitialized_store_fails_closed(db):
    db.delete(db.get(ResourceGenerationRow, 1))
    db.flush()
    with pytest.raises(ResourceConflict):
        SqlAlchemyResourceClaims(db).replace_plan(owner(), (MACHINE,), WINDOW)


def test_batch_can_swap_windows_and_increments_generation_once(db):
    store = SqlAlchemyResourceClaims(db)
    first, second = owner(), owner()
    later = ResourceWindow(WINDOW.finish, WINDOW.finish + timedelta(hours=1))
    store.replace_plans(((first, (MACHINE,), WINDOW), (second, (MACHINE,), later)))
    generation = store.generation()
    store.replace_plans(((first, (MACHINE,), later), (second, (MACHINE,), WINDOW)))
    assert store.generation() == generation + 1
    row = db.get(ResourceReservationRow, (first.kind, first.id, MACHINE.kind.value, MACHINE.key))
    assert row.start.replace(tzinfo=UTC) == later.start


def test_batch_internal_conflict_does_not_delete_existing_reservations(db):
    store = SqlAlchemyResourceClaims(db)
    first, second = owner(), owner()
    store.replace_plan(first, (MACHINE,), WINDOW)
    generation = store.generation()
    with pytest.raises(ResourceConflict):
        store.replace_plans(((first, (MACHINE,), WINDOW), (second, (MACHINE,), WINDOW)))
    assert store.generation() == generation
    assert len(list(db.scalars(select(ResourceReservationRow)))) == 1
