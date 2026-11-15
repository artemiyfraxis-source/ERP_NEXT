from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.modules.equipment_setup.domain import IssuedOperationRef, RecipeVersionRef, SetupStatus, SetupTask
from app.modules.equipment_setup.application import SetupResourceConflict
from app.modules.production.resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceOwner, ResourceWindow
from app.modules.production.resource_infrastructure import ActiveResourceRow, ResourceGenerationRow, ResourceReservationRow, SqlAlchemyResourceClaims
from app.modules.references.infrastructure import SqlAlchemyTechOperationRepository
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.shared.persistence import create_schema
from app.workflows.equipment_setup_resources import SetupResourceReservations


NOW = datetime(2026, 11, 3, 8, tzinfo=UTC)
UNIT = uuid4()  # the БД.ОБ unit (tech_operation_records.id)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    create_schema(engine)
    with Session(engine) as session:
        session.add(ResourceGenerationRow(id=1, version=0))
        session.commit()
        session.execute(text("BEGIN IMMEDIATE"))
        yield session
    engine.dispose()


def task():
    return SetupTask.create(operation=IssuedOperationRef(uuid4(), 1, uuid4(), uuid4(), 1), machine_id=UNIT,
        technician_id=uuid4(), recipe=RecipeVersionRef(uuid4(), 1), planned_start=NOW,
        planned_finish=NOW + timedelta(hours=1), now=NOW)


def test_not_integrated_fails_closed(db):
    adapter = SetupResourceReservations(db, assert_legacy_available=lambda task: None)
    with pytest.raises(SetupResourceConflict, match="не подключён"):
        adapter.acquire()


@pytest.mark.parametrize("available", [True, False])
def test_blocked_releases_person_but_machine_only_if_available(db, available):
    adapter = SetupResourceReservations(db, assert_legacy_available=lambda task: None, integration_ready=True)
    first = task()
    adapter.reserve(first)
    started, _ = first.transition(SetupStatus.IN_PROGRESS, actor_id=first.technician_id, now=NOW)
    adapter.reserve(started)
    blocked, _ = started.transition(SetupStatus.BLOCKED, actor_id=first.technician_id, now=NOW,
        reason="Проверка", machine_available=available)
    adapter.block(blocked)
    assert db.get(ActiveResourceRow, ("person", str(first.technician_id))) is None
    assert (db.get(ActiveResourceRow, ("machine", str(first.machine_id))) is None) is available
    resumed, _ = blocked.transition(SetupStatus.IN_PROGRESS, actor_id=first.technician_id, now=NOW)
    adapter.reserve(resumed)
    assert db.get(ActiveResourceRow, ("person", str(first.technician_id))) is not None
    assert db.get(ActiveResourceRow, ("machine", str(first.machine_id))) is not None


def test_unknown_legacy_holder_rejected_before_any_reservation(db):
    def reject(task):
        raise ResourceConflict("Не выбран экземпляр у текущей работы")
    adapter = SetupResourceReservations(db, assert_legacy_available=reject, integration_ready=True)
    first = task()
    with pytest.raises(SetupResourceConflict) as captured:
        adapter.reserve(first)
    assert isinstance(captured.value.__cause__, ResourceConflict)
    assert db.get(ResourceGenerationRow, 1).version == 0


def test_blocked_person_can_really_be_assigned_elsewhere_and_resume_conflicts(db):
    adapter = SetupResourceReservations(db, assert_legacy_available=lambda task: None, integration_ready=True)
    first = task()
    started, _ = first.transition(SetupStatus.IN_PROGRESS, actor_id=first.technician_id, now=NOW)
    adapter.reserve(started)
    blocked, _ = started.transition(SetupStatus.BLOCKED, actor_id=first.technician_id, now=NOW,
        reason="Нет оснастки", machine_available=False)
    adapter.block(blocked)
    other = ResourceOwner("production", uuid4())
    resources = (ResourceKey(ResourceKind.PERSON, str(first.technician_id)), ResourceKey(ResourceKind.MACHINE, str(uuid4())))
    claims = SqlAlchemyResourceClaims(db)
    claims.replace_plan(other, resources, ResourceWindow(NOW, NOW + timedelta(minutes=30)))
    claims.activate(other, uuid4(), resources, NOW)
    resumed, _ = blocked.transition(SetupStatus.IN_PROGRESS, actor_id=first.technician_id, now=NOW)
    with pytest.raises(SetupResourceConflict):
        adapter.reserve(resumed)
    assert db.get(ActiveResourceRow, ("machine", str(first.machine_id))).owner_id == first.id


def test_ready_releases_exact_setup_only(db):
    adapter = SetupResourceReservations(db, assert_legacy_available=lambda task: None, integration_ready=True)
    first = task()
    started, _ = first.transition(SetupStatus.IN_PROGRESS, actor_id=first.technician_id, now=NOW)
    adapter.reserve(started)
    ready, _ = started.transition(SetupStatus.READY, actor_id=first.technician_id, now=NOW)
    adapter.release(ready)
    assert db.get(ActiveResourceRow, ("person", str(first.technician_id))) is None
    assert db.get(ActiveResourceRow, ("machine", str(first.machine_id))) is None


def test_renumbering_the_unit_keeps_its_claims_and_recipes():
    """Owner 05.11.2026: the link is the unit's id — a new inventory number changes nothing that points at it."""
    from app.modules.equipment_setup.recipe_infrastructure import RecipeVersionRow, SqlAlchemyRecipes
    from app.modules.equipment_setup.recipes import ManualRecipeVersion, RecipeField
    from app.modules.references.infrastructure import SqlAlchemyTechOperationRepository
    from app.modules.references.domain import TechOperationRecord, TechOperationTab
    from app.shared.persistence import create_schema
    from app.modules.techcards.infrastructure import RefreshRequestRow  # a БД.ОБ edit queues an ETK refresh
    from app.workflows.equipment_setup_context import SqlAlchemySetupContext

    engine = create_engine("sqlite://")
    create_schema(engine)
    try:
        with Session(engine) as db:
            db.add(ResourceGenerationRow(id=1, version=0))
            unit = SqlAlchemyTechOperationRepository(db)._put(TechOperationRecord(id=UNIT, tab=TechOperationTab.EQUIPMENT, display_text="Автомат", normalized_search="", source_sheet="БД.ОБ",
                sort_key="1", fields={"Тип": "Оборудование", "Инв. номер": "ОБ.027.2", "Наименование": "Автомат резки",
                                      "Расположение": "ЦАР"}))
            db.add(unit)
            db.commit()
            recipes = SqlAlchemyRecipes(db)
            recipe = ManualRecipeVersion(uuid4(), 1, UNIT, "Резка", (), (RecipeField("Длина", "120"),), "Панель", uuid4(), NOW)
            recipes.append(recipe, expected_version=0)
            adapter = SetupResourceReservations(db, assert_legacy_available=lambda task: None, integration_ready=True)
            first = task()
            started, _ = first.transition(SetupStatus.IN_PROGRESS, actor_id=first.technician_id, now=NOW)
            adapter.reserve(started)
            db.commit()

            unit.fields = {**unit.fields, "Инв. номер": "ОБ.027.7"}  # renumbered in БД.ОБ
            db.commit()
            context = SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: None)
            machine = context.machine(UNIT)
            assert (machine.id, machine.inventory_number) == (UNIT, "ОБ.027.7")
            assert context.recipe_exists(RecipeVersionRef(recipe.recipe_id, 1), UNIT)
            assert recipes.list_versions(UNIT, limit=10, offset=0) == (recipe,)
            active = db.get(ActiveResourceRow, ("machine", str(UNIT)))
            assert (active.owner_id, active.machine_id) == (first.id, UNIT)
            other = ResourceOwner("production", uuid4())
            with pytest.raises(ResourceConflict, match="занят"):  # still held under the new number
                SqlAlchemyResourceClaims(db).replace_plan(other, (ResourceKey(ResourceKind.MACHINE, str(UNIT)),),
                                                          ResourceWindow(NOW, NOW + timedelta(minutes=10)))
    finally:
        engine.dispose()
