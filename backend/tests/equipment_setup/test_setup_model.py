"""Setup model rules (owner 18–19.11.2026): classes, unit overrides, values, machine corrections, changeover, the CSV."""

import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.equipment_setup.setup_model import (CurrentConfiguration, MachineCorrection, ParameterOverride,
                                                     SetupModel, SetupParameter, SetupReference, SetupValidationError,
                                                     UnitOverride, changeover, corrected, effective, etk_reason,
                                                     model_and_overrides, next_reference_version, read_proposal,
                                                     setup_values)

PROPOSAL = Path(__file__).parents[3] / "docs" / "data" / "equipment-setup-model-proposal-2026-11-18.csv"
STRIP = SetupParameter("strip_front_mm", "Зачистка начала", "мм", "etk", "Зачистка А", "D34", 1)
DIA = SetupParameter("diameter_mm", "Диаметр реза", "мм", "wire", "Ø жилы", "D42", 2)
APPLICATOR = SetupParameter("applicator", "Аппликатор", "", "tooling", "terApplicator", "", 0)
MODEL = SetupModel("full", (DIA, STRIP), "резка")


def test_parameters_are_ordered_and_checked():
    assert [item.key for item in MODEL.parameters] == ["strip_front_mm", "diameter_mm"]
    with pytest.raises(SetupValidationError):
        SetupModel("none", (STRIP,))
    with pytest.raises(SetupValidationError):
        SetupModel("simple", ())
    with pytest.raises(SetupValidationError):
        SetupModel("full", (STRIP, STRIP))
    with pytest.raises(SetupValidationError):
        SetupParameter("Strip", "Зачистка")
    with pytest.raises(SetupValidationError):
        SetupParameter("strip", "Зачистка", source="excel")


def test_unit_override_replaces_removes_and_adds_parameters():
    wider = SetupParameter("diameter_mm", "Диаметр реза", "мм", "manual", "Эталон", "", 2)
    override = UnitOverride(None, (ParameterOverride("strip_front_mm", removed=True),
                                   ParameterOverride("diameter_mm", parameter=wider),
                                   ParameterOverride("applicator", parameter=APPLICATOR)))
    unit = effective(MODEL, override)
    assert unit.requires_setup == "full"
    assert [(item.key, item.source) for item in unit.parameters] == [("applicator", "tooling"), ("diameter_mm", "manual")]
    assert effective(MODEL, UnitOverride("none")).parameters == ()
    assert effective(MODEL, None) is MODEL
    assert effective(None, None) is None
    with pytest.raises(SetupValidationError):
        ParameterOverride("diameter_mm", removed=True, parameter=wider)


def test_values_are_numbers_or_names_of_the_models_parameters():
    model = SetupModel("full", (STRIP, APPLICATOR))
    assert setup_values(model, {"strip_front_mm": "5,00", "applicator": " A8 "}) == {"applicator": "A8",
                                                                                   "strip_front_mm": "5.00"}
    with pytest.raises(SetupValidationError):
        setup_values(model, {"strip_front_mm": "пять"})
    with pytest.raises(SetupValidationError):
        setup_values(model, {"length_mm": "100"})


def test_machine_correction_applies_the_wires_own_else_the_general_offset():
    corrections = [MachineCorrection("strip_front_mm", Decimal("0.3")),
                   MachineCorrection("strip_front_mm", Decimal("-0.5"), wire="ПВАМ 0,35"),
                   MachineCorrection("diameter_mm", Decimal("-0.05"))]
    values = {"strip_front_mm": Decimal("5"), "diameter_mm": Decimal("0.8"), "length_mm": Decimal("120")}
    assert corrected(values, corrections, "пвам 0.35") == {"strip_front_mm": Decimal("4.5"),
                                                           "diameter_mm": Decimal("0.75"), "length_mm": Decimal("120")}
    assert corrected(values, corrections, "МГТФ 0,2")["strip_front_mm"] == Decimal("5.3")
    with pytest.raises(SetupValidationError):
        corrected({"strip_front_mm": Decimal("0.2")}, corrections, "ПВАМ 0,35")
    with pytest.raises(SetupValidationError):
        MachineCorrection("strip_front_mm", Decimal("1000"))


def test_changeover_lists_only_what_differs():
    model = SetupModel("full", (STRIP, DIA, APPLICATOR))
    current = CurrentConfiguration(uuid4(), {"strip_front_mm": "5.00", "diameter_mm": "0.8", "applicator": "A8"},
                                   wire="ПВАМ 0,35")
    steps = changeover(model, current, {"strip_front_mm": "5", "diameter_mm": "0.9", "applicator": "a8"},
                       wire="ПВАМ 0.35", contact="SPH-002T", tooling="ОБ.069.1", tooling_now="ОБ.069.1")
    assert [(step.key, step.now, step.needed) for step in steps] == [("contact", "", "SPH-002T"),
                                                                     ("diameter_mm", "0.8", "0.9")]
    assert len(changeover(model, None, {"strip_front_mm": "5"})) == 1


def test_reference_needs_a_material_and_versions_count_up():
    now = datetime(2026, 11, 19, 9, tzinfo=UTC)
    SetupReference(uuid4(), uuid4(), "ПВАМ 0,35", "", 1, {"strip_front_mm": "5"}, None, "", "Слава", now)
    with pytest.raises(SetupValidationError):
        SetupReference(uuid4(), uuid4(), "", " ", 1, {}, None, "", "Слава", now)
    assert next_reference_version([]) == 1 and next_reference_version([1, 3]) == 4
    assert etk_reason("зачистка А 5 → 6 мм") == "правка наладчика: зачистка А 5 → 6 мм"


def _rows(*items):
    header = ("inv", "requires_setup", "param_key", "param_name", "unit", "source", "source_field", "register",
              "reason", "reviewed")
    return [dict(zip(header, item)) for item in items]


def test_proposal_takes_only_fully_reviewed_units_and_refuses_contradictions():
    rows = _rows(("ОБ.007", "простая", "strip_mm", "Длина зачистки", "мм", "ЭТК", "", "", "зачистка", "да"),
                 ("ОБ.007", "простая", "diameter_mm", "Диаметр", "мм", "провод", "", "", "зачистка", "да"),
                 ("ОБ.100.1", "простая", "strip_mm", "Длина зачистки", "мм", "ЭТК", "", "", "зачистка", ""),
                 ("ОБ.071.1", "нет", "", "", "", "", "", "", "паяльник", "да"))
    units, skipped = read_proposal(rows)
    assert [(unit.inventory, unit.model.requires_setup, len(unit.model.parameters)) for unit in units] == [
        ("ОБ.007", "simple", 2), ("ОБ.071.1", "none", 0)]
    assert skipped == ["ОБ.100.1"]
    with pytest.raises(SetupValidationError):
        read_proposal(_rows(("ОБ.007", "простая", "strip_mm", "Длина", "мм", "ЭТК", "", "", "a", "да"),
                            ("ОБ.007", "полная", "dia_mm", "Диаметр", "мм", "провод", "", "", "a", "да")))
    with pytest.raises(SetupValidationError):
        read_proposal(_rows(("ОБ.007", "простая", "strip_mm", "Длина", "мм", "Excel", "", "", "a", "да")))


def test_units_of_one_model_give_the_common_answer_and_overrides():
    simple = SetupModel("simple", (STRIP,), "a")
    other = SetupModel("simple", (STRIP, DIA), "a")
    base, overrides = model_and_overrides({"ОБ.100.1": simple, "ОБ.100.2": simple, "ОБ.100.3": other})
    assert base == simple and list(overrides) == ["ОБ.100.3"]
    assert effective(base, overrides["ОБ.100.3"]) == other
    base, overrides = model_and_overrides({"ОБ.027.1": other, "ОБ.027.2": SetupModel("none", (), "b")})
    assert base == other and effective(base, overrides["ОБ.027.2"]) == SetupModel("none", (), "b")


def test_the_proposal_csv_parses_once_reviewed():
    with PROPOSAL.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    units, skipped = read_proposal(rows)
    assert units == [] and len(skipped) > 200  # nothing is seeded before the owner's review
    units, skipped = read_proposal([{**row, "reviewed": "да"} for row in rows])
    classes = [unit.model.requires_setup for unit in units]
    assert skipped == [] and classes.count("full") >= 10 and classes.count("simple") >= 20
    ob019 = next(unit for unit in units if unit.inventory == "ОБ.019")
    assert ob019.model.parameter("strip_front_mm").register == "D34"
    assert all(unit.model.requires_setup == "none" for unit in units if unit.inventory.startswith(("ОБ.070", "ОБ.071")))
