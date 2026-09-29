from datetime import date, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import (
    ProjectTechcard,
    TechcardStatus,
    TechcardValidationError,
    operation_key,
)
from app.shared.clock import moscow_now
from app.shared.persistence import create_schema


NOW = datetime(2026, 9, 23, 10, 0)


def _document(*, changed: bool = False) -> dict:
    return {
        "schema_version": 1,
        "operations": [
            {
                "code": "10",
                "connector": "A",
                "name": "Обработка A",
                "time": 12 if changed else 10,
            },
            {"code": "20", "connector": "B", "name": "Сборка B", "time": 20},
        ],
    }


def _card() -> ProjectTechcard:
    return ProjectTechcard.create(
        project="P-100",
        item_index="01",
        product_name="Жгут",
        source_calculation="Расчёт 7",
        deadline=date(2026, 10, 1),
        now=NOW,
    )


def test_project_techcard_revision_lifecycle_and_manual_carryover():
    draft = _card()
    built = draft.rebuild(_document(), NOW + timedelta(minutes=1))
    issued = built.issue(NOW + timedelta(minutes=2))
    key = operation_key(issued.document["operations"][0])
    manual = {**issued.document["operations"][0], "name": "Ручная правка"}
    edited = issued.edit_operation(key, manual, NOW + timedelta(minutes=3))
    rebuilt = edited.rebuild(_document(changed=True), NOW + timedelta(minutes=4))

    assert draft.status is TechcardStatus.DRAFT
    assert built.revision == 0
    assert issued.revision == 1
    assert edited.revision == 2
    assert rebuilt.revision == 3
    assert rebuilt.document["operations"][0]["name"] == "Ручная правка"
    assert rebuilt.document["operations"][0]["time"] == 10


def test_project_techcard_rejects_duplicate_operations_and_key_change():
    duplicate = {"operations": [{"code": "10"}, {"code": "10"}]}
    with pytest.raises(TechcardValidationError, match="уникальные"):
        _card().rebuild(duplicate, NOW)

    built = _card().rebuild(_document(), NOW)
    key = operation_key(built.document["operations"][0])
    with pytest.raises(TechcardValidationError, match="Электронной ТК нет"):
        built.edit_operation(key, {**built.document["operations"][0], "name": "Правка"}, NOW)
    issued = built.issue(NOW)
    with pytest.raises(TechcardValidationError, match="не меняются"):
        issued.edit_operation(key, {"code": "99", "connector": "A"}, NOW)


@pytest.fixture()
def project_techcards_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def test_project_techcard_api_persists_revisions(project_techcards_api):
    created = project_techcards_api.post(
        "/api/techcards/projects",
        json={
            "project": "P-100",
            "item_index": "01",
            "product_name": "Жгут",
            "source_calculation": "Расчёт 7",
            "deadline": "2026-10-01",
        },
    )
    assert created.status_code == 201
    card = created.json()

    built = project_techcards_api.put(
        f"/api/techcards/projects/{card['id']}/etk",
        json={"version": card["version"], "document": _document()},
    )
    assert built.status_code == 200

    issued = project_techcards_api.post(
        f"/api/techcards/projects/{card['id']}/issue",
        json={"version": built.json()["version"]},
    )
    assert issued.status_code == 200
    key = operation_key(issued.json()["document"]["operations"][0])
    operation = {**issued.json()["document"]["operations"][0], "name": "Ручная правка", "card": {
        "name": "Обработка A", "safety": "", "defects": "", "tooling": [],
        "steps": [{"kind": "Операция", "text": "Обработать", "tolerance": ""}],
    }}

    edited = project_techcards_api.put(
        f"/api/techcards/projects/{card['id']}/operations/{key}",
        json={"version": issued.json()["version"], "operation": operation},
    )
    revisions = project_techcards_api.get(
        f"/api/techcards/projects/{card['id']}/revisions"
    )
    listed = project_techcards_api.get("/api/techcards/projects")

    assert edited.status_code == 200
    assert edited.json()["revision"] == 2
    assert [item["revision"] for item in revisions.json()["items"]] == [1, 2]
    assert listed.json()["items"][0]["project"] == "P-100"


def test_project_techcard_api_rejects_duplicate(project_techcards_api):
    payload = {
        "project": "P-100",
        "item_index": "01",
        "product_name": "Жгут",
        "source_calculation": "",
    }
    assert project_techcards_api.post("/api/techcards/projects", json=payload).status_code == 201
    duplicate = project_techcards_api.post("/api/techcards/projects", json=payload)
    assert duplicate.status_code == 422


def test_auto_build_requires_an_imported_operation_library(project_techcards_api):
    created = project_techcards_api.post(
        "/api/techcards/projects",
        json={"project": "P-300", "item_index": "01", "product_name": "Жгут"},
    ).json()
    response = project_techcards_api.post(
        f"/api/techcards/projects/{created['id']}/build",
        json={"version": created["version"], "config": {"ops": []}},
    )
    assert response.status_code == 422
    assert "не загружена" in response.json()["detail"]


def test_project_etk_operation_edit_normalizes_card_and_logs_revision(project_techcards_api):
    """SUPPLYMAP etkApplyOpEdit_: the card goes through tcRecordNormalize_, the operation is stamped edited,
    revision +1 with «Правка операции N (CODE — разъём)»."""
    card = project_techcards_api.post("/api/techcards/projects", json={
        "project": "P-101", "item_index": "01", "product_name": "Жгут", "source_calculation": "",
    }).json()
    document = {"operations": [{
        "position": 1, "code": "10", "connector": "XS1",
        "card": {"name": "Сборка", "safety": "", "defects": "", "steps": [
            {"number": "1", "kind": "Операция", "text": "Собрать", "tolerance": ""},
        ], "tooling": []},
    }]}
    built = project_techcards_api.put(
        f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": document},
    ).json()
    issued = project_techcards_api.post(
        f"/api/techcards/projects/{card['id']}/issue", json={"version": built["version"]},
    ).json()
    operation = issued["document"]["operations"][0]
    operation["card"]["steps"] = [
        {"kind": "Операция", "text": "Собрать аккуратно", "tolerance": ""},
        {"kind": "Контроль", "text": "", "tolerance": ""},
        {"kind": "Контроль", "text": "Проверить", "tolerance": "±1"},
    ]
    operation["card"]["safety"] = "Очки"

    edited = project_techcards_api.put(
        f"/api/techcards/projects/{card['id']}/operations/{operation_key(operation)}",
        json={"version": issued["version"], "operation": operation},
    )
    revisions = project_techcards_api.get(f"/api/techcards/projects/{card['id']}/revisions").json()["items"]

    assert edited.status_code == 200, edited.text
    saved = edited.json()["document"]["operations"][0]
    assert edited.json()["revision"] == 2
    assert [(s["number"], s["kind"], s["tolerance"]) for s in saved["card"]["steps"]] == [
        ("1", "Операция", ""), ("2", "Контроль", "±1"),
    ]
    assert saved["card"]["name"] == "Сборка"
    assert saved["card"]["safety"] == "ВАЖНО! ТРЕБОВАНИЕ БЕЗОПАСНОСТИ:\nОчки"
    assert saved["edited"] == {"at": "23.09.2026 10:00", "by": "Инженер"}
    assert revisions[-1]["reason"] == "Правка операции 1 (10 — XS1)"


def test_discard_draft_keeps_issued_techcards():
    built = _card().rebuild(_document(), NOW)
    discarded = built.discard_draft(NOW + timedelta(minutes=1))
    assert discarded.document is None
    assert discarded.version == built.version + 1
    with pytest.raises(TechcardValidationError, match="ещё не собран"):
        discarded.discard_draft(NOW)
    issued = built.issue(NOW)
    with pytest.raises(TechcardValidationError, match="Выданную"):
        issued.discard_draft(NOW)


def test_discard_draft_api_removes_built_document(project_techcards_api):
    card = project_techcards_api.post("/api/techcards/projects", json={
        "project": "P-100", "item_index": "01", "product_name": "Жгут", "source_calculation": "",
    }).json()
    built = project_techcards_api.put(
        f"/api/techcards/projects/{card['id']}/etk",
        json={"version": card["version"], "document": _document()},
    ).json()
    discarded = project_techcards_api.request(
        "DELETE", f"/api/techcards/projects/{card['id']}/etk", json={"version": built["version"]},
    )
    assert discarded.status_code == 200
    assert discarded.json()["document"] is None
    assert discarded.json()["status"] == "Черновик"


def test_project_etk_operation_edit_refuses_operation_without_card(project_techcards_api):
    """SUPPLYMAP etkApplyOpEdit_: «У операции N нет карточки»; an unissued draft has no ЭТК to edit yet."""
    card = project_techcards_api.post("/api/techcards/projects", json={
        "project": "P-102", "item_index": "01", "product_name": "Жгут", "source_calculation": "",
    }).json()
    built = project_techcards_api.put(
        f"/api/techcards/projects/{card['id']}/etk", json={"version": card["version"], "document": _document()},
    ).json()
    operation = built["document"]["operations"][0]
    url = f"/api/techcards/projects/{card['id']}/operations/{operation_key(operation)}"

    with_card = {**operation, "card": {"name": "X", "safety": "", "defects": "", "tooling": [],
                                       "steps": [{"kind": "Операция", "text": "Шаг", "tolerance": ""}]}}
    draft = project_techcards_api.put(url, json={"version": built["version"], "operation": with_card})
    issued = project_techcards_api.post(
        f"/api/techcards/projects/{card['id']}/issue", json={"version": built["version"]},
    ).json()
    no_card = project_techcards_api.put(url, json={"version": issued["version"], "operation": operation})

    assert draft.status_code == 422 and "Электронной ТК нет" in draft.json()["detail"]
    assert no_card.status_code == 422 and "нет карточки" in no_card.json()["detail"]
