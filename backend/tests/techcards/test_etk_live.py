"""Operation cards of drafts are live; an issued card is stored (owner, 19.10.2026: «выданная карта молча не меняется»).
Live cards (owner, 17.10.2026): «если карта берётся без изменений — она должна идти из базы данных, а не
копией … если я меняю что-то в ТК — возможность обновить базу». An ETK operation nobody edited by hand shows its
template as it is now (no rebuild, no new revision); a hand-edited one keeps its copy; «Сохранить также в шаблон»
writes the edit into the template and keeps the operation live."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.etk_live import (
    EDITED, SNAPSHOT, TEMPLATE, live_document, stamp_templates, template_record,
)
from app.modules.techcards.operation_cards import OperationRecordEdit, parse_operation_library, parse_operation_template

from .test_etk_golden import CARDS, ERP_LIBRARY, LIBRARY, _cases
from .test_etk_manual import CONFIG as MANUAL_CONFIG
from .test_new_operation_api import _seed
from .test_operation_card_api import EDIT, _template, operation_library_api  # noqa: F401

TEMPLATES = CARDS + parse_operation_library(ERP_LIBRARY["templates"])
BUILT = datetime(2026, 10, 17, 9, 0)


def _cards(document: dict[str, Any]) -> list[Any]:
    return [op.get("card") for op in document["operations"]]


# ── golden: filling a card again from an unchanged template gives exactly the stored copy ──

@pytest.mark.parametrize("case", [*_cases().keys(), "ручная сборка"])
def test_refilling_from_unchanged_templates_reproduces_every_stored_card(case: str):
    if case == "ручная сборка":
        document = build_etk(deepcopy(MANUAL_CONFIG), CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    else:
        document = _cases()[case]()
    stored = deepcopy(document)

    refilled = live_document(document, manual_keys=(), templates=TEMPLATES, built_at=BUILT, refill_all=True)
    stamped = live_document(stamp_templates(document, TEMPLATES), manual_keys=(), templates=TEMPLATES, built_at=BUILT)

    assert _cards(refilled) == _cards(stored)
    assert _cards(stamped) == _cards(stored)
    assert document == stored  # the stored document is never touched
    ids = {card.source_id for card in TEMPLATES}
    for op in refilled["operations"]:
        if not op.get("template_id"):
            assert "card_source" not in op  # an own operation: nothing to follow
        elif op["template_id"] in ids:
            assert op["card_source"] == TEMPLATE


# ── unit: what changes and what does not ──

WIRE = {"name": "ПВ3 черный", "art": "ПВ3", "qty": 1, "length": 100}


def _cut_template(step: str = "Отрезать провод {{WIRE_NAME}} длиной {{LENGTH}} мм") -> dict[str, Any]:
    template = _template("cut-wire", "CUT_WIRE")
    template["rows"][10] = ["Операция", "1", step, "", "", "", "КД"]
    template["rows"].append(["Контроль", "2", "Проверить длину", "", "", "", "-"])
    return template


def _parsed(template: dict[str, Any]):
    return parse_operation_template(source_id=template["id"], title=template["title"], category=template["category"],
                                    rows=template["rows"])


def _config(project: str = "P-400") -> dict[str, Any]:
    return {
        "projectNo": project, "assemblyIndex": "КБ1", "assemblyName": "Жгут", "partQty": 10, "wires": [WIRE],
        "ops": [{"type": "cutWire", "templateId": "cut-wire", "opNum": "CUT_WIRE", "wires": [WIRE],
                 "tPrep": "60", "tOp": "5"}],
    }


def test_a_document_built_before_the_stamp_follows_an_erp_edit_made_after_its_last_update():
    card = _parsed(_cut_template())
    document = build_etk(_config(), [card], operation_library_commit="lib")
    assert "templates" not in document["source"]
    edited = card.with_edit(OperationRecordEdit.from_json({
        "name": card.name, "safety": "", "defects": "", "revision": "",
        "steps": [{"number": "1", "kind": "Операция", "text": "Резать {{WIRE_NAME}}", "tolerance": "-"}], "tooling": [],
    }), changed_at=BUILT + timedelta(hours=1), changed_by="Технолог")

    before = live_document(document, manual_keys=(), templates=[edited], built_at=BUILT + timedelta(hours=2))
    after = live_document(document, manual_keys=(), templates=[edited], built_at=BUILT)

    assert before["operations"][0]["card"] == document["operations"][0]["card"]
    assert after["operations"][0]["card"]["steps"][0]["text"] == "Резать ПВ3 черный"
    assert after["operations"][0]["card_source"] == TEMPLATE
    assert after["operations"][0]["card_changed_at"] == (BUILT + timedelta(hours=1)).isoformat()


def test_without_a_build_config_or_a_template_the_copy_stays():
    card = _parsed(_cut_template())
    document = stamp_templates(build_etk(_config(), [card], operation_library_commit="lib"), [card])
    imported = {key: value for key, value in document.items() if key != "build_config"}

    gone = live_document(document, manual_keys=(), templates=[], built_at=BUILT)
    no_config = live_document(imported, manual_keys=(), templates=[card], built_at=BUILT)

    assert gone["operations"][0]["card_source"] == SNAPSHOT
    assert gone["operations"][0]["card"] == document["operations"][0]["card"]
    assert no_config["operations"][0]["card_source"] == SNAPSHOT


def test_a_template_reloaded_under_another_id_is_found_by_its_code():
    card = _parsed(_cut_template())
    document = stamp_templates(build_etk(_config(), [card], operation_library_commit="lib"), [card])
    renamed = _cut_template("Отрезать {{WIRE_NAME}}")
    renamed["id"] = "cut-wire-2"

    live = live_document(document, manual_keys=(), templates=[_parsed(renamed)], built_at=BUILT)

    assert live["operations"][0]["card_source"] == TEMPLATE
    assert live["operations"][0]["card"]["steps"][0]["text"] == "Отрезать ПВ3 черный"


def test_write_back_keeps_the_placeholders_of_what_was_left_as_shown():
    card = _parsed(_cut_template())
    shown = build_etk(_config(), [card], operation_library_commit="lib")["operations"][0]["card"]
    edited = deepcopy(shown)
    edited["steps"][1]["text"] = "Проверить длину рулеткой"

    record = template_record(card, shown, edited)

    assert [step["text"] for step in record["steps"]] == [
        "Отрезать провод {{WIRE_NAME}} длиной {{LENGTH}} мм", "Проверить длину рулеткой",
    ]
    assert record["name"] == card.name
    assert [tool["name"] for tool in record["tooling"]] == [tool.name for tool in card.tooling]


# ── API: the screens read live cards; «Править операцию» with and without «Сохранить также в шаблон» ──

def _issued(client, project: str = "P-400") -> dict[str, Any]:
    created = client.post("/api/techcards/projects", json={"project": project, "item_index": "КБ1",
                                                           "product_name": "Жгут"}).json()
    built = client.post(f"/api/techcards/projects/{created['id']}/build",
                        json={"version": created["version"], "config": _config(project)})
    assert built.status_code == 200, built.text
    issued = client.post(f"/api/techcards/projects/{created['id']}/issue", json={"version": built.json()["version"]})
    assert issued.status_code == 200, issued.text
    return issued.json()


def _read(client, card: dict[str, Any]) -> dict[str, Any]:
    return client.get(f"/api/techcards/projects/{card['id']}").json()


def _edit(client, card: dict[str, Any], text: str, **extra) -> Any:
    operation = deepcopy(card["document"]["operations"][0])
    operation["card"]["steps"][0]["text"] = text
    key = f"{operation['code']}::{operation['connector']}::{operation['side']}"
    return client.put(f"/api/techcards/projects/{card['id']}/operations/{key}",
                      json={"version": card["version"], "operation": operation, **extra})


def _draft(client, project: str = "P-402") -> dict[str, Any]:
    created = client.post("/api/techcards/projects", json={"project": project, "item_index": "КБ1",
                                                           "product_name": "Жгут"}).json()
    built = client.post(f"/api/techcards/projects/{created['id']}/build",
                        json={"version": created["version"], "config": _config(project)})
    assert built.status_code == 200, built.text
    return built.json()


def _rename_step(client) -> Any:
    return client.put("/api/techcards/templates/cut-wire", json={
        **EDIT, "steps": [{"kind": "Операция", "text": "Резать {{WIRE_NAME}} в размер", "tolerance": ""}],
    })


def test_an_issued_card_does_not_change_when_its_template_changes(operation_library_api):
    client = operation_library_api
    _seed(client, [_cut_template()])
    card = _issued(client)
    op = card["document"]["operations"][0]
    assert op["card_source"] == TEMPLATE and op["card_changed_at"] == ""
    assert op["card"]["steps"][0]["text"] == "Отрезать провод ПВ3 черный длиной 100 мм"

    assert _rename_step(client).status_code == 200
    after = _read(client, card)

    shown = after["document"]["operations"][0]
    assert shown["card"]["steps"][0]["text"] == "Отрезать провод ПВ3 черный длиной 100 мм"
    assert shown["card_source"] == TEMPLATE and shown["card_changed_at"] == ""
    assert (after["version"], after["revision"]) == (card["version"], card["revision"])


def test_a_draft_follows_its_template_live(operation_library_api):
    client = operation_library_api
    _seed(client, [_cut_template()])
    draft = _draft(client)
    assert draft["document"]["operations"][0]["card"]["steps"][0]["text"].endswith("длиной 100 мм")

    assert _rename_step(client).status_code == 200
    after = _read(client, draft)

    assert after["document"]["operations"][0]["card"]["steps"][0]["text"] == "Резать ПВ3 черный в размер"
    assert (after["version"], after["revision"]) == (draft["version"], draft["revision"])


def test_the_card_view_exposes_the_review_note_and_the_revision_source_and_diff(operation_library_api):
    client = operation_library_api
    _seed(client, [_cut_template()])
    card = _issued(client)
    assert card["review_note"] is None
    revisions = client.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"]
    assert revisions[0]["source"] == "manual" and revisions[0]["diff"] is None


def test_a_hand_edited_operation_keeps_its_copy(operation_library_api):
    client = operation_library_api
    _seed(client, [_cut_template()])
    card = _issued(client)
    edited = _edit(client, card, "Отрезать по месту")
    assert edited.status_code == 200, edited.text
    client.put("/api/techcards/templates/cut-wire", json=EDIT)

    op = _read(client, card)["document"]["operations"][0]

    assert op["card_source"] == EDITED and op["card_changed_at"].startswith("2026-09-23T12:00")
    assert op["card"]["steps"][0]["text"] == "Отрезать по месту"


def test_save_to_template_updates_the_template_and_keeps_the_operation_live(operation_library_api):
    client = operation_library_api
    _seed(client, [_cut_template()])
    card = _issued(client)
    other = _issued(client, "P-401")

    saved = _edit(client, card, "Отрезать провод ПВ3 черный ножом", to_template=True)

    assert saved.status_code == 200, saved.text
    body = saved.json()
    template = client.get("/api/techcards/templates/cut-wire").json()["document"]
    assert [step["text"] for step in template["steps"]][1] == "Проверить длину"
    assert body["manual_operation_keys"] == [] and body["revision"] == card["revision"] + 1
    op = body["document"]["operations"][0]
    assert op["card_source"] == TEMPLATE and "edited" not in op
    assert op["card"]["steps"][0]["text"] == "Отрезать провод ПВ3 черный ножом"
    # another issued ETK keeps what it was issued with: the template reaches it as a revision, not silently
    shown = _read(client, other)
    assert shown["document"]["operations"][0]["card"]["steps"][0]["text"].endswith("длиной 100 мм")
    assert shown["revision"] == other["revision"]
    revisions = client.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"]
    assert revisions[-1]["reason"].endswith("— и в шаблон")


def test_save_to_template_of_a_hand_edited_operation_makes_it_live_again(operation_library_api):
    client = operation_library_api
    _seed(client, [_cut_template()])
    card = _issued(client)
    first = _edit(client, card, "Отрезать по месту").json()

    # the hand-edited step is left as it is in the form — it is the edit, so it goes into the template as typed
    saved = _edit(client, first, "Отрезать по месту", to_template=True).json()
    template = client.get("/api/techcards/templates/cut-wire").json()["document"]
    later = client.put("/api/techcards/templates/cut-wire", json={
        **EDIT, "steps": [{"kind": "Операция", "text": "Резать {{WIRE_NAME}}", "tolerance": ""}],
    })

    assert [step["text"] for step in template["steps"]] == ["Отрезать по месту", "Проверить длину"]
    assert saved["manual_operation_keys"] == []
    assert saved["document"]["operations"][0]["card_source"] == TEMPLATE
    assert later.status_code == 200
    op = _read(client, card)["document"]["operations"][0]
    assert op["card_source"] == TEMPLATE and op["card"]["steps"][0]["text"] == "Отрезать по месту"


def test_a_template_gone_from_the_library_leaves_the_copy(operation_library_api):
    client = operation_library_api
    _seed(client, [_cut_template()])
    card = _issued(client)
    _seed(client, [_template("other", "OTHER")])

    op = _read(client, card)["document"]["operations"][0]

    assert op["card_source"] == TEMPLATE  # the stored stamp, not the live library, says where the card came from
    assert op["card"] == {key: value for key, value in card["document"]["operations"][0]["card"].items()}


def test_card_view_exposes_review_note_and_shows_an_issued_document_as_stored():
    from uuid import uuid4

    from app.modules.techcards.etk_read import card_view
    from app.modules.techcards.project_cards import ProjectTechcard, TechcardStatus

    parsed = _parsed(_cut_template())
    document = stamp_templates(build_etk(_config(), [parsed], operation_library_commit="lib"), [parsed])
    changed = _parsed(_cut_template("Иначе {{WIRE_NAME}}"))
    now = datetime(2026, 10, 19, 9, 0)
    card = ProjectTechcard(
        id=uuid4(), version=3, project="P-1", item_index="КБ1", product_name="Жгут", source_calculation="",
        deadline=None, status=TechcardStatus.ISSUED, revision=1, document=document, manual_operation_keys=(),
        created_at=now, updated_at=now, issued_at=now, review_note="Проверить длину резки",
    )
    issued = card_view(card, templates=[changed])
    draft = card_view(replace(card, status=TechcardStatus.DRAFT), templates=[changed])

    assert issued["review_note"] == "Проверить длину резки"
    assert issued["document"]["operations"][0]["card"] == document["operations"][0]["card"]
    assert draft["document"]["operations"][0]["card"]["steps"][0]["text"] == "Иначе ПВ3 черный"
