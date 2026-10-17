import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BEFORE = "0025_references_gost_awg_seed"
AFTER = "0026_production_output_corrections"


def _session_id_nullable(connection: sqlite3.Connection) -> bool:
    columns = {row[1]: row for row in connection.execute("PRAGMA table_info(production_outputs)")}
    return columns["session_id"][3] == 0


def _unique_indexes(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA index_list({table})") if row[2] == 1}


def _defect_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(production_defects)")}


def _replacement_foreign_keys(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row[2], row[4]) for row in connection.execute("PRAGMA foreign_key_list(production_defects)")
        if row[3] == "replaced_by_output_id"
    ]


def test_correction_migration_relaxes_output_session_and_marks_replaced_defects(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "production.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        assert not _session_id_nullable(connection)
        assert "replaced_by_output_id" not in _defect_columns(connection)

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        assert _session_id_nullable(connection)
        assert "ux_production_outputs_correction_of_id" in _unique_indexes(connection, "production_outputs")
        assert "replaced_by_output_id" in _defect_columns(connection)
        assert "ux_production_defects_replaced_by_output_id" in _unique_indexes(connection, "production_defects")
        assert _replacement_foreign_keys(connection) == [("production_outputs", "id")]

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        assert not _session_id_nullable(connection)
        assert "ux_production_outputs_correction_of_id" not in _unique_indexes(connection, "production_outputs")
        assert "replaced_by_output_id" not in _defect_columns(connection)

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        assert _session_id_nullable(connection)
