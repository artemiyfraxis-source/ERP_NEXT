import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0192_machines"
LINK = ("INSERT INTO production_machine_links (equipment_id, inventory_number, kind, token_id, machine_id, bound_at,"
        " version) VALUES (?, ?, ?, ?, ?, '2026-11-18 10:00:00', ?)")
SNAPSHOT = ("INSERT INTO production_machine_snapshots (equipment_id, link_state, boot, seq, proto, registers, reported_at)"
            " VALUES (?, ?, ?, ?, ?, '{}', '2026-11-18 10:00:00')")


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_machine_tables_refuse_bad_rows_and_downgrade_drops_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "machines.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    unit, token = uuid.uuid4().hex, uuid.uuid4().hex
    with sqlite3.connect(path) as connection:
        connection.execute(LINK, (unit, "ОБ.019", "ob019_modbus", token, "guid-108", 1))
        connection.execute(SNAPSHOT, (unit, "ok", "boot", 0, 1))
        for bad in (
            (uuid.uuid4().hex, "ОБ.020", "ob020", uuid.uuid4().hex, "pc", 1),  # unknown kind
            (uuid.uuid4().hex, "ОБ.019", "ob019_modbus", uuid.uuid4().hex, "pc", 1),  # second link of an ОБ
            (uuid.uuid4().hex, "ОБ.021", "ob019_modbus", token, "pc", 1),  # one token, two ОБ
            (uuid.uuid4().hex, "ОБ.022", "ob019_modbus", uuid.uuid4().hex, "  ", 1),  # no PC
            (uuid.uuid4().hex, "ОБ.023", "ob019_modbus", uuid.uuid4().hex, "pc", 0),  # version
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(LINK, bad)
        for bad in ((unit, "ok", "boot", 1, 1), (uuid.uuid4().hex, "lost", "boot", 0, 1),
                    (uuid.uuid4().hex, "ok", "boot", -1, 1), (uuid.uuid4().hex, "ok", "boot", 0, 2)):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(SNAPSHOT, bad)
    command.downgrade(config, "-1")
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"production_machine_links", "production_machine_snapshots"} & tables
