"""Covered labels move on rebuild; source snapshots and already launched operations keep their identity."""

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.production.launchable import launch_route
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.etk_derive import derive, route_override, ROUTE_NOT_APPLIED
from app.modules.techcards.operation_cards import parse_operation_library

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((ROOT.parent / "frontend/src/__fixtures__/fakra1116Configs.json").read_text(encoding="utf-8"))
LIBRARY = [card for name in ("techcard-library.json", "techcard-library-erp.json")
           for card in parse_operation_library(json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))["templates"])]


def cards(config):
    by_code = {card.code: card for card in LIBRARY}
    return [replace(by_code[op["templateId"]], source_id=op["templateId"]) for op in config["ops"]]


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda item: item["kb"])
@pytest.mark.parametrize("saved_route", [False, True])
def test_saved_covered_labels_move_to_final_with_their_input_and_quantity(fixture, saved_route):
    config = deepcopy(fixture["config"])
    if saved_route:
        config["route"] = {"source": "generated", "blocks": [
            {"key": "old", "ops": list(range(1, len(config["ops"]))), "name": "Старый ПФ"},
            {"key": "final", "ops": [len(config["ops"])], "final": True},
        ], "edges": [{"from": "old", "to": "final"}]}
    config["opOrder"] = [op["opKey"] for op in config["ops"]]
    original = deepcopy(config)
    doc = build_etk(config, cards(config))
    assert config == original
    source = doc["build_config"]["ops"]
    keys = [op["opKey"] for op in source]
    assert keys[-4:] == ["coaxInsLabel", "coaxHeat", "coaxVswr", "coaxConnColor"]
    assert keys.index("coaxProtectIns") < keys.index("coaxPinCrimpA")
    assert doc["build_config"]["opOrder"] == keys
    final = next(block for block in doc["route"]["blocks"] if block["final"])
    assert keys.index("coaxInsLabel") + 1 in final["ops"]
    edges = doc["route"]["edges"]
    assert {"from": "labels", "to": "final", "per_unit": 1} in edges
    assert not any(edge["from"] == "labels" and edge["to"] == "marked" for edge in edges)
    assert next(block for block in doc["route"]["blocks"] if block["key"] == "marked")["name"] == "Кабель с трубками и деталями"
    printed = doc["operations"][keys.index("coaxPrintLabel")]
    installed = doc["operations"][keys.index("coaxInsLabel")]
    expected = Decimal(str(config["partQty"])) * Decimal(str(source[keys.index("coaxPrintLabel")]["units"]))
    assert sum(Decimal(row["norm"].replace(",", ".")) for row in printed["result"]) == expected
    assert any(Decimal(row["norm"].replace(",", ".")) == expected for row in installed["semis"])
    launched = launch_route(doc)
    assert [step.stage for step in launched.steps][-4:] == [op["label"] for op in source[-4:]]
    assert all(step.final for step in launched.steps[-4:])
    assert build_etk(doc["build_config"], cards(config)) == doc


@pytest.mark.parametrize("already_ordered", [False, True])
def test_conflicting_manual_pf_is_rejected_instead_of_silently_replaced(already_ordered):
    config = deepcopy(FIXTURES[0]["config"])
    if already_ordered:
        config = build_etk(config, cards(config))["build_config"]
    config["route"] = {"source": "manual", "blocks": [
        {"key": "early", "ops": list(range(1, len(config["ops"]))), "name": "Ручной ПФ"},
        {"key": "final", "ops": [len(config["ops"])], "final": True},
    ], "edges": [{"from": "early", "to": "final"}]}
    with pytest.raises(ValueError, match="ПФ"):
        build_etk(config, cards(config))


def test_refresh_reports_conflicting_route_override_and_keeps_the_generated_final_pf():
    config = deepcopy(FIXTURES[0]["config"])
    doc = build_etk(config, cards(config))
    manual = deepcopy(doc)
    manual["route"] = {"source": "manual", "blocks": [
        {"key": "early", "ops": list(range(1, len(config["ops"]))), "name": "Ручной ПФ"},
        {"key": "final", "ops": [len(config["ops"])], "final": True},
    ], "edges": [{"from": "early", "to": "final"}]}
    result, conflicts = derive(config, {"route": route_override(manual)}, lambda value: value, cards(config))
    assert any(conflict["kind"] == ROUTE_NOT_APPLIED for conflict in conflicts)
    assert result["route"]["source"] == "generated"
    final = next(block for block in result["route"]["blocks"] if block["final"])
    label = next(index for index, op in enumerate(result["build_config"]["ops"], 1) if op["opKey"] == "coaxInsLabel")
    assert label in final["ops"]


def test_legacy_config_without_tab_uses_the_finishing_pf_rule():
    config = deepcopy(FIXTURES[0]["config"])
    config.pop("tab", None)
    doc = build_etk(config, cards(config))
    assert doc["build_config"]["tab"] == "coax"
    final = next(block for block in doc["route"]["blocks"] if block["final"])
    keys = [doc["build_config"]["ops"][position - 1]["opKey"] for position in final["ops"]]
    assert keys == ["coaxInsLabel", "coaxHeat", "coaxVswr", "coaxConnColor"]
