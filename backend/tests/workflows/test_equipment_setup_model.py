"""Setup model read API and the guarded seed: classes per unit, overrides, online machines, the card part."""

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.equipment_setup.setup_model_infrastructure import (SetupConfigurationRow, SetupMachineCorrectionRow,
                                                                    SetupModelRow, SetupReferenceRow,
                                                                    SetupUnitOverrideRow)
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.machines import MachineLinkRow
from app.modules.references.infrastructure import (EquipmentApplicatorRow, EquipmentMachineRow, EquipmentModelRow,
                                                   EquipmentUnitRow)
from app.modules.identity.infrastructure import IdentityApiTokenRow
from app.shared.persistence import AuditEventRow
from app.workflows.equipment_setup_model import seed
from tests.identity.test_api_tokens import _issue, _login, tokens_api  # noqa: F401

LIST = "/api/production/setup-model"
SEEDED = select(func.count()).select_from(AuditEventRow).where(AuditEventRow.event_type.like("equipment_setup.%"))
HEADER = ("inv", "requires_setup", "param_key", "param_name", "unit", "source", "source_field", "register", "reason",
          "reviewed")


def _model(session):
    model_id = uuid4()
    session.add(EquipmentModelRow(id=model_id))
    session.flush()
    return model_id


def _unit(session, inventory, name, model_id, row=EquipmentMachineRow, catalog="ob"):
    unit_id = uuid4()
    session.add_all([
        EquipmentUnitRow(id=unit_id, catalog=catalog, model_id=model_id, **{f"{catalog}_id": unit_id}),
        row(id=unit_id, tab=catalog, equipment_model_id=model_id, display_text=name, normalized_search="",
            source_sheet="", sort_key=inventory, fields={"Инв. номер": inventory, "Наменование": name}),
    ])
    return unit_id


@pytest.fixture
def setup_db(tokens_api):
    sessions, clock = tokens_api
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    with sessions.begin() as session:
        cutter, strippers, presses, applicators = (_model(session) for _ in range(4))
        units = {
            "ОБ.019": _unit(session, "ОБ.019", "Автомат резки", cutter),
            "ОБ.100.1": _unit(session, "ОБ.100.1", "Зачистка", strippers),
            "ОБ.100.2": _unit(session, "ОБ.100.2", "Зачистка", strippers),
            "ОБ.100.3": _unit(session, "ОБ.100.3", "Зачистка", strippers),
            "ОБ.071.1": _unit(session, "ОБ.071.1", "Паяльная станция", presses),
            "A8": _unit(session, "A8", "Аппликатор A8", applicators, EquipmentApplicatorRow, "ob_applicators"),
        }
    admin = _login("admin")
    yield sessions, clock, admin, units
    del app.state.production_uow_factory


def _rows():
    stripper = [("strip_mm", "Длина зачистки", "мм", "ЭТК", "terStrip", ""), ("diameter_mm", "Диаметр", "мм", "провод", "", "")]
    items = [("ОБ.019", "полная", "strip_front_mm", "Зачистка начала", "мм", "ЭТК", "Зачистка А", "D34", "резка", "да"),
             ("ОБ.019", "полная", "diameter_mm", "Диаметр реза", "мм", "провод", "Ø жилы", "D42", "резка", "да"),
             *[(inv, "простая", *param, "зачистка", "да") for inv in ("ОБ.100.1", "ОБ.100.2") for param in stripper],
             ("ОБ.100.3", "простая", *stripper[0], "зачистка", "да"),
             ("ОБ.071.1", "нет", "", "", "", "", "", "", "паяльник", "да"),
             ("A8", "нет", "", "", "", "", "", "", "оснастка", "")]
    return [dict(zip(HEADER, item)) for item in items]


def test_seed_is_a_dry_run_by_default_and_applies_models_with_overrides(setup_db):
    sessions, clock, admin, units = setup_db
    with sessions() as session:
        plan = seed(session, _rows(), now=clock(), apply=False, source="test.csv")
    assert plan["applied"] is False and plan["units"] == 5 and plan["skipped_not_reviewed"] == ["A8"]
    assert plan["overrides"] == {"ОБ.100.3": "новая"}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SetupModelRow)) == 0
        result = seed(session, _rows(), now=clock(), apply=True, source="test.csv")
    assert result["by_class"] == {"нет": 1, "простая": 3, "полная": 1}
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SetupModelRow)) == 3
        assert session.scalar(select(func.count()).select_from(SetupUnitOverrideRow)) == 1
        again = seed(session, _rows(), now=clock(), apply=True, source="test.csv")
    assert all(text.endswith("без изменений") for text in again["models"].values()) and again["overrides"] == {}


def test_seed_refuses_an_unknown_inventory_number(setup_db):
    sessions, clock, _admin, _units = setup_db
    rows = _rows() + [dict(zip(HEADER, ("ОБ.555", "нет", "", "", "", "", "", "", "нет такого", "да")))]
    with sessions() as session:
        result = seed(session, rows, now=clock(), apply=True, source="test.csv")
        assert "ОБ.555" in result["refused"]
        assert session.scalar(select(func.count()).select_from(SetupModelRow)) == 0


def test_seed_journals_each_change_once_and_takes_a_recorded_decision(setup_db):
    sessions, clock, _admin, units = setup_db
    decision = "решение Claude 19.11 (владелец: не ждать)"
    rows = [{**row, "reviewed": decision} for row in _rows()]
    with sessions() as session:
        plan = seed(session, rows, now=clock(), apply=False, source="test.csv")
    assert plan["units"] == 6 and plan["skipped_not_reviewed"] == []
    with sessions() as session:
        assert session.scalar(SEEDED) == 0  # the dry run left no journal
        seed(session, rows, now=clock(), apply=True, source="test.csv")
    with sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type.like("equipment_setup.%"))).all()
        assert sorted(event.event_type for event in events) == ["equipment_setup.model_seeded"] * 4 + [
            "equipment_setup.unit_override_seeded"]
        override = next(event for event in events if event.event_type.endswith("override_seeded"))
        assert override.aggregate_id == units["ОБ.100.3"] and override.details["parameters"] == "−diameter_mm"
        assert {event.actor_name for event in events} == {"Модель наладки (CSV владельца)"}
        again = seed(session, rows, now=clock(), apply=True, source="test.csv")
    assert again["overrides"] == {} and all(text.endswith("без изменений") for text in again["models"].values())
    with sessions() as session:
        assert session.scalar(SEEDED) == len(events)


def test_list_and_card_show_classes_online_configuration_and_references(setup_db):
    sessions, clock, admin, units = setup_db
    with sessions() as session:
        seed(session, _rows(), now=clock(), apply=True, source="test.csv")
    token = _issue(admin, scopes=("machine:report",), name="Мост ОБ.019", days=365)
    with sessions.begin() as session:
        token_id = session.scalar(select(IdentityApiTokenRow.id).limit(1))
        session.add(MachineLinkRow(equipment_id=units["ОБ.019"], inventory_number="ОБ.019", kind="ob019_modbus",
                                   token_id=token_id, machine_id="guid-108", pc_name="PC-108", bound_at=clock(),
                                   version=1))
        reference = uuid4()
        session.add(SetupReferenceRow(id=reference, unit_id=units["ОБ.019"], wire="ПВАМ 0,35", wire_key="пвам 0,35",
                                      version=1, setup_values={"strip_front_mm": "5"}, created_by=uuid4(),
                                      created_by_name="Слава", created_at=clock()))
        session.flush()
        session.add(SetupConfigurationRow(unit_id=units["ОБ.019"], wire="ПВАМ 0,35", reference_id=reference,
                                          setup_values={"strip_front_mm": "5"}, set_by=uuid4(), set_by_name="Слава",
                                          set_at=clock(), version=1))
        session.add(SetupMachineCorrectionRow(unit_id=units["ОБ.019"], parameter_key="strip_front_mm", wire_key="",
                                              offset_value=Decimal("-0.3"), set_by=uuid4(), set_by_name="Слава",
                                              set_at=clock(), version=1))
    assert token
    body = admin.get(LIST).json()
    rows = {row["inventory"]: row for row in body["rows"]}
    assert rows["ОБ.019"]["requires_setup"] == {"code": "full", "label": "полная"}
    assert rows["ОБ.019"]["online"] and rows["ОБ.019"]["configured"] and rows["ОБ.019"]["parameters"] == 2
    assert rows["ОБ.100.3"]["own_setup"] and rows["ОБ.100.3"]["parameters"] == 1
    assert rows["ОБ.071.1"]["requires_setup"]["label"] == "нет"
    assert rows["A8"]["requires_setup"]["label"] == "не задано" and rows["A8"]["catalog"] == "Аппликаторы"
    assert body["counts"] == {"none": 1, "simple": 3, "full": 1, "unset": 1, "online": 1}

    card = admin.get(f"{LIST}/ОБ.019").json()
    assert [(item["key"], item["source"], item["register"]) for item in card["parameters"]] == [
        ("strip_front_mm", "ЭТК", "D34"), ("diameter_mm", "провод", "D42")]
    assert card["configuration"]["wire"] == "ПВАМ 0,35" and card["configuration"]["set_by"] == "Слава"
    assert card["references"][0]["version"] == 1
    assert card["corrections"] == [{"parameter": "strip_front_mm", "wire": "", "offset": "-0.300", "reason": ""}]
    assert card["online"] and card["connection"]["live"] is False  # linked, no snapshot yet
    machine_card = admin.get("/api/production/machines/card/ОБ.019").json()
    assert machine_card["setup_model"]["requires_setup"]["label"] == "полная"
    assert admin.get(f"{LIST}/ОБ.555").status_code == 404


def test_setup_model_needs_the_machines_section(setup_db):
    _sessions, _clock, _admin, _units = setup_db
    assert TestClient(app).get(LIST).status_code == 401
