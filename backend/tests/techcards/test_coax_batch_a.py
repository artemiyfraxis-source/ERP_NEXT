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
