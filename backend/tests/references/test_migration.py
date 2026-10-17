import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_gost_awg_seed_keeps_admin_rows_and_downgrade_removes_only_that_kind(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "references.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    command.upgrade(config, "0024_procurement_money")
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO reference_entries (id, kind, key, normalized_key, value, position)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                ("5" * 32, "gost_awg", "0.35", "0.35", "20"),
                ("6" * 32, "supplier", "chipdip.ru", "chipdip.ru", "ЧИП и ДИП"),
            ),
        )

    command.upgrade(config, "0025_references_gost_awg_seed")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            """
            SELECT key, normalized_key, value, position FROM reference_entries
            WHERE kind = 'gost_awg' ORDER BY position
            """
        ).fetchall() == [
            ("0.35", "0.35", "20", 0),
            ("0,75", "0.75", "18", 1),
            ("1,0", "1", "17", 2),
        ]

    command.downgrade(config, "0024_procurement_money")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT kind, key FROM reference_entries"
        ).fetchall() == [("supplier", "chipdip.ru")]


def test_gost_awg_full_seed_matches_spr_kab_and_keeps_admin_rows(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "references.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    command.upgrade(config, "0042_shipping_project_addressees")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO reference_entries (id, kind, key, normalized_key, value, position)
            VALUES (?, 'gost_awg', '1,5', '1.5', '15', 3)
            """,
            ("7" * 32,),
        )

    command.upgrade(config, "0043_references_gost_awg_full_seed")
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT normalized_key, value FROM reference_entries WHERE kind = 'gost_awg' ORDER BY position"
        ).fetchall()
    assert dict(rows) == {
        "0.35": "22", "0.75": "18", "1": "17", "1.5": "15",
        "0.01": "36", "0.02": "34", "0.03": "32", "0.05": "30", "0.08": "28", "0.12": "26",
        "0.2": "24", "0.5": "20", "2.5": "14", "4": "12", "6": "10", "10": "8",
        "16": "6", "25": "4", "35": "2",
    }
    assert [key for key, _value in rows[:4]] == ["0.35", "0.75", "1", "1.5"]
    assert len(rows) == 19

    command.downgrade(config, "0042_shipping_project_addressees")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT normalized_key, value FROM reference_entries WHERE kind = 'gost_awg' ORDER BY position"
        ).fetchall() == [("0.35", "22"), ("0.75", "18"), ("1", "17"), ("1.5", "15")]
