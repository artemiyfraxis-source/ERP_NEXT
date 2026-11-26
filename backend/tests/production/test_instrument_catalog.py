"""The catalog facade shares production validation and the caller's transaction."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.production.instrument_catalog import InstrumentCatalog
from app.modules.production.quality import RegisterInstrumentCommand, UpdateInstrumentCommand
from app.modules.production.traceability import InstrumentStatus
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_instruments_api import BASE, PENDING, _as


def test_catalog_fields_roundtrip_version_check_and_audit(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    fields = {"article": "NCT-1", "notes": "Проверить разъёмы", "measurement_category": "Целостность",
              "custom_fields": {"Зона хранения": "Шкаф 1"}}
    response = client.post("/api/production/instruments", json={**BASE, "status": PENDING, **fields})
    assert response.status_code == 200, response.text
    item = response.json()["instrument"]
    assert all(item[key] == value for key, value in fields.items())
    changed = {"article": "NCT-2", "custom_fields": {"Зона хранения": "Шкаф 2"}}
    response = client.patch(f"/api/production/instruments/{item['id']}", json={"version": 1, **changed})
    assert response.status_code == 200, response.text
    assert response.json()["instrument"]["version"] == 2
    assert client.patch(f"/api/production/instruments/{item['id']}", json={"version": 1, "notes": "stale"}).status_code == 409
    from app.main import app
    with app.state.production_uow_factory() as uow:
        catalog = InstrumentCatalog(uow)
        assert [str(row.id) for row in catalog.list()] == [item["id"]]
        row = catalog.get(UUID(item["id"]))
        assert row.article == "NCT-2" and row.notes == fields["notes"]
        events = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.aggregate_id == row.id)).all()
        assert len(events) == 2
        update = next(event for event in events if event.event_type == "production.instrument_updated")
        assert update.details["source"] == "quality"
        assert update.details["before"]["article"] == "NCT-1"
        assert update.details["after"]["custom_fields"] == changed["custom_fields"]


@pytest.mark.parametrize("changes", [
    {"article": "x" * 513}, {"measurement_category": "x" * 256},
    {"article": None}, {"notes": None}, {"measurement_category": None},
    {"custom_fields": None}, {"custom_fields": []}, {"custom_fields": {"x": 1}},
])
def test_invalid_catalog_fields_do_not_partially_save(production_api, changes):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = client.post("/api/production/instruments", json={**BASE, "status": PENDING}).json()["instrument"]
    response = client.patch(f"/api/production/instruments/{item['id']}", json={"version": 1, "name": "changed", **changes})
    assert response.status_code == 422, response.text
    assert client.get("/api/production/instruments").json()["instruments"] == [item]


def test_facade_composes_atomically_and_carries_source_and_legacy_identity(production_api):
    from app.main import app
    factory = app.state.production_uow_factory
    actor = Actor(id=uuid4(), name="Редактор", department="ОТК")
    now, legacy = datetime.now(timezone.utc), uuid4()
    command = RegisterInstrumentCommand(
        inventory_number="TEST.123", name="Тестер", instrument_type="Тестер", status=InstrumentStatus.UNVERIFIED,
        verification_number=None, verified_at=None, verification_due=None, actor=actor, now=now,
        article="NCT-1", source="references", legacy_unit_id=legacy,
    )
    with pytest.raises(RuntimeError, match="outer command failed"):
        with factory() as uow:
            item = InstrumentCatalog(uow).register(command)
            InstrumentCatalog(uow).update(UpdateInstrumentCommand(
                item.id, 1, {"custom_fields": {"Стеллаж": "1"}}, actor, now, "excel", legacy))
            uow.session.flush()
            raise RuntimeError("outer command failed")
    with factory() as uow:
        assert InstrumentCatalog(uow).list() == []
        assert list(uow.session.scalars(select(AuditEventRow))) == []
    with factory() as uow:
        item = InstrumentCatalog(uow).register(command)
        InstrumentCatalog(uow).update(UpdateInstrumentCommand(
            item.id, 1, {"custom_fields": {"Стеллаж": "1"}}, actor, now, "excel", legacy))
        uow.commit()
    with factory() as uow:
        item = InstrumentCatalog(uow).get(item.id)
        assert item.custom_fields == {"Стеллаж": "1"} and item.version == 2
        events = list(uow.session.scalars(select(AuditEventRow)))
        assert {event.details["source"] for event in events} == {"references", "excel"}
        assert all(event.details["legacy_unit_id"] == str(legacy) for event in events)


def test_reference_permission_does_not_grant_catalog_identity_writes(production_api):
    client, _ = production_api
    _as({"erpref": "edit", "production": "view"})
    assert client.post("/api/production/instruments", json={**BASE, "status": PENDING}).status_code == 403
