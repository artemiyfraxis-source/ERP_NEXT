"""Миграция 0149: КОЛ-ВО БРАКА строки приёмки — пустой столбец и CHECK; строки не переписаны, индекс живой строки цел."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

AFTER = "0149_receiving_defect_quantity"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


# The revision right before this migration (it changes when the migration is renumbered at merge).
BEFORE = ScriptDirectory.from_config(_config(Path("unused.db"))).get_revision(AFTER).down_revision


def _line(connection, number: int, *, status="Ожидание", procurement=None, fact="10", **extra) -> None:
    columns = {
        "id": f"{number:032d}", "source_event_id": f"e{number:031d}", "source_idempotency_key": f"key-{number}",
        "procurement_line_id": procurement or f"p{number:031d}", "legacy_id": f"SM-{number}", "status": status,
        "version": 2, "created_at": "2026-10-20 10:00:00", "warehouse": "ЦАР", "article": "CON-001", "series": "",
        "name": "Разъём", "item_type": "Комплектующие", "purpose": "FRX.001", "goal": "", "source_link": "",
        "supplier": "ООО Поставка", "ordered_quantity": 10, "unit": "шт", "quality_document_required": 0,
        "fact_quantity": fact, "nonconformities": '["\\u041c"]', "backorder_quantity": 0, "package": "",
        "warehouse_event_emitted": 0, **extra,
    }
    connection.execute(
        f"INSERT INTO receiving_lines ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        tuple(columns.values()),
    )


def _columns(connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(receiving_lines)")}


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    command.upgrade(_config(path), BEFORE)
    return path


def test_upgrade_adds_an_empty_checked_column_and_downgrade_removes_it(database):
    with sqlite3.connect(database) as connection:
        _line(connection, 1)
        _line(connection, 2, status="Принято", fact=None)
        before = connection.execute("SELECT * FROM receiving_lines ORDER BY id").fetchall()

    command.upgrade(_config(database), AFTER)

    with sqlite3.connect(database) as connection:
        assert "defect_quantity" in _columns(connection)
        rows = connection.execute("SELECT * FROM receiving_lines ORDER BY id").fetchall()
        assert [row[:-1] for row in rows] == before and [row[-1] for row in rows] == [None, None]  # nothing rewritten
        connection.execute(f"UPDATE receiving_lines SET defect_quantity = 10 WHERE id = '{1:032d}'")  # ≤ fact
        for wrong in ("11", "0", "-1"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(f"UPDATE receiving_lines SET defect_quantity = {wrong} WHERE id = '{1:032d}'")
        with pytest.raises(sqlite3.IntegrityError):  # no fact — no defect count
            connection.execute(f"UPDATE receiving_lines SET defect_quantity = 1 WHERE id = '{2:032d}'")
        # The partial unique index of the live line survived the table rebuild with its condition.
        _line(connection, 3, procurement="p" + "9" * 31)
        _line(connection, 4, procurement="p" + "9" * 31, status="Архив ВК")
        with pytest.raises(sqlite3.IntegrityError):
            _line(connection, 5, procurement="p" + "9" * 31)

    command.downgrade(_config(database), BEFORE)

    with sqlite3.connect(database) as connection:
        assert "defect_quantity" not in _columns(connection)
        assert connection.execute("SELECT count(*) FROM receiving_lines").fetchone() == (4,)
        with pytest.raises(sqlite3.IntegrityError):
            _line(connection, 6, procurement="p" + "9" * 31)
