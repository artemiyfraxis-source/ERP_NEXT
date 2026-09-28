from datetime import datetime
import base64
import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.shared.persistence import create_schema


NOW = datetime(2026, 9, 23, 12, 0)
SOURCE_COMMIT = "df683f87768f98735a87e68bde2be023b48dc03a"


def _template(source_id: str, code: str) -> dict:
    rows = [[f"{code} | Операция"]] + [[] for _ in range(11)]
    rows[8] = ["Инструмент", "1", "Инструмент", "", "", "", "ОБ.001"]
    rows[10] = ["Операция", "1", "Выполнить операцию", "", "", "", "КД"]
    return {"id": source_id, "title": rows[0][0], "category": "Сборка", "rows": rows}


@pytest.fixture()
def operation_library_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    actor = Actor(
        id=UUID("30000000-0000-0000-0000-000000000009"),
        name="Администратор",
        department="ERP",
    )
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor,
        email="admin@example.test",
        roles=frozenset({"admin"}),
        permissions={"techcards": "edit", "references": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    del app.state.references_uow_factory
    engine.dispose()


def test_imports_lists_and_reads_lossless_operation_cards(operation_library_api):
    imported = operation_library_api.put(
        "/api/techcards/templates/supplymap-library",
        json={"sourceCommit": SOURCE_COMMIT, "templates": [_template("cut-wire", "CUT_WIRE")]},
    )
    listed = operation_library_api.get("/api/techcards/templates")
    detail = operation_library_api.get("/api/techcards/templates/cut-wire")

    assert imported.status_code == 200
    assert imported.json() == {"templates": 1, "source_commit": SOURCE_COMMIT}
    assert listed.json()["metadata"]["template_count"] == 1
    assert listed.json()["items"][0]["code"] == "CUT_WIRE"
    assert detail.json()["document"]["steps"][0]["text"] == "Выполнить операцию"
    assert detail.json()["raw_rows"][8][2] == "Инструмент"


def test_engineer_imports_grapher_snapshot_without_mutating_card(operation_library_api):
    snapshot = {
        "project": {"designation": "708К.2", "name": "Контроллер"},
        "harnesses": [{
            "designation": "КБ1", "quantity": 2,
            "design": {"schemaVersion": 1, "content": {
                "connectors": [], "cables": [], "diffPairs": [],
                "wires": [{
                    "id": "w1", "lengthMm": 125,
                    "materialBinding": {"displayName": "ПВАМ 0,35"},
                }],
            }},
        }],
    }
    encoded = base64.b64encode(json.dumps(snapshot).encode()).decode()
    response = operation_library_api.post("/api/techcards/grapher/import", json={
        "filename": "snapshot.json", "content_base64": encoded, "components": [],
    })
    assert response.status_code == 200, response.text
    assert response.json()["harnesses"][0]["groups"][0]["material"] == "ПВАМ 0,35"


def test_library_import_replaces_previous_snapshot(operation_library_api):
    operation_library_api.put(
        "/api/techcards/templates/supplymap-library",
        json={"sourceCommit": SOURCE_COMMIT, "templates": [_template("one", "OP_ONE")]},
    )
    operation_library_api.put(
        "/api/techcards/templates/supplymap-library",
        json={"sourceCommit": SOURCE_COMMIT, "templates": [_template("two", "OP_TWO")]},
    )
    listed = operation_library_api.get("/api/techcards/templates")

    assert [item["id"] for item in listed.json()["items"]] == ["two"]


def test_non_admin_cannot_replace_operation_library(operation_library_api):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(
            id=UUID("30000000-0000-0000-0000-000000000010"),
            name="Инженер",
            department="Производство",
        ),
        email="engineer@example.test",
        roles=frozenset({"engineer"}),
        permissions={"techcards": "edit"},
    )
    response = operation_library_api.put(
        "/api/techcards/templates/supplymap-library",
        json={"sourceCommit": SOURCE_COMMIT, "templates": [_template("one", "OP_ONE")]},
    )
    assert response.status_code == 403


def test_auto_builds_a_persistent_project_etk_from_the_imported_library(operation_library_api):
    operation_library_api.put(
        "/api/techcards/templates/supplymap-library",
        json={"sourceCommit": SOURCE_COMMIT, "templates": [_template("one", "OP_ONE")]},
    )
    created = operation_library_api.post(
        "/api/techcards/projects",
        json={"project": "P-200", "item_index": "КБ1", "product_name": "Жгут"},
    ).json()

    built = operation_library_api.post(
        f"/api/techcards/projects/{created['id']}/build",
        json={
            "version": created["version"],
            "config": {
                "projectNo": "P-200",
                "assemblyIndex": "КБ1",
                "assemblyName": "Жгут",
                "partQty": 10,
                "ops": [{
                    "type": "heatTut", "templateId": "one", "opNum": "OP_ONE",
                    "tPrep": "60", "tOp": "15", "tMachine": "10", "tutCount": 1,
                }],
            },
        },
    )

    assert built.status_code == 200
    document = built.json()["document"]
    assert document["operations"][0]["code"] == "OP_ONE"
    assert document["operations"][0]["card"]["steps"][0]["text"] == "Выполнить операцию"
    assert document["operations"][0]["time"][0]["norm"] == "3,5"
    assert document["source"]["operation_library_commit"] == SOURCE_COMMIT


def test_auto_build_enriches_crimp_from_current_reference_snapshot(operation_library_api):
    operation_library_api.put(
        "/api/techcards/templates/supplymap-library",
        json={"sourceCommit": SOURCE_COMMIT, "templates": [_template("prs", "PRS_TERMINAL_auto")]},
    )
    snapshot = operation_library_api.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 14,
        "source_updated_at": "2026-09-23T08:00:00+03:00",
        "records": [{
            "tab": "ter", "display_text": "Molex 39000079", "source_sheet": "БД.ТЕР",
            "fields": {
                "terArticle": "39000079", "terApplicator": "S10/M10/M19",
                "terCrimpHeight": "1,14 - 1,24", "terPullForceMin": "156",
                "terPullForceMax": "156", "awgFrom": "16", "awgTo": "16",
                "mm2From": "1,5", "mm2To": "1,5",
            },
        }],
    })
    created = operation_library_api.post(
        "/api/techcards/projects",
        json={"project": "P-201", "item_index": "КБ1", "product_name": "Жгут"},
    ).json()
    built = operation_library_api.post(
        f"/api/techcards/projects/{created['id']}/build",
        json={"version": created["version"], "config": {
            "assemblyName": "Жгут", "partQty": 1,
            "ops": [{
                "type": "prsTerm", "templateId": "prs", "opNum": "PRS_TERMINAL_auto",
                "side": "A", "ep": {"termArt": "Molex 39000079"},
                "wires": [{"name": "ПВАМ 1,5", "art": "ПВАМ 1,5", "qty": 1}],
            }],
        }},
    )

    assert snapshot.status_code == 200
    assert built.status_code == 200
    document = built.json()["document"]
    assert document["operations"][0]["fields"]["applicator"] == "S10/M10/M19"
    assert document["operations"][0]["fields"]["crimpHeight"] == "1,14 - 1,24"
    assert document["source"]["technology_snapshot"]["schema_version"] == 14
    assert document["source"]["technology_snapshot"]["source_updated_at"].startswith("2026-09-23")


def test_auto_build_uses_workshop_gost_awg_map_from_references(operation_library_api):
    operation_library_api.put(
        "/api/techcards/templates/supplymap-library",
        json={"sourceCommit": SOURCE_COMMIT, "templates": [_template("prs", "PRS_TERMINAL_auto")]},
    )
    operation_library_api.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 14,
        "records": [{
            "tab": "ter", "display_text": "TEST-22", "source_sheet": "БД.ТЕР",
            "fields": {
                "terArticle": "TEST-22", "terCrimpHeight": "0,84 - 0,89",
                "awgFrom": "22", "awgTo": "22",
            },
        }],
    })
    maps = operation_library_api.put("/api/references/maps", json={
        "suppliers": [], "countries": [], "gost_awg": [{"key": "0,5", "value": "22"}],
    })
    created = operation_library_api.post(
        "/api/techcards/projects",
        json={"project": "P-202", "item_index": "КБ1", "product_name": "Жгут"},
    ).json()
    built = operation_library_api.post(
        f"/api/techcards/projects/{created['id']}/build",
        json={"version": created["version"], "config": {
            "assemblyName": "Жгут", "partQty": 1,
            "ops": [{
                "type": "prsTerm", "templateId": "prs", "opNum": "PRS_TERMINAL_auto",
                "side": "A", "ep": {"termArt": "TEST-22"},
                "wires": [{"name": "ПВАМ 0,5", "art": "ПВАМ 0,5", "qty": 1}],
            }],
        }},
    )

    assert maps.status_code == 200
    assert built.status_code == 200
    assert built.json()["document"]["operations"][0]["fields"]["crimpHeight"] == "0,84 - 0,89"
