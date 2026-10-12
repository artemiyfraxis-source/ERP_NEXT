"""0108_coax_cable_aliases: a full-name column and the alias table over existing cards; nothing existing changes."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def test_migration_adds_names_keeps_cards_and_refuses_bad_aliases(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, "0107_stage_ladder")
    with sqlite3.connect(database) as connection:
        for card_id, name in (("c1", "RG-316"), ("c2", "RG-58")):
            connection.execute(
                "INSERT INTO coax_cables (id, name, name_key, family, span_note, fc_note, max_operating_note, notes,"
                " param_notes, sources, confidence, verified, changes, position, imported_at)"
                " VALUES (?, ?, ?, '', '', '', '', '', '{}', '[]', '', 1, '[]', 0, '2026-10-12')",
                (card_id, name, name.lower().replace("-", "")))
        before = connection.execute("SELECT * FROM coax_cables ORDER BY id").fetchall()

    command.upgrade(config, "0108_coax_cable_aliases")
    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT * FROM coax_cables ORDER BY id").fetchall()
        assert [row[:-1] for row in after] == before and {row[-1] for row in after} == {""}  # full_name '' for each
        connection.execute("INSERT INTO coax_cable_aliases (id, cable_id, alias, alias_key, kind, supplier, url, position)"
                           " VALUES ('a1', 'c1', '1951', '1951', 'supplier_article', 'ГСМ Репитеры', '', 0)")
        for bad in (
            "INSERT INTO coax_cable_aliases (id, cable_id, alias, alias_key, kind) VALUES ('b1', 'c2', '1951', '1951', 'mark')",
            "INSERT INTO coax_cable_aliases (id, cable_id, alias, alias_key, kind) VALUES ('b2', 'c2', 'X', 'x', 'nickname')",
            "INSERT INTO coax_cable_aliases (id, cable_id, alias, alias_key, kind) VALUES ('b3', 'c2', '42', '42', 'supplier_article')",
            "INSERT INTO coax_cable_aliases (id, cable_id, alias, alias_key, kind) VALUES ('b4', 'c2', '', '', 'mark')",
            "INSERT INTO coax_cable_aliases (id, cable_id, alias, alias_key, kind) VALUES ('b5', NULL, 'Y', 'y', 'mark')",
        ):
            try:
                connection.execute(bad)
            except sqlite3.IntegrityError:
                continue
            raise AssertionError(f"accepted: {bad}")

    command.downgrade(config, "0107_stage_ladder")
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "coax_cable_aliases" not in tables
        assert connection.execute("SELECT * FROM coax_cables ORDER BY id").fetchall() == before
