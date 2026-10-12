"""Миграция 0111: таблица маршрутов v3 позиций. Ничего существующего не меняется; правила таблицы держит база — один
маршрут на позицию, версия ≥ 1, версия чертежа ≥ 0, чертёж — существующая строка КД (не удалить, пока есть маршрут)."""

import sqlite3
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config

BEFORE = "0110_stage_warehouse_accepted"
AFTER = "0111_harness_route_v3"
LINKED = "application/vnd.fraxis.harness+json"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _id(number: int) -> str:
    return UUID(int=number).hex


def _route(connection, number, *, index="КБ1", revision=1, drawing=1, drawing_revision=3, by="Технолог",
           document='{"version": 1}'):
    connection.execute(
        "INSERT INTO techcard_harness_routes (id, project, item_index, revision, drawing_document_id,"
        " drawing_revision, document, updated_at, updated_by) VALUES (?, '708К.4', ?, ?, ?, ?, ?, ?, ?)",
        (_id(100 + number), index, revision, _id(drawing), drawing_revision, document,
         "2026-10-12 10:00:00.000000", by),
    )


def test_migration_adds_the_route_table_with_its_rules_and_changes_nothing_else(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO techcard_kitset_documents (id, project, item_index, file_name, content_type, size, content,"
            " file_id, url, uploaded_at, uploaded_by) VALUES (?, '708К.4', 'КБ1', 'Чертёж', ?, 2, ?, '', '',"
            " '2026-10-12 09:00:00.000000', 'Технолог')", (_id(1), LINKED, b"{}"))
        before = connection.execute("SELECT * FROM techcard_kitset_documents ORDER BY id").fetchall()

    command.upgrade(config, AFTER)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        assert connection.execute("SELECT * FROM techcard_kitset_documents ORDER BY id").fetchall() == before
        assert connection.execute("SELECT count(*) FROM techcard_harness_routes").fetchone() == (0,)
        _route(connection, 1)
        _route(connection, 2, index="КБ2", drawing_revision=0)
        refused = {
            "second route of a position": dict(index="КБ1"),
            "revision 0": dict(index="КБ3", revision=0),
            "negative drawing revision": dict(index="КБ4", drawing_revision=-1),
            "no such drawing": dict(index="КБ5", drawing=99),
            "empty author": dict(index="КБ6", by=" "),
            "empty index": dict(index=" "),
            "no document": dict(index="КБ7", document=None),
        }
        for name, values in refused.items():
            try:
                _route(connection, 10 + len(name), **values)
            except sqlite3.IntegrityError:
                continue
            raise AssertionError(f"accepted: {name}")
        try:
            connection.execute("DELETE FROM techcard_kitset_documents WHERE id = ?", (_id(1),))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("a drawing with a route was deleted")

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "techcard_harness_routes" not in tables
        assert connection.execute("SELECT * FROM techcard_kitset_documents ORDER BY id").fetchall() == before
