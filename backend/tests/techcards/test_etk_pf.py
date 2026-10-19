"""ПФ of the ETK (owner + Vasily 13.10): the ETK is cut into semi-finished products — a ПФ is a block of the scheme
(`document.route`). The technologist saves the grouping by hand (PUT …/route, a new revision of an issued ETK); the
order of `block.ops` is the order of work inside the ПФ; `pf_documents` computes what each ПФ takes and hands on."""

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
from app.modules.techcards.application import _keep_hand_route
from app.modules.techcards.domain.route import RouteOp, parse_route, pf_documents, route_json, route_totals
from app.modules.techcards.etk_builder import attach_route, build_etk, document_route
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.operation_cards import parse_operation_library
from app.shared.clock import moscow_now
from app.shared.persistence import create_schema


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
NOW = datetime(2026, 10, 13, 10, 0)

# 720К.-КБ1: 1 cutWire · 2 prsTerm · 3 insTerm · 4 prsTerm · 5 insTerm, batch 100.
GROUPS = {
    "blocks": [
        {"key": "cut", "ops": [1]},
        {"key": "A", "ops": [3, 2]},
        {"key": "B", "ops": [5, 4], "final": True},
    ],
    "edges": [{"from": "cut", "to": "A", "per_unit": 2}, {"from": "A", "to": "B"}],
}


def document() -> dict:
    config = deepcopy(FIXTURES["files"]["kit-720К.-КБ1.json"]["payload"]["config"])
    return build_etk(config, CARDS)


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


def issued_card(api: TestClient) -> dict:
    card = api.post("/api/techcards/projects", json={
        "project": "720К.", "item_index": "КБ1", "product_name": "Жгут", "source_calculation": "",
    }).json()
    built = api.put(f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": document()})
    assert built.status_code == 200, built.text
    issued = api.post(f"/api/techcards/projects/{card['id']}/issue", json={"version": built.json()["version"]})
    assert issued.status_code == 200, issued.text
    return issued.json()


def test_hand_order_inside_a_block_survives_route_json_and_the_read_path():
    ops = [RouteOp(position, name=f"Оп {position}", minutes=Decimal(position)) for position in range(1, 6)]
    route = parse_route(GROUPS, ops)
    shown = route_json(route, route_totals(route, {op.position: op.minutes for op in ops}, Decimal(1)))

    assert [(block["key"], block["ops"]) for block in shown["blocks"]] == [("cut", [1]), ("A", [3, 2]), ("B", [5, 4])]
    # Unnamed hand-made block — the product of its last operation by the order of work, not by the largest number.
    assert shown["blocks"][1]["name"] == "Оп 2" and shown["source"] == "manual"
    assert shown["blocks"][1]["minutes"] == 5

    stored = {**document(), "route": shown}
    assert [block["ops"] for block in document_route(stored)["blocks"]] == [[1], [3, 2], [5, 4]]
    # A derived scheme still goes ascending.
    derived = parse_route(GROUPS, ops, manual=False)
    assert [block.ops for block in derived.blocks] == [(1,), (2, 3), (4, 5)]


def test_save_route_writes_a_new_revision_of_the_issued_etk(api):
    card = issued_card(api)
    saved = api.put(f"/api/techcards/projects/{card['id']}/route", json={"version": card["version"], "route": GROUPS})

    assert saved.status_code == 200, saved.text
    body = saved.json()
    route = body["document"]["route"]
    assert body["revision"] == card["revision"] + 1 and body["version"] == card["version"] + 1
    assert route["source"] == "manual" and route["final"] == "B"
    assert [(block["key"], block["ops"]) for block in route["blocks"]] == [("cut", [1]), ("A", [3, 2]), ("B", [5, 4])]
    assert body["document"]["operations"] == card["document"]["operations"]
    assert body["document"]["build_config"] == card["document"]["build_config"]
    revisions = api.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"]
    assert revisions[-1]["reason"] == "Правка ПФ" and revisions[-1]["revision"] == body["revision"]
    assert revisions[-1]["document"]["route"]["blocks"][1]["ops"] == [3, 2]


@pytest.mark.parametrize(("route", "message"), [
    ({"blocks": [{"key": "A", "ops": [1, 2, 3]}, {"key": "B", "ops": [4], "final": True}], "edges": [{"from": "A", "to": "B"}]},
     "операция 5 не входит ни в один блок"),
    ({"blocks": [{"key": "A", "ops": [1, 2, 3, 4, 5]}], "edges": []}, "не отмечен итоговый блок"),
    ({"blocks": [{"key": "A", "ops": [1, 2]}, {"key": "B", "ops": [3, 4, 5], "final": True}], "edges": []},
     "не доходит до итогового блока"),
    ({"blocks": []}, "нет ни одного блока"),
])
def test_save_route_refuses_a_bad_scheme_with_422_and_keeps_the_card(api, route, message):
    card = issued_card(api)
    refused = api.put(f"/api/techcards/projects/{card['id']}/route", json={"version": card["version"], "route": route})

    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("Схема полуфабрикатов: ") and message in refused.json()["detail"]
    revisions = api.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"]
    assert [item["revision"] for item in revisions] == [1]


def test_save_route_checks_the_version_and_the_built_document(api):
    card = issued_card(api)
    stale = api.put(f"/api/techcards/projects/{card['id']}/route", json={"version": card["version"] - 1, "route": GROUPS})
    assert stale.status_code == 422 and "изменилась" in stale.json()["detail"]

    empty = api.post("/api/techcards/projects", json={
        "project": "720К.", "item_index": "КБ2", "product_name": "Жгут", "source_calculation": "",
    }).json()
    refused = api.put(f"/api/techcards/projects/{empty['id']}/route", json={"version": empty["version"], "route": GROUPS})
    assert refused.status_code == 422 and "не собрана" in refused.json()["detail"]


def test_a_draft_keeps_the_scheme_without_a_revision(api):
    card = api.post("/api/techcards/projects", json={
        "project": "720К.", "item_index": "КБ1", "product_name": "Жгут", "source_calculation": "",
    }).json()
    built = api.put(f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": document()}).json()
    saved = api.put(f"/api/techcards/projects/{card['id']}/route", json={"version": built["version"], "route": GROUPS})

    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 0 and saved.json()["status"] == "Черновик"
    assert saved.json()["document"]["route"]["blocks"][2]["ops"] == [5, 4]


def test_rebuild_keeps_the_hand_cut_pf_while_the_operations_are_the_same():
    previous = document()
    ops = [RouteOp(position, name=str(position)) for position in range(1, 6)]
    route = parse_route(GROUPS, ops)
    previous["route"] = route_json(route, route_totals(route, {}, Decimal(1)))

    kept = _keep_hand_route(previous, document())
    assert kept["route"]["source"] == "manual" and [block["ops"] for block in kept["route"]["blocks"]] == [[1], [3, 2], [5, 4]]

    fewer = document()
    fewer["operations"] = fewer["operations"][:4]
    assert _keep_hand_route(previous, fewer)["route"]["source"] == "derived"


def test_pf_documents_take_components_and_outside_pieces_and_hand_on_the_last_result():
    plain = document()
    ops = [RouteOp(position, name=str(position)) for position in range(1, 6)]
    route = parse_route(GROUPS, ops)
    shown = attach_route({**plain, "route": route_json(route, route_totals(route, {}, Decimal(1)))})
    pf = pf_documents(shown)

    assert [(item["number"], item["key"], item["ops"], item["final"]) for item in pf] == [
        (1, "cut", [1], False), (2, "A", [3, 2], False), (3, "B", [5, 4], True),
    ]
    operations = plain["operations"]
    # What the ПФ hands on is the result of its last operation by the order of work — op 2 for «A», op 4 for «B».
    assert pf[1]["output"] == operations[1]["result"] and pf[2]["output"] == operations[3]["result"]
    # Pieces from outside the ПФ — its incoming edges, per one piece and per batch of 100.
    assert pf[0]["inputs"] == []
    assert pf[1]["inputs"] == [{"block": "cut", "number": 1, "name": shown["route"]["blocks"][0]["name"],
                                "per_unit": 2, "norm": "200", "unit": "шт"}]
    assert [item["block"] for item in pf[2]["inputs"]] == ["A"]
    # Components of every operation of the ПФ, one article added up.
    articles = [row.get("art") or row.get("name") for operation in operations[1:3] for row in operation["components"]]
    assert sorted({row.get("art") or row.get("name") for row in pf[1]["components"]}) == sorted(set(articles))
    assert len(pf[1]["components"]) == len(set(articles))
    assert pf[1]["minutes"] == shown["route"]["blocks"][1]["minutes"]


def test_pf_documents_add_up_one_article_and_survive_empty_documents():
    shown = {
        "kit": {"part_quantity": "10"},
        "operations": [
            {"components": [{"art": "T-1", "name": "Терминал", "norm": "20", "unit": "шт"}], "result": [{"name": "ПФ1"}]},
            {"components": [{"art": "T-1", "name": "Терминал", "norm": "2,5", "unit": "шт"},
                            {"name": "Провод", "norm": "по месту", "unit": "м"}], "result": [{"name": "Жгут"}]},
        ],
        "route": {"blocks": [{"key": "b1", "name": "Жгут", "ops": [2, 1], "final": True, "minutes": 3}], "edges": []},
    }
    [pf] = pf_documents(shown)
    assert pf["components"] == [{"art": "T-1", "name": "Терминал", "norm": "22,5", "unit": "шт"},
                                {"name": "Провод", "norm": "по месту", "unit": "м"}]
    assert pf["output"] == [{"name": "ПФ1"}] and pf["inputs"] == []
    assert pf_documents(None) == [] and pf_documents({"operations": []}) == []
    assert pf_documents({"operations": [{}]}) == []
