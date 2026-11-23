from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.equipment_setup.domain import SetupValidationError
from app.modules.equipment_setup.recipes import (EtkNominal, ManualRecipeVersion, RecipeField, latest_for_wire, ob019_proposal,
                                                 ob019_recipe_fields, plc_value)

NOW = datetime(2026, 11, 18, 12, tzinfo=UTC)
MACHINE = uuid4()
OWNER = {"strip_front_mm": "5", "pull_front_mm": "5", "strip_back_mm": "5", "pull_back_mm": "5",
         "diameter_mm": "0.80", "knife_back_mm": "0.10"}


def recipe(version=1, values=OWNER, wire="ПВАМ 0,35", etk=Decimal(5), at=NOW, recipe_id=None):
    geometry, panel = ob019_recipe_fields(wire, values, {"strip_front_mm": etk, "strip_back_mm": etk})
    return ManualRecipeVersion(recipe_id or uuid4(), version, MACHINE, f"{wire} · ОБ.019", geometry, panel, "Проверено", uuid4(), at)


def by_key(values):
    return {item.key: (item.value, item.source) for item in values}


def test_recipe_fields_keep_the_panel_values_and_the_etk_strip_they_were_recorded_against():
    geometry, panel = ob019_recipe_fields(" ПВАМ  0,35 ", OWNER, {"strip_front_mm": Decimal(5), "strip_back_mm": None})
    assert geometry == (RecipeField("Провод", "ПВАМ 0,35"), RecipeField("Зачистка начала по ЭТК", "5,00", "мм"))
    assert [(item.label, item.value) for item in panel] == [
        ("Зачистка начала", "5,00"), ("Стягивание начала", "5,00"), ("Зачистка конца", "5,00"),
        ("Стягивание конца", "5,00"), ("Диаметр реза", "0,80"), ("Отвод ножа", "0,10")]
    with pytest.raises(SetupValidationError, match="Отвод ножа"):
        ob019_recipe_fields("ПВАМ 0,35", {**OWNER, "knife_back_mm": ""}, {})


@pytest.mark.parametrize(("key", "value", "message"), [
    ("diameter_mm", "0.805", "0,01"), ("diameter_mm", "-1", "диапазона"), ("strip_front_mm", "700", "диапазона"),
    ("strip_front_mm", "3–4", "число"), ("speed", "1", "Неизвестный")])
def test_panel_value_must_fit_the_register(key, value, message):
    with pytest.raises(SetupValidationError, match=message):
        plc_value(key, value)
    assert plc_value("length_mm", "1250,5") == Decimal("1250.50")


def test_recipe_wins_over_etk_and_length_comes_from_the_job_only():
    values = by_key(ob019_proposal(recipe(version=3), EtkNominal("ПВАМ 0,35", Decimal(120), Decimal(5), Decimal(5),
                                                                   conductor_mm=Decimal("0.77"))))
    assert values["length_mm"] == (Decimal(120), "ЭТК")
    assert values["strip_front_mm"] == (Decimal("5.00"), "Рецепт, версия 3")
    assert values["diameter_mm"] == (Decimal("0.80"), "Рецепт, версия 3")
    assert values["knife_back_mm"] == (Decimal("0.10"), "Рецепт, версия 3")
    assert by_key(ob019_proposal(recipe(), None, Decimal(300)))["length_mm"] == (Decimal(300), "Задание")


def test_changed_etk_strip_keeps_the_machine_offset_of_the_recipe():
    # Recorded: ETK 5 needed 5,30 on the machine; a job with ETK 7 starts at 7,30, pull-off shifted the same.
    corrected = recipe(values={**OWNER, "strip_front_mm": "5.30", "pull_front_mm": "5.30"})
    values = by_key(ob019_proposal(corrected, EtkNominal("ПВАМ 0,35", None, Decimal(7), Decimal(5))))
    assert values["strip_front_mm"] == (Decimal("7.30"), "ЭТК, поправка рецепта +0,30")
    assert values["pull_front_mm"] == (Decimal("7.30"), "Рецепт, версия 1")
    assert values["strip_back_mm"] == (Decimal("5.00"), "Рецепт, версия 1")


def test_changed_etk_strip_preserves_no_pull_and_half_pull_modes():
    saved = recipe(values={**OWNER, "pull_front_mm": "0", "pull_back_mm": "2.5"})
    values = by_key(ob019_proposal(saved, EtkNominal("ПВАМ 0,35", None, Decimal(7), Decimal("4.05"))))
    assert values["pull_front_mm"] == (Decimal(0), "Рецепт, версия 1")
    assert values["pull_back_mm"] == (Decimal("2.03"), "Рецепт, версия 1")


def test_etk_nominal_without_recipe_full_partial_and_no_strip():
    values = by_key(ob019_proposal(None, EtkNominal("МГТФ 0,12", Decimal(160), Decimal(4), Decimal(0),
                                                    partial_front=False, conductor_mm=Decimal("0.39"))))
    assert values["strip_front_mm"] == (Decimal(4), "ЭТК")
    assert values["pull_front_mm"] == (Decimal("6.00"), "ЭТК: стягивание (зачистка + 2,00)")  # past the 4 мм strip
    assert values["pull_back_mm"] == (Decimal(0), "ЭТК: без зачистки")
    assert values["diameter_mm"] == (Decimal("0.54"), "Ø жилы + 0,15")  # owner 20.11.2026: 0,39 + 0,15
    assert values["knife_back_mm"] == (None, "Нет данных")
    partial = by_key(ob019_proposal(None, EtkNominal("МГТФ 0,12", None, Decimal(4), None, partial_front=True)))
    assert partial["pull_front_mm"] == (Decimal("2.00"), "ЭТК: полустягивание")  # half of the 4 мм strip
    assert partial["strip_back_mm"] == (None, "Нет данных")
    assert partial["diameter_mm"] == (None, "Нет данных")  # no Ø жилы in the reference
    # Half of 4,15 is 2,075: rounded half up to 2,08 like the card (banker's rounding would give 2,08 too; 4,05 → 2,03
    # half up vs 2,02 half even); full stays within the PLC word (655,35).
    odd = by_key(ob019_proposal(None, EtkNominal("МГТФ 0,12", None, Decimal("4.05"), None, partial_front=True)))
    assert odd["pull_front_mm"] == (Decimal("2.03"), "ЭТК: полустягивание")
    top = by_key(ob019_proposal(None, EtkNominal("МГТФ 0,12", None, Decimal("654.00"), None, partial_front=False)))
    assert top["pull_front_mm"][0] == Decimal("655.35")


def test_recipe_holds_only_the_recorded_panel_values():
    from app.modules.equipment_setup.recipes import recipe_holds
    assert recipe_holds(recipe(), "diameter_mm") and recipe_holds(recipe(), "strip_front_mm")
    assert not recipe_holds(recipe(), "length_mm") and not recipe_holds(None, "diameter_mm")


def test_newest_version_of_the_same_wire_is_picked_first():
    old = recipe(version=1, at=NOW)
    new = recipe(version=2, at=NOW + timedelta(hours=1), recipe_id=old.recipe_id,
                 values={**OWNER, "diameter_mm": "0.75"})
    other = recipe(wire="ПВАМ 0,5", at=NOW + timedelta(hours=2))
    assert latest_for_wire((old, new, other), "пвам  0.35") == new
    assert latest_for_wire((old, other), "МГТФ 0,12") is None


def test_speed_preset_is_optional_and_only_recorded_values_are_proposed():
    from app.modules.equipment_setup.recipes import recipe_holds

    saved = recipe(values={**OWNER, "speed_feed": "35", "speed_cutter": "0", "speed_strip": "99"})
    values = by_key(ob019_proposal(saved, EtkNominal("ПВАМ 0,35", Decimal(120))))
    assert values["speed_feed"] == (Decimal(35), "Рецепт, версия 1")
    assert values["speed_cutter"] == (Decimal(0), "Рецепт, версия 1")
    assert values["speed_strip"] == (Decimal(99), "Рецепт, версия 1")
    assert values["speed_pipe"] == (None, "Нет данных")
    assert recipe_holds(saved, "speed_feed") and not recipe_holds(saved, "speed_pipe")
    speed_fields = [field for field in saved.panel_settings if field.label == "Подача провода"]
    assert speed_fields == [RecipeField("Подача провода", "35", "")]
    assert by_key(ob019_proposal(recipe(), None))["speed_feed"] == (None, "Нет данных")
    assert by_key(ob019_proposal(None, None))["speed_feed"] == (None, "Нет данных")


@pytest.mark.parametrize("value", ["-1", "100", "12.5", "abc", "NaN"])
def test_speed_preset_rejects_values_the_plc_cannot_hold(value):
    with pytest.raises(SetupValidationError):
        ob019_recipe_fields("ПВАМ 0,35", {**OWNER, "speed_feed": value}, {})
