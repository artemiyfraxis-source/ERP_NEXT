"""ПФ split by the generator's rule (owner 13.10: «автоматику надо оставлять»): stages of the generator's «Схема
полуфабрикатов» — cut wires, cut tubes, crimped wires, side A, side B, the product — become the ETK's ПФ. A typical
generator tab applies it when it builds the ETK; «Сложная сборка» does not (the owner groups it himself); the
«Предложить разбивку на ПФ» button shows it without saving."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.production.launchable import launch_route, semifinished_steps
from app.modules.techcards.application import _keep_hand_route
from app.modules.techcards.domain.pf_split import generator_split, op_stages, same_split
from app.modules.techcards.domain.route import RouteOp, derive_route, parse_route
from app.modules.techcards.etk_builder import attach_route, build_etk, generator_tab, propose_route
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import parse_operation_library
from app.shared.clock import moscow_now
from app.shared.persistence import create_schema


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
NOW = datetime(2026, 10, 13, 10, 0)


def config(name: str = "kit-720К.-КБ1.json", **changes) -> dict:
    return {**deepcopy(FIXTURES["files"][name]["payload"]["config"]), **changes}


def build(cfg: dict) -> dict:
    return build_etk(cfg, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


def shape(route: dict) -> list:
    return [(block["key"], block["ops"], block["final"]) for block in route["blocks"]]


def links(route: dict) -> list:
    return sorted((edge["from"], edge["to"], edge["per_unit"]) for edge in route["edges"])


# ── the rule ─────────────────────────────────────────────────────────────────────────────────────────────────────

HARNESS = [  # cut → crimp → side A → crimp B → side B → final: TUT on, shrink, label
    RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провод отрезанный"),
    RouteOp(2, "cutTut", "CUT_TUT_short", "ТУТ"),
    RouteOp(3, "prsTerm", "PRS_TERMINAL_auto", "Провод с терминалом", side="A", key="prsA1"),
    RouteOp(4, "insTerm", "INS_terminal_1.00", "Сторона А", side="A", key="insA1"),
    RouteOp(5, "prsTerm", "PRS_TERMINAL_auto", "Провод с терминалом В", side="B", key="prsB1"),
    RouteOp(6, "stripCable", "STRIP_WIRE", "Экран", side="B", key="shieldB1"),
    RouteOp(7, "insTerm", "INS_terminal_1.00", "Сторона В", side="B", key="insB1"),
    RouteOp(8, "insTut", "INS_TUT", "ТУТ надета", key="insHs"),
    RouteOp(9, "heatTut", "HEAT_TYT_short", "Жгут", key="heatHs"),
]


def test_a_typical_harness_is_cut_into_the_generator_stages_in_etk_order():
    assert op_stages(HARNESS) == {1: "wires", 2: "tubes", 3: "crimped", 4: "A", 5: "crimped", 6: "B", 7: "B",
                                  8: "final", 9: "final"}
    route = generator_split(HARNESS, "Жгут питания")

    assert [(block.key, block.ops, block.final) for block in route.blocks] == [
        ("wires", (1,), False), ("tubes", (2,), False), ("crimped", (3, 5), False), ("A", (4,), False),
        ("B", (6, 7), False), ("final", (8, 9), True),
    ]
    assert [block.name for block in route.blocks] == [
        "Нарезанные провода", "Нарезанные трубки", "Обжатые провода", "Сторона А собрана", "Сторона В собрана",
        "Жгут питания",
    ]
    assert route.generated and route.manual
    # Crimped wires are made of cut wires; side A joins side B (not the crimp of B that follows it in the ETK);
    # the TUT pieces wait for the product's «insTut».
    assert sorted((edge.source, edge.target) for edge in route.edges) == [
        ("A", "B"), ("B", "final"), ("crimped", "A"), ("crimped", "B"), ("tubes", "final"), ("wires", "crimped"),
    ]


def test_the_split_follows_per_unit_of_the_current_scheme_and_is_stable():
    current = parse_route({
        "blocks": [{"key": f"b{op.position}", "ops": [op.position], "final": op.position == 9} for op in HARNESS],
        "edges": [{"from": "b1", "to": "b3", "per_unit": 2}, {"from": "b3", "to": "b4"}, {"from": "b4", "to": "b5"},
                  {"from": "b5", "to": "b6"}, {"from": "b6", "to": "b7"}, {"from": "b7", "to": "b8"},
                  {"from": "b2", "to": "b8", "per_unit": 3}, {"from": "b8", "to": "b9"}],
    }, HARNESS)
    route = generator_split(HARNESS, "", current)
    per_unit = {(edge.source, edge.target): edge.per_unit for edge in route.edges}

    assert per_unit[("wires", "crimped")] == Decimal(2) and per_unit[("tubes", "final")] == Decimal(3)
    assert same_split(generator_split(HARNESS, "", route), route)
    assert not same_split(route, derive_route(HARNESS))


def test_fixture_harness_cut_crimp_a_b_gives_four_pf():
    document = build(config())
    proposal = propose_route(document)

    assert proposal["rule"] == "generator" and not proposal["same"]
    assert shape(proposal["route"]) == [("wires", [1], False), ("crimped", [2, 4], False), ("A", [3], False), ("B", [5], True)]
    assert links(proposal["route"]) == [("A", "B", 1), ("crimped", "A", 1), ("crimped", "B", 1), ("wires", "crimped", 1)]
    assert proposal["route"]["source"] == "generated"


# ── generation ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_typical_tab_builds_its_etk_cut_into_pf_and_launches_by_pf():
    document = build(config(tab="inter"))

    assert document["route"]["source"] == "generated"
    assert shape(document["route"]) == [("wires", [1], False), ("crimped", [2, 4], False), ("A", [3], False), ("B", [5], True)]
    steps = semifinished_steps(launch_route(document).steps)
    assert [(step.position, [item.position for item in step.etk_ops]) for step in steps] == [
        (1, [1]), (2, [2, 4]), (3, [3]), (4, [5]),
    ]
    # The read path keeps it «generated» (not a hand edit).
    assert attach_route(document)["route"]["source"] == "generated"


def test_complex_assembly_and_old_configs_are_not_split_and_launch_per_operation():
    complex_document = build(config(tab="complex"))
    old_document = build(config())  # a config from before 13.10 does not say its tab

    for document in (complex_document, old_document):
        assert document["route"]["source"] == "derived"
        assert len(document["route"]["blocks"]) == len(document["operations"])
        assert semifinished_steps(launch_route(document).steps) == ()
        assert len(launch_route(document).steps) == len(document["operations"])


def test_generator_tab_reads_the_marker_and_tells_old_complex_configs_by_their_features():
    assert generator_tab(config(tab="inter")) == "inter"
    assert generator_tab(config(tab="complex")) == "complex"
    assert generator_tab(config()) == "inter"
    # Pinout connections are in almost every generated harness: they do not make a simple cable complex.
    pinout = [{"from": "X1:1", "to": "X2:1"}]
    assert generator_tab(config(connections=pinout)) == "inter"
    assert generator_tab(config(bigAssembly=True)) == "complex"
    assert generator_tab(config("kit-708К.2-КБ1.json")) == "complex"
    assert generator_tab({"mode": "coax", "ops": []}) == "coax"
    assert generator_tab({"mode": "manual", "ops": []}) == "manual"
    assert generator_tab(None) == ""


def test_a_hand_edit_survives_regeneration_and_a_generated_split_survives_an_old_rebuild():
    previous = build(config(tab="inter"))
    hand = {**previous, "route": {**previous["route"], "source": "manual", "blocks": [
        {"key": "cut", "ops": [1, 2, 3]}, {"key": "rest", "ops": [4, 5], "final": True}],
        "edges": [{"from": "cut", "to": "rest", "per_unit": 1}]}}

    kept = _keep_hand_route(hand, build(config(tab="inter")))
    assert kept["route"]["source"] == "manual"
    assert [block["ops"] for block in kept["route"]["blocks"]] == [[1, 2, 3], [4, 5]]

    # A generated split (an old ETK moved onto ПФ) outlives a rebuild that has none of its own …
    assert _keep_hand_route(previous, build(config()))["route"]["source"] == "generated"
    # … and a fresh generated split replaces it.
    fresh = build(config(tab="inter"))
    assert _keep_hand_route(previous, fresh) is fresh


# ── manual assembly ──────────────────────────────────────────────────────────────────────────────────────────────

MANUAL = {
    "mode": "manual", "assemblyName": "Жгут питания", "partQty": 10,
    "ops": [{"type": "customOp", "label": label, "tPrep": "60", "tOp": "10"}
            for label in ("Резка", "Зачистка", "Пайка", "Усадка")],
    "route": {"blocks": [{"key": "b1", "name": "Заготовка", "ops": [1, 2]}, {"key": "b2", "ops": [3, 4], "final": True}],
              "edges": [{"from": "b1", "to": "b2", "per_unit": 2}]},
}


def test_a_manual_assembly_is_proposed_by_its_own_blocks():
    document = build(deepcopy(MANUAL))
    assert propose_route(document)["same"] is True

    # An older stored scheme (one ПФ per operation) — the proposal brings the blocks back.
    old = {**document, "route": {
        "source": "derived", "blocks": [{"key": f"b{n}", "ops": [n], "final": n == 4} for n in range(1, 5)],
        "edges": [{"from": f"b{n}", "to": f"b{n + 1}"} for n in range(1, 4)]}}
    proposal = propose_route(old)
    assert proposal["rule"] == "blocks" and not proposal["same"]
    assert [(block["key"], block["ops"]) for block in proposal["route"]["blocks"]] == [("b1", [1, 2]), ("b2", [3, 4])]
    assert links(proposal["route"]) == [("b1", "b2", 2)]
    assert proposal["route"]["source"] == "manual"


# ── «Предложить разбивку на ПФ» ──────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def issued(api: TestClient, document: dict) -> dict:
    card = api.post("/api/techcards/projects", json={
        "project": "720К.", "item_index": "КБ1", "product_name": "Жгут", "source_calculation": "",
    }).json()
    built = api.put(f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": document})
    assert built.status_code == 200, built.text
    result = api.post(f"/api/techcards/projects/{card['id']}/issue", json={"version": built.json()["version"]})
    assert result.status_code == 200, result.text
    return result.json()


def test_propose_returns_the_split_without_saving_and_says_when_it_is_already_so(api):
    card = issued(api, build(config()))
    assert card["document"]["route"]["source"] == "derived"

    proposed = api.post(f"/api/techcards/projects/{card['id']}/route/propose")
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["same"] is False and body["rule"] == "generator"
    assert shape(body["route"]) == [("wires", [1], False), ("crimped", [2, 4], False), ("A", [3], False), ("B", [5], True)]

    unchanged = api.get(f"/api/techcards/projects/{card['id']}").json()
    assert (unchanged["version"], unchanged["revision"]) == (card["version"], card["revision"])
    assert unchanged["document"]["route"]["source"] == "derived"
    assert len(api.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"]) == 1

    saved = api.put(f"/api/techcards/projects/{card['id']}/route", json={"version": card["version"], "route": body["route"]})
    assert saved.status_code == 200, saved.text
    assert api.post(f"/api/techcards/projects/{card['id']}/route/propose").json()["same"] is True


def test_propose_answers_404_and_422(api):
    assert api.post("/api/techcards/projects/00000000-0000-0000-0000-000000000001/route/propose").status_code == 404
    card = api.post("/api/techcards/projects", json={
        "project": "720К.", "item_index": "КБ2", "product_name": "Жгут", "source_calculation": "",
    }).json()
    missing = api.post(f"/api/techcards/projects/{card['id']}/route/propose")
    assert missing.status_code == 422 and missing.json()["detail"] == "Электронная техкарта ещё не собрана"
