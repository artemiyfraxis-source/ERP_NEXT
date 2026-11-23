"""Legacy FAKRA identity survives finishing reorder without losing manual work or sketches."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

import pytest

from app.modules.techcards import etk_builder
from app.modules.techcards.application import _hand_route, _keep_hand_route
from app.modules.techcards.etk_derive import derive, route_override
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import (
    OperationSketch, ProjectTechcard, TechcardValidationError, document_sketches, operation_key,
)
from app.workflows.etk_refresh import Context, NOTE_PREFIX, REASON_BUILDER, evaluate
from app.workflows.techcard_build import Refs
from tests.references.test_load_mirror import sessions  # noqa: F401
from .test_etk_coax_fakra import FRESH, _fresh_cards
from .test_project_cards import project_techcards_api  # noqa: F401

NOW = datetime(2026, 11, 23, 12)


def legacy(*, valid_route=True):
    config = deepcopy(FRESH[0]["config"])
    cards = _fresh_cards(config)
    with patch.object(etk_builder, "_coax_finishing_config", lambda value: value), patch(
        "app.modules.techcards.etk_builder.validate_coax_finishing", lambda *_: None,
    ):
        old = etk_builder.build_etk(config, cards)
    for op in old["operations"]:
        op.pop("operation_key", None)
        op.pop("opKey", None)
    current = etk_builder.build_etk(config, cards)
    if valid_route:
        old["route"] = deepcopy(current["route"])
        old_positions = {op["opKey"]: index for index, op in enumerate(config["ops"], 1)}
        new_keys = [op["opKey"] for op in current["build_config"]["ops"]]
        for block in old["route"]["blocks"]:
            block["ops"] = [old_positions[new_keys[pos - 1]] for pos in block["ops"]]
    old["route"]["source"] = "manual"
    edge = next(edge for edge in old["route"]["edges"] if edge["from"] == "marked" and edge["to"] == "A")
    edge["per_unit"] = 2
    label = old["operations"][6]
    assert operation_key(label) == "INS_MARK_LABEL::7::"
    label["card"]["steps"][0]["text"] = "Ручная инструкция технолога"
    card = ProjectTechcard.create(project="708К.1", item_index="КБ1", product_name="Кабель",
                                  source_calculation="", deadline=None, now=NOW).rebuild(old, NOW).issue(NOW)
    key = operation_key(label)
    card = replace(card, inputs=config, manual_operation_keys=(key,), overrides={
        "operations": {key: {"operation": deepcopy(label), "base_template_version": ""}},
        "route": route_override(old),
    })
    ctx = Context(Refs(None), tuple(cards), "", None, "identity-test", REASON_BUILDER, NOW)
    return card, ctx, current


def test_legacy_fakra_keeps_instruction_manual_edges_and_uploaded_sketch(sessions):
    card, ctx, _ = legacy()
    old_key = card.manual_operation_keys[0]
    sketch = OperationSketch(old_key, "label.png", "image/png", 5, "Технолог", NOW)
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(card)
        uow.techcards.put_sketch(card.id, sketch, b"image")
        uow.commit()
    updated, revision, outcome = evaluate(card, ctx, saved_sketch_keys=(old_key,))
    assert outcome.status == "revised" and outcome.conflicts == ()
    assert revision is not None and updated.revision == 2
    new_key = "INS_MARK_LABEL::op:coaxInsLabel::"
    assert updated.manual_operation_keys == (new_key,)
    assert list(updated.overrides["operations"]) == [new_key]
    label = next(op for op in updated.document["operations"] if operation_key(op) == new_key)
    assert label["position"] == 17
    assert label["card"]["steps"][0]["text"] == "Ручная инструкция технолога"
    assert next(edge for edge in updated.document["route"]["edges"]
                if edge["from"] == "marked" and edge["to"] == "A")["per_unit"] == 2
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(updated)
        uow.commit()
        saved = uow.techcards.sketches(card.id)
        assert document_sketches(saved, updated.document)[0].operation_key == new_key
        assert document_sketches(saved, updated.document, card.document)[0].operation_key == old_key
        assert uow.techcards.sketch_content(card.id, new_key)[1] == b"image"
        assert uow.techcards.sketch_content(card.id, old_key)[1] == b"image"
        # Uploading through the new key replaces the same row, old pinned keys remain usable.
        uow.techcards.put_sketch(card.id, replace(sketch, operation_key=new_key), b"new")
        uow.commit()
        assert len(uow.techcards.sketches(card.id)) == 1
        assert uow.techcards.sketch_content(card.id, old_key)[1] == b"new"
    again, repeated_revision, result = evaluate(updated, ctx, saved_sketch_keys=(old_key,))
    assert repeated_revision is None and result.status == "unchanged"


def test_invalid_legacy_manual_route_holds_the_issued_revision_with_all_saved_content():
    card, ctx, _ = legacy(valid_route=False)
    updated, revision, outcome = evaluate(card, ctx, saved_sketch_keys=card.manual_operation_keys)
    assert revision is None and outcome.status == "conflict"
    assert updated.revision == card.revision and updated.document == card.document
    assert updated.overrides == card.overrides and updated.manual_operation_keys == card.manual_operation_keys
    assert updated.review_note.startswith(NOTE_PREFIX)


@pytest.mark.parametrize("saved_kind", ["edit", "sketch", "manual_key"])
def test_unmappable_saved_key_never_issues_a_revision(saved_kind):
    card, ctx, _ = legacy()
    key = "INS_MARK_LABEL::unknown::"
    if saved_kind == "edit":
        card = replace(card, overrides={**card.overrides, "operations": {key: {"operation": {"code": "INS_MARK_LABEL"}}}})
    elif saved_kind == "manual_key":
        card = replace(card, manual_operation_keys=(key,))
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=(key,) if saved_kind == "sketch" else ())
    assert revision is None and result.status == "conflict"
    assert updated.document == card.document and updated.revision == card.revision


def test_rebuild_preserves_manual_block_array_order_and_migrates_overrides():
    card, ctx, current = legacy()
    marked = next(block for block in card.document["route"]["blocks"] if block["key"] == "marked")
    marked["ops"] = [4, 6, 5]
    config = deepcopy(card.inputs)
    config["route"] = card.document["route"]
    rebuilt = etk_builder.build_etk(config, ctx.templates)
    assert next(block for block in rebuilt["route"]["blocks"] if block["key"] == "marked")["ops"] == [4, 6, 5]
    updated = card.rebuild(_keep_hand_route(card.document, current), NOW)
    assert next(block for block in updated.document["route"]["blocks"] if block["key"] == "marked")["ops"] == [4, 6, 5]
    assert list(updated.overrides["operations"]) == ["INS_MARK_LABEL::op:coaxInsLabel::"]


def test_save_uses_the_same_finishing_validator_as_refresh():
    _, _, document = legacy()
    route = deepcopy(document["route"])
    final = next(block for block in route["blocks"] if block["final"])
    final["ops"][0], final["ops"][1] = final["ops"][1], final["ops"][0]
    with pytest.raises(ValueError, match="Наклейка этикеток"):
        _hand_route(document, route)
    bad_color = deepcopy(document["route"])
    final = next(block for block in bad_color["blocks"] if block["final"])
    final["ops"] = [final["ops"][-1], *final["ops"][:-1]]
    with pytest.raises(ValueError, match="Цветовая маркировка"):
        _hand_route(document, bad_color)


def test_misaligned_saved_recipe_cannot_guess_legacy_operation_identity():
    card, ctx, _ = legacy()
    card.document["build_config"]["ops"] = card.document["build_config"]["ops"][1:]
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=card.manual_operation_keys)
    assert revision is None and result.status == "conflict"
    assert updated.document == card.document


def test_save_api_rejects_shrink_before_label_without_changing_revision(project_techcards_api):
    client = project_techcards_api
    _, _, document = legacy()
    created = client.post("/api/techcards/projects", json={
        "project": "708К.1", "item_index": "КБ1", "product_name": "Кабель", "source_calculation": "",
    }).json()
    base = f"/api/techcards/projects/{created['id']}"
    built = client.put(f"{base}/etk", json={"version": created["version"], "document": document})
    assert built.status_code == 200
    issued = client.post(f"{base}/issue", json={"version": built.json()["version"]})
    assert issued.status_code == 200
    before = issued.json()
    route = deepcopy(document["route"])
    final = next(block for block in route["blocks"] if block["final"])
    final["ops"][0], final["ops"][1] = final["ops"][1], final["ops"][0]
    rejected = client.put(f"{base}/route", json={"version": before["version"], "route": route})
    assert rejected.status_code == 422 and "Наклейка этикеток" in rejected.json()["detail"]
    after = client.get(base).json()
    assert (after["version"], after["revision"], after["document"]["route"]) == (
        before["version"], before["revision"], before["document"]["route"])


def test_duplicate_semantic_identity_is_a_build_conflict_not_a_positional_fallback():
    card, ctx, _ = legacy()
    card.inputs["ops"].append(deepcopy(card.inputs["ops"][6]))
    updated, revision, result = evaluate(card, ctx)
    assert revision is None and result.status == "conflict"
    assert updated.document == card.document and updated.revision == card.revision


def test_manual_rebuild_rejects_unmapped_saved_route_without_replacing_its_key_by_none():
    card, _, current = legacy()
    card = replace(card, manual_operation_keys=(), overrides={"route": {
        "blocks": [{"key": "old", "ops": ["REMOVED::7::"]}], "edges": [],
    }})
    current["route"]["source"] = "manual"
    with pytest.raises(TechcardValidationError, match="сохранённой схемы ПФ"):
        card.rebuild(_keep_hand_route(card.document, current), NOW)
    assert card.overrides["route"]["blocks"][0]["ops"] == ["REMOVED::7::"]


@pytest.mark.parametrize("field,value", [("code", "CHANGED"), ("side", "B"), ("opKey", "otherRole")])
def test_manual_edit_cannot_change_identity_fields_behind_an_explicit_key(field, value):
    _, _, current = legacy()
    card = ProjectTechcard.create(project="708К.1", item_index="КБ1", product_name="Кабель",
                                  source_calculation="", deadline=None, now=NOW).rebuild(current, NOW).issue(NOW)
    edited = deepcopy(current["operations"][0])
    key = operation_key(edited)
    edited[field] = value
    with pytest.raises(TechcardValidationError, match="ключ операции"):
        card.edit_operation(key, edited, NOW)


def legacy_wire():
    config = {
        "mode": "wire", "partQty": 10, "assemblyName": "Harness",
        "ops": [{"type": "cutTut", "opKey": f"cutShr{index}", "opNum": "CUT_TUT",
                 "tutArt": "TUBE", "tutName": "Tube", "tutLen": length, "tOp": "6"}
                for index, length in ((1, 20), (2, 40))],
    }
    old = etk_builder.build_etk(config, [])
    for op in old["operations"]:
        op.pop("operation_key", None)
        op.pop("opKey", None)
    etk_builder._distinct_typed_keys(old["operations"])
    old["route"]["source"] = "manual"
    first = old["operations"][0]
    first["card"] = {"steps": [{"text": "MANUAL_FOR_cutShr1"}]}
    key = operation_key(first)
    card = ProjectTechcard.create(project="WIRE", item_index="KB1", product_name="Harness",
                                  source_calculation="", deadline=None, now=NOW).rebuild(old, NOW).issue(NOW)
    inputs = deepcopy(config)
    inputs["ops"].reverse()
    card = replace(card, inputs=inputs, manual_operation_keys=(key,), overrides={
        "operations": {key: {"operation": deepcopy(first)}}, "route": route_override(old),
    })
    ctx = Context(Refs(None), (), "", None, "wire-identity-test", REASON_BUILDER, NOW)
    return card, ctx


def test_wire_reorder_keeps_manual_instruction_route_and_sketch_on_its_semantic_operation():
    card, ctx = legacy_wire()
    old_key = card.manual_operation_keys[0]
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=(old_key,))
    assert result.status == "revised" and revision is not None and not result.conflicts
    key = "CUT_TUT::op:cutShr1::"
    assert updated.manual_operation_keys == (key,)
    assert list(updated.overrides["operations"]) == [key]
    ops = updated.document["operations"]
    assert [op["opKey"] for op in ops] == ["cutShr2", "cutShr1"]
    assert ops[1]["card"]["steps"][0]["text"] == "MANUAL_FOR_cutShr1"
    assert ops[0]["card"] is None
    assert {block["key"]: block["ops"] for block in updated.document["route"]["blocks"]} == {"b1": [2], "b2": [1]}
    assert updated.document["route"]["edges"] == card.document["route"]["edges"]
    sketch = OperationSketch(old_key, "tube.png", "image/png", 5, "Technologist", NOW)
    assert document_sketches([sketch], updated.document)[0].operation_key == key
    assert document_sketches([replace(sketch, operation_key=key)], updated.document, card.document)[0].operation_key == old_key
    _, repeated, result = evaluate(updated, ctx, saved_sketch_keys=(old_key,))
    assert repeated is None and result.status == "unchanged"


@pytest.mark.parametrize("problem", ["missing_recipe", "duplicate_source_identity", "duplicate_target_identity"])
def test_ambiguous_wire_identity_preserves_issued_revision(problem):
    card, ctx = legacy_wire()
    if problem == "missing_recipe":
        card.document.pop("build_config")
    elif problem == "duplicate_source_identity":
        card.document["build_config"]["ops"][1]["opKey"] = "cutShr1"
    else:
        card.inputs["ops"][0]["opKey"] = "cutShr1"
    before = deepcopy(card.document)
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=card.manual_operation_keys)
    assert revision is None and result.status == "conflict"
    assert updated.document == before and updated.revision == card.revision
    assert updated.overrides == card.overrides and updated.manual_operation_keys == card.manual_operation_keys


def test_unresolved_wire_semantics_cannot_fall_back_to_a_reused_positional_key():
    card, _ = legacy_wire()
    # A replacement operation occupies the same legacy key, but no longer has the saved role.
    replacement = deepcopy(card.document)
    replacement["build_config"]["ops"][0]["opKey"] = "replacementCut"
    with pytest.raises(TechcardValidationError, match="позиционный ключ"):
        card.rebuild(replacement, NOW)
