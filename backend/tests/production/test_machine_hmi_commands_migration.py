"""Migration 0211: «Закрыть / Открыть пульт на ПК» are machine commands the queue accepts (junk still refused), the
snapshot keeps the last state seen (a 16-bit word with its time, both or neither); the downgrade drops the panel
commands and the columns and leaves the other commands alone."""

import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0211_machine_hmi_commands"
INSERT = ("INSERT INTO production_machine_writes (id, equipment_id, purpose, command, status, payload, summary, created_by,"
          " created_at, taken_at, finished_at) VALUES (?, ?, 'control', ?, 'done', '{}', 'Пульт', ?,"
          " '2026-11-20 10:00:00', '2026-11-20 10:00:01', '2026-11-20 10:00:02')")
LINK = ("INSERT INTO production_machine_links (equipment_id, inventory_number, kind, token_id, machine_id, bound_at,"
        " version) VALUES (?, ?, 'ob019_modbus', ?, 'guid-108', '2026-11-20 10:00:00', 1)")
SNAPSHOT = ("INSERT INTO production_machine_snapshots (equipment_id, link_state, boot, seq, proto, registers, reported_at,"
            " last_state, last_state_at) VALUES (?, 'port_busy', 'boot', 0, 1, '{}', '2026-11-20 10:00:00', ?, ?)")
AT = "2026-11-20 09:59:00"


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_panel_commands_and_the_last_state_are_constrained_and_the_downgrade_drops_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "hmi.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    person = uuid.uuid4().hex
    units = [uuid.uuid4().hex for _ in range(4)]
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for name, unit in zip(("hmi_close", "hmi_open", "stop"), units):
            connection.execute(INSERT, (uuid.uuid4().hex, unit, name, person))
        for junk in ("hmi_kill", "taskkill", "HMI_CLOSE"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(INSERT, (uuid.uuid4().hex, units[3], junk, person))
        for number, unit in enumerate(units[:2]):
            connection.execute(LINK, (unit, f"ОБ.{number}", uuid.uuid4().hex))
        connection.execute(SNAPSHOT, (units[0], 102, AT))
        connection.execute(SNAPSHOT, (units[1], None, None))
        for unit, state, at in ((units[2], 70000, AT), (units[2], -1, AT), (units[2], 102, None), (units[3], None, AT)):
            connection.execute(LINK, (unit, f"ОБ.{unit[:6]}", uuid.uuid4().hex))
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(SNAPSHOT, (unit, state, at))
            connection.execute("DELETE FROM production_machine_links WHERE equipment_id = ?", (unit,))
    command.downgrade(config, "-1")
    with sqlite3.connect(path) as connection:
        left = sorted(name for (name,) in connection.execute("SELECT command FROM production_machine_writes"))
        columns = {name for _, name, *_ in connection.execute("PRAGMA table_info(production_machine_snapshots)")}
        snapshots = connection.execute("SELECT COUNT(*) FROM production_machine_snapshots").fetchone()[0]
        connection.execute("PRAGMA foreign_keys = OFF")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(INSERT, (uuid.uuid4().hex, units[3], "hmi_close", person))
    assert left == ["stop"]
    assert not {"last_state", "last_state_at"} & columns and snapshots == 2
