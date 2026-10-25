import json
from pathlib import Path

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library

ROOT = Path(__file__).resolve().parents[2]
CARDS = parse_operation_library(json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))["templates"])
CARD = {card.code: card for card in CARDS}


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
