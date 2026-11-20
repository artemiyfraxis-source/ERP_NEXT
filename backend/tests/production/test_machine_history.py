"""ОБ.019 speed history (plan machine-speed-history S1): the bridge's report writes a row only when the counter, state,
run or setup changed — `start` on the first report and a new boot; UPH alone, an unchanged or re-sent snapshot, another
machine or a lost link write nothing; a clock stepped back never puts a row before the previous one."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.production import machine_history as history
from app.modules.production import machine_runs as runs
from app.modules.production import machines as rules
from app.modules.production.machine_history import MachineSpeedRow
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machine_writes_api import _job
from tests.workflows.test_machines_api import WORDS, _bridge, _report, _unit, machines_api  # noqa: F401

RUN = "/api/production/machines/card/ОБ.019/run"
SETUP = "|L325|S5/?|P?/?|M?"  # WORDS: length 325 mm, front strip 5 mm, the rest of the setup not in the snapshot


@pytest.fixture(autouse=True)
def _history_log_on(monkeypatch):
    """A migration test in the same worker runs alembic's fileConfig, which switches existing loggers off (production
    migrates in its own process): keep this module's logger on so the error-log assertions see its records."""
    monkeypatch.setattr(history.log, "disabled", False)


def _rows(sessions) -> list[MachineSpeedRow]:
    with sessions() as session:
        return list(session.scalars(select(MachineSpeedRow).order_by(MachineSpeedRow.id)))


def _words(**changes) -> dict:
    """WORDS with registers changed: _words(**{"649": 2})."""
    return {**WORDS, **changes}


class Bridge:
    def __init__(self, admin, boot="boot-1"):
        self.headers, self.boot, self.seq = _bridge(admin), boot, 0

    def report(self, registers=None, **extra):
        self.seq += 1
        answer = _report(self.headers, boot=self.boot, seq=self.seq, registers=registers, **extra)
        assert answer.status_code == 200, answer.text
        return answer.json()


def test_the_first_report_writes_start_and_an_unchanged_or_uph_only_report_writes_nothing(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = Bridge(admin)
    bridge.report()
    [row] = _rows(sessions)
    assert (row.equipment_id, row.reason, row.boot, row.seq) == (ob019, "start", "boot-1", 1)
    assert (row.counter_total, row.counter_batch, row.counter_order, row.uph) == (0x10001, 4, 0, 3621)
    assert (row.state_code, row.alarm_code, row.run_write_id, row.run_kind, row.wire_key) == (102, 0, None, None, "")
    assert row.setup_key == SETUP
    clock.now += timedelta(seconds=1)
    bridge.report()  # the heartbeat: nothing changed
    bridge.report(_words(**{"641": 4000}))  # UPH alone
    bridge.report(_words(**{"641": 4000, "782": 4}))  # the alarm word alone
    assert len(_rows(sessions)) == 1


@pytest.mark.parametrize(("change", "reason"), [
    ({"649": 2}, "counter"),
    ({"645": 5}, "counter"),
    ({"643": 1, "644": 0}, "counter"),
    ({"766": 103}, "state"),
    ({"32": 15000}, "setup"),
    ({"34": 700}, "setup"),
])
def test_a_counter_state_or_setup_change_writes_one_row_with_its_reason(machines_api, change, reason):
    sessions, clock, admin, _, _ = machines_api
    bridge = Bridge(admin)
    bridge.report()
    clock.now += timedelta(seconds=2)
    bridge.report(_words(**change))
    bridge.report(_words(**change))  # the same again: nothing new
    rows = _rows(sessions)
    assert [item.reason for item in rows] == ["start", reason]
    assert rows[1].seq == 2 and rows[1].recorded_at > rows[0].recorded_at


def test_a_setup_change_gives_the_new_canonical_key(machines_api):
    sessions, _, admin, _, _ = machines_api
    bridge = Bridge(admin)
    bridge.report()
    bridge.report(_words(**{"32": 15050, "38": 650, "39": 0, "36": 100, "37": 0, "40": 0, "41": 0}))
    assert _rows(sessions)[-1].setup_key == "|L150.5|S5/6.5|P1/0|M?"


def test_an_open_run_writes_a_run_row_with_its_kind_and_wire(machines_api):
    sessions, clock, admin, _, _ = machines_api
    bridge = Bridge(admin)
    bridge.report()
    group_id = _job(sessions)
    sent = admin.post(RUN, json={"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102},
                      headers=_csrf(admin))
    assert sent.status_code == 200, sent.text
    clock.now += timedelta(seconds=1)
    bridge.report()  # the same words, but the machine now has an open run
    rows = _rows(sessions)
    assert [item.reason for item in rows] == ["start", "run"]
    assert (str(rows[1].run_write_id), rows[1].run_kind, rows[1].wire_key) == (sent.json()["id"], "cutting", "пвам 0,35")
    assert rows[1].setup_key == "пвам 0,35" + SETUP  # the colour «красный» does not change the setup


def test_releasing_the_machine_link_keeps_the_history_and_only_drops_the_run_reference(machines_api):
    """`machines_admin release` deletes the link and, by cascade, its writes: the history row stays with its run kind."""
    sessions, clock, admin, ob019, _ = machines_api
    bridge = Bridge(admin)
    bridge.report()
    group_id = _job(sessions)
    sent = admin.post(RUN, json={"kind": "cutting", "group_id": str(group_id), "length_mm": "325", "confirm_state": 102},
                      headers=_csrf(admin))
    assert sent.status_code == 200, sent.text
    clock.now += timedelta(seconds=1)
    bridge.report()
    with sessions() as session:
        assert rules.release(session, "ОБ.019") is True
        session.commit()
    rows = _rows(sessions)
    assert [item.reason for item in rows] == ["start", "run"]
    # PostgreSQL nulls the write reference (ON DELETE SET NULL; checked by the migrations-pg job); SQLite tests do not
    # enforce foreign keys. Either way the row and its run kind stay.
    assert (rows[1].run_kind, rows[1].equipment_id) == ("cutting", ob019)


def test_a_counter_drop_is_written_and_a_new_boot_writes_start(machines_api):
    sessions, clock, admin, _, _ = machines_api
    bridge = Bridge(admin)
    bridge.report(_words(**{"649": 50, "650": 0}))
    bridge.report(_words(**{"649": 0, "650": 0}))  # reset on the panel
    restarted = Bridge.__new__(Bridge)
    restarted.headers, restarted.boot, restarted.seq = bridge.headers, "boot-2", 0
    restarted.report(_words(**{"649": 0, "650": 0}))  # the same values after the bridge restarted
    rows = _rows(sessions)
    assert [(item.reason, item.boot, item.seq, item.counter_total) for item in rows] == [
        ("start", "boot-1", 1, 50), ("counter", "boot-1", 2, 0), ("start", "boot-2", 1, 0)]


def test_a_resent_snapshot_writes_no_second_row_and_raises_nothing(machines_api, caplog):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = Bridge(admin)
    bridge.report()
    # The same boot and seq again, with other words: the snapshot is not accepted, so no history either.
    again = _report(bridge.headers, boot="boot-1", seq=1, registers=_words(**{"649": 9}))
    assert again.status_code == 200 and again.json()["accepted"] is False
    assert len(_rows(sessions)) == 1
    # A parallel report that got past the snapshot check: the UNIQUE (equipment, boot, seq) refuses it, quietly.
    with sessions() as session:
        link = session.get(rules.MachineLinkRow, ob019)
        words = rules.checked_registers(link.kind, _words(**{"649": 9}))
        with caplog.at_level(logging.ERROR, logger=history.__name__):
            assert history.record(session, link, words, "boot-1", 1, clock.now) is None
        session.commit()
    assert len(_rows(sessions)) == 1
    assert caplog.records == []  # a duplicate is not an error


def test_any_other_history_error_is_logged_and_swallowed(machines_api, caplog):
    sessions, clock, admin, ob019, _ = machines_api
    Bridge(admin).report()
    with sessions() as session:
        link = session.get(rules.MachineLinkRow, ob019)
        words = {**rules.checked_registers(link.kind, WORDS), 766: 70000}  # breaks the state CHECK, not the UNIQUE
        with caplog.at_level(logging.ERROR, logger=history.__name__):
            assert history.record(session, link, words, "boot-1", 2, clock.now) is None
        session.commit()  # the outer transaction survived the failed savepoint
    assert [record.levelno for record in caplog.records] == [logging.ERROR]
    assert str(ob019) in caplog.records[0].getMessage()
    assert len(_rows(sessions)) == 1


@pytest.mark.parametrize("broken", ["integrity", "exception"])
def test_a_failing_history_never_fails_the_machine_report(machines_api, monkeypatch, caplog, broken):
    sessions, clock, admin, ob019, _ = machines_api
    if broken == "integrity":
        monkeypatch.setattr(history, "setup_key", lambda wire, words: " ")  # the setup CHECK refuses the row
    else:
        def fail(*args):
            raise RuntimeError("сбой чтения прогона")
        monkeypatch.setattr(history, "open_run", fail)
    with caplog.at_level(logging.ERROR, logger=history.__name__):
        answer = Bridge(admin).report(_words(**{"649": 7, "650": 0}))
    assert answer["accepted"] is True
    with sessions() as session:
        snapshot = session.get(rules.MachineSnapshotRow, ob019)
        assert snapshot is not None and snapshot.registers["649"] == 7
    assert _rows(sessions) == []
    assert len(caplog.records) == 1 and str(ob019) in caplog.records[0].getMessage()


def test_a_duplicate_is_told_by_postgres_sqlstate_and_constraint_name():
    class Diag:
        def __init__(self, name):
            self.constraint_name = name

    class Orig(Exception):
        def __init__(self, sqlstate, name):
            super().__init__("duplicate key value violates unique constraint")
            self.sqlstate, self.diag = sqlstate, Diag(name)

    def error(orig):
        return IntegrityError("INSERT", {}, orig)

    assert history._duplicate(error(Orig("23505", history.UNIQUE_NAME)))
    assert not history._duplicate(error(Orig("23505", "pk_production_machine_speed_rows")))
    assert not history._duplicate(error(Orig("23514", history.UNIQUE_NAME)))
    sqlite = Exception("UNIQUE constraint failed: production_machine_speed_rows.equipment_id, "
                       "production_machine_speed_rows.boot, production_machine_speed_rows.seq")
    assert history._duplicate(error(sqlite))
    assert not history._duplicate(error(Exception("CHECK constraint failed: ck_production_machine_speed_rows_state")))


def test_run_kinds_match_the_frozen_check_of_0210():
    # Migration 0210 froze CHECK run_kind IN ('cutting', 'manual'): a new run kind needs a new migration.
    assert runs.KINDS == ("cutting", "manual")


def test_a_clock_stepped_back_is_clamped_to_the_previous_row(machines_api):
    sessions, clock, admin, _, _ = machines_api
    bridge = Bridge(admin)
    bridge.report()
    first = _rows(sessions)[0].recorded_at
    clock.now -= timedelta(hours=1)
    bridge.report(_words(**{"649": 2}))
    rows = _rows(sessions)
    assert [item.reason for item in rows] == ["start", "counter"]
    assert rows[1].recorded_at == first


def test_a_lost_link_or_another_machine_writes_nothing(machines_api):
    sessions, clock, admin, _, _ = machines_api
    bridge = Bridge(admin)
    bridge.report(link="port_busy")
    bridge.report(link="no_plc", registers={})
    assert _rows(sessions) == []
    with sessions.begin() as session:
        _unit(session, "ОБ.099.1", "Пресс BZW")
    press = _report(_bridge(admin, name="Агент ОБ.099.1"), machine="guid-82", inventory="ОБ.099.1",
                    registers={"D5006": 1})
    assert press.status_code == 200, press.text
    assert _rows(sessions) == []


def test_record_ignores_a_report_without_the_counter_or_the_state(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    Bridge(admin).report(link="port_busy")
    with sessions() as session:
        link = session.get(rules.MachineLinkRow, ob019)
        words = rules.checked_registers(link.kind, WORDS)
        assert history.record(session, link, {k: v for k, v in words.items() if k != 650}, "b", 1, clock.now) is None
        assert history.record(session, link, {k: v for k, v in words.items() if k != 766}, "b", 1, clock.now) is None
        assert history.record(session, link, words, "  ", 1, clock.now) is None


def test_the_marking_part_of_the_setup_key():
    blank = {address: 0 for address in history.MARK_WORDS}
    assert history.mark_key({}) == "?"
    assert history.mark_key({**blank, 440: 0x4142}) == "0"  # marking off: the texts do not matter
    on = {**blank, 400: 1, 440: 0x4142, 470: 0x4300, 411: 3}
    assert history.mark_key(on) == "1/F-B/3"
    assert history.mark_key({**on, 400: 0x8001}) == "1/F-B/3"  # only bit 0 is the switch
    assert history.setup_key("пвам 0,35", {32: 32500, 33: 0, **on}) == "пвам 0,35|L325|S?/?|P?/?|M1/F-B/3"


def test_the_identity_key_is_a_bigint_and_rows_take_increasing_ids(machines_api):
    sessions, clock, admin, _, _ = machines_api
    bridge = Bridge(admin)
    for value in range(3):
        bridge.report(_words(**{"649": value, "650": 0}))
    ids = [row.id for row in _rows(sessions)]
    assert ids == sorted(ids) and len(set(ids)) == 3
