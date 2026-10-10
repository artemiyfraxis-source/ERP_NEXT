"""Миграция 0091: библиотека компонентов. Копии шаблонов из чертежей и их истории переносятся в таблицу без повторов
(по содержимому), байты чертежей и истории не меняются, плюс начальные шаблоны JST SH; правила таблицы держит база;
хэши считаются так же, как в приложении."""

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

from app.modules.techcards.harness.components import candidate_from_snapshot, version_sha256

BEFORE = "0091_harness_revisions"
AFTER = "0092_harness_component_library"
LINKED = "application/vnd.fraxis.harness+json"
UNLINKED = "application/vnd.fraxis.harness-unlinked+json"
TEMPLATE = "22222222-3333-4444-8555-666666666666"
OTHER = "33333333-4444-4555-8666-777777777777"
SEED = Path(__file__).parents[2] / "data" / "harness-library" / "seed.json"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _id(number: int) -> str:
    return UUID(int=number).hex


def _snapshot(snapshot_id: int, template: str, version: int, code: str, *, project="p", sha="a"):
    return {"snapshotId": str(UUID(int=snapshot_id)), "projectId": project, "sourceTemplateId": template,
            "sourceVersion": version, "sourceVersionSha256": sha * 64, "schemaVersion": 5, "code": code,
            "name": f"Разъём {code}", "articleBindings": [{"sourceId": "technology", "entityType": "connector",
                                                          "articleKey": code}],
            "assets": [], "content": {"schemaVersion": 5, "views": [version]},
            "createdUtc": f"2026-10-0{snapshot_id % 9 + 1}T00:00:00Z", "updatedUtc": "2026-10-05T00:00:00Z"}


def _drawing(*snapshots) -> bytes:
    return json.dumps({"schemaVersion": 1, "connectors": [], "componentSnapshots": list(snapshots),
                       "componentPlacements": []}, ensure_ascii=False).encode()


def _insert(connection, number, index, content_type, content, uploaded_at="2026-10-01 10:00:00.000000"):
    connection.execute(
        "INSERT INTO techcard_kitset_documents (id, project, item_index, file_name, content_type, size, content,"
        " file_id, url, uploaded_at, uploaded_by) VALUES (?, '708К.4', ?, ?, ?, ?, ?, '', '', ?, ?)",
        (_id(number), index, f"Чертёж {number}", content_type, None if content is None else len(content), content,
         uploaded_at, f"Технолог {number}"),
    )


def _revision(connection, number, document, content):
    connection.execute(
        "INSERT INTO techcard_harness_revisions (id, document_id, revision, content, content_sha256, size, kind,"
        " restored_from, first_revision, closed, saved_by, started_at, saved_at) VALUES (?, ?, ?, ?, ?, ?, 'saved',"
        " NULL, ?, 0, 'Мастер', '2026-09-30 10:00:00', '2026-09-30 10:00:00')",
        (UUID(int=900 + number).hex, _id(document), number, content, hashlib.sha256(content).hexdigest(),
         len(content), number),
    )


def test_copies_move_into_the_library_once_drawings_stay_and_the_seed_lands(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database_path = tmp_path / "harness.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    ds1069 = _snapshot(1, TEMPLATE, 3, "DS1069")
    with sqlite3.connect(database_path) as connection:
        # The same revision in two drawings (another snapshot id, project, time) — one row.
        _insert(connection, 1, "КБ1", LINKED, _drawing(ds1069), "2026-10-03 10:00:00.000000")
        _insert(connection, 2, "КБ2", UNLINKED, _drawing(_snapshot(2, TEMPLATE, 3, "DS1069", project="q")))
        # The next revision of that template, and another template with the same code — its own key.
        _insert(connection, 3, "КБ3", LINKED, _drawing(_snapshot(3, TEMPLATE, 4, "DS1069", sha="b"),
                                                       _snapshot(4, OTHER, 3, "ds1069", sha="c")))
        # Not a template revision (no id of a template) — stays in the drawing, named in the output.
        _insert(connection, 4, "КБ4", LINKED, _drawing({**ds1069, "sourceTemplateId": "t1"}))
        _insert(connection, 5, "КБ5", "application/pdf", b"%PDF")
        _insert(connection, 6, "КБ6", LINKED, b"not json")
        # A copy only the history still holds.
        _revision(connection, 1, 1, _drawing(_snapshot(5, TEMPLATE, 2, "DS1069", sha="d")))
        documents = connection.execute("SELECT * FROM techcard_kitset_documents ORDER BY id").fetchall()
        history = connection.execute("SELECT * FROM techcard_harness_revisions ORDER BY id").fetchall()

    command.upgrade(config, AFTER)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT * FROM techcard_kitset_documents ORDER BY id").fetchall() == documents
        assert connection.execute("SELECT * FROM techcard_harness_revisions ORDER BY id").fetchall() == history
        rows = connection.execute(
            "SELECT template_id, connector_ref, revision, version_sha256, content_sha256, created_by, snapshot_png,"
            " image_sha256, assets, model, code, name, article_bindings FROM harness_component_templates"
            " ORDER BY connector_ref, revision").fetchall()
    moved = [row for row in rows if row[5] != "Василий (TECHMAP-GRAPHER)"]
    assert [(UUID(row[0]), row[1], row[2], row[5]) for row in moved] == [
        (UUID(TEMPLATE), "DS1069", 2, "Мастер"), (UUID(TEMPLATE), "DS1069", 3, "Технолог 2"),
        (UUID(TEMPLATE), "DS1069", 4, "Технолог 3"), (UUID(OTHER), f"DS1069 ({OTHER[:8]})", 3, "Технолог 3"),
    ]
    # The content hash is the application's (`components.content_sha256`), so a later write dedupes against it.
    from app.modules.techcards.harness.components import Candidate
    assert moved[1][4] == candidate_from_snapshot(ds1069).content_sha256
    assert all(row[6] is None and row[7] is None for row in moved)

    seeded = [row for row in rows if row[5] == "Василий (TECHMAP-GRAPHER)"]
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    assert [row[1] for row in seeded] == [entry["connectorRef"] for entry in seed["templates"]]
    for row, entry in zip(seeded, seed["templates"]):
        assets, model, bindings = json.loads(row[8]), json.loads(row[9]), json.loads(row[12])
        assert row[3] == version_sha256(code=row[10], name=row[11], article_bindings=bindings, assets=assets,
                                        model=model)
        assert Candidate(template_id=UUID(row[0]), revision=row[2], version_sha256=row[3], code=row[10],
                         name=row[11], article_bindings=bindings, assets=assets, model=model).content_sha256 == row[4]
        picture = (SEED.parent / entry["picture"]).read_bytes()
        assert row[6] == picture and row[7] == hashlib.sha256(picture).hexdigest() == assets[0]["sha256"]
    output = capsys.readouterr().out
    assert "копий 5 (не шаблоны — 1), перенесено без повторов 4, начальных шаблонов 19, всего строк 23" in output
    assert "КБ6: не JSON" in output

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'harness_component_templates'").fetchall()
        assert connection.execute("SELECT * FROM techcard_kitset_documents ORDER BY id").fetchall() == documents


def test_the_table_holds_its_rules(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database_path = tmp_path / "harness.db"
    command.upgrade(_config(database_path), AFTER)

    def add(connection, number, *, ref="X1", template=TEMPLATE, revision=1, version="a" * 64, content=None,
            png=None, image=None):
        connection.execute(
            "INSERT INTO harness_component_templates (id, template_id, connector_ref, revision, version_sha256,"
            " content_sha256, code, name, article_bindings, assets, model, snapshot_png, image_sha256, created_by,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, 'X1', 'Разъём', '[]', '[]', '{}', ?, ?, 'Технолог',"
            " '2026-10-10 10:00:00')",
            (UUID(int=number).hex, UUID(template).hex, ref, revision, version,
             content or hashlib.sha256(str(number).encode()).hexdigest(), png, image),
        )

    with sqlite3.connect(database_path) as connection:
        add(connection, 1)
        add(connection, 2, revision=2, version="b" * 64, png=b"png", image="c" * 64)
        for number, bad in enumerate((
            dict(revision=1, version="d" * 64),            # the key and number are taken
            dict(ref="X2", revision=2, version="b" * 64),  # the pin (template, number, hash) names another row
            dict(ref="X2", content=hashlib.sha256(b"1").hexdigest()),  # the same content twice
            dict(ref="X2", revision=0),                    # not positive
            dict(ref="X2", version="abc"),                 # not a sha256
            dict(ref="   ", revision=5),                   # an empty key
            dict(ref="X2", png=b"png"),                    # a picture without its hash
            dict(ref="X2", image="e" * 64),                # a hash without its picture
        ), start=10):
            with pytest.raises(sqlite3.IntegrityError):
                add(connection, number, **bad)
        assert connection.execute("SELECT count(*) FROM harness_component_templates").fetchone()[0] == 2 + 19
