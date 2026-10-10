"""Журнал правок «Схемы полуфабрикатов» (owner, 10.10.2026): a build whose scheme differs from the generator's automatic
one writes `techcards.scheme_corrected` in the same transaction — from «Типовая» (its hand-edited `config.route`
against `derive_route`) and from «Ручная сборка» opened by «Поправить вручную» (against the scheme rebuilt from
`generator_origin`). The journal is read back by GET /api/techcards/scheme-corrections and the CLI."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.modules.techcards.application import (
    AutoBuildProjectTechcardCommand, CreateProjectTechcardCommand, auto_build_project_techcard,
    create_project_techcard,
)
from app.modules.techcards.assembly_schema import generator_to_assembly
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards import scheme_corrections
from app.modules.techcards.scheme_corrections import EVENT, scheme_correction
from app.shared.clock import MOSCOW
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor
from tests.techcards.test_assembly_drafts_api import INDEX, PROJECT, _card, _csrf, _token, drafts_api  # noqa: F401


NOW = datetime(2026, 10, 10, 12, 0, tzinfo=MOSCOW)
WIRE = {"name": "ПВ3 черный", "art": "ПВ3", "qty": 2, "length": 100}
# Резка провода и ТУТ сходятся в надевании ТУТ, потом опрессовка — изделие: b1, b2 → b3 → b4.
GENERATOR = {
    "projectNo": PROJECT, "assemblyIndex": INDEX, "assemblyName": "Жгут", "partQty": 10, "wires": [WIRE],
    "ops": [
        {"type": "cutWire", "opNum": "CUT_WIRE", "label": "Резка провода", "wires": [WIRE]},
        {"type": "cutTut", "opNum": "CUT_TUT", "label": "Резка ТУТ", "tutCount": 2},
        {"type": "insTut", "opNum": "INS_TUT", "label": "Надеть ТУТ"},
        {"type": "prsTerm", "opNum": "PRS_TERM", "label": "Опрессовка", "side": "A", "ep": {"termArt": "T1"},
         "wires": [WIRE]},
    ],
}
DERIVED = {
    "blocks": [
        {"key": "b1", "name": "", "ops": [1], "final": False}, {"key": "b2", "name": "", "ops": [2], "final": False},
        {"key": "b3", "name": "", "ops": [3], "final": False}, {"key": "b4", "name": "", "ops": [4], "final": True},
    ],
    "edges": [{"from": "b1", "to": "b3", "per_unit": 1}, {"from": "b2", "to": "b3", "per_unit": 1},
              {"from": "b3", "to": "b4", "per_unit": 1}],
}
# Мастер слил опрессовку в блок надевания ТУТ и взял две заготовки провода на штуку.
CORRECTED = {
    "blocks": [
        {"key": "b1", "name": "", "ops": [1], "final": False}, {"key": "b2", "name": "", "ops": [2], "final": False},
        {"key": "b3", "name": "", "ops": [3, 4], "final": True},
    ],
    "edges": [{"from": "b1", "to": "b3", "per_unit": 2}, {"from": "b2", "to": "b3", "per_unit": 1}],
}


def _typical(route: dict | None = None) -> dict:
    config = deepcopy(GENERATOR)
    if route is not None:
        config["route"] = deepcopy(route)
    return config


def _build(config: dict) -> dict:
    return build_etk(config, [], operation_library_commit="lib-1")


def _manual() -> dict:
    """«Поправить вручную»: the generator config as «Ручная сборка», the way /assembly/from-generator gives it."""
    return generator_to_assembly(deepcopy(GENERATOR))


# ── what counts as a correction ──

def test_typical_without_a_hand_edited_scheme_or_with_the_automatic_one_logs_nothing():
    assert scheme_correction(_build(_typical())) is None
    assert scheme_correction(_build(_typical(DERIVED))) is None


def test_typical_scheme_changes_are_listed_against_the_derived_scheme():
    details = scheme_correction(_build(_typical(CORRECTED)))

    assert details is not None and details["mode"] == "типовая"
    assert details["changes"] == [
        "блок убран: «Жгут» (операции: «Опрессовка»)",
        "итоговый блок изменён: было «Жгут» → стало «Жгут»",
        "операция перенесена: «Опрессовка» — из «Жгут» в «Жгут»",
        "шт на 1: «ПВ3 100мм» → «Жгут» было 1 → стало 2",
        "вход убран: «ПВ3 100мм (ТУТ надет)» → «Жгут»",
    ]
    assert details["ops"] == [
        {"position": 1, "code": "CUT_WIRE", "type": "cutWire", "label": "Резка провода"},
        {"position": 2, "code": "CUT_TUT", "type": "cutTut", "label": "Резка ТУТ"},
        {"position": 3, "code": "INS_TUT", "type": "insTut", "label": "Надеть ТУТ"},
        {"position": 4, "code": "PRS_TERM", "type": "prsTerm", "label": "Опрессовка"},
    ]
    assert [block["ops"] for block in details["auto_route"]["blocks"]] == [[1], [2], [3], [4]]
    assert details["auto_route"]["edges"][0] == {"from": "b1", "to": "b3", "per_unit": 1}
    assert [block["ops"] for block in details["final_route"]["blocks"]] == [[1], [2], [3, 4]]
    assert details["final_route"]["edges"][0] == {"from": "b1", "to": "b3", "per_unit": 2}


def test_typical_with_its_own_operation_order_derives_the_automatic_scheme_in_that_order():
    # Another order of the generator operations (the op-order override) is taken as given: no correction by itself.
    config = _typical()
    config["ops"] = [config["ops"][1], config["ops"][0], *config["ops"][2:]]
    assert scheme_correction(_build(config)) is None
    config["route"] = {
        "blocks": [
            {"key": "b1", "name": "", "ops": [1], "final": False}, {"key": "b2", "name": "", "ops": [2], "final": False},
            {"key": "b3", "name": "", "ops": [3], "final": False}, {"key": "b4", "name": "", "ops": [4], "final": True},
        ],
        "edges": [{"from": "b2", "to": "b3", "per_unit": 1}, {"from": "b3", "to": "b4", "per_unit": 1},
                  {"from": "b1", "to": "b4", "per_unit": 1}],
    }
    changes = scheme_correction(_build(config))["changes"]
    assert changes == ["вход добавлен: «ТУТ, 2 отрезков» → «Жгут»", "вход убран: «ТУТ, 2 отрезков» → «ПВ3 100мм (ТУТ надет)»"]


def test_manual_from_generator_unchanged_logs_nothing():
    manual = _manual()
    assert scheme_correction(_build(manual), GENERATOR) is None
    # Without the origin the manual build has nothing to be compared with; a manual origin is not a generator one.
    assert scheme_correction(_build(manual)) is None
    assert scheme_correction(_build(manual), manual) is None


def test_manual_from_generator_lists_added_moved_reordered_operations_and_inputs():
    manual = _manual()
    # The master adds his own check into the product block (seq 5), takes two wire pieces per piece and does the TUT
    # cut before the wire cut (operations come in the scheme's order: the TUT block first).
    manual["ops"].append({"type": "customOp", "seq": 5, "label": "Проверка", "tPrep": "0", "tOp": "10",
                          "steps": [{"kind": "Контроль", "text": "Осмотр"}]})
    manual["ops"] = [manual["ops"][1], manual["ops"][0], *manual["ops"][2:]]
    manual["route"] = {
        "blocks": [
            {"key": "b2", "name": "", "ops": [1], "final": False}, {"key": "b1", "name": "", "ops": [2], "final": False},
            {"key": "b3", "name": "", "ops": [3], "final": False}, {"key": "b4", "name": "", "ops": [4, 5], "final": True},
        ],
        "edges": [{"from": "b1", "to": "b3", "per_unit": 2}, {"from": "b2", "to": "b3", "per_unit": 1},
                  {"from": "b3", "to": "b4", "per_unit": 1}],
    }
    details = scheme_correction(_build(manual), GENERATOR)

    assert details is not None and details["mode"] == "ручная из генератора"
    assert details["changes"] == [
        "операция добавлена: «Проверка» — в блок «Жгут»",
        "порядок операций изменён: было 1, 2, 3, 4 → стало 2, 1, 3, 4",
        "шт на 1: «ПВ3 100мм» → «ПВ3 100мм (ТУТ надет)» было 1 → стало 2",
    ]
    assert [(op["position"], op["seq"], op["code"]) for op in details["ops"]] == [
        (1, 2, "CUT_TUT"), (2, 1, "CUT_WIRE"), (3, 3, "INS_TUT"), (4, 4, "PRS_TERM"), (5, 5, ""),
    ]
    assert [op["code"] for op in details["auto_ops"]] == ["CUT_WIRE", "CUT_TUT", "INS_TUT", "PRS_TERM"]
    assert details["auto_route"]["blocks"][0]["ops"] == [1]


def test_manual_removed_operation_and_split_block():
    manual = _manual()
    manual["ops"] = manual["ops"][:3]  # опрессовку убрали — изделием стал блок надевания ТУТ
    manual["route"] = {
        "blocks": [
            {"key": "b1", "name": "", "ops": [1], "final": False}, {"key": "b2", "name": "", "ops": [2], "final": False},
            {"key": "b3", "name": "", "ops": [3], "final": True},
        ],
        "edges": [{"from": "b1", "to": "b3", "per_unit": 1}, {"from": "b2", "to": "b3", "per_unit": 1}],
    }
    changes = scheme_correction(_build(manual), GENERATOR)["changes"]
    assert "операция убрана: «Опрессовка» — была в блоке «Жгут»" in changes
    assert changes[0].startswith("блок убран: «Жгут»")
    assert any(line.startswith("итоговый блок изменён") for line in changes)


def test_a_malformed_origin_never_stops_the_build_and_logs_nothing():
    manual = _manual()
    manual["route"]["edges"][0]["per_unit"] = 3
    assert scheme_correction(_build(manual), {"ops": "не список"}) is None
    assert scheme_correction(_build(manual), {"ops": ["не операция"]}) is None
    assert scheme_correction(_build(manual), "не объект") is None  # type: ignore[arg-type]


def test_the_generators_own_hand_route_in_the_origin_is_not_the_automatic_scheme():
    origin = _typical(CORRECTED)
    manual = generator_to_assembly(deepcopy(origin))  # the manual tab opened over the corrected typical scheme
    details = scheme_correction(_build(manual), origin)
    assert details is not None and "вход убран: «ПВ3 100мм (ТУТ надет)» → «Жгут»" in details["changes"]


def test_rebuilding_with_the_same_scheme_as_the_last_build_is_not_a_new_correction():
    document = _build(_typical(CORRECTED))
    assert scheme_correction(document, previous=_build(_typical(CORRECTED))) is None
    assert scheme_correction(document, previous=_build(_typical())) is not None


# ── the build writes the event in its own transaction ──

def _application(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'erp.db'}")
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)  # noqa: E731
    with factory() as uow:
        uow.operation_cards.replace((), source_commit="lib-1", imported_at=NOW)
        uow.commit()
    return engine, sessions, factory


def _events(sessions) -> list[AuditEventRow]:
    with sessions() as session:
        return list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == EVENT)
                                    .order_by(AuditEventRow.occurred_at)))


def test_application_build_writes_the_event_with_the_card_fields_once(tmp_path):
    engine, sessions, factory = _application(tmp_path)
    actor = Actor(name="Мастер", department="Цех")
    card = create_project_techcard(CreateProjectTechcardCommand(
        project=PROJECT, item_index=INDEX, product_name="Жгут", source_calculation="", deadline=None,
        actor=actor, now=NOW,
    ), factory())

    def build(config, now, origin=None):
        nonlocal card
        card = auto_build_project_techcard(AutoBuildProjectTechcardCommand(
            card_id=card.id, expected_version=card.version, actor=actor, now=now, config=config,
            generator_origin=origin,
        ), factory())

    build(_typical(), NOW)
    assert _events(sessions) == []
    build(_typical(CORRECTED), NOW + timedelta(minutes=1))
    build(_typical(CORRECTED), NOW + timedelta(minutes=2))  # re-saved as is — not a new correction
    [event] = _events(sessions)
    assert event.aggregate_id == card.id and event.actor_name == "Мастер"
    assert {key: event.details[key] for key in ("project", "item_index", "card_id", "revision",
                                                "operation_library_commit", "mode")} == {
        "project": PROJECT, "item_index": INDEX, "card_id": str(card.id), "revision": card.revision,
        "operation_library_commit": "lib-1", "mode": "типовая",
    }
    engine.dispose()


# ── reading the journal ──

def test_endpoint_reads_newest_first_with_since_and_project_and_a_tcgen_token(drafts_api):  # noqa: F811
    sessions, clock, admin = drafts_api
    card = _card(admin)
    built = admin.post(f"/api/techcards/projects/{card['id']}/build", headers=_csrf(admin), json={
        "version": card["version"], "config": _typical(CORRECTED),
    })
    assert built.status_code == 200, built.text
    manual = _manual()
    manual["route"]["edges"][0]["per_unit"] = 3
    clock.now = clock.now + timedelta(days=2)
    rebuilt = admin.post(f"/api/techcards/projects/{card['id']}/build", headers=_csrf(admin), json={
        "version": built.json()["version"], "config": manual, "generator_origin": GENERATOR,
    })
    assert rebuilt.status_code == 200, rebuilt.text
    # Without the origin (an old client) the manual build still works and logs nothing.
    plain = admin.post(f"/api/techcards/projects/{card['id']}/build", headers=_csrf(admin), json={
        "version": rebuilt.json()["version"], "config": _manual(),
    })
    assert plain.status_code == 200, plain.text

    items = admin.get("/api/techcards/scheme-corrections").json()["items"]
    assert [item["mode"] for item in items] == ["ручная из генератора", "типовая"]
    assert items[0]["changes"] == ["шт на 1: «ПВ3 100мм» → «ПВ3 100мм (ТУТ надет)» было 1 → стало 3"]
    assert items[0]["card_id"] == card["id"] and items[0]["actor"] == "Admin"

    token = _token(admin, "tcgen")
    agent = TestClient(app)
    since = (clock.now.date() - timedelta(days=1)).isoformat()
    recent = agent.get("/api/techcards/scheme-corrections", headers=token, params={"since": since})
    assert recent.status_code == 200, recent.text
    assert [item["mode"] for item in recent.json()["items"]] == ["ручная из генератора"]
    by_project = agent.get("/api/techcards/scheme-corrections", headers=token, params={"project": "708к.4"})
    assert len(by_project.json()["items"]) == 2
    assert agent.get("/api/techcards/scheme-corrections", headers=token,
                     params={"project": "999К.1"}).json()["items"] == []
    assert agent.get("/api/techcards/scheme-corrections", headers=_token(admin, "assembly")).status_code == 403


def test_cli_prints_a_russian_summary_and_json(tmp_path, monkeypatch, capsys):
    engine, sessions, factory = _application(tmp_path)
    actor = Actor(name="Мастер", department="Цех")
    card = create_project_techcard(CreateProjectTechcardCommand(
        project=PROJECT, item_index=INDEX, product_name="Жгут", source_calculation="", deadline=None,
        actor=actor, now=NOW,
    ), factory())
    auto_build_project_techcard(AutoBuildProjectTechcardCommand(
        card_id=card.id, expected_version=card.version, actor=actor, now=NOW, config=_typical(CORRECTED),
    ), factory())
    engine.dispose()
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'erp.db'}")

    assert scheme_corrections.main(["--since", "2026-10-01"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("Правки схемы полуфабрикатов с 2026-10-01: 1")
    assert f"2026-10-10 12:00 · {PROJECT} / {INDEX} · ред. 0 · типовая · Мастер" in text
    assert "  - шт на 1: «ПВ3 100мм» → «Жгут» было 1 → стало 2" in text
    assert "Чаще всего правят:" in text and "  шт на 1 — 1" in text

    assert scheme_corrections.main(["--since", "2026-10-01", "--project", PROJECT, "--json"]) == 0
    [event] = json.loads(capsys.readouterr().out)
    assert event["project"] == PROJECT and event["mode"] == "типовая" and len(event["changes"]) == 5

    assert scheme_corrections.main(["--since", "2026-10-11"]) == 0
    assert capsys.readouterr().out.strip() == "Правок схемы полуфабрикатов с 2026-10-11 нет."
    monkeypatch.delenv("ERP_DATABASE_URL")
    assert scheme_corrections.main(["--since", "2026-10-01"]) == 2

