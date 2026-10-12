"""«Маршрут v3» of a position: the validator (parseRouteV3 + ERP rules), generation from the drawing and the stored
route — save with the route revision (stale → 409), «Построить по чертежу», marking a route of a changed drawing."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.harness.route_v3 import (
    FINAL_ID, RouteError, generate_route, initial_route, is_complete, missing_refs, parse_route_v3, source_refs,
    starter_fragment,
)
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

from . import grapher_fixture as fx

PROJECT = "708К.4"
BASE = f"/api/techcards/kitsets/{PROJECT}/harness"
NOW = datetime(2026, 10, 12, 10, 0)
WIRES = [f"w{n}" for n in range(1, 11)]


def _refs(kind, *ids):
    return [{"kind": kind, "id": item} for item in ids]


def _fragment(fid, refs, **extra):
    return {"id": fid, "title": fid.upper(), "mode": "source", "refs": refs, "objectIds": [ref["id"] for ref in refs],
            "backgroundOpacity": 0.2, "drawingCopy": {"hiddenObjectIds": []}, "createdAt": "2026-10-12", **extra}


def _generated(design=None):
    design = design or fx.design()
    route = initial_route(design)
    route["fragments"] = [starter_fragment(design, "2026-10-12")]
    return generate_route(route, design)


# ── Generation ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_generate_from_the_fixture_drawing_gives_cut_cards_and_a_final_holding_every_part():
    design = fx.design()
    route = _generated(design)
    kinds = {node["id"]: node["kind"] for node in route["nodes"]}
    assert kinds == {
        "prepared:wire": "semiFinished", "prepared:cable": "semiFinished", "prepared:covering": "semiFinished",
        "fragment:whole-harness": "assembly", FINAL_ID: "final",
    }
    by_id = {node["id"]: node for node in route["nodes"]}
    # Cable members are cut with their cable, not as loose wires (createInitialRouteV3).
    assert by_id["prepared:wire"]["refs"] == _refs("wire", *WIRES[:8])
    assert by_id["prepared:cable"]["refs"] == _refs("cable", "c1")
    assert by_id["prepared:covering"]["refs"] == _refs("covering", "cv1", "cv2")
    final = by_id[FINAL_ID]
    assert final["title"] == "Готовый жгут" and final["refs"] == source_refs(design)
    assert {ref["kind"] for ref in final["refs"]} == {"wire", "cable", "covering", "connector"}
    # Layout of the author: x = 32 + column·304, y = 32 + depth·328.
    assert [(node["x"], node["y"]) for node in route["nodes"]] == [(32, 32), (336, 32), (640, 32), (32, 360), (32, 688)]
    assert sorted((edge["from"], edge["to"]) for edge in route["edges"]) == [
        ("fragment:whole-harness", FINAL_ID), ("prepared:cable", "fragment:whole-harness"),
        ("prepared:covering", "fragment:whole-harness"), ("prepared:wire", "fragment:whole-harness"),
    ]
    assert route["generated"] is True
    assert parse_route_v3(route, design) == []
    assert is_complete(route, design) and missing_refs(route, design) == []


def test_generate_without_full_coverage_makes_no_final_and_refuses_partial_reuse():
    design = fx.design()
    route = initial_route(design)
    route["fragments"] = [_fragment("pair", _refs("wire", "w1", "w2"))]
    generated = generate_route(route, design)
    assert all(node["kind"] != "final" for node in generated["nodes"])
    assert not is_complete(generated, design)
    assert parse_route_v3(generated, design) == []
    # Later snapshots depend on the earlier card holding their parts; a card is indivisible.
    route["fragments"].append(_fragment("one", _refs("wire", "w1")))
    with pytest.raises(RouteError, match="часть полуфабриката"):
        generate_route(route, design)


def test_generate_resets_graph_edits_like_the_author():
    design = fx.design()
    route = {**_generated(design), "graphEdited": True}
    route["nodes"] = [{**node, "width": 500} for node in route["nodes"]]
    again = generate_route(route, design)
    assert again["graphEdited"] is False and all("width" not in node for node in again["nodes"])


# ── Validator ───────────────────────────────────────────────────────────────────────────────────────────────────


def _with(route, **changes):
    return {**deepcopy(route), **changes}


def test_validator_refuses_duplicate_ids():
    route = _generated()
    node = route["nodes"][0]
    assert "повторяет id" in parse_route_v3(_with(route, nodes=[*route["nodes"], dict(node)]))[0]
    edge = route["edges"][0]
    assert "повторяет id" in parse_route_v3(_with(route, edges=[*route["edges"], dict(edge)]))[0]
    fragment = route["fragments"][0]
    assert "повторяет id" in parse_route_v3(_with(route, fragments=[fragment, dict(fragment)]))[0]
    assert "дважды" in parse_route_v3(_with(route, preparedRefs=[*route["preparedRefs"], route["preparedRefs"][0]]))[0]


def test_validator_refuses_cycles_self_links_and_dangling_links():
    route = _generated()
    cycle = {"id": "back", "from": "fragment:whole-harness", "to": "prepared:wire"}
    assert parse_route_v3(_with(route, edges=[*route["edges"], cycle])) == ["связи карточек образуют цикл"]
    self_link = {"id": "self", "from": "prepared:wire", "to": "prepared:wire"}
    assert parse_route_v3(_with(route, edges=[*route["edges"], self_link]))
    dangling = {"id": "x", "from": "prepared:wire", "to": "nowhere"}
    assert parse_route_v3(_with(route, edges=[*route["edges"], dangling]))
    unknown_fragment = [{**route["nodes"][3], "fragmentIds": ["nope"]}]
    assert "несуществующий слепок" in parse_route_v3(_with(route, nodes=route["nodes"][:3] + unknown_fragment))[0]


def test_validator_requires_parts_to_exist_in_the_drawing():
    design = fx.design()
    route = _generated(design)
    assert parse_route_v3(route, design) == []
    smaller = deepcopy(design)
    smaller["wires"] = [wire for wire in smaller["wires"] if wire["id"] != "w1"]
    errors = parse_route_v3(route, smaller)
    assert errors == ["в чертеже нет частей, на которые ссылается маршрут: провод w1"]
    assert missing_refs(route, smaller) == [{"kind": "wire", "id": "w1"}]
    assert not is_complete(route, smaller)
    bad_kind = _with(route, preparedRefs=[{"kind": "screw", "id": "w1"}])
    assert "части заданы неверно" in parse_route_v3(bad_kind, design)[0]


def test_validator_allows_at_most_one_final_and_nothing_after_it():
    design = fx.design()
    route = _generated(design)
    final = next(node for node in route["nodes"] if node["kind"] == "final")
    second = {**final, "id": "final-2"}
    assert parse_route_v3(_with(route, nodes=[*route["nodes"], second]), design) == [
        "у маршрута больше одной карточки «Готовый жгут»"]
    after = {"id": "after", "from": FINAL_ID, "to": "prepared:wire"}
    assert parse_route_v3(_with(route, edges=[*route["edges"], after]), design)
    # A draft without the final card yet (snapshots don't cover everything) may be stored.
    draft = initial_route(design)
    assert parse_route_v3(draft, design) == [] and not is_complete(draft, design)


def test_snapshots_hold_hidden_ids_and_isolated_geometry_never_a_drawing_copy():
    design = fx.design()
    route = _generated(design)
    fragment = route["fragments"][0]
    full_copy = {**fragment, "drawingCopy": {"hiddenObjectIds": [], "document": design}}
    assert "копию всего чертежа" in parse_route_v3(_with(route, fragments=[full_copy]), design)[0]
    isolated_geometry = {"connectors": [], "wires": [deepcopy(design["wires"][0])], "cables": []}
    isolated = {**fragment, "mode": "isolated",
                "isolatedDrawingCopy": {"hiddenObjectIds": ["w2"], "document": isolated_geometry}}
    assert parse_route_v3(_with(route, fragments=[isolated]), design) == []
    no_geometry = {**fragment, "mode": "isolated", "isolatedDrawingCopy": {"hiddenObjectIds": []}}
    assert "своего рисунка" in parse_route_v3(_with(route, fragments=[no_geometry]), design)[0]
    nested = {**isolated, "isolatedDrawingCopy": {"hiddenObjectIds": [], "document": {
        **isolated_geometry, "manufacturingRoute": {}}}}
    assert "вложен маршрут" in parse_route_v3(_with(route, fragments=[nested]), design)[0]
    duplicate_hidden = {**fragment, "drawingCopy": {"hiddenObjectIds": ["w1", "w1"]}}
    assert parse_route_v3(_with(route, fragments=[duplicate_hidden]), design)


def test_validator_checks_shape_numbers_and_operations():
    design = fx.design()
    route = _generated(design)
    assert parse_route_v3(None) == ["это не маршрут v3"]
    assert parse_route_v3(_with(route, version=2)) == ["это не маршрут v3"]
    assert parse_route_v3(_with(route, backgroundOpacity=2))
    assert parse_route_v3(_with(route, graphEdited="true"))
    node = route["nodes"][0]
    for bad in ({"width": -2}, {"quantity": 0}, {"x": True}, {"operatorConfirmed": "yes"}, {"kind": "other"}):
        assert parse_route_v3(_with(route, nodes=[{**node, **bad}, *route["nodes"][1:]]), design), bad
    good_op = {"id": "op", "title": "Резка", "kind": "static", "lengthMm": 0, "speedMmPerMinute": 1, "employees": 1,
               "quantity": 3, "minutesEach": 2}
    with_ops = [{**node, "quantity": 2, "operations": [good_op]}, *route["nodes"][1:]]
    assert parse_route_v3(_with(route, nodes=with_ops), design) == []
    bad_op = [{**node, "operations": [{**good_op, "minutesEach": -1}]}, *route["nodes"][1:]]
    assert "операции" in parse_route_v3(_with(route, nodes=bad_op), design)[0]
    art = [{"wireId": "w1", "color": "#ff0000", "start": "tin"}]
    assert parse_route_v3(_with(route, wireArtwork=art), design) == []
    assert parse_route_v3(_with(route, wireArtwork=[{"wireId": "w1", "color": "red"}]), design)
    assert parse_route_v3(_with(route, wireArtwork=[{"wireId": "zz"}]), design)


# ── Stored route over the API ──────────────────────────────────────────────────────────────────────────────────


def _principal(permissions, name="Технолог"):
    return lambda: Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name=name, department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}), permissions=permissions,
    )


@pytest.fixture()
def route_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)  # noqa: E731
    app.state.techcards_uow_factory = factory
    clock = {"now": NOW}
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    app.dependency_overrides[moscow_now] = lambda: clock["now"]
    with factory() as uow:
        uow.kitsets.save(Kitset(
            project=PROJECT, customer="ООО Заказчик", calc_source="", updated_at=NOW, updated_by="Технолог",
            created_at=NOW, assemblies=(KitAssembly(index="КБ1", name="Жгут питания", run=Decimal("10")),
                                        KitAssembly(index="КБ2", name="Жгут сигнальный", run=Decimal("5"))),
        ))
        uow.commit()
    fx.library(factory)
    yield TestClient(app), sessions, clock
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def _drawing(client, design=None, revision=None):
    saved = client.put(f"{BASE}/КБ1", json={"design": design or fx.design(), "revision": revision})
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_route_needs_a_drawing_and_reads_empty_before_the_first_save(route_api):
    client, _sessions, _clock = route_api
    assert client.get(f"{BASE}/КБ1/route").json() == {
        "route": None, "drawing": None, "stale": False, "missing_refs": [], "complete": False}
    assert client.post(f"{BASE}/КБ1/route/generate").status_code == 404
    assert client.put(f"{BASE}/КБ1/route", json={"document": initial_route(fx.design()), "drawing_revision": 0}
                      ).status_code == 404
    assert client.get(f"{BASE}/КБ9/route").status_code == 404


def test_generate_stores_a_complete_route_on_the_current_drawing_revision(route_api):
    client, sessions, _clock = route_api
    drawing = _drawing(client)
    built = client.post(f"{BASE}/кб1/route/generate", json={"revision": None})
    assert built.status_code == 200, built.text
    body = built.json()
    assert body["route"]["revision"] == 1 and body["route"]["drawing_revision"] == drawing["revision"]
    assert body["route"]["drawing_document_id"] == drawing["document"]["id"]
    assert body["route"]["updated_by"] == "Технолог"
    assert body["complete"] is True and body["stale"] is False and body["missing_refs"] == []
    assert [node["kind"] for node in body["route"]["document"]["nodes"]] == [
        "semiFinished", "semiFinished", "semiFinished", "assembly", "final"]
    assert client.get(f"{BASE}/КБ1/route").json() == body
    with Session(sessions.kw["bind"]) as session:
        events = [row.event_type for row in session.scalars(select(AuditEventRow))]
    assert "techcards.harness_route_generated" in events


def test_save_with_a_stale_route_revision_is_409_and_keeps_the_newer_route(route_api):
    client, _sessions, clock = route_api
    drawing = _drawing(client)
    first = client.post(f"{BASE}/КБ1/route/generate").json()["route"]
    edited = deepcopy(first["document"])
    edited["nodes"][0]["quantity"] = 2
    clock["now"] = NOW + timedelta(minutes=1)
    saved = client.put(f"{BASE}/КБ1/route", json={
        "document": edited, "revision": first["revision"], "drawing_revision": drawing["revision"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["route"]["revision"] == 2
    # A second tab still holds revision 1.
    late = deepcopy(first["document"])
    late["nodes"][0]["quantity"] = 7
    stale = client.put(f"{BASE}/КБ1/route", json={
        "document": late, "revision": first["revision"], "drawing_revision": drawing["revision"]})
    assert stale.status_code == 409 and "обновите страницу" in stale.json()["detail"]
    assert client.get(f"{BASE}/КБ1/route").json()["route"]["document"]["nodes"][0]["quantity"] == 2
    # A first save over an existing route, a generate on an old revision — the same conflict.
    assert client.put(f"{BASE}/КБ1/route", json={
        "document": late, "drawing_revision": drawing["revision"]}).status_code == 409
    assert client.post(f"{BASE}/КБ1/route/generate", json={"revision": 1}).status_code == 409
    # A drawing revision the server has not reached.
    assert client.put(f"{BASE}/КБ1/route", json={
        "document": late, "revision": 2, "drawing_revision": drawing["revision"] + 5}).status_code == 409


def test_save_refuses_an_invalid_route_with_every_reason(route_api):
    client, _sessions, _clock = route_api
    drawing = _drawing(client)
    route = _generated()
    route["edges"].append({"id": "back", "from": "fragment:whole-harness", "to": "prepared:wire"})
    refused = client.put(f"{BASE}/КБ1/route", json={"document": route, "drawing_revision": drawing["revision"]})
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"] == ["связи карточек образуют цикл"]
    assert refused.json()["detail"]["message"].startswith("Маршрут не сохранён")
    foreign = initial_route(fx.design())
    foreign["preparedRefs"].append({"kind": "wire", "id": "чужой"})
    assert client.put(f"{BASE}/КБ1/route", json={"document": foreign, "drawing_revision": 1}).status_code == 422
    assert client.get(f"{BASE}/КБ1/route").json()["route"] is None


def test_a_changed_drawing_marks_the_route_stale_and_lists_removed_parts(route_api):
    client, _sessions, clock = route_api
    drawing = _drawing(client)
    built = client.post(f"{BASE}/КБ1/route/generate").json()
    clock["now"] = NOW + timedelta(hours=1)
    smaller = fx.design()
    smaller["wires"] = [wire for wire in smaller["wires"] if wire["id"] != "w8"]
    newer = _drawing(client, smaller, drawing["revision"])
    read = client.get(f"{BASE}/КБ1/route").json()
    assert read["stale"] is True and read["complete"] is False
    assert read["route"]["drawing_revision"] == drawing["revision"]
    assert read["drawing"] == {"document_id": drawing["document"]["id"], "revision": newer["revision"]}
    assert read["missing_refs"] == [{"kind": "wire", "id": "w8"}]
    # The route naming a removed part is not saved; «Построить по чертежу» rebuilds it on the new revision.
    kept = client.put(f"{BASE}/КБ1/route", json={
        "document": built["route"]["document"], "revision": 1, "drawing_revision": drawing["revision"]})
    assert kept.status_code == 422 and "провод w8" in kept.json()["detail"]["errors"][0]
    route = built["route"]["document"]
    route["fragments"] = [_fragment("whole", [ref for ref in source_refs(smaller)])]
    rebuilt_fragments = client.put(f"{BASE}/КБ1/route", json={
        "document": {**route, "nodes": [], "edges": [], "preparedRefs": initial_route(smaller)["preparedRefs"]},
        "revision": 1, "drawing_revision": drawing["revision"]})
    assert rebuilt_fragments.status_code == 200, rebuilt_fragments.text
    assert rebuilt_fragments.json()["stale"] is True  # still built on the older revision
    rebuilt = client.post(f"{BASE}/КБ1/route/generate", json={"revision": 2}).json()
    assert rebuilt["stale"] is False and rebuilt["complete"] is True and rebuilt["missing_refs"] == []
    assert rebuilt["route"]["drawing_revision"] == newer["revision"]
    assert [fragment["id"] for fragment in rebuilt["route"]["document"]["fragments"]] == ["whole"]


def test_generate_drops_wire_looks_of_wires_gone_from_the_drawing(route_api):
    client, _sessions, clock = route_api
    drawing = _drawing(client)
    draft = {**initial_route(fx.design()), "wireArtwork": [{"wireId": "w8", "color": "#ff0000"},
                                                           {"wireId": "w1", "start": "tin"}]}
    saved = client.put(f"{BASE}/КБ1/route", json={"document": draft, "drawing_revision": drawing["revision"]})
    assert saved.status_code == 200, saved.text
    clock["now"] = NOW + timedelta(hours=1)
    smaller = fx.design()
    smaller["wires"] = [wire for wire in smaller["wires"] if wire["id"] != "w8"]
    _drawing(client, smaller, drawing["revision"])
    rebuilt = client.post(f"{BASE}/КБ1/route/generate", json={"revision": 1})
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["route"]["document"]["wireArtwork"] == [{"wireId": "w1", "start": "tin"}]
    assert rebuilt.json()["complete"] is True


def test_route_follows_the_section_rights_of_the_drawing(route_api):
    client, _sessions, _clock = route_api
    drawing = _drawing(client)
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.get(f"{BASE}/КБ1/route").status_code == 200
    assert client.post(f"{BASE}/КБ1/route/generate").status_code == 403
    assert client.put(f"{BASE}/КБ1/route", json={
        "document": initial_route(fx.design()), "drawing_revision": drawing["revision"]}).status_code == 403
    app.dependency_overrides[authenticated_principal] = _principal({})
    assert client.get(f"{BASE}/КБ1/route").status_code == 403
