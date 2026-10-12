"""GET/POST …/harness/{index}/documents — the drawing's connection table, cut list and specification, computed on the
server from the saved drawing (or the editor's unsaved one); Excel download; same rights as reading the drawing."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor

PROJECT = "708К.4"
BASE = f"/api/techcards/kitsets/{PROJECT}/harness"
NOW = datetime(2026, 10, 10, 10, 0)
GOLDEN = {case["name"]: case for case in json.loads(
    (Path(__file__).parent / "harness_documents_golden.json").read_text(encoding="utf-8"))}


def _principal(permissions):
    return lambda: Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}), permissions=permissions,
    )


@pytest.fixture()
def client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)  # noqa: E731
    app.state.techcards_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    with factory() as uow:
        uow.kitsets.save(Kitset(
            project=PROJECT, customer="ООО Заказчик", calc_source="", updated_at=NOW, updated_by="Технолог",
            created_at=NOW, assemblies=(KitAssembly(index="КБ1", name="Жгут питания", run=Decimal("10")),),
        ))
        uow.commit()
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.techcards_uow_factory
    engine.dispose()


def _document():
    design = deepcopy(GOLDEN["source-versions"]["document"])
    design["requiredWriterContractVersion"] = 2
    return design


def test_documents_of_the_saved_drawing(client):
    assert client.get(f"{BASE}/КБ1/documents", params={"kind": "bom"}).status_code == 404
    assert client.put(f"{BASE}/КБ1", json={"design": _document(), "version": None}).status_code == 200

    bom = client.get(f"{BASE}/кб1/documents", params={"kind": "bom", "quantity": 3})
    assert bom.status_code == 200, bom.text
    metres = [row for row in bom.json()["rows"] if row["unit"] == "м"]
    assert [(row["amount"], row["object_ids"]) for row in metres] == [(0.6006, ["W1", "W2"]), (0.3003, ["W3"])]

    connections = client.get(f"{BASE}/КБ1/documents", params={"kind": "connections"}).json()
    assert [row["index"] for row in connections["rows"]] == ["W1", "W2", "W3"]
    cut = client.get(f"{BASE}/КБ1/documents", params={"kind": "cut", "quantity": 2}).json()
    assert cut["quantity"] == 2 and [item["id"] for item in cut["items"]] == ["W1", "W2", "W3"]
    assert {sheet["object_id"] for sheet in cut["sheets"]} == {"W1", "W2", "W3"}

    assert client.get(f"{BASE}/КБ1/documents", params={"kind": "other"}).status_code == 422
    assert client.get(f"{BASE}/КБ1/documents", params={"kind": "bom", "quantity": 0}).status_code == 422
    assert client.get(f"{BASE}/КБ9/documents", params={"kind": "bom"}).status_code == 404


def test_excel_download(client):
    client.put(f"{BASE}/КБ1", json={"design": _document(), "version": None})
    response = client.get(f"{BASE}/КБ1/documents", params={"kind": "connections", "format": "xlsx"})
    assert response.status_code == 200
    # every non-viewable file is a plain download (audit F04); Excel opens it by the .xls name
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "%D0%A2%D0%B0%D0%B1%D0%BB%D0%B8%D1%86%D0%B0" in response.headers["content-disposition"]  # «Таблица»
    text = response.content[2:].decode("utf-16-le")
    assert text.splitlines()[0].startswith("Индекс (Wn)\tМарка") and len(text.splitlines()) == 4


def test_unsaved_drawing_from_the_editor(client):
    design = _document()
    design["wires"][0]["circuit"] = "GND"
    live = client.post(f"{BASE}/КБ1/documents", params={"kind": "connections"}, json={"design": design})
    assert live.status_code == 200, live.text
    assert live.json()["rows"][0]["marking"] == "GND"
    # No drawing in the request — the saved one (none yet).
    assert client.post(f"{BASE}/КБ1/documents", params={"kind": "bom"}, json={}).status_code == 404
    bad = client.post(f"{BASE}/КБ1/documents", params={"kind": "bom"}, json={"design": {"schemaVersion": 9}})
    assert bad.status_code == 422


def test_rights_follow_the_drawing(client):
    client.put(f"{BASE}/КБ1", json={"design": _document(), "version": None})
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "view"})
    assert client.get(f"{BASE}/КБ1/documents", params={"kind": "cut"}).status_code == 200
    assert client.post(f"{BASE}/КБ1/documents", params={"kind": "cut"}, json={"design": _document()}).status_code == 200
    app.dependency_overrides[authenticated_principal] = _principal({})
    assert client.get(f"{BASE}/КБ1/documents", params={"kind": "cut"}).status_code == 403
    assert client.post(f"{BASE}/КБ1/documents", params={"kind": "cut"}, json={}).status_code == 403
