"""Миграция 0089: у позиции не больше одного привязанного чертежа жгута. Лишние (старые) переводятся в отвязанные —
ни одна строка не теряется, — изменения в журнале; потом частичный уникальный индекс не даёт второго привязанного."""

import json
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "0088_api_tokens_assembly_drafts"
AFTER = "0090_harness_one_linked_drawing"
INDEX = "ux_techcard_kitset_documents_linked_harness"
LINKED = "application/vnd.fraxis.harness+json"
UNLINKED = "application/vnd.fraxis.harness-unlinked+json"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _id(number: int) -> str:
    return UUID(int=number).hex


def _insert(connection, number, index, content_type, uploaded_at, project="708К.4"):
    connection.execute(
        "INSERT INTO techcard_kitset_documents (id, project, item_index, file_name, content_type, size, content,"
        " file_id, url, uploaded_at, uploaded_by) VALUES (?, ?, ?, ?, ?, 2, ?, '', '', ?, 'Технолог')",
        (_id(number), project, index, f"{project}{index} — Чертёж жгута {number}", content_type, b"{}", uploaded_at),
    )


def _types(database_path):
    with sqlite3.connect(database_path) as connection:
        return dict(connection.execute("SELECT id, content_type FROM techcard_kitset_documents").fetchall())


def test_duplicates_keep_the_newest_linked_the_rest_become_restorable_and_the_index_holds(tmp_path, capsys):
    database_path = tmp_path / "harness.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        _insert(connection, 1, "КБ1", LINKED, "2026-10-01 10:00:00.000000")
        _insert(connection, 2, "КБ1", LINKED, "2026-10-03 10:00:00.000000")  # the newest — stays linked
        _insert(connection, 3, "кб1", LINKED, "2026-10-02 10:00:00.000000")  # same position, other case
        _insert(connection, 4, "КБ1", UNLINKED, "2026-10-04 10:00:00.000000")  # already unlinked — untouched
        _insert(connection, 5, "КБ1", "application/pdf", "2026-10-05 10:00:00.000000")  # a file — untouched
        _insert(connection, 6, "КБ2", LINKED, "2026-10-01 10:00:00.000000")  # alone on its position
        _insert(connection, 7, "КБ1", LINKED, "2026-10-01 09:00:00.000000", project="709К.1")  # other project

    command.upgrade(config, AFTER)

    assert _types(database_path) == {
        _id(1): UNLINKED, _id(2): LINKED, _id(3): UNLINKED, _id(4): UNLINKED, _id(5): "application/pdf",
        _id(6): LINKED, _id(7): LINKED,
    }
    with sqlite3.connect(database_path) as connection:
        events = connection.execute(
            "SELECT event_type, actor_name, details FROM audit_events ORDER BY details"
        ).fetchall()
        assert connection.execute("SELECT count(*) FROM techcard_kitset_documents").fetchone() == (7,)
        assert connection.execute("SELECT count(*) FROM techcard_kitset_documents WHERE content IS NULL").fetchone() == (0,)
    assert [(event, actor) for event, actor, _details in events] == [
        ("techcards.kitset_harness_unlinked", "Миграция 0089"), ("techcards.kitset_harness_unlinked", "Миграция 0089"),
    ]
    details = sorted((json.loads(raw) for _event, _actor, raw in events), key=lambda item: item["document_id"])
    assert [(item["document_id"], item["kept_document_id"]) for item in details] == [
        (str(UUID(int=1)), str(UUID(int=2))), (str(UUID(int=3)), str(UUID(int=2))),
    ]
    output = capsys.readouterr().out
    assert "отвязано лишних 2" in output and str(UUID(int=1)) in output

    with sqlite3.connect(database_path) as connection:
        indexes = {row[1]: row for row in connection.execute("PRAGMA index_list(techcard_kitset_documents)")}
        assert indexes[INDEX][2] == 1 and indexes[INDEX][4] == 1  # unique, partial
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"UPDATE techcard_kitset_documents SET content_type = '{LINKED}' WHERE id = ?", (_id(1),))
        # Unlinked drawings and files of the position are as many as they were.
        _insert(connection, 8, "КБ1", UNLINKED, "2026-10-06 10:00:00.000000")
        _insert(connection, 9, "КБ1", "application/pdf", "2026-10-06 10:00:00.000000")

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        names = {row[1] for row in connection.execute("PRAGMA index_list(techcard_kitset_documents)")}
        assert INDEX not in names
        assert connection.execute("SELECT count(*) FROM techcard_kitset_documents").fetchone() == (9,)


def test_without_duplicates_nothing_changes(tmp_path, capsys):
    database_path = tmp_path / "harness.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        _insert(connection, 1, "КБ1", LINKED, "2026-10-01 10:00:00.000000")
        _insert(connection, 2, "КБ1", UNLINKED, "2026-10-02 10:00:00.000000")
    command.upgrade(config, AFTER)
    assert _types(database_path) == {_id(1): LINKED, _id(2): UNLINKED}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
    assert "отвязано лишних 0" in capsys.readouterr().out
