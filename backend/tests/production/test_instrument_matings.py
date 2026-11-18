"""Per-side mating counters of instruments: chain constraints, card API, VNA batches and the migration."""

import sqlite3
from datetime import datetime
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.production.domain import ProductionValidationError
from app.modules.production.mating_infrastructure import InstrumentMatingRow, MatingBatchRow
from app.modules.production.matings import status_for, validate_entry
from app.shared.persistence import AuditEventRow
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_box_luhn_migration import _config
from tests.production.test_instruments_api import _as

ADAPTER = [{"series": "3.5mm", "body": "вилка", "center": "штырь"}, {"series": "3.5mm", "body": "розетка", "center": "гнездо"}]


def create(client, number="СИ.006", name="Переход коаксиальный ADP1A-35M-35F", interfaces=ADAPTER, resource=3000, role="adapter"):
    _as({"quality": "edit", "production": "view"})
    response = client.post("/api/production/instruments", json={"inventory_number": number, "name": name, "kind": "Принадлежность"})
    assert response.status_code == 200, response.text
    item = response.json()["instrument"]
    rf = client.put(f"/api/production/instruments/{item['id']}/rf-interfaces", json={
        "version": 0, "role": role, "interfaces": interfaces, "mating_resource": resource})
    assert rf.status_code == 200, rf.text
    assert rf.json()["rf"]["mating_resource"] == resource
    return item["id"]


def post(client, item_id, **body):
    return client.post(f"/api/production/instruments/{item_id}/matings", json={"note": "", **body})


def sessions():
    return app.state.production_uow_factory().session_factory


@pytest.mark.parametrize("total,resource,status", [
    (0, None, "unknown"), (2399, 3000, "ok"), (2400, 3000, "warning"), (2999, 3000, "warning"), (3000, 3000, "exhausted"), (3001, 3000, "exhausted"),
])
def test_status_warns_from_eighty_percent_and_exhausts_at_the_resource(total, resource, status):
    assert status_for(total, resource) == status


@pytest.mark.parametrize("kwargs,message", [
    ({"delta": -1, "kind": "manual"}, "исправлением"),
    ({"delta": 5, "kind": "initial", "note": " "}, "причину"),
    ({"delta": 5, "kind": "initial", "note": "журнал", "first": False}, "один раз"),
    ({"delta": -3, "kind": "correction", "note": "ошибка", "total": 2}, "отрицательным"),
    ({"delta": 0, "kind": "manual"}, "ненулевое"),
])
def test_entry_rules(kwargs, message):
    values = {"end": "A", "note": "", "total": 0, "first": True, **kwargs}
    with pytest.raises(ProductionValidationError, match=message):
        validate_entry(**values)


def test_card_counts_each_side_separately_and_reports_resource(production_api):
    client, _ = production_api
    item = create(client)
    assert post(client, item, end="A", delta=2400, kind="initial", note=" Журнал смены ").status_code == 200
    snapshot = post(client, item, end="B", delta=1, kind="manual").json()
    assert snapshot["resource"] == 3000 and snapshot["status"] == "warning"
    assert [(end["end"], end["total"], end["version"], end["status"], end["can_initialize"]) for end in snapshot["ends"]] == [
        ("A", 2400, 1, "warning", False), ("B", 1, 1, "ok", False)]
    assert snapshot["ends"][0]["percent_used"] == 80.0 and snapshot["ends"][0]["initial_recorded"]
    assert sorted(event["kind"] for event in snapshot["events"]) == ["initial", "manual"]
    assert next(e for e in snapshot["events"] if e["kind"] == "initial")["note"] == "Журнал смены"
    with sessions()() as session:
        audit = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.instrument_mating_added")).all()
        assert len(audit) == 2


def test_initial_only_first_negative_totals_refused_and_corrections_need_reason(production_api):
    client, _ = production_api
    item = create(client)
    assert post(client, item, end="A", delta=3, kind="manual").status_code == 200
    late = post(client, item, end="A", delta=100, kind="initial", note="журнал")
    assert late.status_code == 422 and "один раз" in late.json()["detail"]
    assert post(client, item, end="A", delta=-4, kind="correction", note="ошибка").status_code == 422
    assert post(client, item, end="A", delta=-1, kind="manual").status_code == 422
    assert post(client, item, end="A", delta=-1, kind="correction").status_code == 422
    fixed = post(client, item, end="A", delta=-3, kind="correction", note="Лишние нажатия").json()
    assert fixed["ends"][0]["total"] == 0 and not fixed["ends"][0]["can_initialize"]


def test_stale_version_is_a_conflict_not_a_second_write(production_api):
    client, _ = production_api
    item = create(client)
    assert post(client, item, end="A", delta=1, kind="manual", version=0).status_code == 200
    stale = post(client, item, end="A", delta=-1, kind="correction", note="ошибка", version=0)
    assert stale.status_code == 409
    assert post(client, item, end="A", delta=-1, kind="correction", note="ошибка", version=1).status_code == 200


def test_one_port_load_and_unknown_resource_have_only_side_a(production_api):
    client, _ = production_api
    item = create(client, number="СИ.020", name="Нагрузка SMA", interfaces=[ADAPTER[0]], resource=None, role="load")
    snapshot = client.get(f"/api/production/instruments/{item}/matings").json()
    assert [end["end"] for end in snapshot["ends"]] == ["A"] and snapshot["status"] == "unknown"
    assert snapshot["ends"][0]["percent_used"] is None
    refused = post(client, item, end="B", delta=1, kind="manual")
    assert refused.status_code == 422 and "Сторона B" in refused.json()["detail"]


def test_viewer_reads_but_cannot_write(production_api):
    client, _ = production_api
    item = create(client)
    _as({"quality": "view", "production": "view"})
    assert client.get(f"/api/production/instruments/{item}/matings").status_code == 200
    assert post(client, item, end="A", delta=1, kind="manual").status_code == 403


def test_side_with_history_keeps_its_connector(production_api):
    client, _ = production_api
    item = create(client)
    assert post(client, item, end="B", delta=1, kind="manual").status_code == 200
    put = lambda interfaces, version: client.put(f"/api/production/instruments/{item}/rf-interfaces", json={
        "version": version, "role": "adapter", "interfaces": interfaces, "mating_resource": 3000})
    assert put([ADAPTER[0]], 1).status_code == 422
    assert put([ADAPTER[0], {**ADAPTER[1], "series": "SMA"}], 1).status_code == 422
    assert put([ADAPTER[0], {**ADAPTER[1], "f_max_ghz": 26.5}], 1).status_code == 200


def test_rf_resource_must_be_positive(production_api):
    client, _ = production_api
    item = create(client)
    response = client.put(f"/api/production/instruments/{item}/rf-interfaces", json={
        "version": 1, "role": "adapter", "interfaces": ADAPTER, "mating_resource": 0})
    assert response.status_code == 422


def test_batch_by_inventory_number_is_idempotent_per_run(production_api):
    client, _ = production_api
    dut = create(client)
    cable = create(client, number="СИ.100", name="Кабель C50SMNM.2", interfaces=[
        {"series": "SMA", "body": "вилка", "center": "штырь"}, {"series": "N", "body": "вилка", "center": "штырь"}], resource=None, role="test_cable")
    batch = {"source": "vna-measurement-0f0e", "entries": [
        {"instrument_id": dut, "end": "A", "delta": 1, "kind": "measurement"},
        {"instrument_id": dut, "end": "B", "delta": 1, "kind": "measurement"},
        {"inventory_number": "СИ.100", "end": "A", "delta": 1, "kind": "measurement"}]}
    first = client.post("/api/production/matings/batch", json=batch)
    assert first.status_code == 200, first.text
    assert not first.json()["replayed"] and len(first.json()["events"]) == 3
    replay = client.post("/api/production/matings/batch", json={**batch, "entries": list(reversed(batch["entries"]))})
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert {e["id"] for e in replay.json()["events"]} == {e["id"] for e in first.json()["events"]}
    other = client.post("/api/production/matings/batch", json={**batch, "entries": batch["entries"][:2]})
    assert other.status_code == 409
    totals = {end["end"]: end["total"] for end in client.get(f"/api/production/instruments/{cable}/matings").json()["ends"]}
    assert totals == {"A": 1, "B": 0}


def test_batch_is_all_or_nothing(production_api):
    client, _ = production_api
    dut = create(client)
    load = create(client, number="СИ.021", name="Нагрузка", interfaces=[ADAPTER[0]], resource=None, role="load")
    bad = client.post("/api/production/matings/batch", json={"source": "vna-calibration-1", "entries": [
        {"instrument_id": dut, "end": "A", "delta": 1, "kind": "calibration"},
        {"instrument_id": load, "end": "B", "delta": 1, "kind": "calibration"}]})
    assert bad.status_code == 422
    missing = client.post("/api/production/matings/batch", json={"source": "vna-calibration-2", "entries": [
        {"inventory_number": "СИ.999", "end": "A", "delta": 1, "kind": "calibration"}]})
    assert missing.status_code == 404
    with sessions()() as session:
        assert session.scalars(select(InstrumentMatingRow)).all() == []
        assert session.scalars(select(MatingBatchRow)).all() == []
    manual_kind = client.post("/api/production/matings/batch", json={"source": "x", "entries": [
        {"instrument_id": dut, "end": "A", "delta": 1, "kind": "manual"}]})
    assert manual_kind.status_code == 422


def test_database_refuses_forked_or_negative_chains(production_api):
    client, _ = production_api
    item = create(client)
    assert post(client, item, end="A", delta=2, kind="manual").status_code == 200
    from uuid import UUID
    base = dict(instrument_id=UUID(item), side="A", kind="manual", note="", source=None, actor="x", created_at=datetime(2026, 11, 18))
    for row in (dict(seq=1, delta=1, total_after=3),          # fork of an existing position
                dict(seq=2, delta=-5, total_after=-3, kind="correction", note="x"),  # negative total
                dict(seq=1, delta=1, total_after=1, side="B", kind="measurement"),   # run event without a batch
                dict(seq=3, delta=5, total_after=7, kind="initial", note="x")):      # initial value after history
        with sessions()() as session:
            session.add(InstrumentMatingRow(id=uuid4(), **{**base, **row}))
            with pytest.raises(IntegrityError):
                session.flush()


def test_migration_adds_ledger_and_sets_only_confirmed_adp1a_resource(tmp_path):
    path = tmp_path / "matings.db"
    config = _config(path)
    command.upgrade(config, "0190_godex_marking")
    with sqlite3.connect(path) as connection:
        for index, (name, designation) in enumerate([("Переход ADP1A-35M-35F", ""), ("Переходник SMA S-322", ""),
                                                     ("Переход 3,5 мм", "ADP1A-35F-35F"), ("Переход ADP1A-NMNF", "")]):
            connection.execute(
                "INSERT INTO production_instruments (id, version, inventory_number, name, instrument_type, status, kind, "
                "serial_number, manufacturer, doc_designation, quantity, storage_location, warehouse_grn) "
                "VALUES (?, 1, ?, ?, 'Принадлежность', 'Действует', 'Принадлежность', '', '', ?, 1, 'ОТК', '')",
                (uuid4().hex, f"СИ.{index + 6:03}", name, designation))
    command.upgrade(config, "0191_mating_cycles")
    with sqlite3.connect(path) as connection:
        rows = dict(connection.execute(
            "SELECT i.inventory_number, r.mating_resource FROM production_instruments i "
            "LEFT JOIN production_rf_details r ON r.instrument_id = i.id").fetchall())
        assert rows == {"СИ.006": 3000, "СИ.007": None, "СИ.008": 3000, "СИ.009": None}
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE production_rf_details SET mating_resource = 0")
    command.downgrade(config, "0190_godex_marking")
    command.upgrade(config, "0191_mating_cycles")


def test_item_without_rf_connectors_has_no_counter_on_the_card(production_api):
    client, _ = production_api
    _as({"quality": "edit", "production": "view"})
    item = client.post("/api/production/instruments", json={"inventory_number": "СИ.010", "name": "Рулетка", "kind": "Принадлежность"}).json()["instrument"]
    snapshot = client.get(f"/api/production/instruments/{item['id']}/matings").json()
    assert snapshot["has_connectors"] is False and [end["end"] for end in snapshot["ends"]] == ["A"]
    adapter = create(client)
    assert client.get(f"/api/production/instruments/{adapter}/matings").json()["has_connectors"] is True
