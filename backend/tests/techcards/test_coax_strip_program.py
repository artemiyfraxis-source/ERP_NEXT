"""Manufacturer dimensions and the shared ETK sketch payload, measured from the tip."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import _coax_recipe_steps, _fields, _resolve_card
from app.modules.techcards.operation_cards import parse_operation_library

FIXTURES = json.loads((Path(__file__).resolve().parents[3] /
                      "frontend/src/__fixtures__/coaxStripPrograms.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture,expected", list(zip(FIXTURES, [
    [("dielectric", "3", "±0,2"), ("braid", "7,6", "±0,4"), ("jacket", "12,7", "±0,4")],
    [("dielectric", "1,7", "±0,2"), ("braid", "8,1", "±0,4"), ("jacket", "13,3", "±0,4")],
    [("dielectric", "2,30", "±0,4"), ("foil", "3,35", "минимум номинально"), ("braid", "4,40", "±0,6"), ("jacket", "9,60", "±0,4")],
    [("dielectric", "2,7", "±0,1"), ("foil", "3,7", "±0,2"), ("braid", "8,5", "±0,2"), ("jacket", "13,9", "±0,2")],
])))
def test_card_and_sketch_share_exact_from_tip_geometry(fixture, expected):
    config = deepcopy(fixture["config"])
    operation = fixture["operation"]
    fields = _fields(operation, config)
    assert fields == operation["fields"]
    assert config == fixture["config"]
    end = fields["coax"]
    assert all(end[key] == config["coax"]["sideA"][key] for key in ("l1", "l2", "l3"))
    program = end["stripProgram"]
    assert [(b["layer"], b["value"], b["tolerance"]) for b in program["boundaries"]] == expected
    text = " ".join(_coax_recipe_steps(operation, config))
    assert "от торца жилы" in text
    for b in program["boundaries"]:
        assert f"{b['value']} {b['tolerance']}" in text
    assert "Источник:" not in text
    assert program["source"] not in text
    assert sum(step.startswith("Сторона А:") for step in _coax_recipe_steps(operation, config)) == 1
    if fixture["kind"] == "hfm":
        assert "Размер обрезки прямой оплётки до отворота не задан" in text
        assert "после полной разделки" in text
        assert "v=7,85±0,2" in program["source"]
    elif fixture["kind"] == "bulkhead":
        assert "1,0 мм — участок открытого диэлектрика" in text
        assert "Фольгу сохранить внутри корпуса, оплётку — снаружи насечки" in text
        assert "2,7+1,0" not in text
    else:
        assert "X = " in program["source"] and "Z = " in program["source"]


def test_sums_explicit_tolerances_instead_of_using_nominal_defaults():
    config = deepcopy(FIXTURES[0]["config"])
    config["coax"]["sideA"].update(l1="3,0±0,1", l2="4,6±0,3", l3="9,7±0,2")
    boundaries = _fields(FIXTURES[0]["operation"], config)["coax"]["stripProgram"]["boundaries"]
    assert [b["tolerance"] for b in boundaries] == ["±0,1", "±0,4", "±0,3"]


@pytest.mark.parametrize("change", [{"l2": ""}, {"l1": "9,7", "l3": "3"}, {"l1": "bad"}])
def test_missing_or_reversed_reference_values_do_not_produce_a_machine_program(change):
    config = deepcopy(FIXTURES[0]["config"])
    config["coax"]["sideA"].update(change)
    program = _fields(FIXTURES[0]["operation"], config)["coax"]["stripProgram"]
    assert program["boundaries"] == []
    assert "не рассчитана" in program["note"]


def test_legacy_without_new_recipe_keeps_raw_dimensions_and_gets_one_short_step():
    config = deepcopy(FIXTURES[0]["config"])
    del config["coax"]["fakraCutStrip"]
    operation = FIXTURES[0]["operation"]
    end = _fields(operation, config)["coax"]
    assert [end[key] for key in ("l1", "l2", "l3")] == ["3,0", "4,6", "9,7"]
    assert "dimensionOrder" not in end and "stripProgram" not in end
    assert _coax_recipe_steps(operation, config) == [
        "Сторона А: L1 3,0; L2 4,6; L3 9,7 мм (от торца жилы)."]


def test_legacy_clamp_without_l3_keeps_sketch_dimension_names():
    config = deepcopy(FIXTURES[0]["config"])
    config["coax"].pop("fakraCutStrip", None)
    config["coax"]["sideA"].update(l1="3", l2="6", l3="", kind="clamp")
    assert _coax_recipe_steps(FIXTURES[0]["operation"], config) == [
        "Сторона А: L1 3; L2 6 мм (от торца жилы)."]


def test_only_selected_side_gets_program_and_instructions():
    config = deepcopy(FIXTURES[0]["config"])
    config["coax"]["sideB"] = deepcopy(FIXTURES[1]["config"]["coax"]["sideA"])
    operation = {"type": "coaxCut", "stripSides": ["B"]}
    fields = _fields(operation, config)
    assert "coax" not in fields
    assert fields["coaxB"]["stripProgram"]["boundaries"][-1]["value"] == "13,3"
    text = " ".join(_coax_recipe_steps(operation, config))
    assert "Сторона В" in text and "Сторона А" not in text


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture["kind"])
def test_cut_steps_keep_only_cut_length_and_one_dimension_line_per_side(fixture):
    config = deepcopy(fixture["config"])
    config["coax"].update(lengthBasis="finished", finishedLength=4800, lPlusA=30, lPlusB=25,
                          cableLength=4855, sideB=deepcopy(config["coax"]["sideA"]))
    operation = {"type": "coaxCut", "stripSides": ["A", "B"]}
    fields = _fields(operation, config)
    library = Path(__file__).resolve().parents[2] / "data/techcard-library.json"
    cards = parse_operation_library(json.loads(library.read_text(encoding="utf-8"))["templates"])
    card = next(c for c in cards if c.code == "CUT_COAX+2strip")
    # These legacy phrases also occur in editable production templates.
    card = replace(card, steps=(*card.steps, replace(card.steps[0], text="Задать длину отрезка L по КД."),
                               replace(card.steps[0], text="Задать ступени разделки по КД, ДИ разъёма.")))
    steps = _coax_recipe_steps(operation, config)
    steps += [s["text"] for s in _resolve_card(card, {}, fields)["steps"]]
    text = "\n".join(steps)
    assert "4855 мм" in text
    assert all(word not in text for word in ("Длина готового изделия", "4800", "припуск", "Источник",
                                            "Задать длину отрезка L по КД", "по КД, ДИ разъёма"))
    for side in ("А", "В"):
        assert sum(s.startswith(f"Сторона {side}:") for s in steps) == 1
    assert fields["finishedLength"] == 4800
    assert (fields["lPlusA"], fields["lPlusB"]) == (30, 25)
    assert fields["coax"]["stripProgram"]["source"]
