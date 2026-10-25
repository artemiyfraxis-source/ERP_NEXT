"""Правки построителя «Ручной сборки» (25.10): 16 черновиков по чертежам 663К.1 / 708К.5 упёрлись в одни и те же
дефекты — имя ПФ по чужому блоку, пустое кол-во на выходе, кратное трубкам кол-во ПФ, трубки в штуках вместо метров,
лента без выдачи в комплект. Режим типовых генераторов эти правки не трогают (golden-тесты)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library


ROOT = Path(__file__).resolve().parents[2]
CARDS = parse_operation_library(json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))["templates"])


def build(config: dict) -> dict:
    return build_etk(config, CARDS)


def _wire(art: str, length: int = 300) -> dict:
    return {"name": art, "art": art, "qty": 1, "length": length}


def _config(ops: list[dict], route: dict | None = None, **changes) -> dict:
    config = {"mode": "manual", "projectNo": "P-1", "assemblyIndex": "КБ1", "assemblyName": "Изделие", "partQty": 10,
              "ops": [{"seq": index, "tPrep": "60", "tOp": "5", **op} for index, op in enumerate(ops, start=1)]}
    if route:
        config["route"] = route
    config.update(changes)
    return config


def _operation(document: dict, position: int) -> dict:
    return document["operations"][position - 1]


FINAL = {"type": "customOp", "label": "Сборка"}


def test_a_block_result_name_comes_from_its_own_block_not_the_previous_operation_in_the_list():
    ops = [
        {"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [_wire("красный")]},
        {"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [_wire("синий")]},
        {"type": "tin", "opNum": "SDR_tin"},
        {"type": "seal", "opNum": "SEAL"},
        FINAL,
    ]
    route = {"blocks": [{"key": "a", "ops": [1, 3, 4]}, {"key": "b", "name": "Синий провод", "ops": [2]},
                        {"key": "f", "ops": [5], "final": True}],
             "edges": [{"from": "a", "to": "f", "per_unit": 1}, {"from": "b", "to": "f", "per_unit": 1}]}
    document = build(_config(ops, route))
    tinned = _operation(document, 3)["result"][0]["name"]
    assert "красный" in tinned and "синий" not in tinned
    block = document["route"]["blocks"][0]["name"]
    assert "красный" in block and "синий" not in block


def test_an_explicit_result_still_wins_over_the_block_derived_name():
    ops = [
        {"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [_wire("красный")]},
        {"type": "tin", "opNum": "SDR_tin", "result": "Красный, облуженный"},
        {"type": "seal", "opNum": "SEAL"},
        FINAL,
    ]
    route = {"blocks": [{"key": "a", "ops": [1, 2, 3]}, {"key": "f", "ops": [4], "final": True}],
             "edges": [{"from": "a", "to": "f", "per_unit": 1}]}
    assert _operation(build(_config(ops, route)), 2)["result"][0]["name"] == "Красный, облуженный"


def test_a_block_takes_its_name_basis_from_the_wire_input_not_from_a_tube_cut_block():
    ops = [
        {"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [_wire("кабель")]},
        {"type": "cutTut", "opNum": "CUT_TUT_short", "tutArt": "ТНТ-6/3", "tutCount": 2,
         "tutSegs": [{"len": "20", "count": 2}]},
        {"type": "insTut", "opNum": "INS_TUT", "harness": True},
        FINAL,
    ]
    route = {"blocks": [{"key": "cut", "name": "Кабель нарезан", "ops": [1]}, {"key": "tut", "name": "ТНТ-6/3", "ops": [2]},
                        {"key": "m", "ops": [3, 4], "final": True}],
             "edges": [{"from": "cut", "to": "m", "per_unit": 1}, {"from": "tut", "to": "m", "per_unit": 1}]}
    assert _operation(build(_config(ops, route)), 3)["result"][0]["name"].startswith("Кабель нарезан")


@pytest.mark.parametrize("operation", [
    {"type": "splice", "opNum": "SDR_W_W", "method": "solder", "units": 2},
    {"type": "insHousing", "opNum": "INS_HOUSING"},
])
def test_splice_and_housing_not_last_in_a_block_hand_on_the_per_product_quantity(operation):
    ops = [{"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [_wire("красный")]}, operation, FINAL]
    assert _operation(build(_config(ops)), 2)["result"][0]["norm"] == "10"


@pytest.mark.parametrize("operation", [
    {"type": "insTut", "opNum": "INS_TUT", "tutCount": 8},
    {"type": "endCap", "opNum": "INS_END_CAP", "tutCount": 4},
    {"type": "stripCable", "opNum": "STRIP_CABLE", "tutCount": 2},
    {"type": "foldCore", "opNum": "PREP_FOLD", "tutCount": 3},
])
def test_tube_operations_in_a_block_chain_hand_on_pieces_of_the_product_not_the_tube_count(operation):
    ops = [{"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [_wire("красный")]}, operation, FINAL]
    document = build(_config(ops))
    assert _operation(document, 2)["result"][0]["norm"] == "10"


def test_a_cut_tube_issues_metres_of_its_segments_to_the_kit():
    ops = [{"type": "cutTut", "opNum": "CUT_TUT_short", "tutArt": "ТНТ-6/3 бел", "tutName": "Трубка ТНТ-6/3 бел",
            "tutCount": 2, "tutSegs": [{"len": "20", "count": 2}]}, FINAL]
    [row] = _operation(build(_config(ops, partQty=300)), 1)["components"]
    assert (row["art"], row["norm"], row["unit"]) == ("ТНТ-6/3 бел", "12", "М.")


def test_a_cut_of_several_tube_positions_issues_metres_per_position():
    ops = [{"type": "cutTut", "opNum": "CUT_TUT_short", "tutParts": [
        {"art": "ТУТ 3/1,5", "name": "ТУТ 3/1,5", "count": 4, "len": "25"},
        {"art": "ТУТ 6/3", "name": "ТУТ 6/3", "count": 2, "len": "50"}]}, FINAL]
    rows = _operation(build(_config(ops)), 1)["components"]
    assert [(row["art"], row["norm"], row["unit"]) for row in rows] == [("ТУТ 3/1,5", "1", "М."), ("ТУТ 6/3", "1", "М.")]


def test_a_cut_tube_without_lengths_keeps_pieces():
    ops = [{"type": "cutTut", "opNum": "CUT_TUT_short", "tutArt": "ТУТ", "tutCount": 3}, FINAL]
    [row] = _operation(build(_config(ops)), 1)["components"]
    assert (row["norm"], row["unit"]) == ("30", "Шт.")


def test_insulation_tape_issues_its_material_when_the_operation_names_one():
    tape = {"type": "insWrap", "templateId": "ins-tape-монтаж", "opNum": "INS_tape", "wrapKind": "tape",
            "wrapArt": "Лента ЭРА", "wrapName": "Лента изоляционная ЭРА", "wrapLen": "900"}
    cut = {"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [_wire("красный")]}
    [row] = _operation(build(_config([cut, tape, FINAL])), 2)["components"]
    assert (row["art"], row["norm"], row["unit"]) == ("Лента ЭРА", "9", "М.")
    nameless = {**tape, "wrapArt": "", "wrapName": ""}
    assert _operation(build(_config([cut, nameless, FINAL])), 2)["components"] == []
