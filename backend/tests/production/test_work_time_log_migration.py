import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0105_work_time_log"
COLUMNS = (
    "id, idempotency_key, session_id, sequence, event, occurred_at, task_id, operation_id, operator_id, operator_name,"
    " actor_id, actor_name, project, item, task_number, operation_code, stage, position, seconds_per_unit,"
    " setup_seconds, planned_norm_seconds, good_quantity, defect_quantity, running_seconds, earned_norm_seconds"
)


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _row(key: str, event: str = "start", sequence: int = 0, finish: tuple | None = None) -> tuple:
    ids = [uuid.uuid4().hex for _ in range(6)]
    return (
        ids[0], key, ids[1], sequence, event, "2026-10-12 14:05:00", ids[2], ids[3], ids[4], "Элина",
        ids[5], "Артемий", "П3-0007", "180К.11.КБ1", "ПЗ-0007", "CUT_WIRE+2crimp", "Резка", 1, 36, 60, 3660,
        *(finish or (None, None, None, None)),
    )


def test_work_time_log_table_keeps_its_rows_and_refuses_bad_ones(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "worklog.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    insert = f"INSERT INTO production_work_time_log ({COLUMNS}) VALUES ({', '.join('?' * 25)})"
    with sqlite3.connect(path) as connection:
        connection.execute(insert, _row("k-1"))
        connection.execute(insert, _row("k-2", "finish", 1, (100, 0, 5880, 3660)))
        for bad in (
            _row("k-1"),  # тот же ключ повтора
            _row("k-3", "jump"),  # чужое событие
            _row("k-4", "finish"),  # сдача без штук и факта
            _row("k-5", "start", -1),  # отрицательный номер
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, bad)
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_work_time_log SET running_seconds = 1")
        assert connection.execute("SELECT COUNT(*) FROM production_work_time_log").fetchone() == (2,)

    command.downgrade(config, "-1")  # на родителя, каким бы он ни был после перестановки цепочки
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "production_work_time_log" not in tables
