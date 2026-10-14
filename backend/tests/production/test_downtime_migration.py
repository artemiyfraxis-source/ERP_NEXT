import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REVISION = "0117_production_downtimes"
COLUMNS = (
    "id, task_id, operation_id, operator_id, operator_name, reason, comment, started_at, started_by_id, started_by_name,"
    " project, task_number, item, stage, ended_at, ended_by_id, ended_by_name"
)


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _row(task: str, reason: str = "no_material", comment: str = "", ended: str | None = None, ended_by: str | None = None) -> tuple:
    return (
        uuid.uuid4().hex, task, None, uuid.uuid4().hex, "Элина", reason, comment, "2026-10-13 09:00:00",
        uuid.uuid4().hex, "Мастер", "П3-0007", "ПЗ-0007", "КБ1", "Резка", ended, ended_by, "",
    )


def test_downtime_table_keeps_one_open_per_task_and_refuses_bad_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "downtime.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    insert = f"INSERT INTO production_downtimes ({COLUMNS}) VALUES ({', '.join('?' * 17)})"
    task, other = uuid.uuid4().hex, uuid.uuid4().hex
    who = uuid.uuid4().hex
    with sqlite3.connect(path) as connection:
        connection.execute(insert, _row(task))
        connection.execute(insert, _row(task, "equipment", ended="2026-10-13 10:00:00", ended_by=who))
        connection.execute(insert, _row(other, "other", "сломался стол"))
        for bad in (
            _row(task, "waiting_qc"),  # второй открытый по заданию
            _row(uuid.uuid4().hex, "lunch"),  # причина не из списка
            _row(uuid.uuid4().hex, "other", "  "),  # «Другое» без комментария
            _row(uuid.uuid4().hex, ended="2026-10-13 08:59:00", ended_by=who),  # конец раньше начала
            _row(uuid.uuid4().hex, ended="2026-10-13 10:00:00"),  # конец без того, кто закончил
            _row(uuid.uuid4().hex, ended_by=who),  # кто закончил — без конца
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, bad)
        assert connection.execute("SELECT COUNT(*) FROM production_downtimes").fetchone() == (3,)

    command.downgrade(config, "-1")  # на родителя, каким бы он ни был после перестановки цепочки
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "production_downtimes" not in tables
