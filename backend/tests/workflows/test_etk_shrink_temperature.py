"""Material requirements remain exact, refreshable and separate from heater settings."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from app.modules.references.shrink_temperature import LOWER, UPPER, KIND, DOCUMENT, SOURCE_URL
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.etk_derive import derive, document_diff
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard, operation_key
from app.workflows.etk_refresh import Context, evaluate
from app.workflows.techcard_build import Refs, with_technology

DATA = Path(__file__).resolve().parents[2] / "data"
CARDS = tuple(parse_operation_library(json.loads((DATA / "techcard-library.json").read_text(encoding="utf-8"))["templates"]))
HEAT = next(card for card in CARDS if card.code == "HEAT_TYT_short")
NOW = datetime(2026, 11, 16, 12)


def refs(*rows):
    return Refs(None, heat_shrink_rows=tuple(rows))


def row(article="A", low="85", high="125", **extra):
    return {"Артикул": article, "Наименование": f"ТУТ {article}", LOWER: low, UPPER: high,
            KIND: "оптимальная", DOCUMENT: "Паспорт материала", SOURCE_URL: "https://example.com/passport", **extra}


def config(ops=None):
    return {"projectNo": "ТЕСТ", "assemblyIndex": "КБ1", "assemblyName": "Жгут", "partQty": 1,
            "wires": [], "ops": ops or [
                {"type": "heatTut", "opKey": "heatTut", "templateId": HEAT.source_id,
                 "tutArt": "A", "tutName": "ТУТ A", "tutCount": 1}]}


def enriched(cfg, *rows):
    return with_technology(cfg, refs=refs(*rows))[0]


def document(cfg, *rows):
    return build_etk(enriched(cfg, *rows), CARDS)


def requirements(doc):
    return doc["operations"][-1]["fields"]["shrinkRequirements"]


def test_requirement_snapshot_and_legacy_step_are_material_specific():
    doc = document(config(), row())
    requirement = requirements(doc)[0]
    assert requirement["status"] == "resolved"
    assert requirement["material"] == {"article": "A", "name": "ТУТ A"}
    assert requirement["temperature"]["lower_c"] == 85
    assert requirement["temperature"]["upper_c"] == 125
    assert requirement["temperature"]["document"] == "Паспорт материала"
    steps = doc["operations"][0]["card"]["steps"]
    assert "85…125 °C" in steps[0]["text"]
    assert "150 °C" not in json.dumps(steps, ensure_ascii=False)
    heater = next(tool for tool in doc["operations"][0]["card"]["tooling"] if tool["section"] == "Оборудование")
    assert not heater["program"]


@pytest.mark.parametrize("rows,status", [([], "missing"), ([row(low="bad")], "review"),
                                           ([row(), row(low="100")], "review")])
def test_missing_invalid_or_conflicting_evidence_never_provides_a_default(rows, status):
    doc = document(config(), *rows)
    requirement = requirements(doc)[0]
    assert requirement["status"] == status
    assert "не задано — проверить" in doc["operations"][0]["card"]["steps"][0]["text"]


def test_exact_article_wins_and_does_not_fall_back_to_a_different_article_by_name():
    cfg = config()
    cfg["ops"][0]["tutName"] = "ТУТ B"
    assert requirements(document(cfg, row("B")))[0]["status"] == "missing"
    cfg["ops"][0]["tutArt"] = ""
    assert requirements(document(cfg, row("B")))[0]["status"] == "resolved"


def test_separate_materials_and_pvc_markers():
    cfg = config()
    cfg["ops"][0].update(tutArt="", tutName="", markTube=True, shrinkMaterials=[
        {"art": "A", "name": "ТУТ A"}, {"art": "B", "name": "ТУТ B"},
        {"art": "PVC", "name": "Кембрик ПВХ 4/2мм"}])
    result = requirements(document(cfg, row("A"), row("B", "100", "140")))
    assert [r["material"]["article"] for r in result] == ["A", "B"]
    assert [r["temperature"]["lower_c"] for r in result] == [85, 100]
    cfg["ops"][0]["shrinkMaterials"] = [{"art": "PVC", "name": "Кембрик ПВХ 4/2мм"}]
    assert requirements(document(cfg, row())) == []


def test_legacy_cut_to_heat_recovery_and_coax_specific_end():
    cfg = config()
    cfg["ops"][0].update(tutArt="", tutName="")
    cfg["ops"].insert(0, {"type": "cutTut", "opKey": "cutTut", "tutArt": "A", "tutName": "ТУТ A"})
    assert enriched(cfg, row())["ops"][-1]["shrinkRequirements"][0]["material"]["article"] == "A"
    cfg = config([{"type": "coaxHeat", "opKey": "coaxHeatTtkA", "side": "A", "templateId": HEAT.source_id}])
    cfg["coax"] = {"tutArt": "B", "tutName": "ТУТ B", "sideA": {"parts": [
        {"role": "tube", "art": "A", "name": "ТУТ A"}]}}
    assert requirements(document(cfg, row(), row("B")))[0]["material"]["article"] == "A"


def test_enrichment_is_idempotent_refreshing_and_in_reference_stamp():
    cfg = config()
    first = enriched(cfg, row())
    assert enriched(first, row()) == first
    assert enriched(first)["ops"][0]["shrinkRequirements"][0]["status"] == "missing"
    assert refs(row()).stamp != refs(row(low="90")).stamp
    assert "shrinkRequirements" not in cfg["ops"][0]


def test_full_operation_override_keeps_manual_text_but_refreshes_requirement():
    cfg = config()
    old = document(cfg, row())
    edited = deepcopy(old["operations"][0])
    # The UI editor's DTO keeps only number/kind/text/tolerance, not builder metadata.
    edited["card"]["steps"][0].pop("material_requirement")
    edited["card"]["steps"].append({"number": "9", "kind": "Операция", "text": "Проверить положение по КД", "tolerance": ""})
    override = {"operations": {operation_key(edited): {"operation": edited}}}
    doc, conflicts = derive(cfg, override, lambda c: enriched(c, row(low="90")), CARDS)
    assert doc is not None and not conflicts
    assert requirements(doc)[0]["temperature"]["lower_c"] == 90
    assert "90…125 °C" in doc["operations"][0]["card"]["steps"][0]["text"]
    assert doc["operations"][0]["card"]["steps"][-1]["text"] == "Проверить положение по КД"


def test_refresh_changes_only_affected_documents_and_only_once():
    cfg = config()
    old = document(cfg, row())
    draft = ProjectTechcard.create(project="ТЕСТ", item_index="КБ1", product_name="Жгут",
                                  source_calculation="", deadline=None, now=NOW).rebuild(old, NOW)
    draft = replace(draft, inputs=cfg)
    ctx = Context(refs(row(low="90")), CARDS, "", None, "test", "reference", NOW)
    updated, revision, result = evaluate(draft, ctx)
    assert result.status == "refreshed" and revision is None
    issued = draft.issue(NOW)
    updated, revision, result = evaluate(issued, ctx)
    assert result.status == "revised" and revision.revision == issued.revision + 1
    assert requirements(issued.document)[0]["temperature"]["lower_c"] == 85
    assert evaluate(updated, ctx)[2].status == "unchanged"
    assert evaluate(issued, replace(ctx, refs=refs(row(), row("unrelated"))))[2].status == "unchanged"


def test_program_only_changes_are_detected():
    old = document(config(), row())
    new = deepcopy(old)
    new["operations"][0]["card"]["tooling"][0]["program"] = "Утверждённая программа 7"
    assert any(change["field"] == "Инструмент" for change in document_diff(old, new))


def test_legacy_configs_without_operation_keys_recover_only_one_exact_cut_material():
    cfg = config([{"type": "cutTut", "tutArt": "A", "tutName": "ТУТ A"},
                  {"type": "insTut"}, {"type": "heatTut", "templateId": HEAT.source_id}])
    doc = document(cfg, row())
    assert requirements(doc)[0]["material"]["article"] == "A"
    assert doc["operations"][1]["fields"]["shrinkMaterials"] == [{"article": "A", "name": "ТУТ A"}]
    cfg["ops"].insert(1, {"type": "cutTut", "tutArt": "B", "tutName": "ТУТ B"})
    assert requirements(document(cfg, row(), row("B")))[0]["status"] == "missing"


@pytest.mark.parametrize("phase,material,has_requirement", [
    ("install", "ТУТ A", True), ("print", "ТУТ A", False), ("install", "Кембрик ПВХ 4/2мм", False),
])
def test_integrated_marking_and_print_only_and_pvc(phase, material, has_requirement):
    cfg = config([{"type": "insMark", "markPhase": phase, "marks": [
        {"art": "A", "name": material, "qty": 1, "text": "X1", "target": "X1"}]}])
    op = document(cfg, row())["operations"][0]
    assert ("shrinkRequirements" in op["fields"]) is has_requirement


def test_custom_temperature_override_is_kept_with_a_visible_review_state():
    from app.modules.techcards.etk_derive import THERMAL_OVERRIDE
    cfg = config()
    edited = deepcopy(document(cfg, row())["operations"][0])
    edited["card"]["steps"][0] = {"text": "Нагревать при 180 °C", "tolerance": "", "number": "1", "kind": "Операция"}
    override = {"operations": {operation_key(edited): {"operation": edited}}}
    doc, conflicts = derive(cfg, override, lambda c: enriched(c, row(low="90")), CARDS)
    assert conflicts[0]["kind"] == THERMAL_OVERRIDE
    assert doc["operations"][0]["fields"]["shrinkReview"]
    assert doc["operations"][0]["card"]["steps"][0]["text"] == "Нагревать при 180 °C"
    assert requirements(doc)[0]["temperature"]["lower_c"] == 90


def test_known_old_default_in_full_override_is_replaced_without_touching_program():
    from app.modules.techcards.etk_heat_shrink import LEGACY_STEP
    cfg = config()
    edited = deepcopy(document(cfg, row())["operations"][0])
    edited["card"]["steps"][0] = {"text": LEGACY_STEP, "tolerance": "150 °C", "number": "1", "kind": "Операция"}
    edited["card"]["tooling"][0]["program"] = "Режим технолога 12"
    override = {"operations": {operation_key(edited): {"operation": edited}}}
    doc, conflicts = derive(cfg, override, lambda c: enriched(c, row()), CARDS)
    assert not conflicts
    assert "85…125 °C" in doc["operations"][0]["card"]["steps"][0]["text"]
    assert doc["operations"][0]["card"]["steps"][0]["tolerance"] == ""
    assert doc["operations"][0]["card"]["tooling"][0]["program"] == "Режим технолога 12"


def test_minimum_open_bound_keeps_its_semantics():
    doc = document(config(), row(low="100", high="", **{KIND: "минимальная"}))
    assert requirements(doc)[0]["temperature"]["upper_c"] is None
    assert "не ниже 100 °C (минимальная" in doc["operations"][0]["card"]["steps"][0]["text"]


def test_custom_thermal_tolerance_is_not_mistaken_for_the_old_default():
    from app.modules.techcards.etk_heat_shrink import LEGACY_STEP
    cfg = config()
    edited = deepcopy(document(cfg, row())["operations"][0])
    edited["card"]["steps"][0] = {"text": LEGACY_STEP, "tolerance": "180 °C", "number": "1", "kind": "Операция"}
    doc, conflicts = derive(cfg, {"operations": {operation_key(edited): {"operation": edited}}},
                            lambda c: enriched(c, row()), CARDS)
    assert conflicts and doc["operations"][0]["fields"]["shrinkReview"]
    assert doc["operations"][0]["card"]["steps"][0]["tolerance"] == "180 °C"


def test_free_end_cap_shrinks_the_exact_tube_cut_for_it():
    cfg = config([{"type": "cutTut", "opKey": "cutCap", "tutArt": "A", "tutName": "ТУТ A"},
                  {"type": "endCap", "opKey": "endCap", "opNum": "INS_END_CAP"}])
    assert requirements(document(cfg, row()))[0]["material"]["article"] == "A"

