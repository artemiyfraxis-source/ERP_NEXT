"""Manufacturer dimensions and the shared ETK sketch payload, measured from the tip."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import _coax_recipe_steps, _fields

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
        assert f"{b['value']} {b['tolerance']} мм" in text
    if fixture["kind"] == "hfm":
        assert "Размер обрезки прямой оплётки до отворота не задан" in text
        assert "после полной разделки" in text
        assert "v=7,85±0,2" in program["source"]
    elif fixture["kind"] == "bulkhead":
        assert "2,7+1,0 = 3,7" in text
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


def test_legacy_without_new_recipe_keeps_raw_dimensions_and_card_steps():
    config = deepcopy(FIXTURES[0]["config"])
    del config["coax"]["fakraCutStrip"]
    operation = FIXTURES[0]["operation"]
    end = _fields(operation, config)["coax"]
    assert [end[key] for key in ("l1", "l2", "l3")] == ["3,0", "4,6", "9,7"]
    assert "dimensionOrder" not in end and "stripProgram" not in end
    assert _coax_recipe_steps(operation, config) == []


def test_only_selected_side_gets_program_and_instructions():
    config = deepcopy(FIXTURES[0]["config"])
    config["coax"]["sideB"] = deepcopy(FIXTURES[1]["config"]["coax"]["sideA"])
    operation = {"type": "coaxCut", "stripSides": ["B"]}
    fields = _fields(operation, config)
    assert "coax" not in fields
    assert fields["coaxB"]["stripProgram"]["boundaries"][-1]["value"] == "13,3"
    text = " ".join(_coax_recipe_steps(operation, config))
    assert "Сторона В" in text and "Сторона А" not in text
