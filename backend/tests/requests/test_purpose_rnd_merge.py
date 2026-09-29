"""«Опытный образец (НИОКР)» — то же, что RND (владелец 29.09.2026): справочник, приём старого значения, миграция 0070."""

import sqlite3
from dataclasses import replace
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.modules.requests.domain import STATIC_PURPOSES, validate_draft

from .test_project_reference import draft


def test_purpose_list_has_rnd_but_no_separate_prototype_item():
    assert "RND" in STATIC_PURPOSES
    assert not [item for item in STATIC_PURPOSES if "Опытный образец" in item]


def test_old_prototype_purpose_is_accepted_and_stored_as_rnd():
    assert validate_draft(replace(draft(None), purpose="Опытный образец (НИОКР)")).purpose == "RND"


def _fill(column_type: str, name: str) -> object:
    kind = column_type.upper()
    if name == "id" or "CHAR(32)" in kind or "UUID" in kind:
        return None  # set per row below
    if any(token in kind for token in ("INT", "NUMERIC", "DECIMAL", "FLOAT", "BOOL")):
        return 0
    if "DATETIME" in kind or "TIMESTAMP" in kind:
        return "2026-09-29 10:00:00"
    if "DATE" in kind:
        return "2026-09-29"
    return "x"


def test_migration_moves_existing_prototype_requests_to_rnd(tmp_path):
    database_path = tmp_path / "requests.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0069_techcard_kd_in_database")
    with sqlite3.connect(database_path) as connection:
        columns = connection.execute("PRAGMA table_info(requests)").fetchall()
        required = [(name, kind) for _, name, kind, notnull, default, pk in columns if (notnull or pk) and default is None]
        for index, purpose in enumerate(("Опытный образец (НИОКР)", "FRX")):
            values = {name: _fill(kind, name) for name, kind in required}
            for name, kind in required:
                if values[name] is None:
                    values[name] = f"{index:032x}"
            values.update({"purpose": purpose, "legacy_id": f"REQ-{index}"})
            names = ", ".join(values)
            connection.execute(f"INSERT INTO requests ({names}) VALUES ({', '.join('?' for _ in values)})", list(values.values()))

    command.upgrade(config, "0070_purpose_rnd_merge")
    with sqlite3.connect(database_path) as connection:
        purposes = [row[0] for row in connection.execute("SELECT purpose FROM requests ORDER BY legacy_id")]
    assert purposes == ["RND", "FRX"]
