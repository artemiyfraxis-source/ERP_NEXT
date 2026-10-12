"""Токены «Чтение базы данных» (`refs:read`) / «Загрузка в базу данных» (`refs:write`), owner 12.10.2026: over HTTP a
read token reads «Справочник» and writes nothing, a write token also edits rows (the audit names «API: <name>»), no
token replaces a snapshot or the maps, and a person with «Справочник» edit rights is not affected."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.identity.domain import IdentityUser, hash_password
from app.modules.identity.infrastructure import provision_user
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.persistence import AuditEventRow
from tests.identity.test_api_tokens import _bearer, _csrf, _issue, _login, tokens_api  # noqa: F401  (fixture)

TECH_LEAD_ID = UUID("40000000-0000-0000-0000-000000000003")
GAUGE = {"awg": "20", "section_mm2": "0,5", "conductor_d_mm": "0,81"}


@pytest.fixture()
def refs_api(tokens_api):  # noqa: F811
    sessions, _clock = tokens_api
    with sessions() as session:
        provision_user(session, IdentityUser(
            id=TECH_LEAD_ID, login="techlead", email="techlead@example.test", name="Технолог", department="ОГТ",
            role="installer", site=None, active=True, access_level="tech_lead",
        ), hash_password("techlead-password"))
        session.commit()
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    admin = _login("admin")
    tokens = {
        scope: _bearer(_issue(admin, scopes=(scope,), name=f"Загрузчик {scope}")["token"])
        for scope in ("refs:read", "refs:write", "tcgen")
    }
    yield sessions, tokens
    del app.state.references_uow_factory


def test_a_read_token_reads_the_reference_tables(refs_api):
    _sessions, tokens = refs_api
    client = TestClient(app)
    for path in ("/api/references/tech-operations", "/api/references/cables", "/api/references/bases"):
        response = client.get(path, headers=tokens["refs:read"])
        assert response.status_code == 200, (path, response.text)
    assert client.get("/api/references/cables", headers=tokens["refs:write"]).status_code == 200


def test_a_token_without_refs_write_cannot_edit(refs_api):
    _sessions, tokens = refs_api
    client = TestClient(app)
    for scope in ("refs:read", "tcgen"):
        response = client.post("/api/references/wire-gauges", json=GAUGE, headers=tokens[scope])
        assert response.status_code == 403, (scope, response.text)
        assert client.patch(
            f"/api/references/tech-operations/{UUID(int=1)}", headers=tokens[scope],
            json={"edits": [{"field": "Название", "old": "", "value": "Х"}]},
        ).status_code == 403
    assert client.get("/api/references/cables", headers=tokens["tcgen"]).status_code == 403
    assert client.get("/api/references/cables", headers=tokens["refs:write"]).json()["gauges"] == []


def test_a_write_token_edits_a_row_and_the_audit_names_the_token(refs_api):
    sessions, tokens = refs_api
    response = TestClient(app).post("/api/references/wire-gauges", json=GAUGE, headers=tokens["refs:write"])
    assert response.status_code == 201, response.text
    with sessions() as session:
        actors = session.scalars(
            select(AuditEventRow.actor_name).where(AuditEventRow.event_type.like("references.%"))
        ).all()
    assert actors == ["API: Загрузчик refs:write"]


def test_no_token_replaces_a_snapshot_or_the_maps(refs_api):
    _sessions, tokens = refs_api
    client = TestClient(app)
    for scope in ("refs:read", "refs:write"):
        headers = tokens[scope]
        assert client.put(
            "/api/references/tech-operations/snapshot", headers=headers, json={"schema_version": 1, "records": []},
        ).status_code == 403
        assert client.put(
            "/api/references/tech-operations/supplymap-snapshot", headers=headers, json={"meta": {}, "records": []},
        ).status_code == 403
        assert client.post("/api/references/coax-cards/import", headers=headers, json={}).status_code == 403
        assert client.put(
            "/api/references/maps", headers=headers, json={"suppliers": [], "countries": [], "gost_awg": []},
        ).status_code == 403
        assert client.get("/api/references/maps", headers=headers).status_code == 403


def test_a_person_with_reference_edit_rights_still_edits(refs_api):
    _sessions, _tokens = refs_api
    lead = _login("techlead")
    assert lead.get("/api/references/cables").status_code == 200
    created = lead.post("/api/references/wire-gauges", json=GAUGE, headers=_csrf(lead))
    assert created.status_code == 201, created.text
    operator = _login("operator")
    refused = operator.post("/api/references/wire-gauges", json={**GAUGE, "awg": "22"}, headers=_csrf(operator))
    assert refused.status_code == 403
