"""Explicit pending verification never authorizes a measurement instrument."""

import pytest
from sqlalchemy import select

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401

BASE = {"inventory_number": "TEST-CABLE.001.2", "name": "Тестовый кабельный тестер", "instrument_type": "Тестер"}
EVIDENCE = {"verification_number": "TEST-PV", "verified_at": "2026-09-01T00:00:00", "verification_due": "2027-09-01T00:00:00"}
PENDING = "Поверка не подтверждена"


def _as(permissions):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=MASTER_ID, name="Контролёр", department="ОТК"),
        email="qc@example.test", roles=frozenset({"qc"}), permissions=permissions,
    )


@pytest.mark.parametrize("explicit_nulls", [False, True])
def test_pending_registration_and_listing_preserve_null_evidence(production_api, explicit_nulls):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    evidence = dict.fromkeys(EVIDENCE) if explicit_nulls else {}
    result = client.post("/api/production/instruments", json={**BASE, "status": PENDING, **evidence})
    assert result.status_code == 200, result.text
    instrument = result.json()["instrument"]
    assert instrument == {**BASE, **dict.fromkeys(EVIDENCE), "status": PENDING, "id": instrument["id"], "version": 1}
    assert client.get("/api/production/instruments").json()["instruments"] == [instrument]
    duplicate = client.post("/api/production/instruments", json={**BASE, "status": PENDING})
    assert duplicate.status_code == 422 and "уже есть" in duplicate.json()["detail"]
    with app.state.production_uow_factory() as uow:
        events = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.instrument_registered")).all()
        assert len(events) == 1


def test_legacy_active_payload_keeps_its_contract(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    result = client.post("/api/production/instruments", json={**BASE, **EVIDENCE})
    assert result.status_code == 200, result.text
    instrument = result.json()["instrument"]
    assert instrument["status"] == "Действует"
    assert all(instrument[key] == value for key, value in EVIDENCE.items())


@pytest.mark.parametrize("extra", [
    {}, dict.fromkeys(EVIDENCE), {"verification_number": "TEST-PV"},
    {**EVIDENCE, "verified_at": None}, {**EVIDENCE, "verification_due": None},
    {**EVIDENCE, "verification_number": " "}, {**EVIDENCE, "verification_number": ""},
    {**EVIDENCE, "verification_due": "2026-08-01T00:00:00"},
    {**EVIDENCE, "verified_at": "invalid"}, {**EVIDENCE, "status": "Не используется"},
    {"status": PENDING, "verification_number": ""}, {"status": PENDING, "verification_number": " "},
    {"status": PENDING, "verified_at": EVIDENCE["verified_at"]}, {"status": PENDING, **EVIDENCE},
    {"status": "unknown"},
])
def test_invalid_or_implicit_verification_is_rejected_without_registering(production_api, extra):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    response = client.post("/api/production/instruments", json={**BASE, **extra})
    assert response.status_code == 422, response.text
    assert client.get("/api/production/instruments").json()["instruments"] == []


def test_pending_registration_still_requires_quality_edit_permission(production_api):
    client, _ = production_api
    _as({"quality": "view", "production": "view"})
    assert client.post("/api/production/instruments", json={**BASE, "status": PENDING}).status_code == 403
