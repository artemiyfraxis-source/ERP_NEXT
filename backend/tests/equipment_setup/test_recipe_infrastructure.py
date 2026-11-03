from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.equipment_setup.application import ConcurrentSetupUpdate
from app.modules.equipment_setup.recipes import ManualRecipeVersion, RecipeField, SetupTrialMeasurement
from app.modules.equipment_setup.recipe_infrastructure import RecipeVersionRow, SqlAlchemyRecipes, TrialMeasurementRow


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    RecipeVersionRow.__table__.create(engine)
    TrialMeasurementRow.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def recipe():
    return ManualRecipeVersion(uuid4(), 1, "ОБ.027.2", "Настройка", (), (RecipeField("Поле панели", "12"),), "Ручная запись", uuid4(), datetime.now(UTC))


def test_append_does_not_change_previous_version(db):
    store, first = SqlAlchemyRecipes(db), recipe()
    store.append(first, expected_version=0)
    second = replace(first, version=2, panel_settings=(RecipeField("Поле панели", "13"),))
    store.append(second, expected_version=1)
    assert store.get(first.recipe_id, 1) == first
    assert store.get(first.recipe_id, 2) == second
    with pytest.raises(ConcurrentSetupUpdate):
        store.append(second, expected_version=1)


def test_machine_cannot_change_between_recipe_versions(db):
    store, first = SqlAlchemyRecipes(db), recipe()
    store.append(first, expected_version=0)
    with pytest.raises(ConcurrentSetupUpdate):
        store.append(replace(first, version=2, machine_id="ОБ.027.3"), expected_version=1)


def test_trials_are_separate_exact_values_and_rollback_with_recipe(db):
    store, first = SqlAlchemyRecipes(db), recipe()
    store.append(first, expected_version=0)
    measured = SetupTrialMeasurement(uuid4(), uuid4(), first.recipe_id, 1, "Диаметр", Decimal("2.0100"), "мм", uuid4(), datetime.now(UTC))
    store.add_measurement(measured)
    assert db.get(TrialMeasurementRow, measured.id).value == "2.0100"
    db.flush()
    db.expire_all()
    assert store.measurement(measured.id) == measured
    assert str(store.measurement(measured.id).value) == "2.0100"
    assert store.measurement(uuid4()) is None
    assert store.get(first.recipe_id, 1) == first
    db.rollback()
    assert list(db.scalars(select(RecipeVersionRow))) == []
    assert list(db.scalars(select(TrialMeasurementRow))) == []
