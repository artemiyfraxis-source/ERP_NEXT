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
