from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor


@pytest.fixture()
def references_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    actor = Actor(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        name="Администратор",
        department="ERP",
    )
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor,
        email="admin@example.test",
        roles=frozenset({"admin"}),
        permissions={"references": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 10, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.references_uow_factory
    engine.dispose()


def test_replaces_both_editable_maps_atomically(references_api):
    response = references_api.put("/api/references/maps", json={
        "suppliers": [{"key": "https://www.Example.ru/catalog", "value": "ООО Поставщик"}],
        "countries": [{"key": "  Fraxis  ", "value": "Россия"}],
        "gost_awg": [{"key": "0,35", "value": "22"}],
    })
    fetched = references_api.get("/api/references/maps")
    duplicate = references_api.put("/api/references/maps", json={
        "suppliers": [
            {"key": "example.ru", "value": "Первый"},
            {"key": "www.example.ru/path", "value": "Второй"},
        ],
        "countries": [],
        "gost_awg": [],
    })

    assert response.status_code == 200
    assert fetched.json() == {
        "suppliers": [{"key": "https://www.Example.ru/catalog", "value": "ООО Поставщик"}],
        "countries": [{"key": "Fraxis", "value": "Россия"}],
        "gost_awg": [{"key": "0,35", "value": "22"}],
    }
    assert duplicate.status_code == 422
    assert references_api.get("/api/references/maps").json() == fetched.json()


def test_replaces_and_searches_versioned_technology_snapshot(references_api):
    saved = references_api.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 14,
        "source_updated_at": "2026-09-20T12:00:00+03:00",
        "records": [
            {
                "tab": "op",
                "display_text": "Обжим контакта",
                "search_text": "опрессовка",
                "source_sheet": "БД.ОП",
                "sort_key": "001",
                "fields": {"code": "ОП-01", "tool": "Клещи"},
            },
            {
                "tab": "ob",
                "display_text": "Кримпер",
                "source_sheet": "БД.ОБ",
                "fields": {"code": "ОБ-02"},
            },
        ],
    })
    result = references_api.get("/api/references/tech-operations", params={"tab": "op", "q": "клещи"})

    assert saved.json() == {"records": 2}
    assert result.status_code == 200
    assert result.json()["metadata"]["schema_version"] == 14
    assert result.json()["metadata"]["record_count"] == 2
    assert [item["display_text"] for item in result.json()["records"]] == ["Обжим контакта"]


def test_resolves_terminal_norm_from_current_technology_snapshot(references_api):
    saved = references_api.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 14,
        "records": [{
            "tab": "ter",
            "display_text": "Molex | Micro-Fit 3.0",
            "source_sheet": "БД.ТЕР",
            "fields": {
                "terArticle": "5034290000",
                "terApplicator": "M49/A8",
                "terCrimpHeight": "24 AWG: 0,76 - 0,81; 22 AWG: 0,84 - 0,89",
                "terPullForceMin": "24 AWG: 29,3; 22 AWG: 44,1",
                "awgFrom": "28",
                "awgTo": "22",
            },
        }],
    })
    resolved = references_api.post("/api/references/technology/resolve-terminal", json={
        "article": "MOLEX 5034290000",
        "wires": ["UL1007 24 AWG", "ПВАМ 0,35"],
        "gost_awg": {"0.35": 22},
    })

    assert saved.status_code == 200
    assert resolved.status_code == 200
    assert resolved.json()["applicator"] == "M49/A8"
    assert resolved.json()["crimp_height"] == "24 AWG: 0,76 - 0,81; 0,35 мм² (22 AWG): 0,84 - 0,89"
    assert [item["found"] for item in resolved.json()["by_section"]] == [True, True]


def test_rejects_outdated_technology_snapshot_schema(references_api):
    response = references_api.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 13,
        "records": [],
    })
    assert response.status_code == 422
    assert "v14" in response.json()["detail"]


def test_imports_raw_supplymap_snapshot_and_keeps_terminal_ranges(references_api):
    imported = references_api.put("/api/references/tech-operations/supplymap-snapshot", json={
        "meta": {
            "schemaVersion": 14,
            "updatedAt": "2026-09-23T08:00:00.000Z",
            "columnHeadersByTab": {
                "ter": ["От AWG", "До AWG", "От мм2", "До мм2"],
            },
        },
        "records": [{
            "tabKey": "ter",
            "displayText": "Molex | Micro-Fit 3.0",
            "normalizedSearch": "molex micro-fit 3.0 5034290000",
            "sourceSheet": "БД.ТЕР",
            "sortKey": "",
            "exportValues": ["28", "22", "0,08", "0,35"],
            "terArticle": "5034290000",
            "terApplicator": "M49/A8",
            "terCrimpHeight": "24 AWG: 0,76 - 0,81",
            "terPullForceMin": "24 AWG: 29,3",
        }],
    })
    resolved = references_api.post("/api/references/technology/resolve-terminal", json={
        "article": "5034290000",
        "wires": ["UL1007 24 AWG"],
    })

    assert imported.status_code == 200
    assert imported.json() == {"records": 1, "schema_version": 14}
    assert resolved.status_code == 200
    assert resolved.json()["by_section"][0]["found"] is True


def test_equipment_snapshot_keeps_group_row_and_both_size_machines(references_api):
    # SUPPLYMAP bfef60d: the machine choice by cable size lives in data (two «Оборудование» rows of one
    # operation, «Ø до 5,5 мм» / «свыше» in the name), and a multi-instance machine gets a «(R)» group row
    # beside its instances. The catalog stores БД.ОБ rows as they come — nothing is merged or dropped.
    def ob(base: str) -> dict:
        return {
            "tabKey": "ob", "displayText": base, "normalizedSearch": base.casefold(),
            "sourceSheet": "БД.ОБ", "sortKey": f"Оборудование {base}", "obType": "Оборудование",
        }

    rows = [
        ob("BZW-886+Q2/Станок резки провода с роторной зачисткой мал."),
        ob("BZW-50X/Станок резки провода с роторной зачисткой больш."),
        ob("BZW-D2.0S/Большой стрипер"),
        ob("BZW-D2.0S/Большой стрипер"),
        ob("BZW-D2.0S(R)/Большой стрипер"),
    ]
    imported = references_api.put("/api/references/tech-operations/supplymap-snapshot", json={
        "meta": {"schemaVersion": 14, "updatedAt": "2026-09-28T18:20:00.000Z"},
        "records": rows,
    })
    strippers = references_api.get("/api/references/tech-operations", params={"tab": "ob", "q": "bzw-d2.0s"})
    rotary = references_api.get("/api/references/tech-operations", params={"tab": "ob", "q": "роторной зачисткой"})

    assert imported.json() == {"records": 5, "schema_version": 14}
    assert sorted(item["display_text"] for item in strippers.json()["records"]) == [
        "BZW-D2.0S(R)/Большой стрипер", "BZW-D2.0S/Большой стрипер", "BZW-D2.0S/Большой стрипер",
    ]
    assert {item["fields"]["obType"] for item in strippers.json()["records"]} == {"Оборудование"}
    assert sorted(item["display_text"].split("/")[0] for item in rotary.json()["records"]) == [
        "BZW-50X", "BZW-886+Q2",
    ]


def test_viewer_cannot_replace_maps(references_api):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(
            id=UUID("10000000-0000-0000-0000-000000000002"),
            name="Сотрудник",
            department="Производство",
        ),
        email="staff@example.test",
        roles=frozenset({"staff"}),
        permissions={"references": "view"},
    )
    response = references_api.put(
        "/api/references/maps",
        json={"suppliers": [], "countries": [], "gost_awg": []},
    )
    assert response.status_code == 403


def test_gost_awg_map_is_validated_and_cannot_be_dropped_by_omission(references_api):
    saved = references_api.put("/api/references/maps", json={
        "suppliers": [], "countries": [],
        "gost_awg": [{"key": "0,35", "value": "22"}, {"key": "1,0", "value": "17"}],
    })
    omitted = references_api.put("/api/references/maps", json={"suppliers": [], "countries": []})
    duplicate = references_api.put("/api/references/maps", json={
        "suppliers": [], "countries": [],
        "gost_awg": [{"key": "0,35", "value": "22"}, {"key": "0.35", "value": "20"}],
    })
    not_awg = references_api.put("/api/references/maps", json={
        "suppliers": [], "countries": [], "gost_awg": [{"key": "0,35", "value": "двадцать"}],
    })

    assert saved.status_code == 200
    assert omitted.status_code == 422
    assert duplicate.status_code == 422
    assert not_awg.status_code == 422
    assert references_api.get("/api/references/maps").json()["gost_awg"] == [
        {"key": "0,35", "value": "22"}, {"key": "1,0", "value": "17"},
    ]
