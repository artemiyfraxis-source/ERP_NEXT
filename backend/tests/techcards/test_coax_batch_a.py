"""Shared generator fixtures: full stripping, HFM support and exact ETK quantities."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import normalize_record_edit, parse_operation_library
from app.modules.techcards.template_sheets import make_template_id

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "docs/data/operation-templates-hfm-1116"
FIXTURES = json.loads((ROOT / "frontend/src/__fixtures__/fakraBatchAConfigs.json").read_text(encoding="utf-8"))
ERP = json.loads((ROOT / "backend/data/techcard-library-erp.json").read_text(encoding="utf-8"))
BASE = json.loads((ROOT / "backend/data/techcard-library.json").read_text(encoding="utf-8"))
CARDS = {card.code: card for card in parse_operation_library([*BASE["templates"], *ERP["templates"]])}
PAYLOAD = json.loads((FOLDER / "payload.json").read_text(encoding="utf-8"))


def _build(fixture):
    config = deepcopy(fixture["config"])
    by_id = {card.source_id: card for card in CARDS.values()}
    cards = [replace(by_id.get(op["templateId"]) or CARDS[op["templateId"]], source_id=op["templateId"])
             for op in config["ops"]]
    return build_etk(config, cards)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda item: item["kb"])
def test_machine_strips_both_ends_and_preserves_side_ownership(fixture):
    doc = _build(fixture)
    ops = doc["operations"]
    assert ops[0]["code"] == "CUT_COAX+2strip"
    assert ops[0]["fields"]["coaxStripSides"] == ["A", "B"]
    assert "coax" in ops[0]["fields"] and "coaxB" in ops[0]["fields"]
    text = " ".join(step["text"] for step in ops[0]["card"]["steps"])
    if fixture["kb"] == "КБ3":
        assert ops[0]["fields"]["coax"]["dimensionOrder"] == "HFM"
        assert "MA_AMV003" in text and "после полной разделки" in text
    else:
        assert ops[0]["fields"]["coax"]["dimensionOrder"] == "Amphenol"
        assert "размер 1,0 мм — участок открытого диэлектрика" in text and "13,9 ±0,2 мм" in text
    assert not any(op["type"] == "coaxStrip" for op in ops)
    route = doc["route"]
    assert [block["name"] for block in route["blocks"]][:-1] == [
        "Заготовки", "Кабель с метками и деталями", "Сторона А собрана", "Сторона В собрана"]
    for side, name in (("A", "Сторона А собрана"), ("B", "Сторона В собрана")):
        block = next(block for block in route["blocks"] if block["name"] == name)
        assert all(fixture["config"]["ops"][position - 1]["side"] == side for position in block["ops"])
    assert sorted(position for block in route["blocks"] for position in block["ops"]) == list(range(1, len(ops) + 1))


def test_hfm_support_is_four_operations_per_end_with_no_invented_consumption_or_norms():
    doc = _build(FIXTURES[0])
    ops = doc["operations"]
    support = [op for op in ops if op["type"] == "coaxSupportCrimp"]
    assert len(support) == 2
    for op in support:
        assert op["code"] == "PRS_HFM_SUPPORT"
        assert op["components"] == []  # The sleeves belong to the connector kit, not another BOM demand.
        assert "coaxEnd" in op["fields"]
        text = " ".join(step["text"] for step in op["card"]["steps"])
        assert "полностью разделан" in text and "11WS001_A" in text
        assert "в цеху нет" in text and "не задано" in text
        assert all(not tool.get("inventory_number") for tool in op["card"]["tooling"])
    assert ops[0]["components"][0]["norm"] == "78"  # 4 cables * (1250+25+25) mm * 15 assemblies.
    assert ops[0]["components"][0]["unit"] == "М."
    assert FIXTURES[0]["config"]["coax"]["marks"][0]["text"] == "FRX.708К.120961/AA.{№}"


@pytest.mark.parametrize("channels", [2, 4, 6])
def test_channel_counts_preserve_physical_cables_until_common_housing(channels):
    fixture = deepcopy(FIXTURES[0])
    config = fixture["config"]
    # References alone must be sufficient; replay old operation units without multiplying kits.
    config["coax"].pop("cables")
    for side in ("sideA", "sideB"):
        config["coax"][side]["channels"] = channels
    for op in config["ops"]:
        op["tOp"] = "60"
    before = deepcopy(fixture)
    doc = _build(fixture)
    assert fixture == before
    ops = doc["operations"]
    assert ops[0]["result"][0]["norm"] == str(15 * channels)
    assert ops[0]["time"][0]["norm"] == str(15 * channels)
    text = " ".join(step["text"] for step in ops[0]["card"]["steps"])
    if channels == 4:
        assert text.count("4 кабеля на сборку: 15 сборок × 4 = 60 шт.") == 1
    for side in ("A", "B"):
        for code in ("PRS_HFM_SUPPORT", "PRS_HFM_PIN", "INS_HFM_CONTACT", "PRS_HFM_SHIELD"):
            op = next(op for op in ops if op["code"] == code and op["side"] == side)
            assert op["time"][0]["norm"] == str(15 * channels)
            assert f"×{channels}" in op["card"]["name"]
        housing = next(op for op in ops if op["code"] == "INS_HFM_HOUSING" and op["side"] == side)
        assert housing["result"][0]["norm"] == "15"
        assert housing["time"][0]["norm"] == "15"
    first_housing = next(i for i, op in enumerate(ops) if op["code"] == "INS_HFM_HOUSING")
    assert ops[first_housing]["semis"][0]["norm"] == str(15 * channels)
    assert all(op["result"][0]["norm"] == "15" for op in ops[first_housing:] if op["type"] != "coaxCutTut")
    pins = [op for op in ops if op["code"] == "PRS_HFM_PIN"]
    assert [op["components"][0]["norm"] for op in pins] == ["15", "15"]  # complete connector kits
    cut_tubes = next(op for op in ops if op["type"] == "coaxCutTut")
    assert cut_tubes["result"][0]["norm"] == str(3 * channels * 15)
    assert all(mark["qty"] == str(channels) and mark["batch"] == str(channels * 15)
               for mark in cut_tubes["fields"]["marks"])
    replay = {**fixture, "config": doc["build_config"]}
    assert _build(replay) == doc


def test_one_channel_fakra_keeps_assembly_and_marking_quantities():
    ops = _build(FIXTURES[1])["operations"]
    assert ops[0]["result"][0]["norm"] == "15"
    text = " ".join(step["text"] for step in ops[0]["card"]["steps"])
    assert "кабеля на сборку" not in text
    assert next(op for op in ops if op["type"] == "coaxCutTut")["result"][0]["norm"] == "45"
    assert all(op["result"][0]["norm"] == "15" for op in ops if op["type"] != "coaxCutTut")


def test_legacy_hfm_generic_housing_card_groups_only_at_coax_body():
    frozen = json.loads((ROOT / "frontend/src/__fixtures__/fakra708Configs.json").read_text(encoding="utf-8"))
    fixture = next(item for item in frozen if item["config"]["coax"].get("cables") == 4)
    config = deepcopy(fixture["config"])
    # Historical template IDs can differ from today's library; keep their actual operation codes.
    cards = [replace(CARDS[op["opNum"]], source_id=op["templateId"])
             for op in config["ops"] if op.get("opNum") in CARDS]
    doc = build_etk(config, cards)
    for raw, op in zip(config["ops"], doc["operations"]):
        if str(raw.get("opKey") or "").startswith("coaxBody"):
            assert op["result"][0]["norm"] == "15"
        if raw.get("opKey") == "coaxInsertA":
            assert op["result"][0]["norm"] == "60"
    assert build_etk(doc["build_config"], cards) == doc


@pytest.mark.parametrize("cables,saved_units,expected_units", [(1, 3, 3), (4, 3, 12), (4, 12, 12), (4, 8, 8)])
def test_saved_hfm_without_marks_recounts_tubes_once(cables, saved_units, expected_units):
    frozen = json.loads((ROOT / "frontend/src/__fixtures__/fakra708Configs.json").read_text(encoding="utf-8"))
    fixture = next(item for item in frozen if item["config"]["coax"].get("cables") == 4)
    config = deepcopy(fixture["config"])
    config["coax"]["cables"] = cables
    assert "marks" not in config["coax"]
    tube_types = {"coaxCutTut", "coaxInsTut", "coaxHeat"}
    for op in config["ops"]:
        assert "marks" not in op
        if op["type"] in tube_types:
            op["units"] = saved_units
            op["label"] = f"{op['type']} ×{saved_units}"
            op["tOp"] = "60"
    before = deepcopy(config)
    cards = [replace(CARDS[op["opNum"]], source_id=op["templateId"])
             for op in config["ops"] if op.get("opNum") in CARDS]
    doc = build_etk(config, cards)
    assert config == before
    tubes = [op for op in doc["operations"] if op["type"] in tube_types]
    assert len(tubes) == 3
    assert tubes[0]["result"][0]["norm"] == str(expected_units * 15)
    assert all(op["time"][0]["norm"] == str(expected_units * 15) for op in tubes)
    assert all(op["card"]["name"].endswith(f"×{expected_units}") for op in tubes)
    assert all(op["units"] == expected_units for op in doc["build_config"]["ops"] if op["type"] in tube_types)
    assert build_etk(doc["build_config"], cards) == doc


@pytest.mark.parametrize("last_type", ["coaxCut", "coaxSupportCrimp", "coaxPinCrimp", "coaxShield"])
def test_multichannel_partial_route_still_outputs_cables(last_type):
    fixture = deepcopy(FIXTURES[0])
    operations = fixture["config"]["ops"]
    last = next(i for i, op in enumerate(operations) if op["type"] == last_type)
    fixture["config"]["ops"] = operations[:last + 1]
    assert _build(fixture)["operations"][-1]["result"][0]["norm"] == "60"


@pytest.mark.parametrize("side_count", [1, 2])
@pytest.mark.parametrize("stale_marks", [False, True])
def test_saved_generic_multichannel_tubes_count_connected_ends(side_count, stale_marks):
    fixture = deepcopy(FIXTURES[0])
    config = fixture["config"]
    config["coax"]["sideA"] = {"article": "GEN-4", "channels": 4}
    config["coax"]["sideB"] = {"article": "GEN-4"} if side_count == 2 else None
    config["coax"].pop("markMaterial", None)
    if not stale_marks:
        config["coax"].pop("marks", None)
    config["ops"] = [op for op in config["ops"] if op["type"] in {"coaxCut", "coaxCutTut", "coaxInsTut", "coaxHeat"}]
    for op in config["ops"]:
        op["units"] = side_count
        op["tOp"] = "60"
        op.pop("marks", None)
    doc = _build(fixture)
    tubes = [op for op in doc["operations"] if op["type"] != "coaxCut"]
    assert all(op["time"][0]["norm"] == str(15 * 4 * side_count) for op in tubes)
    assert tubes[0]["result"][0]["norm"] == str(15 * 4 * side_count)
    assert _build({"config": doc["build_config"]}) == doc


def test_assembly_label_is_not_multiplied_by_cable_count():
    fixture = deepcopy(FIXTURES[0])
    fixture["config"]["coax"]["marks"][0]["scope"] = "assembly"
    for op in fixture["config"]["ops"]:
        if op.get("marks"):
            op["marks"][0]["scope"] = "assembly"
    cut = next(op for op in _build(fixture)["operations"] if op["type"] == "coaxCutTut")
    assert cut["result"][0]["norm"] == "135"  # (1 product label + 2 labels * 4 cables) * 15
    assert [(mark["qty"], mark["batch"]) for mark in cut["fields"]["marks"]] == [("1", "15"), ("4", "60"), ("4", "60")]


@pytest.mark.parametrize("clamped", [1, 2])
def test_generic_multichannel_clamp_and_crimp_counts(clamped):
    coax = {"cables": 4, "cableArt": "RTK 031", "cableLength": 1250, "sleeveArt": "Втулка",
            "sideA": {"article": "GEN-4", "shield": "прижим"},
            "sideB": {"article": "GEN-4", "shield": "прижим" if clamped == 2 else "обжим"}}
    config = {"mode": "coax", "partQty": 15, "coax": coax, "ops": [
        {"type": "coaxCut", "opKey": "coaxCut", "templateId": "CUT_COAX+2strip", "units": 1, "tOp": "60"},
        {"type": "coaxInsSleeve", "opKey": "coaxInsSleeve", "templateId": "INS_SLEEVE_coax", "units": 2,
         "clampSides": ["A", "B"][:clamped], "tOp": "60"},
        {"type": "coaxHousing", "opKey": "coaxHousingA", "templateId": "INS_housing_COAX", "side": "A", "tOp": "60"},
    ]}
    doc = _build({"config": config})
    cut, sleeve, housing = doc["operations"]
    assert cut["result"][0]["norm"] == sleeve["semis"][0]["norm"] == "60"
    assert sleeve["time"][0]["norm"] == "120"  # two ends * 4 cables * 15 assemblies
    assert sleeve["fields"]["coaxParts"]["clampCount"] == clamped * 4
    assert sleeve["components"] == ([] if clamped == 2 else [
        {"art": "Втулка", "name": "", "norm": "60", "unit": "Шт."}])
    if clamped == 2:
        assert "обжимную гильзу" not in " ".join(step["text"] for step in sleeve["card"]["steps"])
    assert housing["result"][0]["norm"] == "15"
    assert housing["time"][0]["norm"] == "15"


def test_amphenol_prep_after_strip_takes_ttk_once_in_metres():
    ops = _build(FIXTURES[1])["operations"]
    prep = next(op for op in ops if op["code"] == "INS_FAKRA_BH_PREP")
    ttk = next(row for row in prep["components"] if "ТТК" in row["art"])
    assert (ttk["norm"], ttk["unit"]) == ("0,9", "М.")
    assert sum(1 for op in ops for row in op["components"] if "ТТК" in row["art"]) == 1
    assert [op["code"] for op in ops if op["code"].endswith("BH_PIN")] == ["SDR_FAKRA_BH_PIN"]


def test_support_card_payload_matches_seed_and_keeps_unknown_values_unknown():
    assert [item["code"] for item in PAYLOAD] == ["PRS_HFM_SUPPORT"]
    item = PAYLOAD[0]
    card = CARDS[item["code"]]
    edit = normalize_record_edit(item["record"])
    assert card.source_id == make_template_id(f"{item['code']} | {edit.name}", ())
    assert (card.name, card.safety, card.defects, card.revision, card.steps, card.tooling) == (
        edit.name, edit.safety, edit.defects, edit.revision, edit.steps, edit.tooling)
    assert card.name == "Обжим опорной гильзы HFM"
    assert any("11WS001_A" in tool.name and "в цеху нет" in tool.name for tool in card.tooling)
    assert not any(tool.inventory_number for tool in card.tooling)
    assert set(item["create"]["row"]["norms"].values()) == {""}
    assert item["metadata"]["production_time"] == "не задано"
    assert all(b"\r\n" not in path.read_bytes() for path in (FOLDER / "payload.json", FOLDER / "apply_templates.py"))
