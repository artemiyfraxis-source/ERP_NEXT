"""Own serial assignment preserves vendor identifiers and never partially changes the registry."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import ProductionInstrumentRow, SqlAlchemyInstrumentRepository
from app.modules.production.instrument_serials import (
    InstrumentSerialContext, article_of, assign_in_house_serial, in_house_serial_number, is_in_house_serial,
)
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import RfDetails, RfInterface
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
        id=uuid4(), version=1, inventory_number=number, name="Переходник SMA female–SMA female S-322",
        instrument_type="Принадлежность", verification_number=None, verified_at=None, verification_due=None,
        status=InstrumentStatus.ACTIVE, kind=InstrumentKind.ACCESSORY, serial_number=serial,
    )


def create(client, number="СИ.035", serial=""):
    _as({"quality": "edit", "production": "view"})
    response = client.post("/api/production/instruments", json={
        "inventory_number": number, "name": "Переходник SMA female–SMA female S-322", "kind": "Принадлежность", "serial_number": serial,
    })
    assert response.status_code == 200, response.text
    return response.json()["instrument"]


def sessions():
    return app.state.production_uow_factory().session_factory


@pytest.mark.parametrize("name,model,article", [
    ("Переходник BNC male–BNC male B-311", "", "B-311"),
    ("Прямой переходник SMA–SMA S-312", "", "S-312"),
    ("ВЧ-переходник RP-SMA female–RP-SMA female S-AA322", "", "S-AA322"),
    ("Переход коаксиальный ADP1A-35M-35F (3,5 мм вилка – 3,5 мм розетка)", "", "ADP1A-35M-35F"),
    ("Рулетка измерительная (модель уточняется)", "", None),
    ("Мультиметр цифровой", "Fluke-87V", "Fluke-87V") if False else ("Мультиметр цифровой", "UT61E", "UT61E"),
])
def test_article_comes_from_the_name_then_the_model(name, model, article):
    assert article_of(replace(instrument(), name=name, doc_designation=model)) == article


def test_serial_is_article_dot_next_number_after_taken():
    item = instrument()
    assert in_house_serial_number(item, now=NOW) == "S-322.1"
    taken = frozenset({"S-322.1", "S-322.3", "S-3220.9", "N8B139"})
    assert in_house_serial_number(item, now=NOW, context=InstrumentSerialContext(taken=taken)) == "S-322.4"


def test_item_without_article_needs_a_manual_serial():
    with pytest.raises(ProductionValidationError, match="артикула"):
        in_house_serial_number(replace(instrument(), name="Рулетка измерительная"), now=NOW)


@pytest.mark.parametrize("serial,expected", [("ФР-035", True), ("ADP-ss-26-035", True), ("SI-26-001", True),
                                             ("S-322.1", False), ("N8B139", False), ("", False)])
def test_earlier_in_house_schemes_are_recognised(serial, expected):
    assert is_in_house_serial(serial) is expected


@pytest.mark.parametrize("serial", ["N8B139", "S-322.7"])
def test_vendor_or_article_serial_is_never_overwritten(serial):
    item = instrument(serial=serial)
    assert assign_in_house_serial(item, now=NOW) is item


def test_earlier_in_house_serial_is_replaced():
    assert assign_in_house_serial(instrument(serial="ADP-ss-26-035"), now=NOW).serial_number == "S-322.1"


def test_assign_endpoint_version_audit_and_idempotence(production_api):
    client, _ = production_api
    item = create(client)
    assert item["suggested_serial_number"] == "S-322.1"
    url = f"/api/production/instruments/{item['id']}/serial"
    result = client.post(url, json={"version": 1})
    assert result.status_code == 200, result.text
    assigned = result.json()["instrument"]
    assert assigned["serial_number"] == "S-322.1"
    assert assigned["version"] == 2
    assert assigned["suggested_serial_number"] is None
    assert client.post(url, json={"version": 1}).status_code == 409
    assert client.post(url, json={"version": 2}).json()["instrument"] == assigned
    with sessions()() as session:
        events = list(session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.instrument_serial_assigned")))
        assert len(events) == 1
        assert events[0].aggregate_id == UUID(item["id"])


@pytest.mark.parametrize("source,year", [("registered", 2024), ("first_audit", 2022), ("none", 2026)])
def test_api_cli_and_assignment_share_audit_year_and_structured_ends(production_api, source, year):
    client, _ = production_api
    item = create(client)
    item_id = UUID(item["id"])
    with sessions().begin() as session:
        registration = session.scalar(select(AuditEventRow).where(AuditEventRow.aggregate_id == item_id))
        if source == "none":
            session.delete(registration)
        else:
            registration.occurred_at = datetime(2024, 1, 2)
            if source == "first_audit":
                registration.event_type = "production.instrument_updated"
            session.add(AuditEventRow(id=uuid4(), aggregate_id=item_id,
                event_type="production.instrument_imported", actor_id=ACTOR.id, actor_name=ACTOR.name,
                actor_department=ACTOR.department, occurred_at=datetime(2022, 3, 4), details={}))
        RfRepository(session).save(item_id, RfDetails((RfInterface("N", "вилка", "штырь"),
                                                      RfInterface("SMA", "розетка", "гнездо"))))
    expected = "S-322.1"  # the article decides; dates and ends no longer do
    rows = client.get("/api/production/instruments").json()["instruments"]
    assert rows[0]["suggested_serial_number"] == expected
    edited = client.patch(f"/api/production/instruments/{item_id}", json={"version": 1, "storage_location": "ОТК"})
    assert edited.json()["instrument"]["suggested_serial_number"] == expected
    assert assign_serials(sessions(), actor=ACTOR, now=NOW)[0]["serial_number"] == expected
    assigned = client.post(f"/api/production/instruments/{item_id}/serial", json={"version": 2})
    assert assigned.json()["instrument"]["serial_number"] == expected


def test_rf_save_returns_the_updated_card_suggestion(production_api):
    client, _ = production_api
    item = create(client)
    response = client.put(f"/api/production/instruments/{item['id']}/rf-interfaces", json={
        "version": 0, "role": "adapter", "interfaces": [
            {"series": "RP-SMA", "body": "розетка", "center": "штырь"},
            {"series": "SMA", "body": "розетка", "center": "гнездо"}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["suggested_serial_number"] == "S-322.1"
    assigned = client.post(f"/api/production/instruments/{item['id']}/serial", json={"version": 1})
    assert assigned.json()["instrument"]["serial_number"] == response.json()["suggested_serial_number"]


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
    item = client.patch(f"/api/production/instruments/{item['id']}", json={"version": 1, "name": "Рулетка"}).json()["instrument"]
    assert item["suggested_serial_number"] is None
    assert client.get("/api/production/instruments").status_code == 200
    response = client.post(f"/api/production/instruments/{item['id']}/serial", json={"version": 2})
    assert response.status_code == 422
    assert "артикула" in response.json()["detail"]


def test_serial_collision_is_a_user_error_for_registration_edit_and_assignment(production_api):
    client, _ = production_api
    first = create(client, "СИ.034", "S-322.1")
    second = create(client)
    duplicate = client.post("/api/production/instruments", json={
        "inventory_number": "СИ.036", "name": "Переходник", "kind": "Принадлежность", "serial_number": "S-322.1"})
    assert duplicate.status_code == 422
    assert "номером уже есть" in duplicate.json()["detail"]
    url = f"/api/production/instruments/{second['id']}"
    assert client.patch(url, json={"version": 1, "serial_number": first["serial_number"]}).status_code == 422
    with sessions()() as session:
        assert session.get(ProductionInstrumentRow, UUID(second["id"])).serial_number == ""
    # Assignment skips the taken number instead of colliding with it.
    assert client.post(f"{url}/serial", json={"version": 1}).json()["instrument"]["serial_number"] == "S-322.2"


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
    assert proposed == [{"id": own["id"], "inventory_number": "СИ.035", "serial_number": "S-322.1", "previous": ""}]
    with sessions()() as session:
        assert session.get(ProductionInstrumentRow, UUID(own["id"])).serial_number == ""
    assert assign_serials(sessions(), apply=True, actor=ACTOR, now=NOW) == proposed
    assert assign_serials(sessions(), apply=True, actor=ACTOR, now=NOW) == []
    with sessions()() as session:
        assert session.get(ProductionInstrumentRow, UUID(own["id"])).version == 2
        assert session.get(ProductionInstrumentRow, UUID(vendor["id"])).serial_number == "N8B139"
        assert len(list(session.scalars(select(AuditEventRow).where(
            AuditEventRow.event_type == "production.instrument_serial_assigned")))) == 1


def test_cli_numbers_each_article_in_inventory_order_and_replaces_earlier_scheme(production_api):
    client, _ = production_api
    second = create(client, "СИ.036")
    first = create(client, "СИ.034", "ADP-ss-26-034")
    kept = create(client, "СИ.037", "S-322.5")
    changes = {row["inventory_number"]: row["serial_number"] for row in assign_serials(sessions(), apply=True, actor=ACTOR, now=NOW)}
    assert changes == {"СИ.034": "S-322.6", "СИ.036": "S-322.7"}
    assert assign_serials(sessions(), apply=True, actor=ACTOR, now=NOW) == []
    with sessions()() as session:
        assert session.get(ProductionInstrumentRow, UUID(kept["id"])).serial_number == "S-322.5"


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


def test_cli_prints_inventory_to_new_serial(monkeypatch, capsys):
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr("app.workflows.instrument_serials.assign_serials", lambda *args, **kwargs: [
        {"inventory_number": "СИ.034", "serial_number": "S-322.1", "previous": "ADP-ss-26-034"}])
    main([])
    assert "СИ.034: ADP-ss-26-034 → S-322.1" in capsys.readouterr().out
