"""Lugs on the free tails of «Сложная сборка» (owner 29.10: «нет возможности сделать на свободные концы НШВИ, НКИ и т.д.»,
«отдельную операцию на НКИ, НВИ и т.д. и отдельную на НШВИ», «под них должна быть зачистка отдельно от резки»): the
generator sends a strip per lug kind (`stripLugF` / `stripLugL`) and a crimp per lug article (`prsLug…`, the lug as the
end's terminal). In the ETK each is its own operation — own card, own key, the lug in «Комплектующие» once per tail end,
and the product says the tails got their lugs."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
WIRES = [
    {"name": "НВ4 0,5 белый", "art": "НВ4 0,5", "qty": 3, "length": 250},
    {"name": "НВ4 1,5 чёрный", "art": "НВ4 1,5", "qty": 2, "length": 300},
]


def lug_ep(art: str, name: str) -> dict[str, Any]:
    return {"connArt": "", "connName": "", "termArt": art, "termName": name, "sdrTmpl": None, "double": False, "perTerm": 2,
            "fold": False, "label": "свободные концы"}


def config(part_qty: int = 5) -> dict[str, Any]:
    return {
        "assemblyName": "Жгут НВ4", "assemblyIndex": "999К.1 КБ1", "partQty": part_qty, "bigAssembly": True,
        "wires": deepcopy(WIRES),
        "ops": [
            {"type": "cutWire", "opKey": "cut", "templateId": "cut-wire-auto-резка", "wires": deepcopy(WIRES)},
            {"type": "stripCable", "opKey": "stripLugF", "templateId": "strip-wire-разделка", "tutCount": 3,
             "strip": {"a": False, "lenA": "", "b": True, "lenB": "10"}},
            {"type": "prsTerm", "opKey": "prsLugF1", "templateId": "prs-ншви-опрессовка-наконечников", "side": "B",
             "ep": lug_ep("НШВИ 0.5-8", "Наконечник НШВИ"), "wires": [{**WIRES[0], "qty": 3}]},
            {"type": "stripCable", "opKey": "stripLugL", "templateId": "strip-wire-разделка", "tutCount": 2,
             "strip": {"a": False, "lenA": "", "b": True, "lenB": "5"}},
            {"type": "prsTerm", "opKey": "prsLugL1", "templateId": "prs-hkи-нви-рпи-опрессовка-наконечников", "side": "B",
             "ep": lug_ep("НКИ 1.5-4", "Наконечник кольцевой НКИ 1.5-4"), "wires": [{**WIRES[1], "qty": 2}]},
        ],
    }


def op_by_key(document: dict[str, Any], key: str) -> dict[str, Any]:
    keys = [op.get("opKey") for op in document["build_config"]["ops"]]
    return document["operations"][keys.index(key)]


def test_ferrules_and_cable_lugs_are_two_crimps_on_their_own_cards_with_the_lug_per_tail_end():
    document = build_etk(config(), CARDS)
    ferrule, ring = op_by_key(document, "prsLugF1"), op_by_key(document, "prsLugL1")
    assert ferrule["code"] == "PRS_НШВИ" and ring["code"].startswith("PRS_") and ring["code"].endswith("/НВИ/РПИ")
    # 3 хвоста × 5 изделий, 2 × 5.
    assert ferrule["components"] == [{"art": "НШВИ 0.5-8", "name": "Наконечник НШВИ", "norm": "15", "unit": "Шт."}]
    assert ring["components"] == [{"art": "НКИ 1.5-4", "name": "Наконечник кольцевой НКИ 1.5-4", "norm": "10", "unit": "Шт."}]


def test_each_lug_kind_has_its_own_strip_and_every_operation_its_own_etk_key():
    document = build_etk(config(), CARDS)
    keys = [(op["code"], op["connector"], op["side"]) for op in document["operations"]]
    assert len(keys) == len(set(keys))
    assert [op_by_key(document, key)["connector"] for key in ("stripLugF", "prsLugF1", "stripLugL", "prsLugL1")] == [
        "свободные концы под втулочные наконечники", "свободные концы НШВИ 0.5-8",
        "свободные концы под кабельные наконечники", "свободные концы НКИ 1.5-4"]
    assert op_by_key(document, "stripLugF")["fields"]["strip"]["lenB"] == "10"
    assert op_by_key(document, "stripLugF")["result"][0]["name"].endswith("; свободные концы разделаны")
    crimped = op_by_key(document, "prsLugF1")["result"][0]["name"]
    assert crimped.endswith("; на свободные концы обжаты наконечники НШВИ 0.5-8") and "разделаны" not in crimped
    assert op_by_key(document, "prsLugL1")["semis"][0]["name"] == crimped
    now = datetime.now(timezone.utc)
    card = ProjectTechcard.create(project="999", item_index="К.1 КБ1", product_name="Жгут НВ4", source_calculation="",
                                  deadline=None, now=now)
    card.rebuild(document, now)  # not refused: «Операции ЭТК должны иметь уникальные код и сторону»


def test_the_lug_crimp_takes_its_applicator_and_norms_from_the_lug_record_like_a_contact():
    from app.modules.references.technology import TerminalRecord
    from app.workflows.techcard_build import enrich_terminal_norms

    lug = TerminalRecord(article="НКИ 1.5-4", strip="5", applicator="M31", mm2_from="0,5", mm2_to="1,5", awg_from="20", awg_to="16")
    enriched = enrich_terminal_norms(config(), [lug])
    ring = op_by_key(build_etk(enriched, CARDS), "prsLugL1")
    assert ring["fields"]["applicator"] == "M31"
    assert [(end["art"], end["applicator"], end["strip"]) for end in ring["fields"]["crimpEnds"]] == [("НКИ 1.5-4", "M31", "5")]
