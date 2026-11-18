"""Миграция 0191: шаблоны карт операций без сторон (A)/(B) — одна карта на код (владелец 17.11.2026)."""

import contextlib
import io
import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

AFTER = "0191_operation_template_sides"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


BEFORE = ScriptDirectory.from_config(_config(Path("unused.db"))).get_revision(AFTER).down_revision
M1, M2, M3, M4 = (uuid4().hex for _ in range(4))
IMAGE = {"row": 19, "col": 9, "driveFileId": "img-a", "width": 654, "height": 674}
EDIT = {"name": "Монтаж", "safety": "", "defects": "", "revision": "", "tooling": [],
        "steps": [{"number": "1", "kind": "Операция", "text": "Правка 22.10", "tolerance": "-"}]}


def _upgrade(path: Path, revision: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        command.upgrade(_config(path), revision)
    return out.getvalue()


@pytest.fixture(scope="module")
def migrated_before(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        path = tmp_path_factory.mktemp("m0191") / "before.db"
        _upgrade(path, BEFORE)
    return path


@pytest.fixture()
def database(migrated_before, tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(migrated_before, path)
    return path


def _insert(connection: sqlite3.Connection, table: str, **values) -> None:
    """A row with the given values; other NOT NULL columns without a default get a plain value of their type."""
    for _, name, kind, notnull, default, _ in connection.execute(f"PRAGMA table_info({table})").fetchall():
        if name in values or not notnull or default is not None:
            continue
        kind = kind.upper()
        values[name] = (uuid4().hex if kind == "CHAR(32)" else 1 if kind in {"INTEGER", "BOOLEAN"}
                        else "2026-11-01 09:00:00" if kind == "DATETIME" else "2026-11-01" if kind == "DATE"
                        else "{}" if kind == "JSON" else "x")
    values = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
              for key, value in values.items()}
    connection.execute(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                       tuple(values.values()))


def _rows(title: str, step: str = "Выполнить") -> list[list[str]]:
    return [[title], ["ПРЕДПРИЯТИЕ"], ["ООО", "", "ТК.01"], ["Операция", "1", step, "", "", "", "-"]]


def _document(code: str, side: str, title: str, step: str = "Выполнить", designation: str = "ТК.01") -> dict:
    return {"code": code, "side": side, "title": title, "name": title.split(" | ")[1].split(" (")[0], "category": "К",
            "designation": designation, "revision": "", "safety": "", "defects": "", "tooling": [], "images": [],
            "steps": [{"number": "1", "kind": "Операция", "text": step, "tolerance": "-"}]}


def _template(connection, template_id: str, code: str, side: str | None, title: str, *, step: str = "Выполнить",
              designation: str = "ТК.01", images=(), edited=None) -> None:
    """side None — the table after 0191 (no `side` column)."""
    _insert(connection, "techcard_operation_templates", id=template_id, code=code, **({} if side is None else {"side": side}),
            title=title,
            name=title.split(" | ")[1].split(" (")[0], category="К", document=_document(code, side, title, step, designation),
            raw_rows=_rows(title, step), images=list(images), source_commit="", imported_at="2026-10-01 09:00:00",
            edited=edited, edited_at="2026-10-22 10:00:00" if edited else None, edited_by="Инженер" if edited else None,
            description="", saved_at=None, saved_by=None)


def _tool(connection, template_id: str, position: int, name: str, model: str | None) -> None:
    _insert(connection, "equipment_template_tools", template_id=template_id, position=position, tool_name=name,
            inventory_number="", candidates=1, model_id=model)


def _json(connection, sql: str, *args):
    value = connection.execute(sql, args).fetchone()[0]
    return None if value is None else json.loads(value)


def test_pairs_become_one_template_per_code_and_saved_references_follow(database):
    with sqlite3.connect(database) as connection:
        _template(connection, "ins-x-монтаж-a", "INS_X", "A", "INS_X | Монтаж (A)", edited=EDIT)
        _template(connection, "ins-x-монтаж-b", "INS_X", "B", "INS_X | Монтаж (B)", edited=EDIT, designation="ТК..")
        # owner 17.09: B owns SDR_COAX_housing although its text differs; A's picture is kept
        _template(connection, "sdr-coax-housing-пайка-экрана-a", "SDR_COAX_housing", "A",
                  "SDR_COAX_housing | Пайка экрана (A)", step="Пайка 3 сек", images=[IMAGE])
        _template(connection, "sdr-coax-housing-пайка-экрана-b", "SDR_COAX_housing", "B",
                  "SDR_COAX_housing | Пайка экрана (B)", step="Пайка 5,0 сек")
        _template(connection, "solo-разделка-a", "SOLO", "A", "SOLO | Разделка (A)")
        _template(connection, "op-one-резка", "OP_ONE", "", "OP_ONE | Резка")
        _tool(connection, "ins-x-монтаж-a", 0, "Пресс", M1)
        _tool(connection, "ins-x-монтаж-a", 1, "Станок", None)
        _tool(connection, "ins-x-монтаж-b", 1, "Станок", M2)  # only B linked, same tool → copied
        _tool(connection, "ins-x-монтаж-b", 2, "Паяльник", M3)  # A has no such row → reported, dropped
        _tool(connection, "sdr-coax-housing-пайка-экрана-a", 0, "Станция", M4)
        _tool(connection, "sdr-coax-housing-пайка-экрана-b", 0, "Станция", None)
        _insert(connection, "techcard_operation_template_snapshots", name="current", source_commit="",
                imported_at="2026-10-01 09:00:00", template_count=7)
        _insert(connection, "techcard_operation_template_drafts", name="Черновик — INS_X | Монтаж (B)",
                existing_template_id="ins-x-монтаж-b", title="INS_X | Монтаж (B)", category="К", description="",
                rows=[], images=[], created_by="Инженер")
        card = uuid4().hex
        _insert(connection, "techcard_projects", id=card, project="713К", item_index="КБ1", status="Черновик",
                manual_operation_keys=[],
                inputs={"ops": [{"templateId": "ins-x-монтаж-b", "opNum": "INS_X", "label": "ins-x-монтаж-a"}]},
                document={"operations": [{"template_id": "sdr-coax-housing-пайка-экрана-a", "title": "x (A)"}],
                          "source": {"templates": {"sdr-coax-housing-пайка-экрана-a": "v1", "op-one-резка": "v2"}}},
                overrides=None)
        _insert(connection, "techcard_revisions", project_card_id=card,
                document={"operations": [{"template_id": "ins-x-монтаж-a"}]})
        _insert(connection, "techcard_generator_drafts", payload={
            "selectedTemplates": {"insTermA1": "ins-x-монтаж-a", "insTermB1": "ins-x-монтаж-b"},
            "times": {"insTermA1": {"tmpl": "ins-x-монтаж-a"}}})
        _insert(connection, "techcard_assembly_drafts", source="manual", draft_config={"ops": [{"templateId": "solo-разделка-a"}]})

    log = _upgrade(database, AFTER)

    with sqlite3.connect(database) as connection:
        assert "side" not in {row[1] for row in connection.execute("PRAGMA table_info(techcard_operation_templates)")}
        templates = {row[0]: row[1:] for row in connection.execute(
            "SELECT id, code, title, raw_rows, images, document, edited FROM techcard_operation_templates")}
        assert set(templates) == {"ins-x-монтаж", "sdr-coax-housing-пайка-экрана", "solo-разделка", "op-one-резка"}
        code, title, raw_rows, images, document, edited = templates["ins-x-монтаж"]
        assert (code, title, json.loads(raw_rows)[0][0]) == ("INS_X", "INS_X | Монтаж", "INS_X | Монтаж")
        assert json.loads(edited) == EDIT
        assert "side" not in json.loads(document) and json.loads(document)["title"] == "INS_X | Монтаж"
        _, title, raw_rows, images, document, _ = templates["sdr-coax-housing-пайка-экрана"]
        assert json.loads(raw_rows)[3][2] == "Пайка 5,0 сек"  # B's grid
        assert json.loads(images) == [IMAGE]
        assert json.loads(document)["images"] == [
            {"row": 19, "col": 9, "drive_file_id": "img-a", "width": 654, "height": 674}]
        assert "side" not in json.loads(templates["op-one-резка"][4])
        with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(code): a side can't come back
            _template(connection, "ins-x-again", "INS_X", None, "INS_X | Монтаж")

        assert sorted(connection.execute(
            "SELECT template_id, position, tool_name, model_id FROM equipment_template_tools")) == sorted([
                ("ins-x-монтаж", 0, "Пресс", M1), ("ins-x-монтаж", 1, "Станок", M2),
                ("sdr-coax-housing-пайка-экрана", 0, "Станция", M4)])
        assert connection.execute("SELECT template_count FROM techcard_operation_template_snapshots").fetchone() == (5,)
        assert connection.execute(
            "SELECT existing_template_id FROM techcard_operation_template_drafts").fetchone() == ("ins-x-монтаж",)

        assert _json(connection, "SELECT inputs FROM techcard_projects") == {
            "ops": [{"templateId": "ins-x-монтаж", "opNum": "INS_X", "label": "ins-x-монтаж"}]}
        assert _json(connection, "SELECT document FROM techcard_projects") == {
            "operations": [{"template_id": "sdr-coax-housing-пайка-экрана", "title": "x (A)"}],
            "source": {"templates": {"sdr-coax-housing-пайка-экрана": "v1", "op-one-резка": "v2"}}}
        assert _json(connection, "SELECT overrides FROM techcard_projects") is None
        assert _json(connection, "SELECT payload FROM techcard_generator_drafts") == {
            "selectedTemplates": {"insTermA1": "ins-x-монтаж", "insTermB1": "ins-x-монтаж"},
            "times": {"insTermA1": {"tmpl": "ins-x-монтаж"}}}
        assert _json(connection, "SELECT draft_config FROM techcard_assembly_drafts") == {
            "ops": [{"templateId": "solo-разделка"}]}
        # issued revisions are history: they keep the old ids, readers resolve them by code
        assert _json(connection, "SELECT document FROM techcard_revisions") == {
            "operations": [{"template_id": "ins-x-монтаж-a"}]}

    assert "слито пар 3, удалено шаблонов 2" in log
    assert "techcard_projects': 4" in log and "techcard_generator_drafts': 3" in log
    assert "ins-x-монтаж-b поз. 2 «Паяльник»" in log and "не перенесена" in log


def test_diverging_sides_stop_the_migration_with_the_list_and_change_nothing(database):
    with sqlite3.connect(database) as connection:
        _template(connection, "edit-a", "EDIT_X", "A", "EDIT_X | Правка (A)", edited=EDIT)
        _template(connection, "edit-b", "EDIT_X", "B", "EDIT_X | Правка (B)")
        _template(connection, "text-a", "TEXT_X", "A", "TEXT_X | Текст (A)", step="Шаг A")
        _template(connection, "text-b", "TEXT_X", "B", "TEXT_X | Текст (B)", step="Шаг B")
        _template(connection, "same-a", "SAME_X", "A", "SAME_X | Пара (A)")
        _template(connection, "same-b", "SAME_X", "B", "SAME_X | Пара (B)")
        _template(connection, "same-x-пара", "OTHER", "", "OTHER | Пара")  # the new id of SAME_X is taken
        _template(connection, "dup-1", "DUP", "", "DUP | Один")
        _template(connection, "dup-2", "dup", "", "dup | Два")

    with pytest.raises(RuntimeError) as error:
        _upgrade(database, AFTER)

    message = str(error.value)
    assert message.startswith("Миграция 0191 остановлена, ничего не изменено")
    for part in ("код EDIT_X: стороны A и B правлены по-разному", "код TEXT_X: содержимое сторон A и B расходится",
                 "код SAME_X: новый id «same-x-пара» уже занят", "код DUP: 2 шаблона без сторон — dup-1, dup-2"):
        assert part in message
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (BEFORE,)
        assert connection.execute("SELECT count(*) FROM techcard_operation_templates").fetchone() == (9,)
        assert "side" in {row[1] for row in connection.execute("PRAGMA table_info(techcard_operation_templates)")}


def test_downgrade_brings_back_the_schema_only(database):
    _upgrade(database, AFTER)
    with contextlib.redirect_stdout(io.StringIO()):
        command.downgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        _template(connection, "one-a", "ONE", "", "ONE | Один")
        _template(connection, "one-b", "ONE", "", "ONE | Один")  # no UNIQUE(code) before 0191
        connection.execute("DELETE FROM techcard_operation_templates WHERE id = 'one-b'")
    _upgrade(database, AFTER)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id FROM techcard_operation_templates").fetchall() == [("one-a",)]
