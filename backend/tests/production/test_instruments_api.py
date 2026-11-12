"""Explicit pending verification never authorizes a measurement instrument."""

import pytest
from dataclasses import replace
from uuid import UUID, uuid4
from sqlalchemy import select

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.production.test_api import MASTER_ID, production_api  # noqa: F401

BASE = {"inventory_number": "TEST-CABLE.001.2", "name": "Тестовый кабельный тестер", "instrument_type": "Тестер"}
EVIDENCE = {"verification_number": "TEST-PV", "verified_at": "2026-09-01T00:00:00", "verification_due": "2027-09-01T00:00:00"}
PENDING = "Поверка не подтверждена"


@pytest.mark.parametrize("instrument_type", [None, "", "   "])
def test_blank_accessory_type_defaults_but_measuring_type_is_required(production_api, instrument_type):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    parent = create(client)
    body = {"inventory_number": "СИ.099", "name": "Переходник", "kind": "Принадлежность", "parent_id": parent["id"]}
    if instrument_type is not None:
        body["instrument_type"] = instrument_type
    response = client.post("/api/production/instruments", json=body)
    assert response.status_code == 200, response.text
    accessory = response.json()["instrument"]
    assert accessory["instrument_type"] == "Принадлежность"
    assert accessory["warehouse_writeoff_id"] is None
    edited = client.patch(f"/api/production/instruments/{accessory['id']}", json={"version": 1, "instrument_type": " "})
    assert edited.status_code == 200, edited.text
    assert edited.json()["instrument"]["instrument_type"] == "Принадлежность"
    body.update(kind="Средство измерений", status=PENDING, inventory_number="СИ.100", parent_id=None)
    assert client.post("/api/production/instruments", json=body).status_code == 422
    assert client.patch(f"/api/production/instruments/{parent['id']}", json={"version": parent["version"], "instrument_type": ""}).status_code == 422


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
    assert instrument == {**BASE, **dict.fromkeys(EVIDENCE), "status": PENDING, "id": instrument["id"], "version": 1,
                          "kind": "Средство измерений", "parent_id": None, "serial_number": "", "manufacturer": "",
                          "doc_designation": "", "quantity": 1, "storage_location": "", "warehouse_grn": "", "warehouse_writeoff_id": None, "files": []}
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


def create(client, number="СИ.001", **extra):
    result = client.post("/api/production/instruments", json={**BASE, "inventory_number": number, "status": PENDING, **extra})
    assert result.status_code == 200, result.text
    return result.json()["instrument"]


def test_patch_verification_renumbering_and_version_conflict(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client, "ОБ.040")
    url = f"/api/production/instruments/{item['id']}"
    response = client.patch(url, json={"version": 1, **EVIDENCE, "inventory_number": "СИ.001",
                                      "serial_number": "ЗН-77", "manufacturer": "Завод", "doc_designation": "ШЦ-I-125",
                                      "storage_location": "ОТК", "warehouse_grn": "ГРН-1"})
    assert response.status_code == 200, response.text
    updated = response.json()["instrument"]
    assert updated["status"] == "Действует" and updated["version"] == 2
    assert updated["inventory_number"] == "СИ.001" and updated["serial_number"] == "ЗН-77"
    stale = client.patch(url, json={"version": 1, "name": "Потерянная правка"})
    assert stale.status_code == 409
    inactive = client.patch(url, json={"version": 2, "status": "Не используется"})
    assert inactive.status_code == 200 and inactive.json()["instrument"]["version"] == 3
    assert client.get("/api/production/instruments").json()["instruments"] == [inactive.json()["instrument"]]
    with app.state.production_uow_factory() as uow:
        events = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.instrument_updated")).all()
        assert len(events) == 2


def test_parent_tree_rules_and_calibration_kit(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    parent = create(client)
    kit = create(client, "СИ.002", parent_id=parent["id"])
    accessory = create(client, "СИ.003", parent_id=parent["id"], kind="Принадлежность", status="Действует", quantity=5)
    url = f"/api/production/instruments/{parent['id']}"
    for changes in ({"parent_id": parent["id"]}, {"parent_id": kit["id"]}, {"parent_id": accessory["id"]},
                    {"parent_id": str(uuid4())}, {"kind": "Принадлежность", "parent_id": kit["id"], "status": "Действует"}):
        response = client.patch(url, json={"version": 1, **changes})
        assert response.status_code == 422, response.text
    detached = client.patch(f"/api/production/instruments/{kit['id']}", json={"version": 1, "parent_id": None})
    assert detached.status_code == 200 and detached.json()["instrument"]["parent_id"] is None


@pytest.mark.parametrize("changes", [
    {"name": None}, {"kind": None}, {"quantity": None}, {"status": None}, {"serial_number": None},
    {"quantity": 2}, {"quantity": 1.5}, {"quantity": True}, {"manufacturer": "x" * 256},
    {"verification_number": "ПВ"}, {"status": "Действует"}, {"unknown": 1},
])
def test_invalid_patch_does_not_change_version_or_audit(production_api, changes):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client)
    response = client.patch(f"/api/production/instruments/{item['id']}", json={"version": 1, **changes})
    assert response.status_code == 422, response.text
    assert client.get("/api/production/instruments").json()["instruments"] == [item]
    with app.state.production_uow_factory() as uow:
        assert not uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.instrument_updated")).all()


@pytest.mark.parametrize("changes", [
    {"parent_id": None}, {"status": PENDING}, {"verification_number": "ПВ"},
    {"verified_at": EVIDENCE["verified_at"]}, {"verification_due": EVIDENCE["verification_due"]}, {"quantity": 0},
])
def test_accessory_registration_rejects_invalid_fields(production_api, changes):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    parent = create(client)
    response = client.post("/api/production/instruments", json={**BASE, "kind": "Принадлежность", "parent_id": parent["id"], **changes})
    assert response.status_code == 422, response.text


def test_next_number_ignores_legacy_and_malformed_numbers_and_does_not_reuse_gaps(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    url = "/api/production/instruments/next-number"
    assert client.get(url).json() == {"inventory_number": "СИ.001"}
    for number in ("ВАЦ-01", "ОБ.086", "СИ-099", "СИ.99", "СИ.008х", "СИ.002", "СИ.007"):
        create(client, number)
    assert client.get(url).json() == {"inventory_number": "СИ.008"}
    create(client, "СИ.999")
    assert client.get(url).json() == {"inventory_number": "СИ.1000"}
    create(client, "СИ.1000")
    assert client.get(url).json() == {"inventory_number": "СИ.1001"}


def test_repository_compare_and_swap_rejects_two_updates_from_same_version(production_api):
    from app.modules.production.application import ConcurrentProductionUpdate

    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client)
    factory = app.state.production_uow_factory
    with factory() as uow:
        stale = uow.instruments.get(UUID(item["id"]))
    assert client.patch(f"/api/production/instruments/{item['id']}", json={"version": 1, "name": "Правка 1"}).status_code == 200
    with pytest.raises(ConcurrentProductionUpdate), factory() as uow:
        uow.instruments.save(replace(stale, version=2, name="Правка 2"))
        uow.commit()
    assert client.get("/api/production/instruments").json()["instruments"][0]["name"] == "Правка 1"


def test_patch_requires_quality_permission(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = create(client)
    _as({"production": "edit"})
    assert client.patch(f"/api/production/instruments/{item['id']}", json={"version": 1, "name": "Правка"}).status_code == 403
