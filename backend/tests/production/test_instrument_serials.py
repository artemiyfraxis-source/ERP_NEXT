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
from app.modules.production.instrument_serials import InstrumentSerialContext, assign_in_house_serial, in_house_serial_number, is_in_house_serial
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
        id=uuid4(), version=1, inventory_number=number, name="Переходник SMA розетка – SMA розетка",
        instrument_type="Принадлежность", verification_number=None, verified_at=None, verification_due=None,
        status=InstrumentStatus.ACTIVE, kind=InstrumentKind.ACCESSORY, serial_number=serial,
    )


def create(client, number="СИ.035", serial=""):
    _as({"quality": "edit", "production": "view"})
    response = client.post("/api/production/instruments", json={
        "inventory_number": number, "name": "Переходник SMA розетка – SMA розетка", "kind": "Принадлежность", "serial_number": serial,
    })
    assert response.status_code == 200, response.text
    return response.json()["instrument"]


def sessions():
    return app.state.production_uow_factory().session_factory


@pytest.mark.parametrize("name,code", [
    ("Переход SMA", "ADP"), ("Прямой переходник", "ADP"), ("ВЧ-переходник", "ADP"), ("Переходник S-322", "ADP"), ("ADP1A-35F-35F", "ADP"),
    ("Кабель тестовый", "CAB"), ("Комплект калибровочный", "KIT"), ("Набор коаксиальных мер", "KIT"),
    ("Нагрузка согласованная", "LOAD"), ("Модуль калибровочный автоматический", "ACM"),
    ("АКМ ACM2520-111", "ACM"), ("Анализатор цепей", "SI"), ("Отвёртка", "SI"), ("Набор отвёрток", "SI"),
])
def test_type_rules_and_unknown_default(name, code):
    assert in_house_serial_number(replace(instrument(), name=name), now=NOW) == f"{code}-26-035"


def test_type_can_come_from_card_model():
    item = replace(instrument(), name="Калибратор", doc_designation="ACM2520-111")
    assert in_house_serial_number(item, now=NOW) == "ACM-26-035"
    assert in_house_serial_number(replace(item, name="Комплект калибровочный"), now=NOW) == "KIT-26-035"


@pytest.mark.parametrize("series,letter", [("SMA", "S"), ("RP-SMA", "P"), ("N", "N"),
                                        ("BNC", "B"), ("TNC", "T"), ("F", "F"), ("3.5mm", "K")])
@pytest.mark.parametrize("center", ["штырь", "гнездо"])
def test_each_connector_letter_uses_contact_gender(series, letter, center):
    # Even with a female body, a male centre contact must produce an upper-case letter.
    body = "вилка" if series == "SMA" and center == "штырь" else "розетка"
    details = RfDetails((RfInterface(series, body, center),))
    expected = letter if center == "штырь" else letter.lower()
    context = InstrumentSerialContext(rf=details)
    assert in_house_serial_number(instrument(), now=NOW, context=context) == f"ADP-{expected}-26-035"


@pytest.mark.parametrize("name,expected", [
    ("Переходник S-322 SMA розетка–розетка", "ADP-ss-26-035"),
    ("Переходник SMA вилка–SMA розетка", "ADP-Ss-26-035"),
    ("Переходник N вилка–SMA розетка", "ADP-Ns-26-035"),
    ("Переходник RP-SMA вилка–SMA розетка", "ADP-Ps-26-035"),
    ("Переходник RP-SMA male–SMA female", "ADP-ps-26-035"),
    ("Нагрузка SMA розетка", "LOAD-s-26-035"),
    ("Переходник неизвестной серии", "ADP-26-035"),
])
def test_explicit_name_fallback_and_end_order(name, expected):
    assert in_house_serial_number(replace(instrument(), name=name), now=NOW) == expected


def test_known_acm_fallback():
    item = replace(instrument("СИ.104"), name="Модуль калибровочный ACM2520-111")
    assert in_house_serial_number(item, now=NOW) == "ACM-kk-26-104"


def test_structured_data_overrides_name_and_does_not_guess_missing_ends():
    item = instrument()
    unknown = RfInterface("тип III", "вилка", "штырь")
    female_n = RfInterface("N", "розетка", "гнездо")
    for ports, segment in [((), ""), ((unknown,), ""), ((unknown, female_n), "-n"),
                           ((female_n, unknown), "-n"), ((female_n, female_n), "-nn")]:
        context = InstrumentSerialContext(rf=RfDetails(ports))
        assert in_house_serial_number(item, now=NOW, context=context) == f"ADP{segment}-26-035"


def test_reversed_sma_polarity_uses_p_and_contact_gender():
    ports = (RfInterface("SMA", "розетка", "штырь"), RfInterface("SMA", "вилка", "гнездо"))
    assert in_house_serial_number(instrument(), now=NOW, context=InstrumentSerialContext(rf=RfDetails(ports))) == "ADP-Pp-26-035"


def test_creation_date_wins_over_assignment_year_and_length_is_bounded():
    assert in_house_serial_number(instrument(), now=NOW,
                                  context=InstrumentSerialContext(datetime(2024, 12, 31))) == "ADP-ss-24-035"
    with pytest.raises(ProductionValidationError, match="128"):
        in_house_serial_number(instrument("СИ." + "9" * 128), now=NOW)


def test_creation_year_uses_the_erp_timezone_at_new_year():
    created = datetime(2024, 12, 31, 22, tzinfo=UTC)
    context = InstrumentSerialContext(created_at=created)
    assert in_house_serial_number(instrument(), now=NOW, context=context) == "ADP-ss-25-035"


@pytest.mark.parametrize("serial,expected", [("ADP-ss-26-034", True), ("LOAD-s-26-110", True),
    ("ACM-kk-26-104", True), ("SI-26-1000", True), ("CAB-NS-26-001", True), ("KIT-26-003", True),
    ("ФР-034", True), ("N8B139", False), ("26030095", False), ("ADP-26-34", False),
    ("ADP-sss-26-034", False), ("VENDOR-ss-26-034", False), ("ADP-ss-2026-034", False),
    ("ADP-ss-26-034-extra", False), ("ADP-s1-26-034", False)])
def test_in_house_recognition(serial, expected):
    assert is_in_house_serial(serial) is expected


@pytest.mark.parametrize("number,serial", [("СИ.035", "ADP-ss-26-035"), ("СИ.0035", "ADP-ss-26-035"), ("СИ.7", "ADP-ss-26-007"), ("СИ.1000", "ADP-ss-26-1000"), ("ОБ.104", "ADP-ss-26-104")])
def test_own_serial_preserves_numeric_suffix(number, serial):
    item = instrument(number)
    assert in_house_serial_number(item, now=NOW) == serial
    assert assign_in_house_serial(item, now=NOW).serial_number == serial
    assert item.serial_number == ""


@pytest.mark.parametrize("serial", ["N8B139", "26030095", "ФР-012", "ADP-ss-26-012"])
def test_existing_serial_is_never_overwritten(serial):
    item = instrument(serial=serial)
    assert assign_in_house_serial(item, now=NOW) is item


def test_measuring_item_can_receive_own_serial_without_changing_verification():
    item = replace(instrument(), kind=InstrumentKind.MEASURING, status=InstrumentStatus.UNVERIFIED)
    assigned = assign_in_house_serial(item, now=NOW)
    assert assigned.serial_number == "ADP-ss-26-035"
    assert assigned.verification_due is None
    assert assigned.status is InstrumentStatus.UNVERIFIED


def test_inventory_without_numeric_suffix_requires_manual_serial():
    with pytest.raises(ProductionValidationError, match="числовой части"):
        assign_in_house_serial(instrument("Без номера"), now=NOW)
    vendor = instrument("Без номера", "VENDOR")
    assert assign_in_house_serial(vendor, now=NOW) is vendor


def test_assign_endpoint_version_audit_and_idempotence(production_api):
    client, _ = production_api
    item = create(client)
    assert item["suggested_serial_number"] == "ADP-ss-26-035"
    url = f"/api/production/instruments/{item['id']}/serial"
    result = client.post(url, json={"version": 1})
    assert result.status_code == 200, result.text
    assigned = result.json()["instrument"]
    assert assigned["serial_number"] == "ADP-ss-26-035"
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
    expected = f"ADP-Ns-{year % 100:02d}-035"
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
    assert response.json()["suggested_serial_number"] == "ADP-Ps-26-035"
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
    assert item["suggested_serial_number"] is None
    assert client.get("/api/production/instruments").status_code == 200
    response = client.post(f"/api/production/instruments/{item['id']}/serial", json={"version": 1})
    assert response.status_code == 422
    assert "числовой части" in response.json()["detail"]


def test_serial_collision_is_a_user_error_for_registration_edit_and_assignment(production_api):
    client, _ = production_api
    first = create(client, "СИ.034", "ADP-ss-26-035")
    second = create(client)
    duplicate = client.post("/api/production/instruments", json={
        "inventory_number": "СИ.036", "name": "Переходник", "kind": "Принадлежность", "serial_number": "ADP-ss-26-035"})
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
    assert proposed == [{"id": own["id"], "inventory_number": "СИ.035", "serial_number": "ADP-ss-26-035"}]
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
        with pytest.raises(ProductionValidationError, match="ADP-ss-26-034"):
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


def test_cli_prints_inventory_to_new_serial(monkeypatch, capsys):
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr("app.workflows.instrument_serials.assign_serials", lambda *args, **kwargs: [
        {"inventory_number": "СИ.034", "serial_number": "ADP-ss-26-034"}])
    main([])
    assert "СИ.034 → ADP-ss-26-034" in capsys.readouterr().out
