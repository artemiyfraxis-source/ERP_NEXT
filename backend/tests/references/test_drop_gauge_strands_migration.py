"""0114_drop_wire_gauge_strands: the column goes, its non-empty values stay in the journal, downgrade brings them back."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

REVISION = "0114_drop_wire_gauge_strands"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _columns(connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(wire_gauges)")}


def test_strands_are_journalled_dropped_and_restored(tmp_path):
    database = tmp_path / "gauges.db"
    config = _config(database)
    command.upgrade(config, "0112_harness_route_v3")
    with sqlite3.connect(database) as connection:
        assert "strands" in _columns(connection)
        connection.execute("UPDATE wire_gauges SET strands = ''")
        kept = connection.execute("SELECT id FROM wire_gauges WHERE awg = '28'").fetchone()[0]
        connection.execute("UPDATE wire_gauges SET strands = '7×0,127' WHERE id = ?", (kept,))
        connection.execute("INSERT INTO wire_gauges (id, awg, section_mm2, strands, position) VALUES (?, NULL, 0.0123, '  ', 99)",
                           (uuid4().hex,))
        gauges_before = connection.execute("SELECT COUNT(*) FROM wire_gauges").fetchone()[0]

    command.upgrade(config, REVISION)
    with sqlite3.connect(database) as connection:
        assert "strands" not in _columns(connection)
        assert connection.execute("SELECT COUNT(*) FROM wire_gauges").fetchone()[0] == gauges_before
        events = connection.execute("SELECT details FROM audit_events WHERE event_type = 'references.wire_gauge_strands_dropped'").fetchall()
        assert len(events) == 1
        assert [(item["awg"], item["strands"]) for item in json.loads(events[0][0])["gauges"]] == [("28", "7×0,127")]

    command.downgrade(config, "0112_harness_route_v3")
    with sqlite3.connect(database) as connection:
        assert dict(connection.execute("SELECT awg, strands FROM wire_gauges WHERE strands <> ''").fetchall()) == {"28": "7×0,127"}


def test_empty_column_leaves_no_event(tmp_path):
    database = tmp_path / "empty.db"
    config = _config(database)
    command.upgrade(config, "0112_harness_route_v3")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE wire_gauges SET strands = ''")
    command.upgrade(config, REVISION)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type = 'references.wire_gauge_strands_dropped'").fetchone()[0] == 0
