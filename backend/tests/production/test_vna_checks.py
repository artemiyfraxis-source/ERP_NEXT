"""«Журнал проверок ВАЦ»: verdict rules, due dates, the program's idempotent records, CHECKs and the migration."""

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.production.domain import ProductionValidationError
from app.modules.production.vna_check_infrastructure import VnaCheckRow
from app.modules.production.vna_checks import add_month, due_items, expected_verdict
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_box_luhn_migration import _config
from tests.production.test_instruments_api import PENDING, _as

SERIAL = "26030095"
NOW = datetime(2026, 9, 22, 8, tzinfo=UTC)  # the production_api fixture clock is 22.09.2026 09:00


def create_vna(client, serial=SERIAL):
    _as({"quality": "edit", "production": "view"})
    response = client.post("/api/production/instruments", json={
        "inventory_number": "СИ.002", "name": "ВАЦ Planar S50180", "kind": "Средство измерений", "serial_number": serial,
        "instrument_type": "Анализатор цепей векторный", "status": PENDING})
    assert response.status_code == 200, response.text
    return response.json()["instrument"]["id"]


def session_check(**overrides):
    body = {"source": f"vna-check-{uuid4()}", "vna_serial": SERIAL, "kind": "session", "passed": True,
            "performed_at": NOW.isoformat(), "operator": "otk", "calibration_id": "cal-1", "acm_serial": "26140008",
            "profile": "10 МГц – 18 ГГц, 1601 т., ПЧ 1 кГц, 0 дБм", "vna_temperature_c": 31.2, "acm_temperature_c": 29.0,
            "confidence_passed": True, "confidence_tx_dev_db": 0.03, "confidence_rx_dev_db": 0.12,
            "load_p1_vswr": 1.023, "load_p2_vswr": 1.021}
    return {**body, **overrides}


def periodic(kind, when, passed=True):
    if kind == "control_standard":
        return {"source": f"vna-check-{uuid4()}", "vna_serial": SERIAL, "kind": kind, "passed": passed, "performed_at": when.isoformat(),
                "standard_inventory": "СИ.009", "reference": "Замер ВАЦ S50180, калибровка АКМ, 16.11.2026, зав. N8B140",
                "vswr_dev": 0.004 if passed else 0.03, "vswr_limit": 0.0153, "s21_dev_db": 0.01, "s21_limit_db": 0.1}
    return {"source": f"vna-check-{uuid4()}", "vna_serial": SERIAL, "kind": kind, "passed": passed, "performed_at": when.isoformat(),
            "standard_inventory": "СИ.003", "kit_ratio": 0.4 if passed else 1.3}


@pytest.mark.parametrize("kind,values,verdict", [
    ("session", {"confidence_passed": True, "confidence_tx_dev_db": 0.02, "load_p1_vswr": 1.06, "load_p2_vswr": 1.02}, True),
    ("session", {"confidence_passed": True, "confidence_tx_dev_db": 0.02, "load_p1_vswr": 1.0601, "load_p2_vswr": 1.02}, False),
    ("session", {"confidence_passed": False, "confidence_tx_dev_db": 0.3}, False),
    ("flex", {"s21_dev_db": 0.05, "vswr_dev": 0.01, "load_p1_vswr": 1.06, "load_p2_vswr": 1.03}, True),
    ("flex", {"s21_dev_db": 0.051, "vswr_dev": 0.01, "load_p1_vswr": 1.02, "load_p2_vswr": 1.03}, False),
    ("flex", {"s21_dev_db": 0.01, "vswr_dev": 0.01, "load_p1_vswr": 1.02, "load_p2_vswr": 1.0601}, False),
    ("control_standard", {"standard_inventory": "СИ.009", "vswr_dev": 0.02, "vswr_limit": 0.015, "s21_dev_db": 0.0, "s21_limit_db": 0.1}, False),
    ("kit_standards", {"standard_inventory": "СИ.003", "kit_ratio": 1.0}, True),
])
def test_verdict_follows_the_numbers(kind, values, verdict):
    assert expected_verdict(kind, values) is verdict


def test_flex_needs_the_load_on_both_cable_ends():
    """СТО ФРАК-002-2026 п. 10.6: LOAD on each cable end while bending, then the thru."""
    with pytest.raises(ProductionValidationError, match="load_p1_vswr"):
        expected_verdict("flex", {"s21_dev_db": 0.01, "vswr_dev": 0.0})


def test_passed_confidence_needs_loads_and_transmission_within_limit():
    with pytest.raises(ProductionValidationError, match="load_p1_vswr"):
        expected_verdict("session", {"confidence_passed": True, "confidence_tx_dev_db": 0.01})
    with pytest.raises(ProductionValidationError, match="0,1 дБ"):
        expected_verdict("session", {"confidence_passed": True, "confidence_tx_dev_db": 0.2, "load_p1_vswr": 1.01, "load_p2_vswr": 1.01})


def test_weekly_and_monthly_due_dates():
    assert add_month(datetime(2026, 1, 31, tzinfo=UTC)) == datetime(2026, 2, 28, tzinfo=UTC)
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    week_ago = now - timedelta(days=7, minutes=1)
    items = {item["kind"]: item for item in due_items(
        {"control_standard": (week_ago, True, week_ago), "kit_standards": (now, False, now - timedelta(days=3))}, now)}
    assert items["control_standard"]["status"] == "overdue"
    assert items["control_standard"]["status_label"] == "Контроль просрочен"
    assert items["kit_standards"]["status"] == "failed"
    assert {item["status"] for item in due_items({}, now)} == {"never"}


def test_program_records_session_once_and_card_shows_journal(production_api):
    client, _ = production_api
    vna = create_vna(client)
    body = session_check()
    first = client.post("/api/production/vna-checks", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["replayed"] is False
    again = client.post("/api/production/vna-checks", json=body)
    assert again.json()["replayed"] is True
    changed = client.post("/api/production/vna-checks", json={**body, "load_p1_vswr": 1.05})
    assert changed.status_code == 422 and "другими результатами" in changed.json()["detail"]
    journal = client.get(f"/api/production/instruments/{vna}/vna-checks").json()
    assert journal["applicable"] is True
    assert len(journal["records"]) == 1 and journal["records"][0]["load_p2_vswr"] == 1.021
    assert {item["status"] for item in journal["due"]} == {"never"}


def test_wrong_verdict_unknown_vna_and_viewer_are_refused(production_api):
    client, _ = production_api
    create_vna(client)
    wrong = client.post("/api/production/vna-checks", json=session_check(load_p2_vswr=1.08))
    assert wrong.status_code == 422 and "не совпадает" in wrong.json()["detail"]
    failed = client.post("/api/production/vna-checks", json=session_check(load_p2_vswr=1.08, passed=False))
    assert failed.status_code == 200, failed.text
    unknown = client.post("/api/production/vna-checks", json=session_check(vna_serial="NOPE"))
    assert unknown.status_code == 404 and "зав. №" in unknown.json()["detail"]
    future = client.post("/api/production/vna-checks", json=session_check(performed_at=(NOW + timedelta(hours=2)).isoformat()))
    assert future.status_code == 422
    _as({"quality": "view", "production": "view"})
    assert client.post("/api/production/vna-checks", json=session_check()).status_code == 403


def test_due_status_by_serial_follows_last_record(production_api):
    client, _ = production_api
    create_vna(client)
    now = NOW
    assert client.post("/api/production/vna-checks", json=periodic("control_standard", now - timedelta(days=8))).status_code == 200
    assert client.post("/api/production/vna-checks", json=periodic("kit_standards", now - timedelta(days=2))).status_code == 200
    due = {item["kind"]: item["status"] for item in client.get(f"/api/production/vna-checks/by-serial/{SERIAL}").json()["due"]}
    assert due == {"control_standard": "overdue", "kit_standards": "ok"}
    assert client.post("/api/production/vna-checks", json=periodic("control_standard", now - timedelta(hours=1), passed=False)).status_code == 200
    due = {item["kind"]: item["status"] for item in client.get(f"/api/production/vna-checks/by-serial/{SERIAL}").json()["due"]}
    assert due["control_standard"] == "failed"
    assert client.post("/api/production/vna-checks", json=periodic("control_standard", now)).status_code == 200
    due = {item["kind"]: item["status"] for item in client.get(f"/api/production/vna-checks/by-serial/{SERIAL}").json()["due"]}
    assert due["control_standard"] == "ok"


def test_database_refuses_a_verdict_that_disagrees_with_numbers(production_api):
    client, _ = production_api
    vna = UUID(create_vna(client))
    now = datetime(2026, 11, 18, tzinfo=UTC)
    base = dict(instrument_id=vna, performed_at=now, recorded_at=now, payload_hash="0" * 64, operator="", actor="x",
                calibration_id="", acm_serial="", profile="", reference="", note="")
    rows = (dict(kind="session", passed=True, confidence_passed=True, confidence_tx_dev_db=0.01, load_p1_vswr=1.07, load_p2_vswr=1.01),
            dict(kind="session", passed=False, confidence_passed=True, confidence_tx_dev_db=0.01),
            dict(kind="flex", passed=True, s21_dev_db=0.2, vswr_dev=0.0, load_p1_vswr=1.01, load_p2_vswr=1.01),
            dict(kind="flex", passed=True, s21_dev_db=0.01, vswr_dev=0.0, load_p1_vswr=1.07, load_p2_vswr=1.01),
            dict(kind="kit_standards", passed=True, standard_inventory="СИ.003", kit_ratio=0.5, load_p1_vswr=1.01),
            dict(kind="control_standard", passed=True, standard_inventory="СИ.009", vswr_dev=0.1, vswr_limit=0.015,
                 s21_dev_db=0.0, s21_limit_db=0.1),
            dict(kind="kit_standards", passed=True, standard_inventory="СИ.003", kit_ratio=1.5),
            dict(kind="flex", passed=True, s21_dev_db=0.01, vswr_dev=0.0, load_p1_vswr=1.01))
    factory = app.state.production_uow_factory().session_factory
    for row in rows:
        with factory() as session:
            session.add(VnaCheckRow(id=uuid4(), source=f"s-{uuid4()}", **{**base, **row}))
            with pytest.raises(IntegrityError):
                session.flush()


def test_migration_creates_the_journal_with_constraints(tmp_path):
    path = tmp_path / "checks.db"
    config = _config(path)
    command.upgrade(config, "0195_vna_check_journal")
    instrument = uuid4().hex
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO production_instruments (id, version, inventory_number, name, instrument_type, status, kind, serial_number, "
            "manufacturer, doc_designation, quantity, storage_location, warehouse_grn) "
            "VALUES (?, 1, 'СИ.002', 'ВАЦ', 'ВАЦ', 'Не используется', 'Средство измерений', '1', '', '', 1, 'ОТК', '')", (instrument,))
        insert = ("INSERT INTO production_vna_checks (id, instrument_id, kind, passed, performed_at, recorded_at, source, payload_hash, "
                  "operator, actor, calibration_id, acm_serial, profile, reference, note, s21_dev_db, vswr_dev, load_p1_vswr, "
                  "load_p2_vswr) VALUES (?, ?, 'flex', ?, '2026-11-18', '2026-11-18', ?, ?, '', 'x', '', '', '', '', '', ?, 0, 1.02, 1.02)")
        connection.execute(insert, (uuid4().hex, instrument, 1, "a", "0" * 64, 0.05))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, (uuid4().hex, instrument, 1, "b", "0" * 64, 0.06))
    with pytest.raises(RuntimeError, match="есть записи"):
        command.downgrade(config, "0194_si_norm_assignment")
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM production_vna_checks")
    command.downgrade(config, "0194_si_norm_assignment")
