from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.modules.equipment_setup.recipes import ManualRecipeVersion, RecipeField, SetupTrialMeasurement

UNIT, OTHER = UUID("00000000-0000-4000-8000-000000000272"), UUID("00000000-0000-4000-8000-000000000273")  # БД.ОБ units


def recipe(**changes):
    value = ManualRecipeVersion(uuid4(), 1, UNIT, "Ручная настройка", (RecipeField("Диаметр", "2", "мм"),), (RecipeField("Значение с панели", "14"),), "Записано с панели", uuid4(), datetime.now(UTC))
    return replace(value, **changes)


def test_recipe_geometry_and_panel_are_separate_and_immutable():
    value = recipe()
    assert value.geometry[0].value == "2"
    assert value.panel_settings[0].value == "14"
    with pytest.raises(FrozenInstanceError):
        value.version = 2


@pytest.mark.parametrize("changes", [{"panel_settings": ()}, {"source": ""}, {"geometry": []}, {"geometry": (RecipeField("a", "1"), RecipeField("A", "2"))}, {"version": True}])
def test_invalid_recipe_rejected(changes):
    with pytest.raises(ValueError):
        recipe(**changes)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), 1.5])
def test_trial_nonfinite_or_float_rejected(value):
    with pytest.raises(ValueError):
        SetupTrialMeasurement(uuid4(), uuid4(), uuid4(), 1, "Диаметр", value, "мм", uuid4(), datetime.now(UTC))


def test_measurement_has_no_inferred_pass_or_machine_command():
    measurement = SetupTrialMeasurement(uuid4(), uuid4(), uuid4(), 1, "Диаметр", Decimal("2.01"), "мм", uuid4(), datetime.now(UTC))
    assert measurement.value == Decimal("2.01")
    assert not hasattr(measurement, "passed")
    assert not hasattr(measurement, "panel_settings")
