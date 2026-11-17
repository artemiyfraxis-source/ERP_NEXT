"""Own serial assignment preserves vendor identifiers and never partially changes the registry."""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import ProductionInstrumentRow, SqlAlchemyInstrumentRepository
from app.modules.production.instrument_serials import assign_in_house_serial, in_house_serial_number
from app.modules.production.traceability import InstrumentKind, InstrumentStatus, MeasurementInstrument
from app.shared.persistence import AuditEventRow
from app.workflows.instrument_serials import assign_serials, main
from tests.factories import Actor
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_instruments_api import _as


NOW = datetime(2026, 11, 17, 12)
ACTOR = Actor(name="Контролёр", department="ОТК")


def instrument(number="СИ.035", serial=""):
    return MeasurementInstrument(
        id=uuid4(), version=1, inventory_number=number, name="SMA розетка – SMA розетка",
        instrument_type="Принадлежность", verification_number=None, verified_at=None, verification_due=None,
        status=InstrumentStatus.ACTIVE, kind=InstrumentKind.ACCESSORY, serial_number=serial,
    )


def create(client, number="СИ.035", serial=""):
    _as({"quality": "edit", "production": "view"})
    response = client.post("/api/production/instruments", json={
        "inventory_number": number, "name": "Переходник SMA", "kind": "Принадлежность", "serial_number": serial,
    })
    assert response.status_code == 200, response.text
    return response.json()["instrument"]


def sessions():
    return app.state.production_uow_factory().session_factory


@pytest.mark.parametrize("number,serial", [("СИ.035", "ФР-035"), ("СИ.0035", "ФР-0035"), ("ОБ.104", "ФР-104")])
def test_own_serial_preserves_numeric_suffix(number, serial):
    item = instrument(number)
    assert in_house_serial_number(number) == serial
    assert assign_in_house_serial(item).serial_number == serial
    assert item.serial_number == ""


@pytest.mark.parametrize("serial", ["N8B139", "ФР-012"])
def test_existing_serial_is_never_overwritten(serial):
    item = instrument(serial=serial)
    assert assign_in_house_serial(item) is item


def test_measuring_item_can_receive_own_serial_without_changing_verification():
    item = replace(instrument(), kind=InstrumentKind.MEASURING, status=InstrumentStatus.UNVERIFIED)
    assigned = assign_in_house_serial(item)
    assert assigned.serial_number == "ФР-035"
    assert assigned.verification_due is None
    assert assigned.status is InstrumentStatus.UNVERIFIED


def test_inventory_without_numeric_suffix_requires_manual_serial():
    with pytest.raises(ProductionValidationError, match="числовой части"):
        assign_in_house_serial(instrument("Без номера"))
    vendor = instrument("Без номера", "VENDOR")
    assert assign_in_house_serial(vendor) is vendor


def test_assign_endpoint_version_audit_and_idempotence(production_api):
    client, _ = production_api
    item = create(client)
    assert item["suggested_serial_number"] == "ФР-035"
    url = f"/api/production/instruments/{item['id']}/serial"
    result = client.post(url, json={"version": 1})
    assert result.status_code == 200, result.text
    assigned = result.json()["instrument"]
    assert assigned["serial_number"] == "ФР-035"
    assert assigned["version"] == 2
    assert assigned["suggested_serial_number"] is None
    assert client.post(url, json={"version": 1}).status_code == 409
    assert client.post(url, json={"version": 2}).json()["instrument"] == assigned
    with sessions()() as session:
        events = list(session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.instrument_serial_assigned")))
        assert len(events) == 1
        assert events[0].aggregate_id == UUID(item["id"])


@pytest.mark.parametrize("permissions", [{"quality": "view", "production": "view"}, {"production": "edit"}])
def test_assignment_requires_registry_edit_access(production_api, permissions):
    client, _ = production_api
    item = create(client)
    _as(permissions)
    response = client.post(f"/api/production/instruments/{item['id']}/serial", json={"version": 1})
    assert response.status_code == 403


def test_assignment_preserves_vendor_and_rejects_unknown_or_invalid(production_api):
    client, _ = production_api
    item = create(client, serial="N8B139")
    assert client.post(f"/api/production/instruments/{item['id']}/serial", json={"version": 1}).json()["instrument"] == item
    assert client.post(f"/api/production/instruments/{uuid4()}/serial", json={"version": 1}).status_code == 404
    assert client.post(f"/api/production/instruments/{item['id']}/serial", json={"version": 0}).status_code == 422


def test_legacy_inventory_without_numeric_suffix_stays_readable(production_api):
    client, _ = production_api
    item = create(client, "Без номера")
    assert item["suggested_serial_number"] is None
    assert client.get("/api/production/instruments").status_code == 200
    response = client.post(f"/api/production/instruments/{item['id']}/serial", json={"version": 1})
    assert response.status_code == 422
    assert "числовой части" in response.json()["detail"]


def test_serial_collision_is_a_user_error_for_registration_edit_and_assignment(production_api):
    client, _ = production_api
    first = create(client, "СИ.034", "ФР-035")
    second = create(client)
    duplicate = client.post("/api/production/instruments", json={
        "inventory_number": "СИ.036", "name": "Переходник", "kind": "Принадлежность", "serial_number": "ФР-035"})
    assert duplicate.status_code == 422
    assert "номером уже есть" in duplicate.json()["detail"]
    url = f"/api/production/instruments/{second['id']}"
    assert client.patch(url, json={"version": 1, "serial_number": first["serial_number"]}).status_code == 422
    assert client.post(f"{url}/serial", json={"version": 1}).status_code == 422
    with sessions()() as session:
        assert session.get(ProductionInstrumentRow, UUID(second["id"])).serial_number == ""


def test_database_index_protects_nonempty_serials_and_allows_empty(production_api):
    client, _ = production_api
    create(client, "СИ.033", "N8B139")
    create(client, "СИ.034")
    create(client, "СИ.035")
    with pytest.raises(IntegrityError), sessions().begin() as session:
        SqlAlchemyInstrumentRepository(session).save(instrument("СИ.036", "N8B139"))


def test_cli_preview_apply_and_repeat(production_api):
    client, _ = production_api
    own = create(client)
    vendor = create(client, "СИ.036", "N8B139")
    proposed = assign_serials(sessions(), actor=ACTOR, now=NOW)
    assert proposed == [{"id": own["id"], "inventory_number": "СИ.035", "serial_number": "ФР-035"}]
    with sessions()() as session:
        assert session.get(ProductionInstrumentRow, UUID(own["id"])).serial_number == ""
    assert assign_serials(sessions(), apply=True, actor=ACTOR, now=NOW) == proposed
    assert assign_serials(sessions(), apply=True, actor=ACTOR, now=NOW) == []
    with sessions()() as session:
        assert session.get(ProductionInstrumentRow, UUID(own["id"])).version == 2
        assert session.get(ProductionInstrumentRow, UUID(vendor["id"])).serial_number == "N8B139"
        assert len(list(session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.instrument_serial_assigned")))) == 1


def test_cli_reports_projected_duplicates_before_any_write(production_api):
    client, _ = production_api
    first = create(client, "СИ.034")
    second = create(client, "ОБ.034")
    for apply in (False, True):
        with pytest.raises(ProductionValidationError, match="ФР-034"):
            assign_serials(sessions(), apply=apply, actor=ACTOR, now=NOW)
    with sessions()() as session:
        assert all(session.get(ProductionInstrumentRow, UUID(item["id"])).serial_number == ""
                   for item in (first, second))


def test_cli_reports_existing_duplicates_from_legacy_database(production_api):
    client, _ = production_api
    first = create(client, "СИ.034", "VENDOR")
    with sessions().begin() as session:
        session.execute(text("DROP INDEX uq_production_instruments_serial_number"))
        SqlAlchemyInstrumentRepository(session).save(instrument("СИ.036", "VENDOR"))
    with pytest.raises(ProductionValidationError, match="VENDOR: СИ.034, СИ.036"):
        assign_serials(sessions(), apply=True, actor=ACTOR, now=NOW)
    with sessions()() as session:
        assert session.get(ProductionInstrumentRow, UUID(first["id"])).version == 1


def test_cli_defaults_to_dry_run(monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr("app.workflows.instrument_serials.assign_serials", lambda sessions, **kwargs: calls.append(kwargs) or [])
    main([])
    assert calls[0]["apply"] is False
    assert "Будет назначено: 0" in capsys.readouterr().out
