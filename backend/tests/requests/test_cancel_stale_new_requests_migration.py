"""Migration 0160 (owner 28.10.2026, «сделай так, чтобы там пусто было — там сейчас всё старое», «нет, и после 28 тоже»): every
«Новый» request is cancelled with an audit event, whatever its date; requests in work stay; the downgrade restores them."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REVISION = "0160_cancel_stale_new_requests"
INSERT = (
    "INSERT INTO requests (id, legacy_id, creation_key, creation_index, creation_fingerprint, designation, status,"
    " version, created_at, actor_id, actor_name, actor_department, task_type, priority, purpose, delivery_point, name,"
    " item_type, category, quantity, unit, quality_document, procurement_deadline, planned_date)"
    " VALUES (?, ?, ?, 0, 'f', '663К.1.001', ?, 1, ?, ?, 'Домрачев Виктор', 'Снабжение', 'Закупка', 'Обычный',"
    " 'Производство', 'ЦАР', 'SYM-001T-P0.6', 'Терминал', 'Покупное', 10, 'шт', 'Нет', '2026-10-12', '2026-10-12')"
)


def _status(connection, legacy_id: str) -> tuple[str, int]:
    return connection.execute("SELECT status, version FROM requests WHERE legacy_id = ?", (legacy_id,)).fetchone()


def test_old_new_requests_are_cancelled_and_the_downgrade_brings_them_back(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, f"{REVISION}-1")
    with sqlite3.connect(path) as connection:
        for index, (legacy_id, status, created_at) in enumerate((
            ("SM-OLD-NEW", "Новый", "2026-10-06 16:36:50+03:00"),
            ("SM-OLD-WORK", "В работе", "2026-10-06 16:36:50+03:00"),
            ("SM-FRESH-NEW", "Новый", "2026-10-28 09:00:00+03:00"),
        )):
            connection.execute(INSERT, (f"{index:032x}", legacy_id, f"k{index}", status, created_at, "a" * 32))
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        assert _status(connection, "SM-OLD-NEW") == ("Отменено", 2)
        assert _status(connection, "SM-OLD-WORK") == ("В работе", 1)
        assert _status(connection, "SM-FRESH-NEW") == ("Отменено", 2)
        events = connection.execute(
            "SELECT aggregate_id, actor_name FROM audit_events WHERE event_type = 'request.cancelled'"
        ).fetchall()
        assert sorted(events) == [(f"{0:032x}", "Очистка заявок"), (f"{2:032x}", "Очистка заявок")]
    command.downgrade(config, f"{REVISION}-1")
    with sqlite3.connect(path) as connection:
        assert _status(connection, "SM-OLD-NEW") == ("Новый", 1)
        assert _status(connection, "SM-FRESH-NEW") == ("Новый", 1)
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE event_type = 'request.cancelled'").fetchone() == (0,)
