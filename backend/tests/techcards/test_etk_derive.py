"""etk_derive: a card rebuilt from its inputs and overrides, the diff and the hashes (owner 19.10.2026)."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time
from typing import Any

import pytest

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.etk_derive import (
    MISSING_OPERATION, NO_INPUTS, NO_INPUTS_MESSAGE, builder_hash, derive, derived_hash, document_diff, route_override,
)
from app.modules.techcards.etk_live import stamp_templates
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import operation_key
from app.workflows.techcard_build import with_technology

BACKEND = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((BACKEND / "data" / "techcard-library.json").read_text(encoding="utf-8"))
ERP_LIBRARY = json.loads((BACKEND / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"] + ERP_LIBRARY["templates"])
COMMIT = LIBRARY["sourceCommit"]
CONFIG_713 = json.loads((Path(__file__).with_name("generator_713k4_config.json")).read_text(encoding="utf-8"))
COAX_FIXTURE = BACKEND.parent / "frontend" / "src" / "fixtures" / "coaxSmaRg316Rev1.json"
CONFIG_COAX = json.loads(COAX_FIXTURE.read_text(encoding="utf-8"))["document"]["build_config"]


def enrich(config: Any) -> Any:
    return with_technology(config, None)[0]


def current_path(config: dict[str, Any]) -> dict[str, Any]:
    return stamp_templates(build_etk(enrich(config), CARDS, operation_library_commit=COMMIT), CARDS)


def run(config: dict[str, Any], overrides: Any = None):
    return derive(config, overrides, enrich, CARDS, operation_library_commit=COMMIT)


CASES = [pytest.param(CONFIG_713, id="713К.4"), pytest.param(CONFIG_COAX, id="228К.20-коакс")]


@pytest.mark.parametrize("config", CASES)
def test_derive_equals_the_current_build_path_and_is_repeatable(config):
    started = time.perf_counter()
    document, conflicts = run(config)
    elapsed = time.perf_counter() - started
    print(f"\nderive: {len(document['operations'])} операций за {elapsed * 1000:.0f} мс")
    assert conflicts == []
    assert document == current_path(config)
    assert run(config)[0] == document


def test_inputs_are_not_changed_by_derive():
    before = deepcopy(CONFIG_713)
    run(CONFIG_713)
    assert CONFIG_713 == before


@pytest.mark.parametrize("inputs", [None, {}])
def test_no_inputs_is_a_clear_result_not_an_error(inputs):
    document, conflicts = derive(inputs, {"operations": {}}, enrich, CARDS)
    assert document is None
    assert conflicts == [{"kind": NO_INPUTS, "operation_key": "", "message": NO_INPUTS_MESSAGE}]


def test_a_config_the_builder_rejects_is_a_conflict():
    document, conflicts = derive({"mode": "wire", "ops": "x"}, None, enrich, CARDS)
    assert document is None and conflicts[0]["kind"] == "build_failed"


def test_an_operation_edit_is_applied_and_survives_by_key():
    base, _ = run(CONFIG_713)
    target = base["operations"][1]
    key = operation_key(target)
    edited = deepcopy(target)
    edited["result"] = [{**edited["result"][0], "name": "Правка технолога"}]
    overrides = {"operations": {key: {"base_template_version": base["source"]["templates"][target["template_id"]],
                                      "operation": edited}}}
    document, conflicts = run(CONFIG_713, overrides)
    assert conflicts == []
    assert document["operations"][1]["result"][0]["name"] == "Правка технолога"
    assert document["operations"][0] == base["operations"][0]


def test_an_edit_on_a_changed_template_is_kept_and_reported():
    base, _ = run(CONFIG_713)
    target = base["operations"][0]
    overrides = {"operations": {operation_key(target): {"base_template_version": "старая", "operation": target}}}
    document, conflicts = run(CONFIG_713, overrides)
    assert document["operations"][0] == target
    assert [item["kind"] for item in conflicts] == ["template_changed"]


def test_an_edit_of_a_missing_operation_is_a_conflict_never_dropped():
    overrides = {"operations": {"НЕТ_ТАКОЙ::x::A": {"base_template_version": "", "operation": {"code": "НЕТ_ТАКОЙ"}}}}
    document, conflicts = run(CONFIG_713, overrides)
    assert document == run(CONFIG_713)[0]
    assert conflicts == [{"kind": MISSING_OPERATION, "operation_key": "НЕТ_ТАКОЙ::x::A",
                          "message": conflicts[0]["message"]}]


def _hand_route(document: dict[str, Any]) -> dict[str, Any]:
    keys = [operation_key(op) for op in document["operations"]]
    return {"source": "manual", "blocks": [
        {"key": "b1", "name": "Заготовки", "final": False, "ops": keys[:-1]},
        {"key": "b2", "name": "Готовое изделие", "final": True, "ops": keys[-1:]},
    ], "edges": [{"from": "b1", "to": "b2", "per_unit": 1}]}


def test_a_hand_cut_route_is_applied_by_operation_keys():
    base, _ = run(CONFIG_713)
    override = _hand_route(base)
    document, conflicts = run(CONFIG_713, {"route": override})
    assert conflicts == []
    assert document["route"]["source"] == "manual"
    assert [block["ops"] for block in document["route"]["blocks"]] == [
        list(range(1, len(base["operations"]))), [len(base["operations"])]]
    assert route_override(document)["blocks"][0]["ops"] == override["blocks"][0]["ops"]


def test_a_route_naming_a_missing_operation_is_a_conflict_and_the_built_route_stays():
    base, _ = run(CONFIG_713)
    override = _hand_route(base)
    override["blocks"][0]["ops"].append("НЕТ::x::A")
    document, conflicts = run(CONFIG_713, {"route": override})
    assert document["route"] == base["route"]
    assert [(item["kind"], item["operation_key"]) for item in conflicts] == [(MISSING_OPERATION, "НЕТ::x::A")]


def test_diff_lists_a_changed_result_text():
    old, _ = run(CONFIG_713)
    new = deepcopy(old)
    new["operations"][0]["result"][0]["name"] = "Другой результат"
    new["source"]["templates"] = {"x": "volatile"}
    changes = document_diff(old, new)
    assert [item["field"] for item in changes] == ["Результат"]
    assert changes[0]["new"].startswith("Другой результат")
    assert changes[0]["operation_key"] == operation_key(old["operations"][0])
    assert document_diff(old, deepcopy(old)) == []


def _housing_depths(depth: str) -> dict[str, Any]:
    config = deepcopy(CONFIG_COAX)
    config["coax"]["sideB"]["contactDepth"] = depth
    return config


def test_diff_lists_a_changed_contact_depth_and_the_control_step():
    old, _ = run(_housing_depths("0,38…1,14"))
    new, _ = run(_housing_depths("1,88…2,41"))
    changes = document_diff(old, new)
    fields = {item["field"] for item in changes}
    assert "Заглубление контакта" in fields and "Ход операции" in fields
    depths = [item for item in changes if item["field"] == "Заглубление контакта"]
    assert {(item["old"], item["new"]) for item in depths} == {("0,38…1,14", "1,88…2,41")}
    assert any(item["operation_key"].startswith("INS_housing_COAX") for item in depths)


def test_diff_names_added_and_removed_operations_and_route_changes():
    old, _ = run(CONFIG_713)
    new = deepcopy(old)
    removed = new["operations"].pop()
    changes = document_diff(old, new)
    assert {"operation_key": operation_key(removed), "label": changes[0]["label"], "field": "Операция",
            "old": "есть", "new": ""} in changes


def test_hashes_are_stable_and_follow_every_part():
    assert builder_hash() == builder_hash() and len(builder_hash()) == 16
    args = (CONFIG_713, {}, {"v": 1}, {"t": "1"}, builder_hash())
    assert derived_hash(*args) == derived_hash(*deepcopy(args))
    assert derived_hash(CONFIG_713, {"a": 1}, {"v": 1}, {"t": "1"}, builder_hash()) != derived_hash(*args)
    assert derived_hash(CONFIG_713, {}, {"v": 2}, {"t": "1"}, builder_hash()) != derived_hash(*args)
    assert derived_hash(CONFIG_713, {}, {"v": 1}, {"t": "2"}, builder_hash()) != derived_hash(*args)
    assert derived_hash(CONFIG_713, {}, {"v": 1}, {"t": "1"}, "другой") != derived_hash(*args)
    changed = deepcopy(CONFIG_713)
    changed["partQty"] = 1
    assert derived_hash(changed, {}, {"v": 1}, {"t": "1"}, builder_hash()) != derived_hash(*args)
