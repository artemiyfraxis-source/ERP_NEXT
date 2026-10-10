"""Миграция 0090: история версий чертежа жгута. Каждый чертёж (привязанный и отвязанный, с байтами) получает версию 1
«начальная» — свои байты, автора и время; строки КД не меняются; правила таблицы держит база."""

import hashlib
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "0089_harness_one_linked_drawing"
AFTER = "0090_harness_revisions"
LINKED = "application/vnd.fraxis.harness+json"
UNLINKED = "application/vnd.fraxis.harness-unlinked+json"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _id(number: int) -> str:
    return UUID(int=number).hex


def _insert(connection, number, index, content_type, content, uploaded_at="2026-10-01 10:00:00.000000"):
    connection.execute(
        "INSERT INTO techcard_kitset_documents (id, project, item_index, file_name, content_type, size, content,"
        " file_id, url, uploaded_at, uploaded_by) VALUES (?, '708К.4', ?, ?, ?, ?, ?, '', '', ?, ?)",
        (_id(number), index, f"Чертёж {number}", content_type, None if content is None else len(content), content,
         uploaded_at, f"Технолог {number}"),
    )


def test_every_drawing_gets_its_bytes_as_revision_one_and_nothing_else_changes(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database_path = tmp_path / "harness.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    drawings = {1: b'{"schemaVersion":1,"wires":[]}', 2: b'{"a":1}', 3: "{\"имя\":\"чертёж\"}".encode()}
    with sqlite3.connect(database_path) as connection:
        _insert(connection, 1, "КБ1", LINKED, drawings[1], "2026-10-03 10:00:00.000000")
        _insert(connection, 2, "КБ1", UNLINKED, drawings[2])
        _insert(connection, 3, "КБ2", LINKED, drawings[3])
        _insert(connection, 4, "КБ1", "application/pdf", b"%PDF")       # a file — not a drawing
        _insert(connection, 5, "КБ3", UNLINKED, None)                   # no bytes — named, skipped
        before = connection.execute("SELECT * FROM techcard_kitset_documents ORDER BY id").fetchall()

    command.upgrade(config, AFTER)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT * FROM techcard_kitset_documents ORDER BY id").fetchall() == before
        rows = connection.execute(
            "SELECT document_id, revision, content, content_sha256, size, kind, restored_from, saved_by, saved_at"
            " FROM techcard_harness_revisions ORDER BY document_id").fetchall()
        drawing_count = connection.execute(
            "SELECT count(*) FROM techcard_kitset_documents WHERE content_type IN (?, ?) AND content IS NOT NULL",
            (LINKED, UNLINKED)).fetchone()[0]
    assert len(rows) == drawing_count == 3
    assert [(row[0], row[1], row[5], row[6], row[7]) for row in rows] == [
        (_id(1), 1, "initial", None, "Технолог 1"), (_id(2), 1, "initial", None, "Технолог 2"),
        (_id(3), 1, "initial", None, "Технолог 3"),
    ]
    for document_id, _revision, content, sha, size, *_rest in rows:
        number = UUID(hex=document_id).int
        assert content == drawings[number] and size == len(content)
        assert sha == hashlib.sha256(content).hexdigest()
    assert rows[0][8].startswith("2026-10-03 10:00:00")
    output = capsys.readouterr().out
    assert "строк КД 4, начальных версий записано 3" in output and str(UUID(int=5)) in output


def test_the_table_holds_its_rules(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database_path = tmp_path / "harness.db"
    command.upgrade(_config(database_path), AFTER)
    sha = "a" * 64

    def add(connection, revision, *, document=1, kind="saved", restored_from=None, digest=sha):
        connection.execute(
            "INSERT INTO techcard_harness_revisions (id, document_id, revision, content, content_sha256, size, kind,"
            " restored_from, saved_by, saved_at) VALUES (?, ?, ?, x'7b7d', ?, 2, ?, ?, 'Технолог',"
            " '2026-10-10 10:00:00')",
            (UUID(int=1000 + revision + 100 * document).hex, _id(document), revision, digest, kind, restored_from),
        )

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _insert(connection, 1, "КБ1", LINKED, b"{}")
        add(connection, 1)
        add(connection, 2, kind="restored", restored_from=1)
        for bad in (
            dict(revision=1),                                   # the same number twice
            dict(revision=0),                                   # not positive
            dict(revision=3, kind="edited"),                    # an unknown kind
            dict(revision=3, restored_from=1),                  # «restored from» on a plain save
            dict(revision=3, kind="restored", restored_from=3),  # restored from itself
            dict(revision=3, digest="abc"),                     # not a sha256
            dict(revision=1, document=9),                       # no such КД row
        ):
            with pytest.raises(sqlite3.IntegrityError):
                add(connection, **bad)
        with pytest.raises(sqlite3.IntegrityError):  # a drawing with history is not deleted
            connection.execute("DELETE FROM techcard_kitset_documents WHERE id = ?", (_id(1),))
        assert connection.execute("SELECT count(*) FROM techcard_harness_revisions").fetchone() == (2,)
