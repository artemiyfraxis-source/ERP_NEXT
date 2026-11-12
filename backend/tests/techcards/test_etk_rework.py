"""Explicit customer-supplied rework: metres once, pieces throughout, legacy documents unchanged."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import build_etk, generator_tab
from app.modules.techcards.etk_derive import derive
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard
from app.workflows.techcard_build import with_technology

BACKEND = Path(__file__).resolve().parents[2]
CARDS = parse_operation_library(json.loads((BACKEND / "data/techcard-library.json").read_text(encoding="utf-8"))["templates"])
BY_CODE = {card.code: card.source_id for card in CARDS}


def config(ends="A", treatment="tin", mark=True):
    def op(kind, key, code, side=""):
        return {"type": kind, "opKey": key, "templateId": BY_CODE[code], "opNum": code, "tOp": "6", "side": side}
    ops = [op("cutWire", "reworkCut", "CUT_WIRE_hand")]
    if treatment != "none":
        for side in (["A", "B"] if ends == "AB" else [ends]):
            ops.append(op("stripCable", f"reworkStrip{side}", "STRIP_WIRE", side))
            if treatment == "tin":
                ops.append(op("tin", f"reworkTin{side}", "SDR_tin", side))
    if mark:
        ops.append({**op("insMark", "reworkMark", "INS_MARK"), "marks": [
            {"art": "Бирка", "name": "Бирка", "text": "ДИ-1", "qty": 1, "target": "Изделие", "place": "на изделии", "placeKind": "item", "placeMm": ""},
        ]})
    return {"mode": "rework", "tab": "rework", "projectNo": "P-1", "assemblyIndex": "КБ13", "assemblyName": "Доработанное изделие", "partQty": 10,
            "rework": {"input": {"id": "kit-2", "art": "ДИ-1", "name": "Давальческое изделие", "unit": "М."},
                       "cutLength": 250, "ends": ends, "treatment": treatment, "stripLength": "5", "mark": mark},
            "ops": ops}


def test_metres_are_consumed_exactly_once_and_all_operations_carry_ten_pieces():
    raw = config(ends="AB")
    before = deepcopy(raw)
    doc = build_etk(raw, CARDS)
    assert raw == before
    assert doc["kit"]["mode"] == generator_tab(raw) == "rework"
    assert doc["operations"][0]["components"] == [{"art": "ДИ-1", "name": "Давальческое изделие", "norm": "2,5", "unit": "М."}]
    inputs = [item for op in doc["operations"] for item in op["components"] if item["art"] == "ДИ-1"]
    assert len(inputs) == 1
    for i, op in enumerate(doc["operations"]):
        assert op["result"][0]["norm"] == "10"
        assert op["result"][0]["unit"] == "Шт."
        if i:
            assert op["semis"] == doc["operations"][i - 1]["result"]
        else:
            assert op["semis"] == []
    assert doc["operations"][-1]["components"][0]["norm"] == "10"
    assert doc["operations"][-1]["result_label"] == "Изделие"
    assert doc["operations"][1]["fields"]["strip"] == {"a": True, "b": False, "lenA": "5", "lenB": ""}
    assert all(op["time"][0]["norm"] == "1" for op in doc["operations"][:-1])


def test_one_vs_both_ends_only_adds_selected_treatments():
    one = build_etk(config(), CARDS)["operations"]
    both = build_etk(config(ends="AB"), CARDS)["operations"]
    assert [op["type"] for op in one] == ["cutWire", "stripCable", "tin", "insMark"]
    assert [op["type"] for op in both] == ["cutWire", "stripCable", "tin", "stripCable", "tin", "insMark"]
    assert one[:3] == both[:3]
    assert one[-1]["components"] == both[-1]["components"]
    assert one[-1]["card"] == both[-1]["card"]
    cut_only = build_etk(config(treatment="none", mark=False), CARDS)
    assert len(cut_only["operations"]) == 1


@pytest.mark.parametrize("value", [None, "", 0, -1, "bad", "NaN", "Infinity", float("inf")])
def test_invalid_cut_lengths_fail(value):
    raw = config()
    raw["rework"]["cutLength"] = value
    with pytest.raises(ValueError, match="длина реза"):
        build_etk(raw, CARDS)


@pytest.mark.parametrize("value", [None, 0, -1, "NaN", "Infinity", 1.5, 9007199254740992])
def test_invalid_piece_quantities_fail(value):
    raw = config()
    raw["partQty"] = value
    with pytest.raises(ValueError, match="количество"):
        build_etk(raw, CARDS)


def test_missing_template_and_unselected_manufacturing_operations_fail():
    raw = config()
    raw["ops"][1]["templateId"] = "missing"
    with pytest.raises(ValueError, match="карточка"):
        build_etk(raw, CARDS)
    raw = config()
    raw["ops"].append({"type": "insHousing"})
    with pytest.raises(ValueError, match="операции"):
        build_etk(raw, CARDS)
    raw = config()
    raw["ops"].reverse()
    with pytest.raises(ValueError, match="операции"):
        build_etk(raw, CARDS)


def test_units_and_strip_bounds_and_mark_material_are_validated():
    raw = config(ends="AB")
    raw["rework"]["stripLength"] = 126
    with pytest.raises(ValueError, match="зачистка"):
        build_etk(raw, CARDS)
    raw = config()
    raw["rework"]["input"]["unit"] = "Шт."
    with pytest.raises(ValueError, match="метрах"):
        build_etk(raw, CARDS)
    raw = config()
    raw["ops"][-1]["marks"][0].update(art="ТУТ 4/2", name="ТУТ 4/2")
    with pytest.raises(ValueError, match="длина трубки"):
        build_etk(raw, CARDS)


def test_issue_and_reference_rederive_keep_mode_input_and_consumption():
    raw = config()
    original = deepcopy(raw)
    enrich = lambda c: with_technology(c, None)[0]
    document, conflicts = derive(raw, None, enrich, CARDS)
    assert conflicts == []
    now = datetime(2026, 11, 11, 12)
    card = ProjectTechcard.create(project="P-1", item_index="КБ13", product_name="Доработанное изделие",
                                 source_calculation="", deadline=None, now=now).rebuild(document, now).issue(now)
    restored = json.loads(json.dumps(card.document["build_config"], ensure_ascii=False))
    again, conflicts = derive(restored, None, enrich, CARDS)
    assert conflicts == []
    assert again == document
    assert again["build_config"]["rework"] == raw["rework"]
    assert raw == original


def test_absent_mode_does_not_activate_rework_even_for_663_project():
    raw = {"projectNo": "663К.1", "assemblyName": "Изделие", "partQty": 10,
           "wires": [{"art": "Провод", "name": "Провод", "length": 250, "qty": 1}],
           "ops": [{"type": "cutWire", "templateId": BY_CODE["CUT_WIRE_hand"], "tOp": "6"}]}
    legacy = build_etk(raw, CARDS)
    with_unused = build_etk({**raw, "rework": config()["rework"]}, CARDS)
    with_unused["build_config"].pop("rework")
    assert legacy == with_unused


def test_combined_manufacturing_card_and_changed_physical_route_are_rejected():
    raw = config(treatment="none", mark=False)
    raw["ops"][0]["templateId"] = BY_CODE["CUT_WIRE+2strip"]
    with pytest.raises(ValueError, match="карточка не соответствует"):
        build_etk(raw, CARDS)
    raw = config()
    raw["route"] = {"blocks": [], "edges": [{"from": "cut", "to": "strip", "per_unit": 2}]}
    with pytest.raises(ValueError, match="одно изделие последовательно"):
        build_etk(raw, CARDS)


def test_reference_dimensions_and_mark_count_survive_but_raw_wire_consumption_does_not():
    raw = config()
    raw["wires"] = [{"art": "ДИ-1", "outerDiameter": "3", "conductorDiameter": "1", "qty": 999, "length": 999}]
    raw["ops"][0]["wires"] = [{"art": "Чужой провод", "qty": 1000, "length": 1000}]
    raw["ops"][-1]["marks"][0]["qty"] = 2
    doc = build_etk(raw, CARDS)
    cut, *_, mark = doc["operations"]
    assert cut["components"][0]["norm"] == "2,5"
    assert doc["build_config"]["wires"][0]["outerDiameter"] == "3"
    assert doc["build_config"]["wires"][0]["conductorDiameter"] == "1"
    assert "wires" not in doc["build_config"]["ops"][0]
    assert mark["components"][0]["norm"] == "20"
    assert mark["time"][0]["norm"] == "2"


def test_database_inputs_and_issued_revision_keep_rework(tmp_path):
    from app.modules.techcards.application import ChangeProjectTechcardCommand, issue_project_techcard
    from tests.techcards.library import load_library
    from tests.techcards.test_etk_inputs_overrides import _app, _build, _change

    engine, factory, card = _app(tmp_path)
    try:
        templates = json.loads((BACKEND / "data/techcard-library.json").read_text(encoding="utf-8"))["templates"]
        load_library(factory, templates)
        raw = config()
        built = _build(factory, card, raw, inputs=raw)
        issued = issue_project_techcard(ChangeProjectTechcardCommand(**_change(built, 1)), factory())
        with factory() as uow:
            saved = uow.techcards.get(issued.id)
            assert saved.inputs == raw
            assert saved.document["kit"]["mode"] == "rework"
            revisions = uow.techcards.revisions(issued.id)
            assert revisions[-1].document["build_config"]["rework"] == raw["rework"]
    finally:
        engine.dispose()


def test_rework_route_keeps_product_names_and_proposes_the_same_chain():
    from app.modules.techcards.etk_builder import propose_route

    doc = build_etk(config(ends="AB"), CARDS)
    assert [block["name"] for block in doc["route"]["blocks"]] == [op["result"][0]["name"] for op in doc["operations"]]
    assert all(edge["per_unit"] == 1 for edge in doc["route"]["edges"])
    proposed = propose_route(doc)
    assert proposed["same"] is True
    assert proposed["route"] == doc["route"]


@pytest.mark.parametrize("length,quantity,norm", [("0.01", 1, "0,00001"), (0.0000001, 10, "0,000000001"), (250, 10, "2,5")])
def test_positive_consumption_is_not_rounded_to_zero(length, quantity, norm):
    raw = config(treatment="none", mark=False)
    raw["partQty"] = quantity
    raw["rework"]["cutLength"] = length
    assert build_etk(raw, CARDS)["operations"][0]["components"][0]["norm"] == norm


@pytest.mark.parametrize("side,letter", [("A", "А"), ("B", "В")])
def test_selected_end_and_length_are_visible_in_template_steps(side, letter):
    doc = build_etk(config(ends=side), CARDS)
    for op in doc["operations"][1:3]:
        text = " ".join(step["text"] for step in op["card"]["steps"])
        assert f"конец {letter}" in text
        assert "5 мм" in text


def test_reference_enrichment_keeps_dimensions_linear_time_and_refreshes():
    from app.workflows.techcard_build import Refs

    raw = config(treatment="none", mark=False)
    raw["wires"] = [{"art": "ДИ-1", "name": "Давальческое изделие", "qty": 1, "length": 250}]
    def run(diameter, speed):
        refs = Refs(snapshot={"schema_version": 1},
                    wire_rows=({"Марка": "ДИ-1", "Наружный диаметр, мм": diameter},),
                    operations=({"opNumber": "CUT_WIRE_hand", "Тип операции": "Погонный", "Скорость проката, сек/м": speed,
                                 "Скорость работы инструмента": "0,2", "Кол-во операций инструмента": "5"},))
        doc, conflicts = derive(raw, None, lambda c: with_technology(c, refs=refs)[0], CARDS)
        assert conflicts == []
        return doc
    doc = run("3", "3")
    assert doc["build_config"]["wires"][0]["outerDiameter"] == 3
    assert doc["operations"][0]["time"][0]["norm"] == "0,18"
    assert run("3", "3") == doc
    refreshed = run("4", "0,25")
    assert refreshed["build_config"]["wires"][0]["outerDiameter"] == 4
    assert refreshed["operations"][0]["time"][0]["norm"] == "0,33"
    assert refreshed["operations"][0]["components"] == doc["operations"][0]["components"]


def test_other_modes_connection_and_coax_settings_do_not_leak_into_rework():
    raw = config()
    clean = build_etk(raw, CARDS)
    raw.update(tab="coax", coax={"vswrNorm": {"vswrMax": 2}},
               connections=[{"wire": "Чужой провод", "from": {"end": "A", "conn": "XP1", "pin": 1},
                             "to": {"end": "B", "conn": "XP2", "pin": 2}}])
    assert build_etk(raw, CARDS) == clean
