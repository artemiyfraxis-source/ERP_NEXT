"""Миграция 0113: номера запусков существующих заданий по порядку создания внутри КБ, NULL без техкарты, уникальность."""

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

CARD_A, CARD_B = UUID(int=0xA), UUID(int=0xB)


def _task(connection, number: str, created_at: str, techcard: UUID | None) -> None:
    connection.execute(
        "INSERT INTO production_tasks (id, number, source_idempotency_key, source_fingerprint, version, created_at,"
        " project, item, name, quantity, status, launched_by, handed_to, flow_mode, techcard_id)"
        " VALUES (?, ?, ?, 'f', 1, ?, '708К', 'КБ1', 'Жгут', 10, 'running', 'Мастер', '', 'mixed', ?)",
        (UUID(int=int(number[-4:]) + 0x100).hex, number, f"key-{number}", created_at,
         techcard.hex if techcard else None),
    )


def test_launch_seq_backfills_by_creation_inside_each_card_and_is_unique(tmp_path):
    database = tmp_path / "launch-seq.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database.as_posix()}")
    command.upgrade(config, "0112_harness_route_v3")
    with sqlite3.connect(database) as connection:
        _task(connection, "ПЗ-0003", "2026-10-03 09:00:00", CARD_A)
        _task(connection, "ПЗ-0001", "2026-10-01 09:00:00", CARD_A)
        _task(connection, "ПЗ-0002", "2026-10-01 09:00:00", CARD_A)  # тот же момент — по номеру ПЗ
        _task(connection, "ПЗ-0004", "2026-10-02 09:00:00", CARD_B)
        _task(connection, "ПЗ-0005", "2026-10-02 09:00:00", None)

    command.upgrade(config, "0113_task_launch_seq")

    with sqlite3.connect(database) as connection:
        rows = dict(connection.execute("SELECT number, launch_seq FROM production_tasks").fetchall())
        assert rows == {"ПЗ-0001": 1, "ПЗ-0002": 2, "ПЗ-0003": 3, "ПЗ-0004": 1, "ПЗ-0005": None}
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE production_tasks SET launch_seq = 1 WHERE number = 'ПЗ-0003'")
        _task(connection, "ПЗ-0006", "2026-10-04 09:00:00", None)  # без техкарты номера нет — и не мешает

    command.downgrade(config, "0112_harness_route_v3")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(production_tasks)")}
        assert "launch_seq" not in columns
        assert connection.execute("SELECT count(*) FROM production_tasks").fetchone() == (6,)
