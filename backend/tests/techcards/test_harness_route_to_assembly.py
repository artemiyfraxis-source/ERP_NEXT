"""«Заполнить техкарту» (owner 13.10): the route v3 cards → a «Ручная сборка» draft — blocks and arrows of the
semi-finished scheme, operations in the cards' order, ETK positions 1..n, per_unit from the cards' quantities; a route
without «Готовый жгут» is refused; a dry run first, then the draft is stored through `assembly_drafts` and the project
techcard is not touched."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.main import app
from app.modules.techcards.assembly_drafts_infrastructure import AssemblyDraftRow
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.harness.route_to_assembly import RouteAssemblyError, assembly_from_route, card_order
from app.modules.techcards.harness.route_v3 import FINAL_ID
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import AuditEventRow

from . import grapher_fixture as fx
from .test_harness_route_v3 import BASE, _drawing, _generated, _principal, route_api  # noqa: F401 — the fixture

POSITION = {"project": "708К.4", "index": "КБ1", "name": "Жгут питания", "part_qty": 10}


def _op(title, **extra):
    return {"id": f"op-{title}", "title": title, "kind": "static", "lengthMm": 0, "speedMmPerMinute": 1000,
            "employees": 1, "quantity": 1, "minutesEach": 1, **extra}


def _route():
    """The fixture drawing's generated route with operations on the cards a master would put there."""
    route = _generated()
    ops = {
        "prepared:wire": [_op("Резка проводов", quantity=8, minutesEach=0.5, templateId="cut-wire", code="CUT_WIRE")],
        "prepared:cable": [_op("Разделка кабеля", kind="running", lengthMm=3000, speedMmPerMinute=1500, employees=2)],
        "fragment:whole-harness": [_op("Опрессовка", minutesEach=2), _op("Сборка в корпус", minutesEach=3)],
        FINAL_ID: [_op("Контроль", minutesEach=1.5)],
    }
    route["nodes"] = [{**node, "operations": ops.get(node["id"], [])} for node in route["nodes"]]
    return route


def test_cards_become_blocks_in_route_order_and_the_etk_numbers_operations_1_to_n():
    config, notes = assembly_from_route(_route(), **POSITION)
    assert config["mode"] == "manual" and config["assemblyIndex"] == "КБ1" and config["partQty"] == 10
    # Inputs first (top row left to right), then the assembly, the final card last; within a card — its own order.
    assert [op["label"] for op in config["ops"]] == [
        "Резка проводов", "Разделка кабеля", "Нарезанные оболочки", "Опрессовка", "Сборка в корпус", "Контроль"]
    assert [op["seq"] for op in config["ops"]] == [1, 2, 3, 4, 5, 6]
    assert config["ops"][0] == {"type": "libraryOp", "templateId": "cut-wire", "opNum": "CUT_WIRE",
                                "label": "Резка проводов", "qty": "8", "tPrep": "0", "tOp": "30", "seq": 1}
    # «Погонная»: 3000 мм / 1500 мм/мин / 2 чел. = 1 мин на жгут.
    assert config["ops"][1] == {"type": "customOp", "label": "Разделка кабеля", "qty": "1", "tPrep": "0", "tOp": "60",
                                "seq": 2}
    # A card without operations is still a step of the route: an own operation named after it, with a note.
    assert config["ops"][2]["type"] == "customOp"
    assert notes == ["Карточка «Нарезанные оболочки» без операций — в техкарте она операция «Нарезанные оболочки» "
                     "без нормы и хода; дополните её в «Ручной сборке»"]
    route = config["route"]
    assert route["source"] == "manual"
    assert [(block["key"], block["name"], block["ops"], block["final"]) for block in route["blocks"]] == [
        ("b1", "Нарезанные провода", [1], False), ("b2", "Нарезанные кабели", [2], False),
        ("b3", "Нарезанные оболочки", [3], False), ("b4", "Сборка жгута", [4, 5], False),
        ("b5", "Жгут питания", [6], True)]
    assert sorted((edge["from"], edge["to"], edge["per_unit"]) for edge in route["edges"]) == [
        ("b1", "b4", 1), ("b2", "b4", 1), ("b3", "b4", 1), ("b4", "b5", 1)]

    etk = build_etk(config, [], operation_library_commit="")
    assert [operation["position"] for operation in etk["operations"]] == [1, 2, 3, 4, 5, 6]
    assert [operation["connector"] for operation in etk["operations"]] == ["1", "2", "3", "4", "5", "6"]
    assert etk["kit"]["mode"] == "manual" and etk["route"]["source"] == "manual"
    assert etk["route"]["final"] == "b5"
    assert [block["ops"] for block in etk["route"]["blocks"]] == [[1], [2], [3], [4, 5], [6]]
    # The assembly's first operation takes the cut blanks; its second the product of the first; the last is the product.
    assert [row["name"] for row in etk["operations"][3]["semis"]] == [
        "Нарезанные провода", "Нарезанные кабели", "Нарезанные оболочки"]
    assert etk["operations"][4]["result"][0]["name"] == "Сборка жгута"
    assert etk["operations"][5]["result_label"] == "Изделие"
    assert etk["operations"][5]["result"][0]["name"] == "Жгут питания"
    # Time per batch of 10: 8 × 30 s × 10 = 40 min for the cut.
    assert etk["operations"][0]["time"][0]["norm"] == "40"


def test_per_unit_is_source_pieces_per_one_target_piece():
    route = _route()
    quantities = {"prepared:wire": 8, "prepared:cable": 1, "fragment:whole-harness": 2, FINAL_ID: 1}
    route["nodes"] = [{**node, **({"quantity": quantities[node["id"]]} if node["id"] in quantities else {})}
                      for node in route["nodes"]]
    config, _notes = assembly_from_route(route, **POSITION)
    edges = {(edge["from"], edge["to"]): edge["per_unit"] for edge in config["route"]["edges"]}
    # 8 cut wires per harness into 2 assemblies → 4 per assembly; 1 cable into 2 → 0.5; 2 assemblies into the harness.
    assert edges == {("b1", "b4"): 4, ("b2", "b4"): 0.5, ("b3", "b4"): 0.5, ("b4", "b5"): 2}
    etk = build_etk(config, [], operation_library_commit="")
    assert {(edge["from"], edge["to"]): edge["per_unit"] for edge in etk["route"]["edges"]} == edges
    # The assembly's first operation takes 4 cut wires per piece × batch 10.
    assert {row["name"]: row["norm"] for row in etk["operations"][3]["semis"]}["Нарезанные провода"] == "40"
    # Thirds round to 4 decimals and stay positive.
    route["nodes"] = [{**node, "quantity": 3} if node["id"] == "fragment:whole-harness" else node for node in route["nodes"]]
    edges = {(edge["from"], edge["to"]): edge["per_unit"] for edge in assembly_from_route(route, **POSITION)[0]["route"]["edges"]}
    assert edges[("b2", "b4")] == 0.3333 and edges[("b1", "b4")] == 2.6667


def test_a_route_without_a_final_card_is_refused_in_russian():
    route = _route()
    route["nodes"] = [node for node in route["nodes"] if node["kind"] != "final"]
    route["edges"] = [edge for edge in route["edges"] if edge["to"] != FINAL_ID]
    with pytest.raises(RouteAssemblyError, match="нет карточки «Готовый жгут»"):
        assembly_from_route(route, **POSITION)
    with pytest.raises(RouteAssemblyError, match="нет карточек"):
        assembly_from_route({**route, "nodes": [], "edges": []}, **POSITION)


def test_a_card_that_does_not_reach_the_final_card_is_refused_with_the_scheme_message():
    route = _route()
    route["edges"] = [edge for edge in route["edges"] if edge["from"] != "prepared:covering"]
    with pytest.raises(RouteAssemblyError, match="Схема полуфабрикатов: блок «Нарезанные оболочки» не доходит"):
        assembly_from_route(route, **POSITION)


def test_card_order_reads_like_the_canvas_among_ready_cards():
    nodes = [{"id": "final", "x": 0, "y": 900}, {"id": "right", "x": 600, "y": 0}, {"id": "left", "x": 0, "y": 0},
             {"id": "low", "x": 0, "y": 300}, {"id": "mid", "x": 300, "y": 600}]
    edges = [{"from": "right", "to": "mid"}, {"from": "left", "to": "mid"}, {"from": "mid", "to": "final"},
             {"from": "low", "to": "final"}]
    # Ready at first: left, right, low → left (top-left), right (top), low; mid once both inputs are done.
    assert [node["id"] for node in card_order(nodes, edges)] == ["left", "right", "low", "mid", "final"]


# ── Over the API: preview, then save as a draft ────────────────────────────────────────────────────────────────


def _with_route(client):
    drawing = _drawing(client)
    built = client.post(f"{BASE}/КБ1/route/generate", json={"revision": None}).json()
    document = deepcopy(built["route"]["document"])
    document["nodes"] = [{**node, "operations": [_op(f"Операция {node['title']}")]} for node in document["nodes"]]
    saved = client.put(f"{BASE}/КБ1/route", json={
        "document": document, "revision": built["route"]["revision"], "drawing_revision": drawing["revision"]})
    assert saved.status_code == 200, saved.text
    return drawing, saved.json()


def _drafts(sessions):
    with Session(sessions.kw["bind"]) as session:
        return list(session.scalars(select(AssemblyDraftRow)))


def test_preview_then_save_stores_a_manual_draft_and_leaves_the_techcard_alone(route_api):  # noqa: F811
    client, sessions, _clock = route_api
    drawing, saved = _with_route(client)
    revision = saved["route"]["revision"]
    preview = client.post(f"{BASE}/кб1/route/draft?dry_run=true", json={"revision": revision})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True and body["draft"] is None and body["revision"] == revision
    assert body["operations"] == 5 and body["route"]["source"] == "manual"
    assert [block["name"] for block in body["route"]["blocks"]][-1] == "Жгут питания"
    assert body["build_config"]["route"]["blocks"][-1]["final"] is True
    assert any("Библиотека операционных карт не загружена" in warning for warning in body["warnings"])
    assert _drafts(sessions) == []

    # A save needs the previewed revision; a route changed since the preview is refused.
    assert client.post(f"{BASE}/КБ1/route/draft", json={}).status_code == 409
    stale = client.post(f"{BASE}/КБ1/route/draft", json={"revision": revision - 1})
    assert stale.status_code == 409 and "посмотрите результат заново" in stale.json()["detail"]

    stored = client.post(f"{BASE}/КБ1/route/draft", json={"revision": revision})
    assert stored.status_code == 200, stored.text
    draft = stored.json()["draft"]
    assert draft["source"] == "manual" and draft["status"] == "draft" and draft["item_index"] == "КБ1"
    assert draft["note"] == f"Из маршрута КБ1, ред. {revision} (чертёж ред. {drawing['revision']})"
    rows = _drafts(sessions)
    assert len(rows) == 1 and rows[0].draft_config == body["build_config"]
    assert str(rows[0].drawing_document_id) == drawing["document"]["id"]
    assert str(rows[0].id) == draft["id"] and rows[0].source == "manual" and rows[0].final_config is None
    # The project techcard is not built: no card, nothing issued.
    with Session(sessions.kw["bind"]) as session:
        events = [row.event_type for row in session.scalars(select(AuditEventRow))]
        assert session.scalar(select(func.count()).select_from(AssemblyDraftRow)) == 1
        assert session.scalar(select(func.count()).select_from(ProjectTechcardRow)) == 0
    assert "techcards.assembly_draft_created" in events


def test_refusals_no_route_no_final_and_rights(route_api):  # noqa: F811
    client, _sessions, _clock = route_api
    drawing = _drawing(client)
    missing = client.post(f"{BASE}/КБ1/route/draft?dry_run=true")
    assert missing.status_code == 404 and "нет маршрута" in missing.json()["detail"]

    built = client.post(f"{BASE}/КБ1/route/generate", json={"revision": None}).json()
    document = deepcopy(built["route"]["document"])
    document["nodes"] = [node for node in document["nodes"] if node["kind"] != "final"]
    document["edges"] = [edge for edge in document["edges"] if edge["to"] != FINAL_ID]
    saved = client.put(f"{BASE}/КБ1/route", json={
        "document": document, "revision": built["route"]["revision"], "drawing_revision": drawing["revision"]}).json()
    refused = client.post(f"{BASE}/КБ1/route/draft?dry_run=true", json={"revision": saved["route"]["revision"]})
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"] == [
        "В маршруте нет карточки «Готовый жгут» — отметьте итоговую карточку (вид «Готовый жгут»): из неё техкарта "
        "берёт изделие"]
    assert refused.json()["detail"]["message"].startswith("Техкарта не заполнена:")

    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.post(f"{BASE}/КБ1/route/draft?dry_run=true").status_code == 403


def test_a_route_of_a_changed_drawing_is_previewed_with_a_warning(route_api):  # noqa: F811
    client, _sessions, _clock = route_api
    drawing, saved = _with_route(client)
    _drawing(client, fx.design(), revision=drawing["revision"])
    preview = client.post(f"{BASE}/КБ1/route/draft?dry_run=true", json={"revision": saved["route"]["revision"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["warnings"][0].startswith(f"Маршрут построен на чертеже ред. {drawing['revision']}, чертёж уже ред.")
