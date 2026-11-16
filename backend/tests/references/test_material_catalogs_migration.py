"""0181 preserves complete material identity and historical links while changing ownership."""

import importlib.util
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.material_catalog_reconcile import snapshot, verify
from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyTechOperationRepository
from app.modules.references.material_catalogs import route
from tests.migrations.check_seeded_downgrade import evidence
from tests.references.test_stock_links_migration import LOT, _insert
from tests.shipping.test_pack_decision_migration import _config

BEFORE = "0180_equipment_catalogs_cutover"
AFTER = "0181_material_catalogs_cutover"


@pytest.fixture()
def material_db(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "materials.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    fields = [
        {"Наименование": "ТУТ 8/4", "Группа": "ТУТ", "Артикул": "A-8", "Особое поле": "не потерять"},
        {"Наименование": "ТТК 6/2", "Группа": "ТТК", "Артикул": "A-6"},
        {"Наименование": "Оплётка 6", "Группа": "Оплётка"},
        {"Наименование": "Флюс", "Группа": "Расходник"},
        {"Наименование": "Новая серия термоусадочной трубки", "Группа": "Новая группа"},
        {"Наименование": "Неизвестный материал", "Раздел оболочек": "Прочее"},
    ]
    ids = [uuid4().hex for _ in fields]
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, series, name, item_type, purpose,"
            " unit, storage_location, inbound, written_off, reserved, source_receiving_ids)"
            " VALUES (?, 1, 'ЦАР', 'FRX.26.0000042', '', 'A-8', '', 'ТУТ 8/4', '', '', 'м', '', 5, 0, 0, '[]')", (LOT,))
        for index, (identifier, payload) in enumerate(zip(ids, fields)):
            connection.execute("INSERT INTO tech_operation_records (id,tab,display_text,normalized_search,source_sheet,"
                "sort_key,fields,mirror_key,edited_in_erp) VALUES (?,'mat',?,'search','БД.МАТ',?,?,?,1)",
                (identifier, payload["Наименование"], str(index), json.dumps(payload, ensure_ascii=False), f"mirror-{index}"))
            _insert(connection, uuid4().hex, record=identifier)
        connection.execute("INSERT INTO reference_entries VALUES (?, 'custom_column', 'mat:Заметка', 'mat:заметка', 'Заметка', 0)",
                           (uuid4().hex,))
        connection.execute("INSERT INTO reference_entries VALUES (?, 'column_view', 'mat|test', 'mat|test', '[\"Заметка\"]', 0)",
                           (uuid4().hex,))
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    with engine.connect() as connection:
        before = snapshot(connection)
    before_state = evidence(engine)
    command.upgrade(config, AFTER)
    yield path, config, ids, engine, before, before_state
    engine.dispose()


def test_upgrade_reconciles_every_table_payload_link_and_independent_metadata(material_db):
    path, _, ids, engine, before, _ = material_db
    with engine.connect() as connection:
        result = verify(connection, before)
    assert result["ok"], result
    assert result["active_partition"] == {"mat": 1, "heat_shrink": 3, "cov": 2}
    assert result["archived_materials"] == 5
    assert result["stock_links"] == 6
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        metadata = dict(connection.execute("SELECT key,value FROM reference_entries WHERE kind='custom_column'"))
        assert {key: metadata[key] for key in ("mat:Заметка", "heat_shrink:Заметка", "cov:Заметка")} == {
            "mat:Заметка": "Заметка", "heat_shrink:Заметка": "Заметка", "cov:Заметка": "Заметка"}
        for sql in ("UPDATE tech_operation_records SET fields='{}' WHERE id=?", "DELETE FROM tech_operation_records WHERE id=?",
                    "UPDATE tech_operation_records SET material_archived=0 WHERE id=?"):
            with pytest.raises(sqlite3.IntegrityError, match="frozen"):
                connection.execute(sql, (ids[0],))


def test_deleting_live_record_keeps_journal_and_cannot_resurrect_archive(material_db):
    path, _, ids, engine, _, _ = material_db
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("DELETE FROM heat_shrink_records WHERE id=?", (ids[0],))
        assert connection.execute("SELECT count(*) FROM reference_stock_links").fetchone() == (6,)
        assert connection.execute("SELECT heat_shrink_id FROM reference_stock_links WHERE article='SXH-001T' AND "
                                  "record_tab='heat_shrink' AND heat_shrink_id IS NULL").fetchone() == (None,)
    with Session(engine) as session:
        repository = SqlAlchemyTechOperationRepository(session)
        assert repository.get(UUID(ids[0])) is None
        assert UUID(ids[0]) not in {row.id for row in repository.list(tab=None, query="", limit=5000)}
        assert len(repository.list(tab=TechOperationTab.MATERIAL, query="", limit=5000)) == 1


def test_reconciliation_detects_payload_and_unrelated_stock_changes(material_db):
    path, _, ids, engine, before, _ = material_db
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE heat_shrink_records SET fields='{}' WHERE id=?", (ids[0],))
        connection.execute("UPDATE warehouse_lots SET inbound=99 WHERE id=?", (LOT,))
    with engine.connect() as connection:
        result = verify(connection, before)
    assert not result["ok"]
    assert any("warehouse_lots" in error for error in result["errors"])
    assert any("payload" in error for error in result["errors"])


def test_database_rejects_two_stock_targets_and_wrong_target_tab(material_db):
    path, _, ids, _, _, _ = material_db
    with sqlite3.connect(path) as connection:
        for assignment in ("record_id='" + ids[0] + "'", "record_tab='cov'"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(f"UPDATE reference_stock_links SET {assignment} WHERE heat_shrink_id=?", (ids[0],))


def test_editing_migrated_record_moves_live_target_with_foreign_keys_enforced(material_db):
    path, _, ids, _, _, _ = material_db
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    try:
        with Session(engine) as session:
            repository = SqlAlchemyTechOperationRepository(session)
            old = repository.get(UUID(ids[0]))
            repository.save(replace(old, fields={**old.fields, "Раздел оболочек": "Гофра"}, edited_in_erp=True))
            session.commit()
            moved = repository.get(old.id)
            assert moved.id == old.id and moved.tab == TechOperationTab.COVERING
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT covering_id,heat_shrink_id,record_tab FROM reference_stock_links "
                                      "WHERE covering_id=?", (ids[0],)).fetchone() == (ids[0], None, "cov")
            fields = json.loads(connection.execute("SELECT fields FROM tech_operation_records WHERE id=?", (ids[0],)).fetchone()[0])
            assert "Раздел оболочек" not in fields
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()


def test_unchanged_cutover_restores_payloads_ids_stock_links_and_roundtrips(material_db):
    path, config, _, engine, _, before_state = material_db
    at_head = evidence(engine)
    command.downgrade(config, BEFORE)
    restored = evidence(engine)
    # As with 0180, independent column/view metadata stays valid at the prior revision.
    # Keep its UUIDs and values so a downgrade/re-upgrade never loses a user's layout.
    assert restored == {**before_state, "reference_entries": at_head["reference_entries"]}
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {f"freeze_legacy_equipment_{action}" for action in ("insert", "update", "delete")} <= triggers
        assert not any(name.startswith("freeze_legacy_material") for name in triggers)
    command.upgrade(config, AFTER)
    assert evidence(engine) == at_head


def test_downgrade_keeps_changes_that_remain_representable_at_the_prior_revision(material_db):
    path, config, ids, engine, _, _ = material_db
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE reference_entries SET value='[\"Своя колонка\"]' WHERE key='heat_shrink|test'")
        connection.execute("UPDATE tech_operation_records SET display_text='Новый флюс' WHERE id=?", (ids[3],))
        link = uuid4().hex
        _insert(connection, link, record=None, tab="heat_shrink")
        connection.execute("UPDATE reference_stock_links SET heat_shrink_id=? WHERE id=?", (ids[0], link))
    before = evidence(engine)
    command.downgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT record_id,record_tab FROM reference_stock_links WHERE id=?", (link,)).fetchone() == (
            ids[0], "mat")
        assert connection.execute("SELECT display_text FROM tech_operation_records WHERE id=?", (ids[3],)).fetchone() == (
            "Новый флюс",)
    command.upgrade(config, AFTER)
    assert evidence(engine) == before


@pytest.mark.parametrize("change", ["payload", "group", "status", "delete", "create", "move", "duplicate", "orphan_link"])
def test_lossy_downgrade_refuses_before_any_schema_or_data_change(material_db, change):
    path, config, ids, engine, _, _ = material_db
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if change in ("payload", "group", "status"):
            assignment = {"payload": "fields='{}'", "group": "material_group='ttk'",
                          "status": "classification_status='unknown'"}[change]
            connection.execute(f"UPDATE heat_shrink_records SET {assignment} WHERE id=?", (ids[0],))
        elif change == "delete":
            connection.execute("DELETE FROM heat_shrink_records WHERE id=?", (ids[0],))
        elif change == "create":
            connection.execute("INSERT INTO heat_shrink_records SELECT ?,tab,display_text,normalized_search,source_sheet,"
                "sort_key,fields,mirror_key,edited_in_erp,equipment_model_id,material_group,classification_status "
                "FROM heat_shrink_records WHERE id=?", (uuid4().hex, ids[0]))
        elif change in ("move", "duplicate"):
            connection.execute("INSERT INTO covering_records SELECT id,'cov',display_text,normalized_search,source_sheet,"
                "sort_key,fields,mirror_key,edited_in_erp,equipment_model_id,'other',classification_status "
                "FROM heat_shrink_records WHERE id=?", (ids[0],))
            if change == "move":
                connection.execute("UPDATE reference_stock_links SET covering_id=heat_shrink_id,heat_shrink_id=NULL,"
                                   "record_tab='cov' WHERE heat_shrink_id=?", (ids[0],))
                connection.execute("DELETE FROM heat_shrink_records WHERE id=?", (ids[0],))
        else:
            _insert(connection, uuid4().hex, record=None, tab="heat_shrink")
        triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
    before = evidence(engine)
    with pytest.raises(RuntimeError, match="forward repair"):
        command.downgrade(config, BEFORE)
    assert evidence(engine) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall() == triggers


@pytest.mark.parametrize("fields", [
    {"Наименование": "ТУТ 8/4", "Группа": "ТУТ"},
    {"Наименование": "ТУТ клеевая", "Группа": "ТУТ"},
    {"Наименование": "Термоусадочная трубка", "Группа": "Новое", "Клеевой слой": "да"},
    {"Наименование": "СТТК", "Клеевой слой": "да"},
    {"Наименование": "Клей adhesive", "Группа": "Клей"},
    {"Наименование": "ТУТ", "Раздел оболочек": "Ленты"},
    {"Наименование": "ТУТ", "Раздел оболочек": "Ошибка"},
    {"Наименование": "Лента тканевая"},
    {"Наименование": "Плетёнка", "Раздел оболочек": "плетёнка"},
    {"Наименование": "Неизвестно"},
])
def test_frozen_migration_classifier_agrees_with_write_routing(fields):
    path = Path(__file__).parents[2] / "migrations/versions/0181_material_catalogs_cutover.py"
    spec = importlib.util.spec_from_file_location("material_cutover", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.classify(fields) == route(fields)
