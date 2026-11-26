"""Legacy FAKRA identity survives finishing reorder without losing manual work or sketches."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

import pytest

from app.modules.techcards import etk_builder
from app.modules.techcards.application import _hand_route, _keep_hand_route
from app.modules.techcards.etk_derive import derive, route_override
from app.modules.techcards.infrastructure import ProjectTechcardRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import (
    OperationSketch, ProjectTechcard, TechcardValidationError, document_sketches, operation_aliases, operation_key,
    with_operation_fingerprints,
)
from app.workflows.etk_refresh import Context, NOTE_PREFIX, REASON_BUILDER, evaluate
from app.workflows.techcard_build import Refs
from tests.references.test_load_mirror import sessions  # noqa: F401
from .test_etk_coax_fakra import FRESH, _fresh_cards
from .test_project_cards import project_techcards_api  # noqa: F401
from .test_etk_builder import CARDS, FIXTURES, kit

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


def legacy_braid(*, saved_kind="edit", backfill=False):
    config = deepcopy(kit("708К.2-КБ1")["config"])
    document = etk_builder.build_etk(config, CARDS)
    assert not config["ops"][9].get("opKey")
    assert document["operations"][9]["components"][0]["art"] == "Оплетка XS-3"
    card = ProjectTechcard.create(project="708К.2", item_index="КБ1", product_name="Жгут",
                                  source_calculation="", deadline=None, now=NOW).rebuild(document, NOW).issue(NOW)
    key = operation_key(card.document["operations"][9])
    if saved_kind == "edit":
        edited = deepcopy(card.document["operations"][9])
        edited["card"]["steps"][0]["text"] = "Ручная инструкция для XS-3"
        card = card.edit_operation(key, edited, NOW)
        card = replace(card, overrides={"operations": {key: {"operation": deepcopy(edited)}}})
    elif saved_kind == "route":
        card.document["route"]["source"] = "manual"
        card = replace(card, overrides={"route": route_override(card.document)})
    if backfill:
        for op in card.document["operations"]:
            op.pop("operation_fingerprint", None)
        for edit in (card.overrides or {}).get("operations", {}).values():
            edit["operation"].pop("operation_fingerprint", None)
    ctx = Context(Refs(None), tuple(CARDS), "", None, "braid-identity-test", REASON_BUILDER, NOW)
    return replace(card, inputs=config), ctx, key


@pytest.mark.parametrize("saved_kind", ["edit", "route", "sketch"])
@pytest.mark.parametrize("backfill", [False, True])
def test_legacy_braid_swap_conflicts_without_issuing_or_moving_saved_content(saved_kind, backfill):
    card, ctx, key = legacy_braid(saved_kind=saved_kind, backfill=backfill)
    card.inputs["ops"][9], card.inputs["ops"][10] = card.inputs["ops"][10], card.inputs["ops"][9]
    before = deepcopy(card.document)
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=(key,) if saved_kind == "sketch" else ())
    assert result.status == "conflict" and revision is None
    assert updated.revision == card.revision and updated.document == before
    assert updated.overrides == card.overrides and updated.manual_operation_keys == card.manual_operation_keys
    assert "Монтаж оплётки" in updated.review_note and "XS-3" in updated.review_note


@pytest.mark.parametrize("backfill", [False, True])
def test_legacy_braid_unchanged_recipe_keeps_instruction_without_conflict(backfill):
    card, ctx, key = legacy_braid(backfill=backfill)
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=(key,))
    assert result.status == "unchanged" and not result.conflicts and revision is None
    assert (updated or card).document["operations"][9]["card"]["steps"][0]["text"] == "Ручная инструкция для XS-3"


@pytest.mark.parametrize("backfill", [False, True])
def test_legacy_braid_move_to_free_key_maps_instruction_route_and_sketch(backfill):
    card, ctx, key = legacy_braid(backfill=backfill)
    card.document["route"]["source"] = "manual"
    card = replace(card, overrides={**card.overrides, "route": route_override(card.document)})
    # Move XS-3 past a different operation; no braid occupies its former key.
    card.inputs["ops"].insert(8, card.inputs["ops"].pop(9))
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=(key,))
    assert result.status == "revised" and revision is not None and not result.conflicts
    new_key = "INS_BRAID::9::"
    assert updated.manual_operation_keys == (new_key,)
    assert list(updated.overrides["operations"]) == [new_key]
    assert updated.document["operations"][8]["card"]["steps"][0]["text"] == "Ручная инструкция для XS-3"
    old_blocks = {block["key"]: block["ops"] for block in card.document["route"]["blocks"]}
    new_blocks = {block["key"]: block["ops"] for block in updated.document["route"]["blocks"]}
    for block_key, positions in old_blocks.items():
        assert new_blocks[block_key] == [9 if pos == 10 else 10 if pos == 9 else pos for pos in positions]
    sketch = OperationSketch(key, "braid.png", "image/png", 5, "Технолог", NOW)
    assert document_sketches([sketch], updated.document)[0].operation_key == new_key
    _, repeated, outcome = evaluate(updated, ctx, saved_sketch_keys=(key,))
    assert repeated is None and outcome.status == "unchanged"


def test_legacy_fingerprints_backfill_on_read_without_changing_stored_document(sessions):
    card, _, key = legacy_braid(backfill=True)
    before = deepcopy(card.document)
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.techcards.save(card)
        uow.commit()
        loaded = uow.techcards.get(card.id)
        fingerprint = loaded.document["operations"][9]["operation_fingerprint"]
        assert loaded.overrides["operations"][key]["operation"]["operation_fingerprint"] == fingerprint
        assert loaded.revision == card.revision and loaded.version == card.version
        assert uow.session.get(ProjectTechcardRow, card.id).document == before
        assert not uow.session.dirty


@pytest.mark.parametrize("recipe_problem", ["missing", "misaligned"])
def test_legacy_missing_fingerprint_cannot_guess_a_reordered_operation(recipe_problem):
    card, ctx, key = legacy_braid(backfill=True)
    if recipe_problem == "missing":
        card.document.pop("build_config")
    else:
        card.document["build_config"]["ops"].pop()
    card.inputs["ops"][9], card.inputs["ops"][10] = card.inputs["ops"][10], card.inputs["ops"][9]
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=(key,))
    assert result.status == "conflict" and revision is None
    assert updated.document == card.document and updated.revision == card.revision


@pytest.mark.parametrize("field", ["tutSegs", "tutParts"])
@pytest.mark.parametrize("missing_recipe", [False, True])
def test_legacy_tube_fingerprint_distinguishes_nested_piece_lengths(field, missing_recipe):
    config = {"mode": "wire", "partQty": 1, "assemblyName": "Жгут", "ops": [
        {"type": "cutTut", "opNum": "CUT_TUT", "tutArt": "TUBE", "tOp": "6",
         field: [{"art": "TUBE", "name": "Tube", "len": length, "count": 1}]} for length in ("20", "40")
    ]}
    previous = etk_builder.build_etk(config, [])
    if missing_recipe:
        previous.pop("build_config")
    config["ops"].reverse()
    rebuilt = etk_builder.build_etk(config, [])
    with pytest.raises(TechcardValidationError, match="позиционный ключ"):
        operation_aliases(previous, rebuilt)


def test_legacy_historical_alias_cannot_be_reused_for_a_different_operation():
    card, ctx, key = legacy_braid()
    card.inputs["ops"].insert(8, card.inputs["ops"].pop(9))
    moved, _, result = evaluate(card, ctx, saved_sketch_keys=(key,))
    assert result.status == "revised"
    # The former XS-3 key becomes the key of an additional XS-5 operation.
    changed_inputs = deepcopy(moved.inputs)
    changed_inputs["ops"][9] = deepcopy(changed_inputs["ops"][10])
    moved = replace(moved, inputs=changed_inputs)
    updated, revision, result = evaluate(moved, ctx, saved_sketch_keys=(key,))
    assert result.status == "conflict" and revision is None
    assert updated.document == moved.document and updated.revision == moved.revision


def test_legacy_historical_sketch_alias_cannot_be_reused_after_original_operation_is_removed():
    card, ctx, key = legacy_braid(saved_kind="sketch")
    card.inputs["ops"].insert(8, card.inputs["ops"].pop(9))
    moved, _, result = evaluate(card, ctx, saved_sketch_keys=(key,))
    assert result.status == "revised"
    changed_inputs = deepcopy(moved.inputs)
    changed_inputs["ops"].pop(8)
    moved = replace(moved, inputs=changed_inputs)
    updated, revision, result = evaluate(moved, ctx, saved_sketch_keys=(key,))
    assert result.status == "conflict" and revision is None
    assert updated.document == moved.document and updated.revision == moved.revision


def test_saved_legacy_edit_fingerprint_is_checked_without_a_previous_document():
    card, ctx, _ = legacy_braid()
    card.inputs["ops"][9], card.inputs["ops"][10] = card.inputs["ops"][10], card.inputs["ops"][9]
    _, conflicts = derive(card.inputs, card.overrides, lambda config: config, ctx.templates)
    assert any(item["kind"] == "missing_operation" for item in conflicts)


@pytest.mark.parametrize("attribute", ["branch", "ep"])
def test_legacy_material_match_cannot_move_saved_content_to_a_different_branch(attribute):
    first, second = ("A", "B") if attribute == "branch" else ({"label": "A"}, {"label": "B"})
    config = {"mode": "wire", "partQty": 1, "assemblyName": "Жгут", "ops": [
        {"type": "insTut", "opNum": "INS_TUT", "tutArt": "TUBE", "tutCount": 1, "tOp": "6", attribute: first}
    ]}
    document = etk_builder.build_etk(config, [])
    card = ProjectTechcard.create(project="BRANCH", item_index="КБ1", product_name="Жгут",
                                  source_calculation="", deadline=None, now=NOW).rebuild(document, NOW).issue(NOW)
    key = operation_key(document["operations"][0])
    config["ops"][0][attribute] = second
    card = replace(card, inputs=config)
    ctx = Context(Refs(None), (), "", None, "branch-identity-test", REASON_BUILDER, NOW)
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=(key,))
    assert result.status == "conflict" and revision is None
    assert updated.document == card.document and updated.revision == card.revision


@pytest.mark.parametrize("saved_kind", ["sketch", "edit", "route"])
@pytest.mark.parametrize("backfill", [False, True])
def test_legacy_label_text_swap_keeps_saved_content_and_revision(saved_kind, backfill):
    config = {"mode": "wire", "partQty": 1, "assemblyName": "Labels", "ops": [
        {"type": "coaxCutTut", "opNum": "PRINT_MARK_LABEL", "markMaterial": "tape", "tOp": "6",
         "marks": [{"text": text, "place": "A", "minMm": 100, "qty": 1}]}
        for text in ("LABEL_A", "LABEL_B")
    ]}
    document = etk_builder.build_etk(config, CARDS)
    card = ProjectTechcard.create(project="LABEL", item_index="KB1", product_name="Labels",
                                  source_calculation="", deadline=None, now=NOW).rebuild(document, NOW).issue(NOW)
    key = operation_key(card.document["operations"][0])
    if saved_kind == "edit":
        edited = deepcopy(card.document["operations"][0])
        edited["card"] = {"steps": [{"text": "MANUAL_FOR_LABEL_A"}]}
        card = card.edit_operation(key, edited, NOW)
        card = replace(card, overrides={"operations": {key: {"operation": deepcopy(edited)}}})
    elif saved_kind == "route":
        card.document["route"]["source"] = "manual"
        card.document["route"]["edges"][0]["per_unit"] = 2
        card = replace(card, overrides={"route": route_override(card.document)})
    if backfill:
        for op in card.document["operations"]:
            op.pop("operation_fingerprint", None)
        for edit in (card.overrides or {}).get("operations", {}).values():
            edit["operation"].pop("operation_fingerprint", None)
    config["ops"].reverse()
    card = replace(card, inputs=config)
    before = deepcopy(card)
    ctx = Context(Refs(None), tuple(CARDS), "", None, "label-identity-test", REASON_BUILDER, NOW)
    updated, revision, result = evaluate(card, ctx, saved_sketch_keys=(key,) if saved_kind == "sketch" else ())
    assert result.status == "conflict" and revision is None
    assert result.conflicts and "позиционный ключ" in updated.review_note
    assert updated.revision == before.revision and updated.document == before.document
    assert updated.overrides == before.overrides and updated.manual_operation_keys == before.manual_operation_keys
    if saved_kind == "sketch":
        sketch = OperationSketch(key, "LABEL_A.png", "image/png", 5, "Technologist", NOW)
        assert document_sketches([sketch], updated.document) == [sketch]


def _legacy_recipe_document(recipes):
    """Keep output fields equal so they cannot mask lost recipe identity in the matcher."""
    return {
        "build_config": {"mode": "wire", "ops": deepcopy(recipes)},
        "operations": [{"position": index, "connector": str(index), "code": "OP",
                        "type": raw["type"], "side": raw.get("side", ""),
                        "template_id": raw.get("templateId", ""), "components": []}
                       for index, raw in enumerate(recipes, 1)],
    }


@pytest.mark.parametrize("duplicate_side", ["source", "target", "both"])
@pytest.mark.parametrize("same_key", [False, True])
def test_legacy_duplicate_fingerprints_conflict_even_when_positional_key_matches(duplicate_side, same_key):
    raw = {"type": "coaxCutTut", "marks": [{"text": "LABEL_A", "place": "A"}]}
    previous = _legacy_recipe_document([raw] * (2 if duplicate_side in {"source", "both"} else 1))
    target = _legacy_recipe_document([raw] * (2 if duplicate_side in {"target", "both"} else 1))
    if not same_key:
        for op in target["operations"]:
            op["connector"] = str(op["position"] + 10)
    with pytest.raises(TechcardValidationError, match="проверка технолога"):
        operation_aliases(previous, target)


# Independently enumerate all fixture fields, excluding only known top-level metadata/timing.
# Do not import the production exclusion set: a newly excluded defining field must fail this test.
_FIXTURE_METADATA = {"templateId", "tPrep", "tOp", "tMachine"}


def _recipe_leaves(value, path=()):
    if isinstance(value, dict) and value:
        for key, item in value.items():
            if path or key not in _FIXTURE_METADATA:
                yield from _recipe_leaves(item, (*path, key))
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from _recipe_leaves(item, (*path, index))
    else:
        yield path, value


def _fixture_recipe_variants():
    variants = {}
    for file in FIXTURES["files"].values():
        for raw in file["payload"].get("config", {}).get("ops", []):
            for path, value in _recipe_leaves(raw):
                variants.setdefault((raw["type"], path), (raw, value))
    return [pytest.param(raw, path, value, id=f"{kind}:{'.'.join(map(str, path))}")
            for (kind, path), (raw, value) in variants.items()]


@pytest.mark.parametrize("raw,path,value", _fixture_recipe_variants())
def test_every_fixture_recipe_field_prevents_silent_transfer_on_swap(raw, path, value):
    changed = deepcopy(raw)
    parent = changed
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = (not value if isinstance(value, bool) else
                       value + 1 if isinstance(value, (int, float)) else
                       value + "_CHANGED" if isinstance(value, str) else "CHANGED")
    previous = _legacy_recipe_document([raw, changed])
    rebuilt = _legacy_recipe_document([changed, raw])
    # All saved content uses these aliases; refuse occupied positional keys, or map to
    # the same recipe if changing the side makes the former key free.
    try:
        aliases = operation_aliases(previous, rebuilt)
    except TechcardValidationError:
        return
    targets = {operation_key(op): index for index, op in enumerate(rebuilt["operations"])}
    for index, op in enumerate(previous["operations"]):
        key = operation_key(op)
        assert targets[aliases.get(key, key)] == 1 - index


@pytest.mark.parametrize("path", [
    ("marks", 0, "text"), ("marks", 0, "place"), ("marks", 0, "position"), ("marks", 0, "minMm"),
    ("marks", 0, "scope"), ("placements", 0, "position"), ("wires", 0, "id"), ("wires", 0, "idx"),
    ("wires", 0, "wireId"), ("ep", "endId"), ("ep", "pin"), ("futureAttribute", "value"),
])
def test_nested_positions_identifiers_and_new_recipe_fields_define_identity(path):
    raw = {"type": "coaxCutTut", "marks": [{"text": "LABEL_A", "place": "A", "position": 1,
                                         "minMm": 100, "scope": "cable"}],
           "placements": [{"position": 1}], "wires": [{"id": "wire-A", "idx": 1, "wireId": "A"}],
           "ep": {"endId": "A", "pin": "1"}, "futureAttribute": {"value": "A"}}
    value = raw
    for part in path:
        value = value[part]
    test_every_fixture_recipe_field_prevents_silent_transfer_on_swap(raw, path, value)


def test_recipe_metadata_changes_keep_unique_legacy_identity():
    raw = {"type": "cutTut", "tutArt": "TUBE", "tutLen": 20,
           "id": "old", "index": 1, "position": 1, "order": 1,
           "created_at": "yesterday", "updatedAt": "yesterday", "timestamp": "yesterday",
           "templateId": "old-template", "tPrep": "10", "tOp": "20", "tMachine": "30"}
    changed = {**raw, "id": "new", "index": 2, "position": 2, "order": 2,
               "created_at": "today", "updatedAt": "today", "timestamp": "today",
               "templateId": "new-template", "tPrep": "11", "tOp": "21", "tMachine": "31",
               "linearTiming": {"setup_seconds": "10", "seconds_per_meter": "2"},
               "markKit": {"norms": {"tubePrint": {"operation_seconds": "10", "setup_seconds": "20"}}},
               "equipmentTools": [{"position": 0, "inventory": "NEW"}],
               "shrinkMaterials": [{"art": "TUBE"}], "shrinkRequirements": [{"status": "missing"}]}
    previous = with_operation_fingerprints(_legacy_recipe_document([raw]))
    target = with_operation_fingerprints(_legacy_recipe_document([changed]))
    assert previous["operations"][0]["operation_fingerprint"] == target["operations"][0]["operation_fingerprint"]
    assert operation_aliases(previous, target) == {}


def test_batch_quantity_keeps_identity_but_piece_quantity_change_conflicts():
    config = {"mode": "wire", "partQty": 1, "assemblyName": "Tube", "ops": [
        {"type": "cutTut", "opNum": "CUT_TUT", "tutArt": "TUBE", "tutCount": 2, "tutLen": 20, "tOp": "6"},
    ]}
    previous = etk_builder.build_etk(config, [])
    config["partQty"] = 2
    resized = etk_builder.build_etk(config, [])
    assert previous["operations"][0]["components"] != resized["operations"][0]["components"]
    assert operation_aliases(previous, resized) == {}
    config["ops"][0]["tutCount"] = 3
    with pytest.raises(TechcardValidationError, match="позиционный ключ"):
        operation_aliases(previous, etk_builder.build_etk(config, []))


@pytest.mark.parametrize("field", ["art", "name", "unit", "color", "position", "id"])
def test_generated_material_attributes_define_identity(field):
    previous = _legacy_recipe_document([{"type": "cutTut"}])
    previous["operations"][0]["components"] = [{field: "A"}]
    target = deepcopy(previous)
    target["operations"][0]["components"][0][field] = "B"
    with pytest.raises(TechcardValidationError, match="позиционный ключ"):
        operation_aliases(previous, target)


@pytest.mark.parametrize("saved_kind", ["edit", "route", None])
def test_duplicate_target_fingerprints_cannot_take_saved_content_without_previous_document(saved_kind):
    raw = {"type": "cutTut", "opNum": "CUT_TUT", "tutArt": "TUBE", "tutLen": 20, "tOp": "6"}
    config = {"mode": "wire", "partQty": 1, "assemblyName": "Tube", "ops": [raw, deepcopy(raw)]}
    previous = with_operation_fingerprints(etk_builder.build_etk(config, []))
    overrides = {}
    if saved_kind == "edit":
        edited = deepcopy(previous["operations"][0])
        edited["card"] = {"steps": [{"text": "MANUAL_FOR_FIRST_TUBE"}]}
        overrides["operations"] = {operation_key(edited): {"operation": edited}}
    elif saved_kind == "route":
        previous["route"]["source"] = "manual"
        previous["route"]["edges"][0]["per_unit"] = 2
        overrides["route"] = route_override(previous)
    document, conflicts = derive(config, overrides, lambda value: value, [])
    if saved_kind is None:
        assert document is not None and conflicts == []
    else:
        assert document is None
        assert any("смысловую идентичность" in item["message"] for item in conflicts)


def test_switch_to_manual_assembly_keeps_unchanged_positional_keys_and_conflicts_on_changed_work():
    """«Поправить вручную»: a manual build has no recipe fingerprints, so a positional key maps only to the same work."""
    from app.modules.techcards.assembly_schema import generator_to_assembly
    from .test_scheme_corrections import GENERATOR

    previous = with_operation_fingerprints(etk_builder.build_etk(deepcopy(GENERATOR), []))
    manual = etk_builder.build_etk(generator_to_assembly(deepcopy(GENERATOR)), [])
    assert previous["operations"][0]["operation_fingerprint"] and operation_key(manual["operations"][0]) == "CUT_WIRE::1::"
    assert operation_aliases(previous, manual) == {}

    changed = deepcopy(manual)
    changed["operations"][0]["result"][0]["name"] = "ПВ3 150мм"
    with pytest.raises(TechcardValidationError, match="позиционный ключ не подтверждает"):
        operation_aliases(previous, changed)
