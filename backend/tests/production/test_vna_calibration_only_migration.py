"""0221_vna_calibration_only_check: a session check row may be «только калибровка» (owner, 26.11.2026)."""

import sqlite3
from uuid import uuid4

from alembic import command
import pytest

from tests.production.test_box_luhn_migration import _config

PARENT = "0220_rf_kit_characterization"
REVISION = "0221_vna_calibration_only_check"
COLUMNS = ("id, instrument_id, kind, passed, performed_at, recorded_at, source, payload_hash, operator, actor, "
           "calibration_id, acm_serial, profile, reference, note, confidence_passed, confidence_tx_dev_db, "
           "confidence_rx_dev_db, load_p1_vswr, load_p2_vswr")
OLD_PASSED = dict(passed=1, confidence_passed=1, confidence_tx_dev_db=0.03, confidence_rx_dev_db=0.1,
                  load_p1_vswr=1.02, load_p2_vswr=1.03)
OLD_FAILED = dict(passed=0, confidence_passed=0, confidence_tx_dev_db=0.4, confidence_rx_dev_db=None,
                  load_p1_vswr=None, load_p2_vswr=None)
CALIBRATION_ONLY = dict(passed=1, confidence_passed=None, confidence_tx_dev_db=None, confidence_rx_dev_db=None,
                        load_p1_vswr=None, load_p2_vswr=None)
HALF_FILLED = (
    dict(CALIBRATION_ONLY, load_p1_vswr=1.02, load_p2_vswr=1.03),   # confidence NULL, LOAD set
    dict(CALIBRATION_ONLY, passed=0),                                # all NULL but not passed
    dict(CALIBRATION_ONLY, confidence_rx_dev_db=0.1),                # a stray confidence value
    dict(OLD_PASSED, load_p2_vswr=None),                             # the old form, half filled
)


def _insert(db, values, note=""):
    row = dict(id=uuid4().hex, instrument_id=uuid4().hex, kind="session", performed_at="2026-11-26",
               recorded_at="2026-11-26", source=f"s-{uuid4()}", payload_hash="0" * 64, operator="", actor="x",
               calibration_id="", acm_serial="", profile="", reference="", note=note, **values)
    names = COLUMNS.split(", ")
    db.execute(f"INSERT INTO production_vna_checks ({COLUMNS}) VALUES ({', '.join('?' for _ in names)})",
               [row[name] for name in names])
    return row["id"]


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "checks.db"
    config = _config(path)
    command.upgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        old = [_insert(db, OLD_PASSED), _insert(db, OLD_FAILED)]
        with pytest.raises(sqlite3.IntegrityError):   # before 0221 a session row needs the confidence test
            _insert(db, CALIBRATION_ONLY)
    command.upgrade(config, REVISION)
    return config, path, old


def test_old_rows_survive_and_still_validate_and_calibration_only_is_accepted(database):
    config, path, old = database
    with sqlite3.connect(path) as db:
        assert sorted(r[0] for r in db.execute("SELECT id FROM production_vna_checks")) == sorted(old)
        _insert(db, OLD_PASSED)
        _insert(db, OLD_FAILED)
        _insert(db, CALIBRATION_ONLY, note="доверительный тест и LOAD отменены решением владельца 26.11.2026")
        with pytest.raises(sqlite3.IntegrityError):   # the old form keeps its own verdict rule
            _insert(db, dict(OLD_PASSED, load_p1_vswr=1.07))
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'ix_vna_check_instrument_kind'").fetchone()


@pytest.mark.parametrize("values", HALF_FILLED)
def test_half_filled_session_rows_are_refused(database, values):
    _, path, _ = database
    with sqlite3.connect(path) as db, pytest.raises(sqlite3.IntegrityError):
        _insert(db, values)


def test_downgrade_refuses_while_calibration_only_rows_exist(database):
    config, path, old = database
    with sqlite3.connect(path) as db:
        added = _insert(db, CALIBRATION_ONLY)
    with pytest.raises(RuntimeError, match="только калибровка"):
        command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM production_vna_checks WHERE id = ?", (added,))
    command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        assert sorted(r[0] for r in db.execute("SELECT id FROM production_vna_checks")) == sorted(old)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(db, CALIBRATION_ONLY)
    command.upgrade(config, REVISION)
