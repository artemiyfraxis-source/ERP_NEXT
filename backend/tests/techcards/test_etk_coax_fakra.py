import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import build_etk, with_coax_connectors
from app.modules.techcards.operation_cards import parse_operation_library

ROOT = Path(__file__).resolve().parents[2]
CARDS = parse_operation_library(json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))["templates"])
CARD = {card.code: card for card in CARDS}

FRESH = json.loads((ROOT.parent / "frontend/src/__fixtures__/fakra1116Configs.json").read_text(encoding="utf-8"))
ERP_CARDS = parse_operation_library(json.loads((ROOT / "data/techcard-library-erp.json").read_text(encoding="utf-8"))["templates"])


def _fresh_cards(config):
    # New card bodies are owned by T2. Declare their codes without importing that worker's diff.
    by_code = {card.code: card for card in [*CARDS, *ERP_CARDS]}
    return [replace(by_code.get(op["templateId"], CARD["INS_TUT"]), source_id=op["templateId"], code=op["templateId"])
            for op in config["ops"]]


@pytest.mark.parametrize("fixture", FRESH, ids=lambda item: item["kb"])
def test_new_fakra_route_independent_preparations_and_label_inputs(fixture):
    config = deepcopy(fixture["config"])
    doc = build_etk(config, _fresh_cards(config))
    ops = doc["operations"]
    assert [block["name"] for block in doc["route"]["blocks"]][:-1] == [
        "Заготовки", "Кабель с метками и деталями", "Сторона А собрана", "Сторона В собрана"]
    assert len(doc["route"]["blocks"]) == 5
    assert [position for block in doc["route"]["blocks"] for position in block["ops"]] == list(range(1, len(ops) + 1))
    assert all(not op["semis"] for op in ops[:3])
    assert len({op["result"][0]["name"] for op in ops[:3]}) == 3
    assert [op["result"][0]["norm"] for op in ops[:3]] == ["15", "45", "45"]
    assert _inputs(ops[0]) == {"RTK 031": ("72,825", "М.")}
    # Frozen payload keeps its parts, but label stock is a printer consumable.
    assert _inputs(ops[1]) == {}
    assert _inputs(ops[2]) == {"ТУТ 4/2мм, прозрачная": ("2,7", "М.")}
    assert ops[3]["semis"][1]["name"] == ops[2]["result"][0]["name"]
    assert ops[3]["semis"][1]["norm"] == "45"
    assert ops[6]["semis"][1]["name"] == ops[1]["result"][0]["name"]
    assert ops[6]["semis"][1]["norm"] == "45"
    texts = " ".join(step["text"] for step in ops[6]["card"]["steps"])
    for mark in config["coax"]["marks"]:
        assert mark["text"] in texts
    assert "100 мм" in texts
    assert "прозрачную ТУТ" in texts
    heat = next(op for op in ops if op["type"] == "coaxHeat")
    heat_text = " ".join(step["text"] for step in heat["card"]["steps"])
    assert "этикетку" in heat_text
    assert "зону соединения" not in heat_text
    assert "нахлёст" not in heat_text
    assert "coaxParts" not in heat["fields"]  # no misleading sleeve-over-connector sketch
    prep = " ".join(step["text"] for op in ops if op["code"] in {"INS_FAKRA_ST_PREP", "INS_FAKRA_RA_PREP"}
                    for step in op["card"]["steps"])
    assert "крышку 59Z163-000/51, уплотнитель 59K14B-1M4/50, гильзу" in prep
    assert "неразделанный кабель" not in prep
    assert "до разделки" not in prep
    assert "установить по отсутствующей" not in prep
    color = next(op for op in ops if op["type"] == "coaxConnColor")
    assert config["coax"]["connColor"]["color"] in color["card"]["steps"][0]["text"]
    assert color["result"][0]["norm"] == "15"  # two connectors, still one product
    config["route"] = doc["route"]
    rebuilt = build_etk(config, _fresh_cards(config))
    assert rebuilt["operations"] == ops  # saved stage kit must not make its blank operations sequential


def test_label_tape_is_a_printer_consumable_while_output_counts_labels():
    config = deepcopy(FRESH[0]["config"])
    printed = next(op for op in config["ops"] if op["opKey"] == "coaxPrintLabel")
    printed["parts"] = [{"art": "VL-S641", "name": "Лента Vell VL-S641", "qty": 0.18, "unit": "М."}]
    assert printed["units"] == 3
    config["partQty"] = 15
    doc = build_etk(config, _fresh_cards(config))
    output = next(op for op in doc["operations"] if op["code"] == "PRINT_MARK_LABEL")
    assert _inputs(output) == {}
    assert any("Лента Vell VL-S641, 18 мм" in tool["name"] for tool in output["card"]["tooling"])
    assert any("лента из состава не расходуется" in text for text in doc["warnings"])
    assert output["result"][0]["norm"] == "45"
    assert build_etk(config, _fresh_cards(config))["operations"] == doc["operations"]


def test_finished_length_rebuild_is_idempotent_and_missing_allowance_blocks():
    config = deepcopy(FRESH[0]["config"])
    cards = _fresh_cards(config)
    config["coax"]["cableLength"] = 4910  # stale cut must never be used as the finished basis
    first = build_etk(config, cards)
    assert first["build_config"]["coax"]["cableLength"] == 4855
    assert build_etk(first["build_config"], cards) == first
    fields = first["operations"][0]["fields"]
    assert fields["finishedLength"] == 4800
    assert (fields["lPlusA"], fields["lPlusB"], fields["cutTolerance"]) == (30, 25, "0…+5 мм")
    assert fields["coax"]["l1"] == "9,7"
    assert fields["coaxB"]["l1"] == "11,6"
    del config["coax"]["lPlusB"]
    with pytest.raises(ValueError, match="припуски"):
        build_etk(config, cards)


def test_mixed_end_cut_card_commands_only_machine_strippable_side():
    config = deepcopy(FRESH[0]["config"])
    config["ops"][0]["stripSides"] = ["B"]
    config["ops"][0]["opKey"] = "coaxCutStrip"
    config["coax"]["sideA"]["kind"] = "bulkhead"
    cut = build_etk(config, _fresh_cards(config))["operations"][0]
    text = " ".join(step["text"] for step in cut["card"]["steps"])
    assert "ТОЛЬКО СО СТОРОНЫ В" in text
    assert "С ДВУХ СТОРОН" not in text
    assert "обеих сторон" not in text
    assert "coax" not in cut["fields"]
    assert cut["fields"]["coaxB"]["connector"] == "59K25H-1M4A4"


def test_unprotected_labels_are_not_described_as_tubes():
    config = deepcopy(FRESH[0]["config"])
    config["coax"]["markProtect"] = False
    config["ops"] = [op for op in config["ops"] if not op.get("protective")]
    doc = build_etk(config, _fresh_cards(config))
    install = next(op for op in doc["operations"] if op["code"] == "INS_MARK_LABEL")
    assert "этикетки наклеены" in install["result"][0]["name"]
    assert "ТУТ" not in install["result"][0]["name"]


def test_missing_label_material_cannot_consume_clear_tubing():
    config = deepcopy(FRESH[0]["config"])
    config["ops"][1]["parts"] = []
    doc = build_etk(config, _fresh_cards(config))
    assert doc["operations"][1]["components"] == []
    assert not any("не задан материал для печати этикеток" in warning for warning in doc["warnings"])
    assert any("Лента Vell VL-S641, 18 мм" in tool["name"] for tool in doc["operations"][1]["card"]["tooling"])


@pytest.mark.parametrize("method,phrase", [("paint", "маркером-краской"), ("laser", "лазерной гравировкой"), ("tape", "цветной лентой")])
def test_colour_method_and_connector_quantity(method, phrase):
    config = deepcopy(FRESH[0]["config"])
    op = next(op for op in config["ops"] if op["type"] == "coaxConnColor")
    op["connColor"]["method"] = method
    op["templateId"] = "MARK_CONN_" + method.upper()
    op["tOp"] = "60"
    operation = next(op for op in build_etk(config, _fresh_cards(config))["operations"] if op["type"] == "coaxConnColor")
    assert phrase in operation["card"]["steps"][0]["text"]
    assert operation["time"][0]["norm"] == "30"  # 2 connectors * 15 products * 1 minute


def _etk(ops: list[dict], **coax: object) -> list[dict]:
    """Сборка FAKRA/HFM вкладки «Коакс» (708К, владелец 25.10): детали комплекта конца лежат в самой операции."""
    config = {"mode": "coax", "partQty": 15, "coax": {
        "cableLength": 1250, "cableArt": "RTK 031", "cableName": "RTK 031",
        "sideA": {"article": "59K13o-1M4A4", "connName": ""}, "sideB": {"article": "59K13o-1M4A4", "connName": ""}, **coax,
    }, "ops": [{**op, "templateId": CARD[op.pop("code")].source_id} for op in ops]}
    return build_etk(config, CARDS)["operations"]


def _inputs(operation: dict) -> dict[str, tuple[str, str]]:
    return {row["art"]: (row["norm"], row["unit"]) for row in operation["components"]}


def test_fakra_operation_takes_its_parts_from_the_operation_itself():
    # Крышка и уплотнитель — на оба конца (2 на изделие), ТТК — в метрах; всё × партия 15.
    put_on = _etk([{"type": "coaxInsSleeve", "side": "", "units": 2, "code": "INS_SLEEVE_coax", "parts": [
        {"art": "59K14B-1M4/50", "name": "Уплотнитель", "qty": 2}, {"art": "ТТК 6/2", "name": "ТТК", "qty": 0.06, "unit": "М."}]}])[0]
    assert _inputs(put_on) == {"59K14B-1M4/50": ("30", "Шт."), "ТТК 6/2": ("0,9", "М.")}


def test_four_cables_of_hfm_multiply_the_cut_input():
    cut = _etk([{"type": "coaxCut", "side": "", "units": 4, "code": "CUT_WIRE_auto"}], cables=4)[0]
    assert _inputs(cut) == {"RTK 031": ("75", "М.")}  # 1,25 м × 4 кабеля × 15 изделий


def test_operation_without_tooling_has_no_tool_lines_but_keeps_its_steps():
    with_tools = _etk([{"type": "coaxShield", "side": "A", "units": 1, "code": "PRS_SLEEVE_coax"}])[0]
    without = _etk([{"type": "coaxShield", "side": "A", "units": 1, "code": "PRS_SLEEVE_coax", "noTooling": True}])[0]
    assert with_tools["card"]["tooling"]
    assert without["card"]["tooling"] == []
    assert [step["text"] for step in without["card"]["steps"]] == [step["text"] for step in with_tools["card"]["steps"]]


def test_fakra_operation_shows_its_own_title_instead_of_the_card_name():
    own = _etk([{"type": "coaxPinCrimp", "side": "A", "code": "PRS_SLEEVE_coax", "label": "Обжим центрального контакта (А)"}])[0]
    plain = _etk([{"type": "coaxPinCrimp", "side": "A", "code": "PRS_SLEEVE_coax"}])[0]
    assert own["card"]["name"] == "Обжим центрального контакта (А)"
    assert plain["card"]["name"] != own["card"]["name"]


def test_put_on_of_one_end_sketches_only_that_end():
    put_on = _etk([{"type": "coaxInsSleeve", "side": "B", "units": 1, "code": "INS_SLEEVE_coax"}])[0]
    config = {"coax": {"sideA": {"article": "59K13o-1M4A4"}, "sideB": {"article": "59K13o-1M4A4"}}}
    ends = with_coax_connectors(put_on, config)["fields"]["coaxParts"]["ends"]
    assert [end["side"] for end in ends] == ["В"]
    both = with_coax_connectors({**put_on, "side": ""}, config)["fields"]["coaxParts"]["ends"]
    assert [end["side"] for end in both] == ["А", "В"]
