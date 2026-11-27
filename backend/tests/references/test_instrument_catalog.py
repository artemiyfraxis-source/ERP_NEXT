"""The reference tab and legacy UUIDs read and edit one canonical registry."""

from datetime import datetime
from uuid import UUID

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, select

from app.database import build_references_uow_factory, build_production_uow_factory
from app.main import app
from app.modules.references.infrastructure import EquipmentMeasuringRow, SqlAlchemyTechOperationRepository
from app.modules.references.domain import ReferenceValidationError, TechOperationTab
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from app.shared import bulk_import
from app.shared.bulk_import import ImportRateLimiter
from tests.factories import Actor
from tests.production.test_instrument_catalog_cutover import (  # noqa: F401
    baseline, database, config, MANIFEST, REVISION,
)

ROOT = "/api/references"
ACTOR = Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Редактор", department="ОТК")


def as_user(permissions, *, service=False):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="catalog@example.test", roles=frozenset(), permissions=permissions,
        service=service, scopes=frozenset({"refs:read", "refs:write"}) if service else frozenset())


@pytest.fixture
def catalog_api(database, monkeypatch):
    monkeypatch.setattr(bulk_import, "RATE_LIMITER", ImportRateLimiter())
    # The API runs today's ORM, so the schema is the head, not the cutover revision (later migrations add columns).
    command.upgrade(config(database), "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    old = {key: getattr(app.state, key, None) for key in ("references_uow_factory", "production_uow_factory")}
    app.state.references_uow_factory = build_references_uow_factory(engine)
    app.state.production_uow_factory = build_production_uow_factory(engine)
    as_user({"erpref": "edit", "quality": "edit", "production": "view"})
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 11, 23, 12)
    yield TestClient(app)
    for key, value in old.items():
        if value is None:
            delattr(app.state, key)
        else:
            setattr(app.state, key, value)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    engine.dispose()


def first(client):
    return client.get(ROOT + "/instruments").json()["instruments"][0]


def test_all_118_rows_are_canonical_and_legacy_ids_project_live_values(catalog_api):
    client = catalog_api
    tab = client.get(ROOT + "/tech-operations?tab=ob_measuring").json()
    canonical = client.get(ROOT + "/instruments").json()
    quality = client.get("/api/production/instruments").json()
    assert len(tab["records"]) == canonical["count"] == canonical["quantity"] == 118
    assert {row["id"] for row in tab["records"]} == {row["id"] for row in quality["instruments"]}
    entry = MANIFEST["entries"][0]
    response = client.get(ROOT + "/instruments/" + entry["unit_id"])
    assert response.status_code == 200, response.text
    item = response.json()["instrument"]
    changed = client.patch(ROOT + "/instruments/" + entry["unit_id"], json={
        "version": item["version"], "name": "Имя из реестра", "article": "", "manufacturer": ""})
    assert changed.status_code == 200, changed.text
    with app.state.references_uow_factory() as uow:
        legacy = uow.tech_operations.get(UUID(entry["unit_id"]))
        assert legacy.id == UUID(entry["unit_id"])
        assert legacy.fields["Наименование"] == legacy.fields["obName"] == "Имя из реестра"
        assert legacy.fields["Артикул"] == legacy.fields["Производитель"] == ""
        assert legacy.equipment_model_id is not None
        raw = uow.session.get(EquipmentMeasuringRow, UUID(entry["unit_id"]))
        assert raw.fields == next(source["measuring"]["fields"] for source in MANIFEST["baseline"]["measuring_units"]
                                  if source["measuring"]["id"] == entry["unit_id"])


@pytest.mark.parametrize("permissions,service,status", [
    ({"erpref": "view"}, False, 403), ({"erpref": "edit"}, False, 403),
    ({"erpref": "edit", "quality": "view"}, False, 403),
    ({}, True, 403), ({"erpref": "edit", "quality": "edit"}, False, 200),
    ({"erpref": "view", "quality": "edit"}, False, 200),
])
def test_identity_requires_quality_edit(catalog_api, permissions, service, status):
    item = first(catalog_api)
    as_user(permissions, service=service)
    result = catalog_api.patch(ROOT + "/instruments/" + item["id"], json={"version": item["version"], "name": "Новое имя"})
    assert result.status_code == status, result.text


def test_reference_view_is_scoped_and_denied_user_has_no_access(catalog_api):
    as_user({"erpref": "view"})
    response = catalog_api.get(ROOT + "/instruments")
    assert response.status_code == 200 and len(response.json()["instruments"]) == 118
    assert response.json()["can_edit_identity"] is False
    assert catalog_api.get("/api/production/instruments").status_code == 403
    as_user({})
    assert catalog_api.get(ROOT + "/instruments").status_code == 403


@pytest.mark.parametrize("mode,identity", [("view", False), ("edit", True)])
def test_quality_alone_opens_the_registry_tab_without_reference_access(catalog_api, mode, identity):
    """ОТК-1: the registry is a «База данных» tab; a person with only «ОТК» reads it (edit: also the passport)."""
    as_user({"quality": mode})
    item = first(catalog_api)
    listed = catalog_api.get(ROOT + "/instruments").json()
    assert len(listed["instruments"]) == 118
    assert listed["can_edit_identity"] is identity and listed["can_edit_custom"] is False
    assert catalog_api.get(ROOT + "/instruments/" + item["id"]).status_code == 200
    assert catalog_api.get(ROOT + "/instruments/" + item["id"] + "/history").status_code == 200
    # Other reference books stay closed to «ОТК» alone.
    assert catalog_api.get(ROOT + "/maps").status_code == 403
    as_user({"production": "edit"})
    assert catalog_api.get(ROOT + "/instruments").status_code == 403


def test_custom_columns_are_independent_versioned_and_journaled(catalog_api):
    client = catalog_api
    as_user({"erpref": "edit"})
    payload = {"tab": "ob_measuring", "name": "Место"}
    added = client.post(ROOT + "/tech-operations/columns", json=payload)
    assert added.status_code == 201 and added.json()["records"] == 118, added.text
    item = first(client)
    edit = {"version": item["version"], "custom_fields": {**item["custom_fields"], "Место": "Полка 2"}}
    result = client.patch(ROOT + "/instruments/" + item["id"], json=edit)
    assert result.status_code == 200, result.text
    assert client.patch(ROOT + "/instruments/" + item["id"], json=edit).status_code == 409
    assert "Место" not in client.get(ROOT + "/tech-operations?tab=ob").json()["custom_columns"]
    assert client.post(ROOT + "/tech-operations/columns/remove", json=payload).status_code == 200
    item = client.get(ROOT + "/instruments/" + item["id"]).json()["instrument"]
    assert "Место" not in item["custom_fields"]
    with app.state.references_uow_factory() as uow:
        removal = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "references.column_removed")).one()
        assert any(value["value"] == "Полка 2" for value in removal.details["values"])
        events = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.aggregate_id == UUID(item["id"]))).all()
        assert any(row.details.get("before", {}).get("custom_fields", {}).get("Место") == "Полка 2" for row in events)


def test_legacy_create_clone_delete_move_and_identity_edits_are_closed(catalog_api):
    client = catalog_api
    entry = MANIFEST["entries"][0]
    assert client.post(ROOT + "/tech-operations", json={"tab": "ob_measuring", "fields": {
        "Наименование": "Дубликат", "Инв. номер": "СИ.999"}}).status_code == 422
    assert client.delete(ROOT + "/equipment/" + entry["unit_id"]).status_code in {409, 422}
    assert client.delete(ROOT + "/equipment/" + entry["instrument_id"]).status_code in {409, 422}
    response = client.patch(ROOT + "/tech-operations/" + entry["unit_id"], json={"version": 1,
        "edits": [{"field": "Наименование", "old": "", "value": "Нельзя"}]})
    assert response.status_code == 409
    with app.state.references_uow_factory() as uow:
        raw = SqlAlchemyTechOperationRepository(uow.session)
        row = raw.get(UUID(entry["unit_id"]))
        with pytest.raises(ReferenceValidationError):
            raw.save(row)
        with pytest.raises(ReferenceValidationError):
            raw.delete(row.id)


def test_history_combines_legacy_events_without_rewriting_them(catalog_api):
    entry = MANIFEST["entries"][0]
    result = catalog_api.get(ROOT + "/instruments/" + entry["unit_id"] + "/history")
    assert result.status_code == 200, result.text
    assert result.json()["instrument_id"] == entry["instrument_id"]
    assert any(row["event_type"] == "production.instrument_catalog_migrated" for row in result.json()["events"])


def test_shared_inactive_accessory_stays_unbound_and_quantity_is_counted_once(catalog_api):
    from app.modules.production.quality import RegisterInstrumentCommand
    from app.modules.production.traceability import InstrumentKind, InstrumentStatus
    with app.state.references_uow_factory() as uow:
        item = uow.instrument_catalog.catalog.register(RegisterInstrumentCommand(
            inventory_number="ПР.999", name="Комплект переходников", instrument_type="Принадлежность",
            verification_number=None, verified_at=None, verification_due=None, kind=InstrumentKind.ACCESSORY,
            status=InstrumentStatus.INACTIVE, quantity=7, actor=ACTOR, now=datetime(2026, 11, 23), source="references"))
        uow.commit()
    result = catalog_api.get(ROOT + "/instruments").json()
    assert result["count"] == 119 and result["quantity"] == 125
    saved = next(row for row in result["instruments"] if row["id"] == str(item.id))
    assert saved["parent_id"] is None and saved["legacy_unit_id"] is None and saved["quantity"] == 7
    tab = catalog_api.get(ROOT + "/tech-operations?tab=ob_measuring").json()
    assert sum(row["id"] == str(item.id) for row in tab["records"]) == 1


def test_reference_view_reads_files_but_cannot_mutate_them(catalog_api):
    from uuid import uuid4
    from app.modules.identity.infrastructure import IdentityUserRow
    from app.modules.production.infrastructure import ProductionInstrumentFileRow
    item = first(catalog_api)
    file_id = uuid4()
    content = b"%PDF-1.7\nscoped passport"
    with app.state.references_uow_factory() as uow:
        uow.session.add(IdentityUserRow(id=ACTOR.id, login="catalog", email="catalog@example.test",
            name=ACTOR.name, department="ОТК", role="master", access_level="custom"))
        uow.session.flush()
        uow.session.add(ProductionInstrumentFileRow(id=file_id, instrument_id=UUID(item["id"]), kind="Паспорт",
            file_name="паспорт.pdf", content=content, uploaded_at=datetime(2026, 11, 23), uploaded_by=ACTOR.id))
        uow.commit()
    as_user({"erpref": "view"})
    data = catalog_api.get(ROOT + "/instruments/" + item["id"]).json()["instrument"]
    assert [file["id"] for file in data["files"]] == [str(file_id)]
    response = catalog_api.get(ROOT + "/instruments/" + item["id"] + "/files/" + str(file_id))
    assert response.status_code == 200 and response.content == content
    assert catalog_api.delete("/api/production/instruments/" + item["id"] + "/files/" + str(file_id)).status_code == 403
    assert catalog_api.get(ROOT + "/instruments/" + str(uuid4()) + "/files/" + str(file_id)).status_code == 404


def test_scoped_serial_suggestion_uses_the_same_taken_numbers_as_quality(catalog_api):
    client = catalog_api
    for number, serial in (("ПР.990", "TEST-322.1"), ("ПР.991", "")):
        result = client.post("/api/production/instruments", json={"inventory_number": number,
            "name": "Переходник TEST-322", "kind": "Принадлежность", "serial_number": serial})
        assert result.status_code == 200, result.text
    item = result.json()["instrument"]
    quality = next(row for row in client.get("/api/production/instruments").json()["instruments"] if row["id"] == item["id"])
    scoped = client.get(ROOT + "/instruments/" + item["id"]).json()["instrument"]
    assert scoped["suggested_serial_number"] == quality["suggested_serial_number"] == "TEST-322.2"
