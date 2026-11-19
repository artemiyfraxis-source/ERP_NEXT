from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.references.infrastructure import HookupWireRow, WireGaugeRow
from app.shared.http_auth import Principal
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.equipment_setup_context import equipment_setup_uow_factory
from app.workflows.ob019_setup import group_choices, group_nominal, router, seed, wire_label
from tests.workflows.test_equipment_setup_context import _record

NOW = datetime(2026, 11, 18, 12, tzinfo=UTC)
ROOT = "/api/production/equipment-setup/ob019"


@pytest.fixture
def world(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'ob019.db').as_posix()}")
    create_schema(engine)
    people = {}
    with Session(engine) as db:
        for key, site, master in (("master", "ЦАР", True), ("operator", "ЦАР", False), ("stranger", "ЖУК", True)):
            person = IdentityUserRow(id=uuid4(), login=key, email=f"{key}@example.test", name=key.title(),
                department="Цех", role="staff", site=site, active=True, site_master=master)
            db.add(person)
            people[key] = Actor(person.id, person.name, person.department)
        machine = _record(db, "ОБ.019", "ЦАР")
        other = _record(db, "ОБ.027.2", "ЦАР")
        gauge = WireGaugeRow(id=uuid4(), awg="22", section_mm2=Decimal("0.35"), conductor_d_mm=Decimal("0.77"))
        db.add(gauge)
        db.flush()
        for colour in ("красный", "синий"):
            db.add(HookupWireRow(id=uuid4(), mark=f"ПВАМ 0,35 {colour}", mark_key=f"пвам0,35{colour}", series="ПВАМ",
                                 gauge_id=gauge.id, updated_at=NOW))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    app.state.equipment_setup_uow_factory = equipment_setup_uow_factory(
        sessionmaker(bind=engine, expire_on_commit=False, class_=Session), integration_ready=True)
    app.state.who = people["master"]

    @app.middleware("http")
    async def session(request, call_next):
        request.state.principal = Principal(actor=app.state.who, email="", roles=frozenset({"staff"}), permissions={})
        return await call_next(request)

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, people=people, machine=machine, other=other, app=app)
    engine.dispose()


def _seed(world, apply):
    with Session(world.engine, expire_on_commit=False) as db:
        return seed(db, now=NOW, apply=apply)


def test_seed_is_a_dry_run_by_default_then_writes_once(world):
    plan = _seed(world, apply=False)
    assert plan["applied"] is False and plan["version"] == 1
    assert [item["value"] for item in plan["panel_settings"]] == ["5,00", "5,00", "5,00", "5,00", "0,80", "0,10"]
    before = world.client.get(f"{ROOT}/{world.machine}/proposal", params={"wire": "ПВАМ 0,35"}).json()
    assert before["recipe"] is None
    # No recipe yet: the cut diameter is Ø жилы 0,77 + 0,10 (owner 19.11.2026).
    assert {item["key"]: (item["value"], item["source"]) for item in before["values"]}["diameter_mm"] == (
        "0.87", "Ø жилы + 0,10")
    assert _seed(world, apply=True)["applied"] is True
    assert "уже есть" in _seed(world, apply=True)["skipped"]


def test_context_lists_reference_wires_and_marks_the_recorded_one(world):
    _seed(world, apply=True)
    body = world.client.get(f"{ROOT}/{world.machine}/context").json()
    assert body["machine"]["inventory_number"] == "ОБ.019"
    assert [item["register"] for item in body["parameters"]] == ["D32", "D34", "D36", "D38", "D40", "D42", "D43"]
    assert body["wires"] == [{"label": "ПВАМ 0,35", "conductor_mm": "0.770", "has_recipe": True}]
    assert body["groups"] == []


def test_proposal_takes_the_seeded_recipe_and_a_saved_version_is_picked_next_time(world):
    _seed(world, apply=True)
    first = world.client.get(f"{ROOT}/{world.machine}/proposal",
                             params={"wire": "ПВАМ 0,35 красный", "length_mm": "250"}).json()
    assert first["wire"] == "ПВАМ 0,35" and first["recipe"]["version"] == 1
    values = {item["key"]: (item["value"], item["source"]) for item in first["values"]}
    assert values["length_mm"] == ("250", "Задание")
    assert values["diameter_mm"] == ("0.80", "Рецепт, версия 1")
    saved = world.client.post(f"{ROOT}/{world.machine}/versions", json={
        **first["publish"], "wire": "ПВАМ 0,35", "context": "вне проекта · 20 шт",
        "values": {"strip_front_mm": "5", "pull_front_mm": "5", "strip_back_mm": "5", "pull_back_mm": "5",
                   "diameter_mm": "0,78", "knife_back_mm": "0,10"}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    again = world.client.get(f"{ROOT}/{world.machine}/proposal", params={"wire": "ПВАМ 0,35"}).json()
    assert again["recipe"]["version"] == 2 and again["publish"]["expected_version"] == 2
    assert {item["key"]: item["value"] for item in again["values"]}["diameter_mm"] == "0.78"
    stale = world.client.post(f"{ROOT}/{world.machine}/versions", json={
        **first["publish"], "wire": "ПВАМ 0,35", "values": {"strip_front_mm": "6", "pull_front_mm": "6",
        "strip_back_mm": "5", "pull_back_mm": "5", "diameter_mm": "0,78", "knife_back_mm": "0,10"}})
    assert stale.status_code == 409


def test_each_correction_is_journaled_by_the_site_operator(world):
    world.app.state.who = world.people["operator"]
    response = world.client.post(f"{ROOT}/{world.machine}/corrections", json={
        "parameter": "diameter_mm", "actual": "0,80", "proposed": "0.77", "live": "0.90", "wire": "ПВАМ 0,35",
        "context": "вне проекта", "quantity": 20})
    assert response.status_code == 200, response.text
    with Session(world.engine) as db:
        event = db.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "equipment_setup.ob019_correction"))
    assert event.aggregate_id == world.machine and event.actor_id == world.people["operator"].id
    assert {key: event.details[key] for key in ("register", "proposed", "live", "actual", "quantity")} == {
        "register": "D42", "proposed": "0,77", "live": "0,90", "actual": "0,80", "quantity": "20"}
    # Publishing a version stays with the site master or an administrator.
    publish = world.client.post(f"{ROOT}/{world.machine}/versions", json={
        "recipe_id": str(uuid4()), "expected_version": 0, "wire": "ПВАМ 0,35", "values": {
            "strip_front_mm": "5", "pull_front_mm": "5", "strip_back_mm": "5", "pull_back_mm": "5",
            "diameter_mm": "0,80", "knife_back_mm": "0,10"}})
    assert publish.status_code == 403


@pytest.mark.parametrize("case", ["other_site", "not_ob019", "bad_value", "unknown_parameter"])
def test_refusals(world, case):
    machine, body = world.machine, {"parameter": "diameter_mm", "actual": "0,80"}
    if case == "other_site":
        world.app.state.who = world.people["stranger"]
    elif case == "not_ob019":
        machine = world.other
    elif case == "bad_value":
        body["actual"] = "0,805"
    else:
        body["parameter"] = "speed"
    expected = 403 if case == "other_site" else 422
    assert world.client.post(f"{ROOT}/{machine}/corrections", json=body).status_code == expected
    read = {"other_site": 403, "not_ob019": 422}.get(case, 200)
    assert world.client.get(f"{ROOT}/{machine}/context").status_code == read
    with Session(world.engine) as db:
        assert db.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "equipment_setup.ob019_correction")) is None


def test_wire_label_drops_colour_and_keeps_awg_marks():
    assert wire_label("ПВАМ 0,35 красный") == "ПВАМ 0,35"
    assert wire_label("UL1007 24AWG") == "UL1007 24AWG"
    assert wire_label("M22759/16 22") == "M22759/16 22"


def _group(cable="ПВАМ 0,35 красный", strip=None, kind="cutWireStrip"):
    strip = strip or {"a": True, "b": True, "lenA": "5", "lenB": "6,5", "modeA": "full", "modeB": "partial"}
    return SimpleNamespace(id=uuid4(), number="ГР-0007", project_key="708К.", recipe={"cable": cable, "operations": [
        {"type": kind, "fields": {"strip": strip}}]})


def test_cutting_group_gives_the_etk_nominal_and_one_choice_per_length():
    group = _group()
    nominal = group_nominal(group.recipe)
    assert (nominal.wire, nominal.strip_front_mm, nominal.strip_back_mm, nominal.partial_front, nominal.partial_back) == (
        "ПВАМ 0,35", Decimal(5), Decimal("6.5"), False, True)
    members = [SimpleNamespace(group_id=group.id, blank_quantity=Decimal(count), row_snapshot={"cut_length_mm": length})
               for count, length in ((10, "120"), (5, "120"), (4, "95.5"))]
    choices = group_choices([group, _group(kind="coaxCut")], members)
    assert [(item["length_mm"], item["quantity"], item["wire"]) for item in choices] == [
        ("95.5", "4", "ПВАМ 0,35"), ("120", "15", "ПВАМ 0,35")]
    assert group_nominal(_group(strip={"a": False, "b": True, "lenB": "3"}).recipe).strip_front_mm == 0
