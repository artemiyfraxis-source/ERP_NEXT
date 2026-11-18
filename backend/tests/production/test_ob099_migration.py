import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0195_ob099_crimp_records"
LINK = ("INSERT INTO production_machine_links (equipment_id, inventory_number, kind, token_id, machine_id, bound_at,"
        " version) VALUES (?, ?, ?, ?, ?, '2026-11-18 10:00:00', 1)")
CRIMP = ("INSERT INTO production_crimp_records (id, equipment_id, source_key, crimped_at, result, peak, received_at)"
         " VALUES (?, ?, ?, '2026-11-18 08:20:37', ?, ?, '2026-11-18 08:21:00')")
ALARM = ("INSERT INTO production_machine_alarm_records (id, equipment_id, source_key, occurred_at, text, received_at)"
         " VALUES (?, ?, ?, '2026-11-18 13:27:03', ?, '2026-11-18 13:28:00')")


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_press_kind_crimp_and_alarm_records_refuse_bad_rows_and_downgrade_drops_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "ob099.db"
    config = _config(path)
    command.upgrade(config, "0194_si_norm_assignment")
    old = uuid.uuid4().hex
    with sqlite3.connect(path) as connection:
        connection.execute(LINK, (old, "ОБ.019", "ob019_modbus", uuid.uuid4().hex, "pc-108"))
    command.upgrade(config, REVISION)
    press = uuid.uuid4().hex
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT kind FROM production_machine_links").fetchall() == [("ob019_modbus",)]
        connection.execute(LINK, (press, "ОБ.099.1", "ob099_fins", uuid.uuid4().hex, "pc-82"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(LINK, (uuid.uuid4().hex, "ОБ.020", "ob020", uuid.uuid4().hex, "pc"))
        connection.execute(CRIMP, (uuid.uuid4().hex, press, "20261118@0", "OK", 519))
        connection.execute(CRIMP, (uuid.uuid4().hex, press, "20261118@80", "NG", None))
        for bad in (
            (uuid.uuid4().hex, press, "20261118@0", "OK", 1),  # the same log line twice
            (uuid.uuid4().hex, press, "20261118@99", "SIGMA", 1),  # unknown result
            (uuid.uuid4().hex, press, "20261118@98", "OK", -1),  # negative peak
            (uuid.uuid4().hex, press, "  ", "OK", 1),  # no key
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(CRIMP, bad)
        connection.execute(ALARM, (uuid.uuid4().hex, press, "2026-11-18 13:27:03|x", "Abnormal terminal crimping"))
        for bad in ((uuid.uuid4().hex, press, "2026-11-18 13:27:03|x", "again"), (uuid.uuid4().hex, press, "k", " ")):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(ALARM, bad)
    command.downgrade(config, "-1")
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not {"production_crimp_records", "production_machine_alarm_records"} & tables
        assert connection.execute("SELECT inventory_number FROM production_machine_links").fetchall() == [("ОБ.019",)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(LINK, (uuid.uuid4().hex, "ОБ.099.2", "ob099_fins", uuid.uuid4().hex, "pc"))
