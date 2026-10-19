"""Splice methods and free tails of «Сложная сборка» (16.10) in the ETK: the 713К.4 КБ1 «Жгут НВ4» shape — 4 splices
crimped with НШВ sleeves (1,0-8 ×2, 0,5-8 ×2), 2 free tails stripped and tinned. A config without `method` builds the
splice exactly as before (soldered, no parts, no words added)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pytest

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
TEMPLATES = {"solder": "sdr-w-w-пайка", "sleeve": "prs-ншви-опрессовка-наконечников", "band": "prs-copper-ленточный-обжим"}
SLEEVES = [
    {"art": "НШВ 1,0-8", "name": "Наконечник штыревой втулочный НШВ 1,0-8", "count": 2},
    {"art": "НШВ 0,5-8", "name": "Наконечник штыревой втулочный НШВ 0,5-8", "count": 2},
]
WIRES = [
    {"name": "НВ4 1,0 красный", "art": "НВ4 1,0", "qty": 4, "length": 300},
    {"name": "НВ4 0,5 белый", "art": "НВ4 0,5", "qty": 4, "length": 250},
    {"name": "НВ4 0,5 чёрный", "art": "НВ4 0,5", "qty": 2, "length": 200},
]
TAIL_WIRES = [WIRES[1]]


def config(method: str | None, *, sleeves: list[dict[str, Any]] | None = None, part_qty: int = 5) -> dict[str, Any]:
    splice: dict[str, Any] = {
        "type": "splice", "opKey": "splice", "templateId": TEMPLATES[method or "solder"], "opNum": "", "units": 4,
        "tPrep": 60, "tOp": 30,
    }
    if method is not None:
        splice["method"] = method
        splice["sleeves"] = deepcopy(SLEEVES if sleeves is None else sleeves)
    return {
        "assemblyName": "Жгут НВ4", "assemblyIndex": "713К.4 КБ1", "partQty": part_qty, "bigAssembly": True,
        "wires": deepcopy(WIRES),
        "ops": [
            {"type": "cutWire", "opKey": "cut", "templateId": "cut-wire-auto-резка", "wires": deepcopy(WIRES)},
            {"type": "stripCable", "opKey": "stripSpl", "templateId": "strip-wire-разделка", "tutCount": 8},
            splice,
            {"type": "stripCable", "opKey": "stripTail", "templateId": "strip-wire-разделка", "tutCount": 2},
            {"type": "tin", "opKey": "tinTail", "templateId": "sdr-tin-лужение", "tOp": 6, "wireIdxs": [1],
             "wires": deepcopy(TAIL_WIRES), "units": 2},
            {"type": "tin", "opKey": "tin", "templateId": "sdr-tin-лужение", "tOp": 6, "wires": deepcopy(WIRES[:1])},
        ],
    }


CUT = "Провода нарезаны: НВ4 1,0 — 4 шт по 300 мм; НВ4 0,5 — 4 шт по 250 мм; НВ4 0,5 — 2 шт по 200 мм"
CRIMPED = f"{CUT}; провода опрессованы гильзой НШВ 1,0-8 и НШВ 0,5-8"


def build(source: dict[str, Any]) -> dict[str, Any]:
    return build_etk(source, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


def op_by_key(document: dict[str, Any], key: str) -> dict[str, Any]:
    keys = [op.get("opKey") for op in document["build_config"]["ops"]]
    return document["operations"][keys.index(key)]


def test_sleeve_crimp_issues_each_sleeve_per_splice_for_the_batch():
    operation = op_by_key(build(config("sleeve")), "splice")
    assert (operation["code"], operation["title"]) == ("PRS_НШВИ", "PRS_НШВИ | Опрессовка наконечников")
    assert operation["components"] == [
        {"art": "НШВ 1,0-8", "name": "Наконечник штыревой втулочный НШВ 1,0-8", "norm": "10", "unit": "Шт."},
        {"art": "НШВ 0,5-8", "name": "Наконечник штыревой втулочный НШВ 0,5-8", "norm": "10", "unit": "Шт."},
    ]
    assert operation["result"][0]["name"] == CRIMPED
    assert operation["semis"][0]["name"] == CUT


def test_same_sleeve_on_two_lines_is_one_row_and_empty_lines_are_skipped():
    sleeves = [
        {"art": "НШВ 0,5-8", "name": "НШВ 0,5-8", "count": 1}, {"art": "НШВ 0,5-8", "name": "НШВ 0,5-8", "count": 3},
        {"art": "", "name": "", "count": 2}, {"art": "НШВ 1,0-8", "count": 0}, "мусор",
    ]
    operation = op_by_key(build(config("sleeve", sleeves=sleeves, part_qty=2)), "splice")
    assert operation["components"] == [{"art": "НШВ 0,5-8", "name": "НШВ 0,5-8", "norm": "8", "unit": "Шт."}]
    assert operation["result"][0]["name"] == f"{CUT}; провода опрессованы гильзой НШВ 0,5-8"


def test_band_crimp_takes_its_material_despite_a_card_without_parts():
    tape = [{"art": "Лента медная 0,1×5", "name": "Лента медная 0,1×5", "count": 4}]
    operation = op_by_key(build(config("band", sleeves=tape)), "splice")
    assert operation["code"] == "PRS_COPPER"
    assert operation["components"] == [{"art": "Лента медная 0,1×5", "name": "Лента медная 0,1×5", "norm": "20", "unit": "Шт."}]
    assert operation["result"][0]["name"] == f"{CUT}; провода соединены ленточным обжимом"
    assert op_by_key(build(config("band", sleeves=[])), "splice")["components"] == []


def test_solder_names_the_joint_and_issues_no_sleeve_left_from_another_method():
    operation = op_by_key(build(config("solder")), "splice")
    assert operation["code"] == "SDR_W_W"
    assert operation["components"] == []
    assert operation["result"][0]["name"] == f"{CUT}; провода спаяны"


@pytest.mark.parametrize("method", ["solder", "sleeve", "band"])
def test_every_method_keeps_the_splice_time_and_the_card_of_its_template(method):
    document = build(config(method))
    operation = op_by_key(document, "splice")
    assert operation["card"] is not None
    assert operation["time"][0]["norm"] == "10"  # 0,5 мин × 4 сростка × 5, подготовка в норму не входит
    assert not document["warnings"]


def test_config_without_method_builds_the_splice_as_before():
    """Pinned from the builder before 16.10: soldered, no parts, the cut passed on under its own name."""
    document = build(config(None))
    operation = op_by_key(document, "splice")
    assert (operation["code"], operation["connector"], operation["components"]) == ("SDR_W_W", "3", [])
    assert operation["result"] == [{"name": CUT, "norm": "", "unit": "Шт."}]
    assert operation["semis"] == [{"name": CUT, "norm": "", "unit": "Шт."}]
    assert operation["time"] == [{"name": CUT, "norm": "10"}]
    # A stray `sleeves` list without `method` is not issued either.
    stray = config(None)
    stray["ops"][2]["sleeves"] = deepcopy(SLEEVES)
    assert op_by_key(build(stray), "splice") == operation


def test_tail_tinning_counts_tail_ends_and_main_tinning_counts_wires():
    document = build(config("sleeve"))
    # 0,1 мин × 2 конца × 5 изделий; лужение основных концов — по проводам (4 шт).
    assert op_by_key(document, "tinTail")["time"][0]["norm"] == "1"
    assert op_by_key(document, "tin")["time"][0]["norm"] == "2"
    without_units = config("sleeve")
    del without_units["ops"][4]["units"]
    assert op_by_key(build(without_units), "tinTail")["time"][0]["norm"] == "2"  # по проводам (4 шт) — как раньше


def test_tail_ops_get_their_own_etk_keys_and_words_next_to_the_splice_strip_and_main_tinning():
    document = build(config("sleeve"))
    keys = [(op["code"], op["connector"], op["side"]) for op in document["operations"]]
    assert len(keys) == len(set(keys))
    assert [op_by_key(document, key)["connector"] for key in ("stripSpl", "stripTail", "tinTail", "tin")] == [
        "2", "свободные концы", "свободные концы", "6"]
    # Stripping the tails does not move the product on; tinning them does.
    assert op_by_key(document, "stripTail")["result"] == [
        {"name": f"{CRIMPED}, свободные концы разделаны", "norm": "10", "unit": "Шт."}]
    assert op_by_key(document, "tinTail")["result"][0]["name"] == f"{CRIMPED}, свободные концы облужены"
    assert op_by_key(document, "tin")["result"][0]["name"] == "Жгут НВ4"
    now = datetime.now(timezone.utc)
    card = ProjectTechcard.create(project="713", item_index="К.4 КБ1", product_name="Жгут НВ4", source_calculation="",
                                  deadline=None, now=now)
    card.rebuild(document, now)  # not refused: «Операции ЭТК должны иметь уникальные код и сторону»
