from dataclasses import replace
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.launchable import route_from_document
from app.modules.production.plan_application import AddPlanEventCommand, add_plan_event
from app.modules.techcards.application import (
    CreateProjectTechcardCommand,
    ReplaceOperationLibraryCommand,
    create_project_techcard,
    replace_operation_library,
)
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from app.modules.techcards.etk_builder import ETK_VERSION
from app.workflows.techcards_all import (
    refresh,
    generate,
    import_supplymap,
    split_item,
    supplymap_routes,
)
from tests.factories import Actor
from tests.techcards.calc_sheets import FakeSheets, calc_sheets, grid


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((ROOT / "tests" / "techcards" / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CONFIG = FIXTURES["files"]["kit-708К.2-КБ1.json"]["payload"]["config"]
NOW = datetime(2026, 9, 29, 22, 0)
ACTOR = Actor(id=UUID("50000000-0000-0000-0000-000000000001"), name="пакетная генерация ТК", department="")
STATE = ("production_uow_factory", "techcards_uow_factory")


@pytest.fixture()
def state():
    """Проекты 708К.4 (КБ1, КБ2), 709К.1 и 710К.1 (по КБ1, КБ2), заведённые из расчёта, и библиотека операций."""
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="engineer@example.test", roles=frozenset({"engineer"}), permissions={"projects": "edit"},
    )
    client = TestClient(app)
    for number in ("708К.4", "709К.1", "710К.1"):
        app.state.kitset_sheets_reader = FakeSheets(calc_sheets(project_cell=number))
        loaded = client.post("/api/techcards/kitsets/load", json={
            "url": f"https://docs.google.com/spreadsheets/d/1calc-{number}/edit",
        })
        assert loaded.status_code == 200, loaded.text
    del app.state.kitset_sheets_reader
    replace_operation_library(ReplaceOperationLibraryCommand(
        source_commit=LIBRARY["sourceCommit"], templates=tuple(LIBRARY["templates"]), actor=ACTOR, now=NOW,
    ), app.state.techcards_uow_factory())
    yield SimpleNamespace(
        techcards_uow_factory=app.state.techcards_uow_factory,
        production_uow_factory=app.state.production_uow_factory,
    )
    app.dependency_overrides.pop(authenticated_principal, None)
    for name in STATE:
        delattr(app.state, name)
    engine.dispose()


def _cards(state) -> dict[tuple[str, str], object]:
    with state.techcards_uow_factory() as uow:
        return {(card.project, card.item_index): card for card in uow.techcards.list()}


def _plan(state, project: str, **fields) -> None:
    add_plan_event(AddPlanEventCommand(
        project=project, blocker=fields.pop("blocker", ""), text=fields.pop("text", ""), deadline=None,
        known_projects=(project,), actor=ACTOR, now=NOW, **fields,
    ), state.production_uow_factory())


def test_split_item_like_tcSplitItem():
    assert split_item("708К.4КБ1") == ("708К.4", "КБ1")
    assert split_item(" 35К.М.34кб12 ") == ("35К.М.34", "КБ12")
    assert split_item("708К.4") == ("708К.4", "")


SHEET = grid([
    ["ИЗДЕЛИЕ", "НАИМЕНОВАНИЕ", "№ ЭТАПА", "ЭТАП", "КОД ОПЕРАЦИИ", "ОПИСАНИЕ ШАГА", "ВРЕМЯ НА ЕД, СЕК",
     "ВРЕМЯ ПОДГОТОВКИ, СЕК", "КОММЕНТАРИЙ"],
    ["708К.4КБ1", "Жгут питания", 1, "Резка", "CUT_WIRE_auto", "Резка провода", "12,5", 60,
     "ТК: https://docs.google.com/spreadsheets/d/tk-file/edit"],
    ["708К.4КБ1", "Жгут питания", 2, "Опрессовка", "PRS_TERMINAL_auto", "Опрессовка ст. А", 8, 0, ""],
    ["708К.4КБ1", "Жгут питания", 3, "Опрессовка", "PRS_TERMINAL_auto", "Опрессовка ст. Б", 8, 0, ""],
    ["708К.4КБ9", "Нет такой КБ", 1, "Резка", "CUT", "", 1, 0, ""],
    ["999К.1КБ1", "Нет проекта", 1, "Резка", "CUT", "", 1, 0, ""],
])


def test_supplymap_sheet_rows_become_routes_with_the_tk_file_link():
    routes = supplymap_routes(SHEET)
    assert set(routes) == {"708К.4КБ1", "708К.4КБ9", "999К.1КБ1"}
    route = routes["708К.4КБ1"]
    assert route["file_url"] == "https://docs.google.com/spreadsheets/d/tk-file/edit"
    assert [stage["tOp"] for stage in route["stages"]] == [12.5, 8, 8]


def test_issued_supplymap_techcards_become_issued_etks_and_a_rerun_changes_nothing(state):
    report = import_supplymap(SHEET, state.techcards_uow_factory, actor=ACTOR, now=NOW, apply=True)
    assert report.done == ["708К.4 КБ1 — этапов 3"]
    assert report.skipped == {"проекта или КБ нет в ERP (отгружен или не заведён)": ["708К.4КБ9", "999К.1КБ1"]}
    card = _cards(state)[("708К.4", "КБ1")]
    assert (card.status, card.revision) == (TechcardStatus.ISSUED, 1)
    assert card.document["source"]["file_url"] == "https://docs.google.com/spreadsheets/d/tk-file/edit"
    # Повтор одного кода в маршруте — разные операции ЭТК; цех видит этапы, как по листу SUPPLYMAP.
    assert [(item["code"], item["connector"]) for item in card.document["operations"]] == [
        ("CUT_WIRE_auto", ""), ("PRS_TERMINAL_auto", ""), ("PRS_TERMINAL_auto", "этап 3"),
    ]
    route = route_from_document(card.document)
    assert [(step.code, step.description, float(step.seconds_per_unit)) for step in route] == [
        ("CUT_WIRE_auto", "Резка провода", 12.5), ("PRS_TERMINAL_auto", "Опрессовка ст. А", 8.0),
        ("PRS_TERMINAL_auto", "Опрессовка ст. Б", 8.0),
    ]
    with state.techcards_uow_factory() as uow:
        assert [item.reason for item in uow.techcards.revisions(card.id)] == ["Перенос выданной ТК из SUPPLYMAP"]

    again = import_supplymap(SHEET, state.techcards_uow_factory, actor=ACTOR, now=NOW, apply=True)
    assert again.done == []
    assert again.skipped["ЭТК уже выдана в ERP — не перезаписываю"] == ["708К.4 КБ1"]
    assert _cards(state)[("708К.4", "КБ1")].version == card.version


def test_a_draft_gives_way_to_the_supplymap_issue_and_dry_run_writes_nothing(state):
    create_project_techcard(CreateProjectTechcardCommand(
        project="708К.4", item_index="КБ1", product_name="Жгут питания", source_calculation="",
        deadline=None, actor=ACTOR, now=NOW,
    ), state.techcards_uow_factory())
    dry = import_supplymap(SHEET, state.techcards_uow_factory, actor=ACTOR, now=NOW, apply=False)
    assert dry.done == ["708К.4 КБ1 — этапов 3"]
    assert _cards(state)[("708К.4", "КБ1")].status is TechcardStatus.DRAFT

    import_supplymap(SHEET, state.techcards_uow_factory, actor=ACTOR, now=NOW, apply=True)
    assert _cards(state)[("708К.4", "КБ1")].status is TechcardStatus.ISSUED


def test_generate_builds_and_issues_only_where_a_techcard_is_missing_and_skips_shipped_and_hold(state):
    import_supplymap(SHEET, state.techcards_uow_factory, actor=ACTOR, now=NOW, apply=True)
    _plan(state, "709К.1", kind="block", blocker="ХОЛД", text="ждём заказчика")
    _plan(state, "710К.1", kind="stage", item_index="КБ2", value="Отгружено")
    asked = []

    def generator(payload):
        asked.extend(item["bom"]["index"] for item in payload["items"])
        assert {"templates", "op", "ter", "coax", "kab_awg", "kab_coax"} <= set(payload["refs"])
        return {"items": [{"key": item["key"], "config": CONFIG, "error": "", "warnings": []} for item in payload["items"]]}

    report = generate(state, generator, None, actor=ACTOR, now=NOW, apply=True)
    assert asked == ["КБ2", "КБ1"]  # 708К.4 КБ1 выдана из SUPPLYMAP, 709К.1 на ХОЛДе, 710К.1 КБ2 отгружена
    assert [line.split(" — ")[0] for line in report.done] == ["708К.4 КБ2", "710К.1 КБ1"]
    assert report.skipped == {
        "ЭТК уже выдана": ["708К.4 КБ1"], "проект на ХОЛДе": ["709К.1"], "КБ отгружена": ["710К.1 КБ2"],
    }
    # Как кнопка «Создать» (SUPPLYMAP «Создать техкарты»): собранная ЭТК сразу выдана — её правят по операциям.
    built = _cards(state)[("710К.1", "КБ1")]
    assert (built.status, built.revision) == (TechcardStatus.ISSUED, 1)
    assert built.document["operations"] and built.product_name == "Жгут питания"

    again = generate(state, generator, None, actor=ACTOR, now=NOW, apply=True)
    assert again.done == [] and asked == ["КБ2", "КБ1"]
    assert again.skipped["ЭТК уже выдана"] == ["708К.4 КБ1", "708К.4 КБ2", "710К.1 КБ1"]


def test_a_build_that_cannot_be_issued_stays_a_draft_and_is_rebuilt_next_time(state):
    no_length = {**CONFIG, "wires": [{**wire, "length": 0} for wire in CONFIG["wires"]]}

    def generator(payload):
        return {"items": [{"key": item["key"], "config": no_length, "error": "", "warnings": []} for item in payload["items"]]}

    report = generate(state, generator, None, actor=ACTOR, now=NOW, apply=True)
    assert report.done == []
    assert any(line.startswith("708К.4 КБ1: собран черновик (операций ") and "не выдан — Не указана длина резки" in line
               for line in report.failed)
    draft = _cards(state)[("708К.4", "КБ1")]
    assert draft.status is TechcardStatus.DRAFT and draft.document

    rerun = generate(state, lambda payload: {"items": [
        {"key": item["key"], "config": CONFIG, "error": "", "warnings": []} for item in payload["items"]
    ]}, None, actor=ACTOR, now=NOW, apply=True)
    assert "708К.4 КБ1" in [line.split(" — ")[0] for line in rerun.done]
    assert _cards(state)[("708К.4", "КБ1")].status is TechcardStatus.ISSUED


def test_generator_errors_are_reported_and_leave_no_card(state):
    def generator(payload):
        return {"items": [{"key": item["key"], "config": None, "error": error, "warnings": []}
                          for item, error in zip(payload["items"], ("Укажите длину кабелей.", "Выберите шаблон хотя бы для одной операции."))]
                + [{"key": item["key"], "config": None, "error": "", "warnings": []} for item in payload["items"][2:]]}

    report = generate(state, generator, None, actor=ACTOR, now=NOW, apply=True)
    assert report.failed[:2] == [
        "708К.4 КБ1: Укажите длину кабелей.",
        "708К.4 КБ2: генератору не из чего собрать операции — в составе нет проводов, разъёмов или контактов",
    ]
    assert report.failed[2] == "709К.1 КБ1: генератор не вернул конфигурацию"
    assert _cards(state) == {}


def test_refresh_rebuilds_issued_etks_of_an_older_builder_and_keeps_supplymap_ones(state):
    import_supplymap(SHEET, state.techcards_uow_factory, actor=ACTOR, now=NOW, apply=True)
    generate(state, lambda payload: {"items": [
        {"key": item["key"], "config": CONFIG, "error": "", "warnings": []} for item in payload["items"]
    ]}, None, actor=ACTOR, now=NOW, apply=True)
    stale = _cards(state)[("710К.1", "КБ1")]
    old_document = {**stale.document, "schema_version": 1}
    for operation in old_document["operations"]:
        operation["result"] = [{**row, "norm": ""} for row in operation["result"]]
    with state.techcards_uow_factory() as uow:
        uow.techcards.save(replace(stale, document=old_document))
        uow.commit()

    dry = refresh(state, actor=ACTOR, now=NOW, apply=False)
    assert dry.done == ["710К.1 КБ1"]
    report = refresh(state, actor=ACTOR, now=NOW, apply=True)
    assert report.done == ["710К.1 КБ1 — ревизия 2"] and report.failed == []
    rebuilt = _cards(state)[("710К.1", "КБ1")]
    assert rebuilt.document["schema_version"] == ETK_VERSION
    assert all(row["norm"] for operation in rebuilt.document["operations"] for row in operation["result"]
               if operation["type"] != "splice")
    assert _cards(state)[("708К.4", "КБ1")].revision == 1  # перенесённую из SUPPLYMAP не трогаем
    assert refresh(state, actor=ACTOR, now=NOW, apply=True).done == []
