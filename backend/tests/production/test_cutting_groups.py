"""708К-like grouped issue: only equal frozen preparation recipes share a card."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.cutting import cutting_recipe
from app.modules.production.domain import ProductionStatus
from app.modules.production.cutting_infrastructure import CuttingGroupRow, CuttingMemberRow
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.production.launchable import launch_operations, launch_route
from app.modules.techcards.project_cards import ProjectTechcard
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import production_api, MASTER_ID  # noqa: F401
from tests.production.test_launch_race import race, interleave  # noqa: F401

NOW = datetime(2026, 9, 22, 9)


def document(length=1000, strip=3, cables=1):
    """Confirmed execution settings, unlike the unresolved settings in the real imported 708К fixture."""
    end = {"connector": "FAKRA straight", "connectorType": "straight", "l1": "3", "l2": "4.6", "l3": "9.7",
           "d1": ".81", "d2": "2.1", "d3": "3.2", "stripMode": "full",
           "stripProgram": {"boundaries": [
               {"layer": "dielectric", "value": str(strip), "tolerance": "±0.2"},
               {"layer": "braid", "value": "7.6", "tolerance": "±0.4"},
               {"layer": "jacket", "value": "12.7", "tolerance": "±0.4"}]}}
    raw = [
        {"type": "coaxCut", "opNumber": "CUT_COAX+2strip", "templateId": "cut", "units": cables,
         "stripSides": ["A", "B"], "tOp": 10, "tPrep": 0},
        {"type": "coaxCutTut", "opNumber": "PRINT_MARK_LABEL", "templateId": "print", "units": 3,
         "markMaterial": "tape", "parts": [{"art": "VL-S641", "qty": 3}],
         "marks": [{"text": "PN-1 {№}", "place": place, "width": 18} for place in ("middle", "A", "B")], "tOp": 5},
        {"type": "coaxCutTut", "opNumber": "CUT_TUT_short", "templateId": "tube", "units": 3, "tOp": 5},
        {"type": "coaxHousing", "opNumber": "ASM", "templateId": "assembly", "tOp": 30},
    ]
    ops = [{"type": op["type"], "code": op["opNumber"], "template_id": op["templateId"],
            "position": i, "card_changed_at": "2026-11-17", "fields": {},
            "card": {"steps": [{"text": "Выполнить по параметрам ЭТК"}], "tooling": [
                {"section": "Оборудование", "inventory_number": f"ОБ.{i}", "program": "P1", "parameters": "confirmed"}]}}
           for i, op in enumerate(raw, 1)]
    ops[0]["fields"] = {"lengthKd": f"{length}мм", "cutTolerance": "0…+5 мм", "coax": deepcopy(end), "coaxB": deepcopy(end)}
    ops[1]["fields"] = {"coaxMarks": deepcopy(raw[1]["marks"])}
    ops[2]["fields"] = {"lengthKd": "60мм"}
    return {"operations": ops, "kit": {"part_quantity": "10"}, "build_config": {
        "ops": raw, "coax": {"cableArt": "RTK 031", "cableLength": length, "cables": cables,
                                "tutArt": "ТУТ 4/2", "tutLength": 60}},
        "route": {"source": "manual", "blocks": [
            {"key": "cut", "ops": [1, 2, 3], "name": "Заготовки", "kit": True},
            {"key": "assembly", "ops": [4], "name": "Сборка", "final": True}],
            "edges": [{"from": "cut", "to": "assembly", "per_unit": 1}]}}


def recipe(doc, project="708К.", site="ЦАР"):
    return cutting_recipe(project, site, doc, launch_operations(launch_route(doc).steps, 10))


def issue(index, doc=None):
    card = ProjectTechcard.create(project="708К.", item_index=index, product_name=f"Сборка {index}",
        source_calculation="", deadline=None, now=NOW).rebuild(doc or document(), NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def item(card, **kwargs):
    return {"techcard_id": str(card.id), "techcard_version": card.version, "launched": 0, "quantity": 10, **kwargs}


def batch(client, cards, **kwargs):
    response = client.post("/api/production/launch-ready/launch", json={"items": [item(c) for c in cards], **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def groups(client):
    response = client.get("/api/production/cutting-groups")
    assert response.status_code == 200, response.text
    return response.json()["groups"]


def test_length_quantity_label_text_and_drawing_identity_do_not_change_recipe():
    first = document()
    second = document(length=2300)
    second["kit"]["part_quantity"] = "20"
    second["drawing_group"] = "angled"
    second["operations"][0]["fields"]["coax"].update(connector="OTHER", connectorType="angled")
    second["build_config"]["ops"][1]["marks"][0]["text"] = "OTHER {№}"
    second["operations"][1]["fields"]["coaxMarks"][0]["text"] = "OTHER {№}"
    a, why = recipe(first)
    b, why_b = recipe(second, project="708К.1")
    assert a is not None, why
    assert b is not None, why_b
    assert a.key == b.key
    assert a.row != b.row


@pytest.mark.parametrize("change", ["strip", "side", "mode", "tolerance", "machine", "tooling", "card", "label", "tube", "cables", "site"])
def test_execution_differences_do_not_group(change):
    original = document()
    altered = deepcopy(original)
    fields = altered["operations"][0]["fields"]
    if change == "strip": fields["coax"]["stripProgram"]["boundaries"][0]["value"] = "4"
    if change == "side": altered["build_config"]["ops"][0]["stripSides"] = ["B"]
    if change == "mode": fields["coax"]["stripMode"] = "partial"
    if change == "tolerance": fields["cutTolerance"] = "±10 мм"
    if change == "machine": altered["operations"][0]["card"]["tooling"][0]["inventory_number"] = "ОБ.99"
    if change == "tooling": altered["operations"][0]["card"]["tooling"].append({"section": "Инструмент", "name": "other"})
    if change == "card": altered["operations"][0]["card_changed_at"] = "2026-11-18"
    if change == "label": altered["build_config"]["ops"][1]["marks"][0]["width"] = 20
    if change == "tube": altered["build_config"]["coax"]["tutLength"] = 70
    if change == "cables": altered = document(cables=4)
    one, _ = recipe(original)
    two, why = recipe(altered, site="ЖУК" if change == "site" else "ЦАР")
    assert two is not None, why
    assert one.key != two.key


@pytest.mark.parametrize("missing", ["stripProgram", "stripMode", "d1"])
def test_missing_strip_settings_are_ineligible(missing):
    doc = document()
    del doc["operations"][0]["fields"]["coax"][missing]
    assert recipe(doc)[0] is None


def test_invalid_program_and_nonpreparation_pf_are_ineligible():
    doc = document()
    doc["operations"][0]["fields"]["coax"]["stripProgram"]["boundaries"] = []
    assert recipe(doc)[0] is None
    doc = document()
    doc["route"]["blocks"][0]["ops"] = [1, 2, 3, 4]
    doc["route"]["blocks"] = doc["route"]["blocks"][:1]
    doc["route"]["edges"] = []
    assert recipe(doc)[0] is None


def test_708k_four_kbs_create_one_shared_and_two_distinct_groups(production_api):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2", document(length=2000)),
             issue("КБ3", document(strip=4)), issue("КБ4", document(cables=4))]
    preview = client.post("/api/production/launch-ready/preview", json={"items": [item(c) for c in cards]})
    assert preview.status_code == 200, preview.text
    assert sorted(len(g["members"]) for g in preview.json()["groups"]) == [1, 1, 2]
    body = batch(client, cards, fingerprint=preview.json()["fingerprint"])
    assert len(body["created"]) == 4 and body["refused"] == []
    actual = groups(client)
    assert sorted(len(g["rows"]) for g in actual) == [1, 1, 2]
    common = next(g for g in actual if len(g["rows"]) == 2)
    assert {r["item"] for r in common["rows"]} == {"КБ1", "КБ2"}
    quad = next(r for g in actual for r in g["rows"] if r["item"] == "КБ4")
    assert Decimal(quad["blank_quantity"]) == 40
    assert Decimal(quad["operation_quantity"]) == 10  # preparation kits, not synthetic cable output
    retry = batch(client, cards)
    assert retry["created"] == [] and len(retry["refused"]) == 4
    assert len(groups(client)) == 3


def test_stale_row_rolls_back_its_whole_compatible_group_only(production_api):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2"), issue("КБ3", document(strip=4))]
    payload = [item(c) for c in cards]
    payload[1]["techcard_version"] += 1
    body = client.post("/api/production/launch-ready/launch", json={"items": payload}).json()
    assert [t["item"] for t in body["created"]] == ["КБ3"]
    assert len(body["refused"]) == 2
    with app.state.production_uow_factory() as uow:
        assert len(uow.production.list()) == 1


def test_manual_join_separate_split_cancel_and_stale_version(production_api):
    client, _ = production_api
    cards = [issue(f"КБ{i}") for i in range(1, 5)]
    batch(client, cards[:2])
    group = groups(client)[0]
    response = client.post("/api/production/tasks", headers={"Idempotency-Key": "separate"},
        json={"techcard_id": str(cards[2].id), "quantity": 10, "issue_separately": True})
    assert response.status_code == 200, response.text
    again = client.post("/api/production/tasks", headers={"Idempotency-Key": "separate"},
        json={"techcard_id": str(cards[2].id), "quantity": 10, "issue_separately": True})
    assert again.json()["task"]["id"] == response.json()["task"]["id"]
    response = client.post("/api/production/tasks", headers={"Idempotency-Key": "joined"},
        json={"techcard_id": str(cards[3].id), "quantity": 10})
    assert response.status_code == 200, response.text
    current = next(g for g in groups(client) if g["id"] == group["id"])
    assert len(current["rows"]) == 3
    url = f"/api/production/cutting-groups/{group['id']}/split"
    assert client.post(url, json={"version": group["version"], "member_ids": [group["rows"][0]["id"]]}).status_code == 409
    split = client.post(url, json={"version": current["version"], "member_ids": [current["rows"][0]["id"]]})
    assert split.status_code == 200, split.text
    split = split.json()["group"]
    assert [r["membership_state"] for r in split["rows"]].count("split") == 1
    cancelled = client.post(f"/api/production/cutting-groups/{group['id']}/cancel", json={"version": split["version"]})
    assert cancelled.status_code == 200 and cancelled.json()["group"]["state"] == "cancelled"
    with app.state.production_uow_factory() as uow:
        assert len(uow.production.with_status(ProductionStatus.RUNNING)) == 4


def test_assignment_counts_each_child_once_and_fitter_gets_one_card(production_api):
    client, operator = production_api
    batch(client, [issue("КБ1"), issue("КБ2", document(length=2000))])
    group = groups(client)[0]
    response = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
        json={"version": group["version"], "operator_ids": [str(operator)]})
    assert response.status_code == 200, response.text
    assigned = response.json()["group"]
    rows = assigned["rows"]
    assert rows[0]["planned_finish"] <= rows[1]["planned_start"]
    assert assigned["norm_seconds"] == sum(r["norm_seconds"] for r in rows)
    mine = client.get("/api/production/my", params={"operator_id": str(operator)})
    assert mine.status_code == 200, mine.text
    assert mine.json()["tasks"] == []
    assert len(mine.json()["cutting_groups"]) == 1
    batch(client, [issue("КБ3")])
    assert len(groups(client)) == 2  # assigned groups no longer accept late rows


def test_partial_output_freezes_membership_and_boxes_remain_per_kb(production_api):
    client, operator = production_api
    batch(client, [issue("КБ1"), issue("КБ2")])
    group = groups(client)[0]
    assigned = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
        json={"version": group["version"], "operator_ids": [str(operator)]}).json()["group"]
    rows = assigned["rows"]
    def start(row):
        return client.post(f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/work/start",
                           params={"on_behalf_of": str(operator)}, json={"inputs": []})
    first = start(rows[0])
    assert first.status_code == 200, first.text
    assert start(rows[1]).status_code == 422  # never open multiple child sessions
    session = first.json()["session"]
    output = client.post(f"/api/production/work/{session['id']}/submit", params={"on_behalf_of": str(operator)},
                         json={"good_quantity": "3", "defect_quantity": "1", "defect_character": "обрыв", "defect_description": "обрыв жилы"})
    assert output.status_code == 200, output.text
    current = groups(client)[0]
    assert current["membership_frozen"]
    assert Decimal(current["rows"][0]["good"]) == 3
    assert Decimal(current["rows"][1]["good"]) == 0
    assert current["rows"][1]["boxes"] == []
    split = client.post(f"/api/production/cutting-groups/{group['id']}/split",
        json={"version": current["version"], "member_ids": [rows[1]["id"]]})
    assert split.status_code == 422
    # A child box cannot become the other KB's downstream input.
    with app.state.production_uow_factory() as uow:
        box = uow.boxes.list(task_id=UUID(rows[0]["task_id"]))[0]
        assert box.task_id != UUID(rows[1]["task_id"])
        assert "другого производственного задания" in box.input_error(item="КБ2", previous_position=1, task_id=UUID(rows[1]["task_id"]))


def test_autoplan_issues_one_group_and_counts_sequential_child_windows(production_api):
    client, _ = production_api
    issue("КБ1")
    issue("КБ2", document(length=2000))
    response = client.post("/api/production/autoplan/preview", json={})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert len(preview["cutting_groups"]) == 1
    identifiers = preview["cutting_groups"][0]["operation_ids"]
    cuts = [p for p in preview["proposals"] if p["operation_id"] in identifiers]
    assert len(cuts) == 2
    assert len({p["operator_id"] for p in cuts}) == 1
    assert cuts[0]["finish"] <= cuts[1]["start"]
    assert all(p["setup_seconds"] == p["changeover_seconds"] == 0 for p in cuts)
    selected = [{k: p[k] for k in ("task_id", "operation_id", "operator_id")} for p in preview["proposals"]]
    partial = client.post("/api/production/autoplan/apply", json={"fingerprint": preview["fingerprint"], "items": [selected[0]]})
    assert partial.status_code == 422, partial.text
    applied = client.post("/api/production/autoplan/apply", json={"fingerprint": preview["fingerprint"], "items": selected})
    assert applied.status_code == 200, applied.text
    assert len(groups(client)) == 1
    assert len(groups(client)[0]["rows"]) == 2
    assert applied.json()["applied"] == 4  # two cuts and two downstream PFs, never an extra group claim


def test_cancel_restore_preserves_member_and_invalidates_preview(production_api):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    row = group["rows"][0]
    url = f"/api/production/tasks/{row['task_id']}/cancel"
    response = client.post(url, json={"version": row["task_version"], "cancelled": True})
    assert response.status_code == 200, response.text
    current = groups(client)[0]
    assert current["rows"][0]["membership_state"] == "cancelled"
    assert current["version"] > group["version"]
    response = client.post(url, json={"version": response.json()["task"]["version"], "cancelled": False})
    assert response.status_code == 200, response.text
    assert groups(client)[0]["rows"][0]["id"] == row["id"]
    assert groups(client)[0]["rows"][0]["membership_state"] == "active"


def test_group_site_access_and_membership_preview_version(production_api):
    client, _ = production_api
    batch(client, [issue("КБ1"), issue("КБ2")])
    group = groups(client)[0]
    card = issue("КБ3")
    payload = {"items": [item(card)]}
    preview = client.post("/api/production/launch-ready/preview", json=payload).json()
    client.post(f"/api/production/cutting-groups/{group['id']}/split",
                json={"version": group["version"], "member_ids": [group["rows"][0]["id"]]})
    stale = client.post("/api/production/launch-ready/launch", json={**payload, "fingerprint": preview["fingerprint"]})
    assert stale.status_code == 409
    from app.modules.identity.infrastructure import IdentityUserRow
    with app.state.production_uow_factory() as uow:
        uow.session.get(IdentityUserRow, MASTER_ID).site = "ЖУК"
        uow.commit()
    assert groups(client) == []
    forbidden = client.get(f"/api/production/cutting-groups/{group['id']}")
    assert forbidden.status_code == 403


@pytest.mark.parametrize("size", [1, 2])
@pytest.mark.parametrize("actor_site,operator_site", [("ЖУК", "ЖУК"), ("", "ЖУК"), ("ЦАР", "ЦАР"), ("", "ЦАР")])
def test_autoplan_respects_existing_group_site(production_api, size, actor_site, operator_site):
    from app.modules.identity.infrastructure import IdentityUserRow

    client, operator = production_api
    batch(client, [issue(f"КБ{i}") for i in range(size)])
    group = groups(client)[0]
    identifiers = {row["operation_id"] for row in group["rows"]}
    with app.state.production_uow_factory() as uow:
        uow.session.get(IdentityUserRow, MASTER_ID).site = actor_site
        uow.session.get(IdentityUserRow, operator).site = operator_site
        uow.commit()
    response = client.post("/api/production/autoplan/preview", json={})
    assert response.status_code == 200, response.text
    preview = response.json()
    cuts = [p for p in preview["proposals"] if p["operation_id"] in identifiers]
    if operator_site == "ЖУК":
        assert cuts == []
        with app.state.production_uow_factory() as uow:
            assert all(not uow.production.get(UUID(row["task_id"])).operation(UUID(row["operation_id"])).operator_ids
                       for row in group["rows"])
    else:
        assert len(cuts) == size
        selected = [{k: p[k] for k in ("task_id", "operation_id", "operator_id")} for p in cuts]
        applied = client.post("/api/production/autoplan/apply",
                              json={"fingerprint": preview["fingerprint"], "items": selected})
        assert applied.status_code == 200, applied.text


def test_autoplan_apply_rechecks_manual_group_assignment_guard(production_api, monkeypatch):
    from app.modules.identity.infrastructure import IdentityUserRow
    from app.modules.production import cutting_application

    client, operator = production_api
    batch(client, [issue("КБ1"), issue("КБ2")])
    group = groups(client)[0]
    identifiers = {row["operation_id"] for row in group["rows"]}
    with app.state.production_uow_factory() as uow:
        uow.session.get(IdentityUserRow, MASTER_ID).site = "ЖУК"
        uow.session.get(IdentityUserRow, operator).site = "ЖУК"
        uow.commit()
    # Simulate a planner regression: persistence must independently reject the invalid assignments.
    monkeypatch.setattr(cutting_application, "planning_groups", lambda *args: ((), {}))
    response = client.post("/api/production/autoplan/preview", json={})
    assert response.status_code == 200, response.text
    preview = response.json()
    cuts = [p for p in preview["proposals"] if p["operation_id"] in identifiers]
    assert len(cuts) == 2
    selected = [{k: p[k] for k in ("task_id", "operation_id", "operator_id")} for p in cuts]
    applied = client.post("/api/production/autoplan/apply",
                          json={"fingerprint": preview["fingerprint"], "items": selected})
    assert applied.status_code == 422, applied.text
    assert "площадки" in applied.json()["detail"]
    with app.state.production_uow_factory() as uow:
        for row in group["rows"]:
            task = uow.production.get(UUID(row["task_id"]))
            assert task.version == row["task_version"]
            assert task.operation(UUID(row["operation_id"])).operator_ids == ()


def test_split_foreign_history_does_not_block_active_group(production_api):
    from app.modules.identity.infrastructure import IdentityUserRow

    client, _ = production_api
    batch(client, [issue("КБ1"), issue("КБ2")])
    group = groups(client)[0]
    row = group["rows"][0]
    url = f"/api/production/cutting-groups/{group['id']}"
    split = client.post(f"{url}/split", json={"version": group["version"], "member_ids": [row["id"]]})
    assert split.status_code == 200, split.text
    handed = client.post(f"/api/production/tasks/{row['task_id']}/handover",
                         json={"version": row["task_version"], "site": "ЖУК"})
    assert handed.status_code == 200, handed.text
    detail = client.get(url)
    assert detail.status_code == 200, detail.text
    current = detail.json()["group"]
    assert groups(client)[0]["id"] == group["id"]
    assert {r["id"]: r["membership_state"] for r in current["rows"]} == {
        row["id"]: "split", group["rows"][1]["id"]: "active"}
    cancelled = client.post(f"{url}/cancel", json={"version": current["version"]})
    assert cancelled.status_code == 200, cancelled.text
    with app.state.production_uow_factory() as uow:
        uow.session.get(IdentityUserRow, MASTER_ID).site = "ЖУК"
        uow.commit()
    assert client.get(url).status_code == 403
    assert groups(client) == []


def test_revalidation_requires_equal_recipe_and_preserves_issued_document(production_api):
    client, operator = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    assigned = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
        json={"version": group["version"], "operator_ids": [str(operator)]}).json()["group"]
    with app.state.techcards_uow_factory() as uow:
        card = uow.techcards.get(cards[0].id)
        changed = deepcopy(card.document)
        changed["operations"][3]["fields"] = {"later": "changed"}
        uow.techcards.save(card.rebuild(changed, NOW))
        uow.commit()
    row = assigned["rows"][0]
    url = f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/work/start"
    stale = client.post(url, params={"on_behalf_of": str(operator)}, json={})
    assert stale.status_code == 409, stale.text
    verified = client.post(f"/api/production/cutting-groups/{group['id']}/revalidate", json={"version": assigned["version"]})
    assert verified.status_code == 200, verified.text
    start = client.post(url, params={"on_behalf_of": str(operator)}, json={})
    assert start.status_code == 200, start.text
    frozen = client.get(row["etk_url"])
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["card"]["document"]["operations"][3]["fields"] == {}


def test_concurrent_issue_and_replay_do_not_duplicate_group_members(race, monkeypatch):
    _, sessions, factory = race
    from app.modules.production.application import DuplicateLaunch, LaunchProductionCommand, launch_production
    from tests.production.test_launch_race import ACTOR, NOW as RACE_NOW
    card = issue("КБ1")
    def command(key):
        doc = document()
        return LaunchProductionCommand(idempotency_key=key, project=card.project, item=card.item_index,
            name=card.product_name, quantity=10, fulfillment_request_id=None, deadline=None,
            operations=launch_operations(launch_route(doc).steps, 10), actor=ACTOR, now=RACE_NOW,
            techcard_id=card.id, techcard_revision=card.revision, cutting_document=doc, cutting_card_version=card.version)
    interleave(monkeypatch, "last_launch_seqs", lambda: launch_production(command("winner"), factory()))
    with pytest.raises(DuplicateLaunch):
        launch_production(command("loser"), factory())
    monkeypatch.undo()
    repeated = launch_production(command("winner"), factory())
    with sessions() as session:
        assert len(session.scalars(select(ProductionTaskRow)).all()) == 1
        assert len(session.scalars(select(CuttingGroupRow)).all()) == 1
        members = session.scalars(select(CuttingMemberRow)).all()
        assert len(members) == 1 and members[0].task_id == repeated.id


def test_grouped_materials_keep_each_requests_entitlement(production_api):
    client, operator = production_api
    from app.modules.fulfillment.domain import FulfillmentStatus
    from app.modules.fulfillment.infrastructure import FulfillmentRequestRow, FulfillmentLineRow
    requests, lines = [uuid4(), uuid4()], [uuid4(), uuid4()]
    reel = uuid4()
    with app.state.production_uow_factory() as uow:
        for index, (request, line) in enumerate(zip(requests, lines)):
            uow.session.add(FulfillmentRequestRow(id=request, number=f"REQ.{index}", source_idempotency_key=f"req{index}",
                source_fingerprint="f", version=1, created_at=NOW, requester="Мастер", purpose=f"708К. КБ{index + 1}",
                deadline=None, status=FulfillmentStatus.ISSUED.value, handout_location=None, return_location=None))
            uow.session.add(FulfillmentLineRow(id=line, request_id=request, position=0, requirement_id=line,
                article="RTK 031", name="Кабель", unit="м", requirement=5, reserved=0, issued=5, returned_good=0,
                defective_returned=0, defective_missing=0, lot_id=reel, lot_warehouse="01", lot_grn="PPM.26.1",
                lot_package="П1", lot_storage_location="A-01"))
        uow.commit()
    for index, request in enumerate(requests):
        card = issue(f"КБ{index + 1}")
        response = client.post("/api/production/tasks", headers={"Idempotency-Key": f"materials-{index}"},
            json={"techcard_id": str(card.id), "quantity": 10, "fulfillment_request_id": str(request)})
        assert response.status_code == 200, response.text
    group = groups(client)[0]
    assigned = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
        json={"version": group["version"], "operator_ids": [str(operator)]}).json()["group"]
    row = assigned["rows"][0]
    url = f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/work/start"
    def start(line, amount):
        return client.post(url, params={"on_behalf_of": str(operator)},
                           json={"materials": [{"fulfillment_line_id": str(line), "quantity": str(amount)}]})
    assert start(lines[1], 1).status_code == 422
    assert start(lines[0], 6).status_code == 422
    with app.state.production_uow_factory() as uow:
        uow.session.get(FulfillmentRequestRow, requests[0]).status = FulfillmentStatus.COMPLETE.value
        uow.commit()
    assert start(lines[0], 1).status_code == 422
    with app.state.production_uow_factory() as uow:
        uow.session.get(FulfillmentRequestRow, requests[0]).status = FulfillmentStatus.ISSUED.value
        uow.commit()
    started = start(lines[0], 3)
    assert started.status_code == 200, started.text
    submitted = client.post(f"/api/production/work/{started.json()['session']['id']}/submit",
                           params={"on_behalf_of": str(operator)}, json={"good_quantity": "2"})
    assert submitted.status_code == 200, submitted.text
    with app.state.production_uow_factory() as uow:
        assert uow.materials.source(requests[0], lines[0]).available == 2
        assert uow.materials.source(requests[1], lines[1]).available == 5


def test_revalidation_refuses_changed_cut_length_without_partial_writes(production_api):
    client, _ = production_api
    cards = [issue("КБ1"), issue("КБ2")]
    batch(client, cards)
    group = groups(client)[0]
    with app.state.techcards_uow_factory() as uow:
        card = uow.techcards.get(cards[1].id)
        uow.techcards.save(card.rebuild(document(length=3000), NOW))
        uow.commit()
    response = client.post(f"/api/production/cutting-groups/{group['id']}/revalidate", json={"version": group["version"]})
    assert response.status_code == 422, response.text
    assert groups(client)[0]["version"] == group["version"]


def test_numeric_storage_scale_does_not_change_recipe_and_tool_numbers_are_preserved():
    original = document(length=1000)
    modified = document(length=2000)
    modified["operations"][0]["fields"]["coax"]["d1"] = "0.810000"
    assert recipe(original)[0].key == recipe(modified)[0].key
    original["operations"][0]["card"]["tooling"][0]["program"] = "1000"
    modified["operations"][0]["card"]["tooling"][0]["program"] = "2000"
    assert recipe(original)[0].key != recipe(modified)[0].key


def test_preview_supports_join_and_separate_in_the_same_batch(production_api):
    client, _ = production_api
    batch(client, [issue("КБ1")])
    second, third = issue("КБ2"), issue("КБ3")
    payload = {"items": [item(second), item(third, issue_separately=True)]}
    preview = client.post("/api/production/launch-ready/preview", json=payload).json()
    response = client.post("/api/production/launch-ready/launch", json={**payload, "fingerprint": preview["fingerprint"]})
    assert response.status_code == 200, response.text
    assert len(response.json()["created"]) == 2 and response.json()["refused"] == []
    assert sorted(len(g["rows"]) for g in groups(client)) == [1, 2]


def test_unknown_label_layout_refuses_grouping_but_keeps_standalone_launch(production_api):
    client, _ = production_api
    doc = document()
    doc["build_config"]["ops"][1]["marks"] = []
    card = issue("КБ1", doc)
    result = batch(client, [card])
    assert len(result["created"]) == 1
    assert result["groups"] == []
    assert result["grouping_refused"] and "раскладка" in result["grouping_refused"][0]["reason"]


def test_partial_legacy_assignment_returns_only_authorized_group_rows(production_api):
    client, operator = production_api
    batch(client, [issue("КБ1"), issue("КБ2")])
    row = groups(client)[0]["rows"][0]
    assigned = client.post(f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/assign",
        json={"version": row["task_version"], "operator_ids": [str(operator)]})
    assert assigned.status_code == 200, assigned.text
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator, name="Оператор", department="Производство"), email="operator@example.test",
        roles=frozenset({"operator"}), permissions={"myops": "edit"})
    response = client.get("/api/production/my")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tasks"] == []
    assert len(body["cutting_groups"]) == 1
    group = body["cutting_groups"][0]
    assert group["partial_view"]
    assert [member["item"] for member in group["rows"]] == [row["item"]]
    assert group["norm_seconds"] == group["rows"][0]["norm_seconds"]


def test_retry_of_legacy_launch_keeps_identity_without_grouping_history(production_api):
    client, _ = production_api
    from app.modules.production.application import LaunchProductionCommand, launch_production
    card = issue("КБ1")
    task = launch_production(LaunchProductionCommand(idempotency_key="legacy-cut", project=card.project,
        item=card.item_index, name=card.product_name, quantity=10, fulfillment_request_id=None, deadline=None,
        operations=launch_operations(launch_route(card.document).steps, 10),
        actor=Actor(id=MASTER_ID, name="Мастер", department="Производство"), now=NOW,
        techcard_id=card.id, techcard_revision=card.revision), app.state.production_uow_factory())
    response = client.post("/api/production/tasks", headers={"Idempotency-Key": "legacy-cut"},
                           json={"techcard_id": str(card.id), "quantity": 10})
    assert response.status_code == 200, response.text
    assert response.json()["task"]["id"] == str(task.id)
    assert groups(client) == []
