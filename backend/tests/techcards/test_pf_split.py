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
    assert op_stages(HARNESS) == {1: "wires", 2: "tubes", 3: "crimpedA", 4: "A", 5: "crimpedB", 6: "B", 7: "B",
                                  8: "final", 9: "final"}
    route = generator_split(HARNESS, "Жгут питания")

    assert [(block.key, block.ops, block.final) for block in route.blocks] == [
        ("wires", (1,), False), ("tubes", (2,), False), ("crimpedA", (3,), False), ("A", (4,), False),
        ("crimpedB", (5,), False), ("B", (6, 7), False), ("final", (8, 9), True),
    ]
    assert [block.name for block in route.blocks] == [
        "Нарезанные провода", "Нарезанные трубки", "Обжатые провода, сторона А", "Сторона А собрана",
        "Обжатые провода, сторона В", "Сторона В собрана", "Жгут питания",
    ]
    assert route.generated and route.manual
    # Crimped wires are made of cut wires; side A goes on to the crimp of side B (the wires go through it);
    # the TUT pieces wait for the product's «insTut».
    assert sorted((edge.source, edge.target) for edge in route.edges) == [
        ("A", "crimpedB"), ("B", "final"), ("crimpedA", "A"), ("crimpedB", "B"), ("tubes", "final"),
        ("wires", "crimpedA"),
    ]


# The owner's cable 14.10: I-DS1071-SCB01X4 (side A) ↔ PHR-4 (side B), the ETK as the generator builds it.
OWNER_CABLE = [
    RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провод отрезанный", key="cutWire"),
    RouteOp(2, "prsTerm", "PRS_TERMINAL_auto", "Провод с терминалом", side="A", key="prsTermA1"),
    RouteOp(3, "insTerm", "INS_terminal_2,54-6,3", "Сторона А", side="A", key="insTermA1"),
    RouteOp(4, "prsTerm", "PRS_TERMINAL_auto", "Провод с терминалом", side="B", key="prsTermB1"),
    RouteOp(5, "insTerm", "INS_terminal_1.00", "Кабель", side="B", key="insTermB1"),
]


def test_the_owner_cable_is_cut_crimp_a_side_a_crimp_b_side_b_each_fed_by_the_previous():
    route = generator_split(OWNER_CABLE, "Кабель I-DS1071 — PHR-4")

    assert [(block.key, block.ops, block.final) for block in route.blocks] == [
        ("wires", (1,), False), ("crimpedA", (2,), False), ("A", (3,), False), ("crimpedB", (4,), False),
        ("B", (5,), True),
    ]
    assert [block.name for block in route.blocks] == [
        "Нарезанные провода", "Обжатые провода, сторона А", "Сторона А собрана", "Обжатые провода, сторона В",
        "Кабель I-DS1071 — PHR-4",
    ]
    assert [(edge.source, edge.target, edge.per_unit) for edge in route.edges] == [
        ("wires", "crimpedA", 1), ("crimpedA", "A", 1), ("A", "crimpedB", 1), ("crimpedB", "B", 1),
    ]
    # Per unit of the current scheme carries over.
    current = parse_route({
        "blocks": [{"key": f"b{op.position}", "ops": [op.position], "final": op.position == 5} for op in OWNER_CABLE],
        "edges": [{"from": "b1", "to": "b2", "per_unit": 4}, {"from": "b2", "to": "b3"}, {"from": "b3", "to": "b4"},
                  {"from": "b4", "to": "b5"}],
    }, OWNER_CABLE)
    assert generator_split(OWNER_CABLE, "", current).edges[0].per_unit == Decimal(4)


def test_crimps_of_both_sides_first_are_a_pf_per_side_in_etk_order():
    # Crimp A, crimp B, then the sides: a crimp ПФ per side, each fed by the previous one.
    ops = [OWNER_CABLE[0], OWNER_CABLE[1], OWNER_CABLE[3], OWNER_CABLE[2], OWNER_CABLE[4]]
    ops = [RouteOp(index, op.type, op.code, op.name, side=op.side, key=op.key) for index, op in enumerate(ops, start=1)]
    route = generator_split(ops)

    assert [(block.key, block.ops, block.name) for block in route.blocks] == [
        ("wires", (1,), "Нарезанные провода"), ("crimpedA", (2,), "Обжатые провода, сторона А"),
        ("crimpedB", (3,), "Обжатые провода, сторона В"), ("A", (4,), "Сторона А собрана"), ("B", (5,), "Кабель"),
    ]
    assert [(edge.source, edge.target) for edge in route.edges] == [
        ("wires", "crimpedA"), ("crimpedA", "crimpedB"), ("crimpedB", "A"), ("A", "B"),
    ]


def test_connectors_crimped_and_mounted_one_by_one_give_a_crimp_pf_and_an_assembly_pf_per_side():
    # The generator's НВ4 harness: splices, then per connector crimp + mount (А1…А4), marking, В1, В2.
    ops = [
        RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провод"),
        RouteOp(2, "stripCable", "STRIP_WIRE", "Концы зачищены", key="stripSpl"),
        RouteOp(3, "splice", "SPLICE", "Сростки", key="splice"),
        *[RouteOp(position, "prsTerm" if position % 2 == 0 else "insTerm", "", f"А{position // 2 - 1}", side="A",
                  key=("prsTermA" if position % 2 == 0 else "insTermA") + str(position // 2 - 1))
          for position in range(4, 12)],
        RouteOp(12, "insMark", "MARK", "Маркировка", key="insMark"),
        RouteOp(13, "prsTerm", "", "В1", side="B", key="prsTermB1"),
        RouteOp(14, "insTerm", "", "В1", side="B", key="insTermB1"),
        RouteOp(15, "prsTerm", "", "В2", side="B", key="prsTermB2"),
        RouteOp(16, "insTerm", "", "Жгут", side="B", key="insTermB2"),
    ]
    route = generator_split(ops)

    assert [(block.key, block.ops, block.name) for block in route.blocks] == [
        ("wires", (1,), "Нарезанные провода"), ("crimped", (2, 3), "Провода со сростками"),
        ("crimpedA", (4, 6, 8, 10), "Обжатые провода, сторона А"), ("A", (5, 7, 9, 11), "Сторона А собрана"),
        ("crimpedB", (12, 13, 15), "Обжатые провода, сторона В"), ("B", (14, 16), "Жгут"),
    ]
    assert [(edge.source, edge.target) for edge in route.edges] == [
        ("wires", "crimped"), ("crimped", "crimpedA"), ("crimpedA", "A"), ("A", "crimpedB"), ("crimpedB", "B"),
    ]


def test_a_side_without_a_hand_crimp_keeps_its_stripping_in_its_own_pf():
    # Side A crimped by hand; side B is stripped and soldered — the strip stays with side B, no crimp ПФ of B.
    ops = [
        RouteOp(1, "cutWireStrip", "CUT_WIRE+2strip", "Провод"),
        RouteOp(2, "prsTerm", "PRS_TERMINAL_auto", "Провод с терминалом", side="A", key="prsTermA1"),
        RouteOp(3, "insTerm", "INS_terminal_1.00", "Сторона А", side="A", key="insTermA1"),
        RouteOp(4, "stripCable", "STRIP_CABLE", "Кабель разделан", side="B", key="stripCableB1"),
        RouteOp(5, "tin", "TIN", "Концы лужёные", side="B", key="tinB1"),
        RouteOp(6, "solderConn", "SOLDER_CONN", "Кабель", side="B", key="solderB1"),
    ]
    assert op_stages(ops) == {1: "wires", 2: "crimpedA", 3: "A", 4: "B", 5: "B", 6: "B"}
    route = generator_split(ops)
    assert [(block.key, block.ops, block.name) for block in route.blocks] == [
        ("wires", (1,), "Нарезанные провода"), ("crimpedA", (2,), "Обжатые провода, сторона А"),
        ("A", (3,), "Сторона А собрана"), ("B", (4, 5, 6), "Кабель"),
    ]
    assert [(edge.source, edge.target) for edge in route.edges] == [("wires", "crimpedA"), ("crimpedA", "A"), ("A", "B")]


def test_unsided_splices_stay_in_the_shared_crimp_before_the_first_side():
    ops = [
        RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провод"),
        RouteOp(2, "stripCable", "STRIP_WIRE", "Концы зачищены", key="stripSpl"),
        RouteOp(3, "splice", "SPLICE", "Сростки", key="splice"),
        RouteOp(4, "insTerm", "INS_terminal_1.00", "Сторона А", side="A", key="insTermA1"),
        RouteOp(5, "insSpl", "INS_TUT", "Трубка на сросток", key="insSpl"),
        RouteOp(6, "prsTerm", "PRS_TERMINAL_auto", "Провод с терминалом", side="B", key="prsTermB1"),
        RouteOp(7, "insTerm", "INS_terminal_1.00", "Жгут", side="B", key="insTermB1"),
    ]
    # The splices before the first side are one shared ПФ; the TUT put on after side A goes with the crimp of B.
    assert op_stages(ops) == {1: "wires", 2: "crimped", 3: "crimped", 4: "A", 5: "crimpedB", 6: "crimpedB", 7: "B"}
    route = generator_split(ops)
    assert [(block.key, block.name) for block in route.blocks] == [
        ("wires", "Нарезанные провода"), ("crimped", "Провода со сростками"), ("A", "Сторона А собрана"),
        ("crimpedB", "Обжатые провода, сторона В"), ("B", "Жгут"),
    ]
    assert [(edge.source, edge.target) for edge in route.edges] == [
        ("wires", "crimped"), ("crimped", "A"), ("A", "crimpedB"), ("crimpedB", "B"),
    ]


def test_coax_has_no_crimp_pf_and_side_b_crimp_after_a_finished_lug_is_the_product():
    # Coax stripping goes with the cut cable, never into a side (owner 14.10: «зачистка идёт до всего остального»).
    coax = [
        RouteOp(1, "coaxCut", "CUT_COAX", "Кабель"),
        RouteOp(2, "coaxStrip", "STRIP_COAX", "Разделка А", side="A"),
        RouteOp(3, "coaxStrip", "STRIP_COAX", "Разделка В", side="B"),
        RouteOp(4, "coaxPin", "SOLDER_PIN", "Пайка А", side="A"),
        RouteOp(5, "coaxPin", "SOLDER_PIN", "Пайка В", side="B"),
    ]
    assert op_stages(coax) == {1: "wires", 2: "wires", 3: "wires", 4: "A", 5: "B"}
    route = generator_split(coax, "Кабель коаксиальный")
    assert [(block.key, block.ops, block.final) for block in route.blocks] == [
        ("wires", (1, 2, 3), False), ("A", (4,), False), ("B", (5,), True),
    ]
    assert [block.name for block in route.blocks][0] == "Нарезанный и разделанный кабель"
    cut_only = [RouteOp(1, "coaxCut", "CUT_WIRE_auto", "Кабель"), RouteOp(2, "coaxPin", "SOLDER_PIN", "Пайка А", side="A"),
                RouteOp(3, "coaxPin", "SOLDER_PIN", "Пайка В", side="B")]
    assert generator_split(cut_only, "Кабель коаксиальный").blocks[0].name == "Нарезанный кабель"
    machine = [RouteOp(1, "coaxCut", "CUT_COAX+2strip", "Резка с разделкой (А и В)"), *cut_only[1:]]
    assert op_stages(machine) == {1: "wires", 2: "A", 3: "B"}
    assert generator_split(machine, "Кабель коаксиальный").blocks[0].name == "Нарезанный и разделанный кабель"
    # A crimp after the last side op is the product's, not a ПФ of its own.
    lug = [OWNER_CABLE[0], OWNER_CABLE[1], OWNER_CABLE[2], OWNER_CABLE[3]]
    assert op_stages(lug) == {1: "wires", 2: "crimpedA", 3: "A", 4: "B"}


def test_the_split_follows_per_unit_of_the_current_scheme_and_is_stable():
    current = parse_route({
        "blocks": [{"key": f"b{op.position}", "ops": [op.position], "final": op.position == 9} for op in HARNESS],
        "edges": [{"from": "b1", "to": "b3", "per_unit": 2}, {"from": "b3", "to": "b4"}, {"from": "b4", "to": "b5"},
                  {"from": "b5", "to": "b6"}, {"from": "b6", "to": "b7"}, {"from": "b7", "to": "b8"},
                  {"from": "b2", "to": "b8", "per_unit": 3}, {"from": "b8", "to": "b9"}],
    }, HARNESS)
    route = generator_split(HARNESS, "", current)
    per_unit = {(edge.source, edge.target): edge.per_unit for edge in route.edges}

    assert per_unit[("wires", "crimpedA")] == Decimal(2) and per_unit[("tubes", "final")] == Decimal(3)
    assert same_split(generator_split(HARNESS, "", route), route)
    assert not same_split(route, derive_route(HARNESS))


def test_fixture_harness_cut_crimp_a_side_a_crimp_b_side_b_gives_five_pf():
    document = build(config())
    proposal = propose_route(document)

    assert proposal["rule"] == "generator" and not proposal["same"]
    assert shape(proposal["route"]) == [
        ("wires", [1], False), ("crimpedA", [2], False), ("A", [3], False), ("crimpedB", [4], False), ("B", [5], True)]
    assert links(proposal["route"]) == [("A", "crimpedB", 1), ("crimpedA", "A", 1), ("crimpedB", "B", 1), ("wires", "crimpedA", 1)]
    assert proposal["route"]["source"] == "generated"


# ── generation ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_typical_tab_builds_its_etk_cut_into_pf_and_launches_by_pf():
    document = build(config(tab="inter"))

    assert document["route"]["source"] == "generated"
    assert shape(document["route"]) == [
        ("wires", [1], False), ("crimpedA", [2], False), ("A", [3], False), ("crimpedB", [4], False), ("B", [5], True)]
    steps = semifinished_steps(launch_route(document).steps)
    assert [(step.position, [item.position for item in step.etk_ops]) for step in steps] == [
        (1, [1]), (2, [2]), (3, [3]), (4, [4]), (5, [5]),
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
    assert shape(body["route"]) == [
        ("wires", [1], False), ("crimpedA", [2], False), ("A", [3], False), ("crimpedB", [4], False), ("B", [5], True)]

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
