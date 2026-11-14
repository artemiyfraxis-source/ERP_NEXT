"""Physical catalog cutover, UUID ownership and all old equipment relation kinds."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.references.domain import EQUIPMENT_TABS, TechOperationRecord, TechOperationTab
from app.modules.references.equipment_catalogs import CATALOG_TABLES
from app.modules.references.equipment_identity import SqlAlchemyEquipmentRepository
from app.modules.references.infrastructure import EquipmentUnitRow, SqlAlchemyTechOperationRepository
from tests.equipment_setup.test_unit_ids_migration import _unit, _setup_rows
from tests.shipping.test_pack_decision_migration import _config
from tests.references.test_api import references_api  # noqa: F401

KINDS = ("Оборудование", "Средства измерений", "Обеспечивающее оборудование", "Аппликаторы", "Матрицы", "Инструмент",
         "Обжимной инструмент", "Оборудование (ПР)")
RELATION_TABLES = ("equipment_unit_links", "equipment_setup_tasks", "equipment_setup_recipe_versions",
                   "production_active_resources", "production_resource_reservations", "production_resource_allocation_machines",
                   "equipment_models", "equipment_operation_links", "equipment_template_tools", "equipment_setup_commands")


@pytest.fixture
def cutover_db(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "catalogs.db"
    config = _config(path)
    command.upgrade(config, "0171_equipment_identity")
    ids = []
    with sqlite3.connect(path) as connection:
        for index, kind in enumerate(KINDS):
            unit_id = _unit(connection, f"ОБ.{index + 1:03}")
            fields = {"Тип": kind, "Инв. номер": f"ОБ.{index + 1:03}", "Наменование": "Исходное имя",
                      "Особое поле": "сохранить", "Подтип": "", "obType": kind}
            connection.execute("UPDATE tech_operation_records SET fields=?, mirror_key=?, edited_in_erp=1 WHERE id=?",
                               (json.dumps(fields, ensure_ascii=False), f"mirror-{index}", unit_id.hex))
            ids.append(unit_id)
        _setup_rows(connection, "ОБ.001", "ОБ.002")
        operation = uuid4()
        connection.execute("INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields)"
                           " VALUES (?, 'op', 'Операция', '', 'БД.ОП', '001', '{}')", (operation.hex,))
        model = connection.execute("SELECT equipment_model_id FROM tech_operation_records WHERE id=?", (ids[0].hex,)).fetchone()[0]
        connection.execute("INSERT INTO equipment_operation_links VALUES (?, 'Машина', 0, ?)", (operation.hex, model))
        connection.execute("INSERT INTO equipment_unit_links VALUES (?, 'tool', ?)", (ids[4].hex, ids[5].hex))
        connection.execute("INSERT INTO equipment_template_tools VALUES ('T1', 0, 'Станок', 'ОБ.001', 1, ?)", (model,))
        connection.execute("INSERT INTO reference_entries VALUES (?, 'custom_column', 'ob:Заметка', 'ob:заметка', 'Заметка', 0)",
                           (uuid4().hex,))
        connection.execute("INSERT INTO reference_stock_links (id, occurred_at, event, lot_id, warehouse, grn, package, article, name,"
                           " record_id, record_tab, record_text, changes, actor_id, actor_name)"
                           " VALUES (?, '2026-11-12', 'linked', ?, 'ЦАР', 'ГРН', '1', 'A', 'Имя', ?, 'ob', 'История', '[]', ?, 'Автор')",
                           (uuid4().hex, uuid4().hex, ids[0].hex, uuid4().hex))
    command.upgrade(config, "0177_agent_devices")
    return path, config, ids


@pytest.mark.parametrize("reclassified", [False, True], ids=["before-reclassify", "after-reclassify"])
def test_cutover_preserves_payloads_uuids_and_every_relation(cutover_db, reclassified):
    path, config, ids = cutover_db
    with sqlite3.connect(path) as connection:
        for index, kind, number in (
            (1, "Средства измерений" if reclassified else "Оборудование", "СИ.105" if reclassified else "ОБ.002"),
            (2, "Обеспечивающее оборудование" if reclassified else "Оборудование", "ОБ.003"),
        ):
            fields = json.loads(connection.execute("SELECT fields FROM tech_operation_records WHERE id=?", (ids[index].hex,)).fetchone()[0])
            fields.update({"Тип": kind, "obType": kind, "Инв. номер": number})
            connection.execute("UPDATE tech_operation_records SET fields=? WHERE id=?",
                               (json.dumps(fields, ensure_ascii=False), ids[index].hex))
        source = {row[0]: row for row in connection.execute("SELECT id, fields, mirror_key, edited_in_erp, equipment_model_id FROM tech_operation_records WHERE tab='ob'")}
        relations = {table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr) for table in RELATION_TABLES}
        assert all(relations.values()), "Every relation kind must have real rows before cutover"
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        seen = set()
        for tab, table in CATALOG_TABLES.items():
            for row in connection.execute(f"SELECT id, fields, mirror_key, edited_in_erp, equipment_model_id FROM {table}"):
                assert row[0] not in seen
                seen.add(row[0])
                original = source[row[0]]
                assert json.loads(row[1]) == json.loads(original[1])
                assert row[2:] == original[2:]
        assert seen == {item.hex for item in ids}
        assert len(source) == connection.execute("SELECT count(*) FROM tech_operation_records WHERE tab='ob'").fetchone()[0]
        assert {table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
                for table in RELATION_TABLES} == relations
        assert connection.execute("SELECT catalog FROM equipment_units WHERE id=?", (ids[1].hex,)).fetchone()[0] == (
            "ob_measuring" if reclassified else "ob")
        assert connection.execute("SELECT catalog FROM equipment_units WHERE id=?", (ids[2].hex,)).fetchone()[0] == (
            "ob_support" if reclassified else "ob")
        for table in ("equipment_unit_links", "equipment_setup_tasks", "equipment_setup_recipe_versions",
                      "production_active_resources", "production_resource_reservations", "production_resource_allocation_machines"):
            targets = {row[2] for row in connection.execute(f"PRAGMA foreign_key_list({table})")}
            assert "equipment_units" in targets and "tech_operation_records" not in targets
        journal = connection.execute("SELECT record_id, equipment_unit_id, record_text FROM reference_stock_links").fetchone()
        assert journal == (None, ids[0].hex, "История")
        assert connection.execute("SELECT count(*) FROM reference_entries WHERE kind='custom_column'").fetchone()[0] == 6
        for sql in ("UPDATE tech_operation_records SET display_text='changed' WHERE tab='ob'",
                    "UPDATE tech_operation_records SET tab='op' WHERE tab='ob'",
                    "DELETE FROM tech_operation_records WHERE tab='ob'"):
            with pytest.raises(sqlite3.IntegrityError, match="frozen"):
                connection.execute(sql)


def test_untouched_cutover_downgrade_and_reupgrade_keep_every_relation(cutover_db):
    from tests.migrations.check_seeded_downgrade import evidence

    path, config, ids = cutover_db
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        before = evidence(engine)
        command.downgrade(config, "0178_equipment_catalogs")
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT count(*) FROM equipment_units").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM tech_operation_records WHERE tab='ob'").fetchone()[0] == len(ids)
            assert connection.execute("SELECT record_id, record_text FROM reference_stock_links").fetchone() == (ids[0].hex, "История")
        command.upgrade(config, "head")
        assert evidence(engine) == before
    finally:
        engine.dispose()


@pytest.mark.parametrize("change", ["edit", "delete", "insert", "binding"])
def test_cutover_downgrade_refuses_changed_live_data_without_any_mutation(cutover_db, change):
    from tests.migrations.check_seeded_downgrade import evidence

    path, config, ids = cutover_db
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            repo = SqlAlchemyTechOperationRepository(session)
            unit = repo.get(ids[7])
            if change == "edit":
                repo.save(replace(unit, fields={**unit.fields, "Наменование": "Новое имя"}))
            elif change == "delete":
                repo.delete(unit.id)
            elif change == "insert":
                repo.save(replace(unit, id=uuid4(), fields={**unit.fields, "Инв. номер": "ОБ.900"}))
            else:
                from app.modules.production.infrastructure import ProductionInstrumentRow
                from app.modules.references.infrastructure import EquipmentInstrumentBindingRow

                instrument = ProductionInstrumentRow(id=uuid4(), version=1, inventory_number="ОБ.002", name="Прибор",
                    instrument_type="Вольтметр", kind="Средство измерений", status="Поверка не подтверждена")
                session.add(instrument)
                session.add(EquipmentInstrumentBindingRow(unit_id=ids[1], instrument_id=instrument.id))
            session.commit()
        before = evidence(engine)
        with pytest.raises(RuntimeError, match="cannot restore a stale archive"):
            command.downgrade(config, "0178_equipment_catalogs")
        assert evidence(engine) == before
    finally:
        engine.dispose()


def test_additive_schema_can_be_removed_before_cutover(cutover_db):
    path, config, _ = cutover_db
    command.upgrade(config, "0178_equipment_catalogs")
    command.downgrade(config, "0177_agent_devices")
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not ({"equipment_units", "equipment_instrument_bindings", *CATALOG_TABLES.values()} & tables)
    command.upgrade(config, "head")


def test_unknown_type_blocks_before_any_copy(cutover_db):
    path, config, ids = cutover_db
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE tech_operation_records SET fields=? WHERE id=?",
                           (json.dumps({"Тип": "Непонятное", "Инв. номер": "ОБ.001"}), ids[0].hex))
    with pytest.raises(RuntimeError, match="unknown type") as error:
        command.upgrade(config, "head")
    assert ids[0].hex in str(error.value) or str(ids[0]) in str(error.value)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM equipment_units").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["Оборудование", "Оборудование (ПР)", ""])
def test_reviewed_instruments_block_cutover_on_type_drift(cutover_db, kind):
    path, config, _ = cutover_db
    identifiers = (UUID("932092f1-c555-492e-82a4-1ae98a3d6654"), UUID("74ecb453-ddcf-482f-bf36-88e7f2b44aba"))
    with sqlite3.connect(path) as connection:
        for index, identifier in enumerate(identifiers):
            temporary = _unit(connection, f"СИ.{900 + index}")
            connection.execute("UPDATE tech_operation_records SET id=?, fields=? WHERE id=?", (
                identifier.hex, json.dumps({"Тип": kind, "obType": "Средства измерений", "Инв. номер": f"СИ.{900 + index}"}), temporary.hex))
    with pytest.raises(RuntimeError, match="Перенос оборудования остановлен: средства измерений") as error:
        command.upgrade(config, "head")
    message = str(error.value)
    for index, identifier in enumerate(identifiers):
        assert identifier.hex in message or str(identifier) in message
        assert f"СИ.{900 + index}" in message
    assert f"Тип={kind!r}" in message and "Средства измерений" in message
    with sqlite3.connect(path) as connection:
        for table in ("equipment_units", *CATALOG_TABLES.values()):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_all_reviewed_instruments_keep_their_approved_catalog(cutover_db):
    from alembic.script import ScriptDirectory

    path, config, _ = cutover_db
    migration = ScriptDirectory.from_config(config).get_revision("0179_equipment_catalogs_cutover").module
    expected = migration.EXPECTED_INSTRUMENT_CATALOGS
    assert len(expected) == 15 and set(expected.values()) == {"Средства измерений"}
    with sqlite3.connect(path) as connection:
        for index, (identifier, kind) in enumerate(expected.items()):
            temporary = _unit(connection, f"СИ.{900 + index}")
            connection.execute("UPDATE tech_operation_records SET id=?, fields=? WHERE id=?", (
                identifier.hex, json.dumps({"Тип": kind, "Инв. номер": f"СИ.{900 + index}"}), temporary.hex))
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        catalogs = dict(connection.execute("SELECT id, catalog FROM equipment_units"))
        assert all(catalogs[identifier.hex] == "ob_measuring" for identifier in expected)


def test_migrated_legacy_machine_stays_selectable_and_resolvable(cutover_db):
    from app.modules.production.equipment_catalog import build_catalog
    from app.modules.references.equipment import physical_equipment

    path, config, ids = cutover_db
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            unit = SqlAlchemyTechOperationRepository(session).get(ids[7])
            assert unit.tab == TechOperationTab.EQUIPMENT
            assert unit.fields["Тип"] == "Оборудование (ПР)"
            assert physical_equipment([unit])[0].inventory_number == "ОБ.008"
            catalog = build_catalog([unit], SqlAlchemyEquipmentRepository(session).models())
            counts, labels = catalog.site_units()
            assert counts[("", str(unit.equipment_model_id))] == 1
            assert ("", str(unit.equipment_model_id)) in labels
            machine = catalog.machine({"section": "Оборудование", "inventory_number": "ОБ.008",
                                       "equipment_model_id": str(unit.equipment_model_id)})
            assert machine is not None and machine.key == str(unit.equipment_model_id)
    finally:
        engine.dispose()


def test_union_order_limit_move_identity_and_database_ownership(cutover_db):
    path, config, ids = cutover_db
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    with Session(engine) as session:
        repo = SqlAlchemyTechOperationRepository(session)
        all_rows = repo.list(tab=EQUIPMENT_TABS, query="Исходное", limit=100)
        assert len(all_rows) == len(ids)
        assert repo.list(tab=EQUIPMENT_TABS, query="Исходное", limit=3) == all_rows[:3]
        old = repo.get(ids[5])
        repo.save(replace(old, fields={**old.fields, "Тип": "Матрицы", "obType": "Матрицы"}))
        session.commit()
        assert repo.get(old.id).tab == TechOperationTab.DIES
        assert repo.get(old.id).equipment_model_id == old.equipment_model_id
        # Reassigning a model updates both ownership and payload together.
        model = SqlAlchemyEquipmentRepository(session).set_unit_model(old.id, None)
        session.commit()
        assert session.get(EquipmentUnitRow, old.id).model_id == UUID(model)
    with engine.connect() as connection:
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("DELETE FROM equipment_dies WHERE id=?", (ids[5].hex,))
            connection.commit()
        connection.rollback()
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("INSERT INTO equipment_units (id, model_id, catalog, ob_id) VALUES (?, ?, 'ob', ?)",
                                       (uuid4().hex, old.equipment_model_id.hex, uuid4().hex))
            connection.commit()
        connection.rollback()
    engine.dispose()


def test_mirror_reload_keeps_uuid_and_erp_classification(cutover_db):
    path, config, ids = cutover_db
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as session:
        repo = SqlAlchemyTechOperationRepository(session)
        original = repo.list(tab=EQUIPMENT_TABS, query="", limit=100)
        incoming = tuple(replace(record, id=uuid4(), tab=TechOperationTab.EQUIPMENT,
                                 fields={**record.fields, "Тип": "Оборудование", "obType": "Оборудование"}) for record in original)
        repo.replace(incoming, schema_version=14, source_updated_at=None, imported_at=datetime(2026, 11, 13),
                     keep_tabs=tuple(tab for tab in TechOperationTab if tab not in EQUIPMENT_TABS), preserve_erp_edits=True)
        session.commit()
        assert repo.list(tab=EQUIPMENT_TABS, query="", limit=100) == original
    engine.dispose()


def test_confirmed_role_moves_units_and_new_units_join_the_catalog(cutover_db):
    path, config, ids = cutover_db
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    with Session(engine) as session, session.begin():
        repo = SqlAlchemyTechOperationRepository(session)
        unit = TechOperationRecord.create(tab=TechOperationTab.EQUIPMENT, display_text="Новый станок",
            search_text="", source_sheet="", sort_key="new", fields={"Тип": "Оборудование", "Наименование": "Новый станок", "Инв. номер": "ОБ.899"})
        repo.save(unit)
        unit = repo.get(unit.id)
        equipment = SqlAlchemyEquipmentRepository(session)
        equipment.set_role(unit.equipment_model_id, "supporting")
        assert repo.get(unit.id).tab == TechOperationTab.SUPPORTING
        added = replace(unit, id=uuid4(), fields={**unit.fields, "Инв. номер": "ОБ.900"})
        repo.save(added)
        assert repo.get(added.id).tab == TechOperationTab.SUPPORTING
        equipment.set_role(unit.equipment_model_id, "operational")
        assert repo.get(unit.id).tab == repo.get(added.id).tab == TechOperationTab.EQUIPMENT
        assert repo.get(unit.id).equipment_model_id == unit.equipment_model_id
    engine.dispose()


@pytest.mark.parametrize("confirmed", [False, True])
def test_cutover_uses_only_confirmed_supporting_role(cutover_db, confirmed):
    path, config, ids = cutover_db
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE equipment_models SET role='supporting', role_confirmed=? WHERE id="
                           "(SELECT equipment_model_id FROM tech_operation_records WHERE id=?)", (confirmed, ids[7].hex))
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT catalog FROM equipment_units WHERE id=?", (ids[7].hex,)).fetchone()[0] == (
            "ob_support" if confirmed else "ob")


def test_all_catalogs_have_independent_columns_even_when_empty(references_api):
    base = "/api/references/tech-operations"
    for tab in CATALOG_TABLES:
        result = references_api.post(base + "/columns", json={"tab": tab, "name": "Проверка"})
        assert result.status_code == 201, result.text
    result = references_api.post(base + "/columns/remove", json={"tab": "ob_tools", "name": "Проверка"})
    assert result.status_code == 200, result.text
    for tab in CATALOG_TABLES:
        data = references_api.get(base, params={"tab": tab}).json()
        assert ("Проверка" in data["custom_columns"]) == (tab != "ob_tools")
    # A default column cannot be removed or shadowed with a custom column.
    assert references_api.post(base + "/columns", json={"tab": "ob_measuring", "name": "Заводской номер"}).status_code == 422


def test_import_identity_key_aliases_and_delete_guard(references_api):
    base = "/api/references/tech-operations"
    created = references_api.post(base, json={"tab": "ob", "fields": {
        "Тип": "Оборудование (ПР)", "Наименование": "Станок", "Инв. номер": "ОБ.900",
    }})
    assert created.status_code == 201, created.text
    unit = created.json()
    assert unit["fields"]["Тип"] == "Оборудование"
    response = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [{"fields": {
        "equipmentKey": "id:" + unit["id"], "Расположение": "ЦАР",
    }}]})
    assert len(response.json()["updated"]) == 1, response.text
    response = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [{"fields": {
        "equipmentKey": "id:" + unit["id"], "Наименование": "Первое", "Наменование": "Второе",
    }}]})
    assert len(response.json()["refused"]) == 1, response.text
    operation = references_api.post(base, json={"tab": "op", "fields": {"Номер": "T", "Название": "Операция"}}).json()
    assert references_api.delete("/api/references/equipment/" + operation["id"]).status_code == 422
    assert references_api.delete("/api/references/equipment/" + unit["id"]).status_code == 200


def test_advertised_inventory_key_is_repeatable_and_matches_own_schema(references_api):
    from app.modules.references.importers import import_schema
    factory = references_api.app.state.references_uow_factory
    schema = import_schema("ob_measuring", factory())
    assert {"equipmentKey", "Заводской номер", "unitId", "mirrorKey"} <= set(schema["fields"])
    payload = {"rows": [{"fields": {"equipmentKey": "inventory:си.900", "Тип": "Средства измерений", "Наименование": "Прибор"}}]}
    first = references_api.post("/api/references/import/ob_measuring?dry_run=0", json=payload).json()
    second = references_api.post("/api/references/import/ob_measuring?dry_run=0", json=payload).json()
    assert len(first["created"]) == 1 and first["refused"] == []
    assert second["unchanged"] == 1 and second["created"] == [] and second["refused"] == []


def test_import_does_not_copy_another_catalogs_columns(references_api):
    from app.modules.references.importers import import_schema

    created = references_api.post("/api/references/import/ob_measuring?dry_run=0", json={"rows": [{"fields": {
        "equipmentKey": "inventory:си.900", "Тип": "Средства измерений", "Наименование": "Прибор"}}]})
    assert created.status_code == 200 and len(created.json()["created"]) == 1
    column = references_api.post("/api/references/tech-operations/columns", json={"tab": "ob_measuring", "name": "Точность"})
    assert column.status_code == 201, column.text
    factory = references_api.app.state.references_uow_factory
    assert "Точность" not in import_schema("ob", factory())["fields"]
    response = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [{"fields": {
        "equipmentKey": "inventory:об.900", "Тип": "Оборудование", "Наименование": "Станок"}}]})
    assert response.status_code == 200 and len(response.json()["created"]) == 1, response.text
    rows = references_api.get("/api/references/tech-operations?tab=ob").json()["records"]
    assert len(rows) == 1 and "Точность" not in rows[0]["fields"]
    own = references_api.post("/api/references/tech-operations/columns", json={"tab": "ob", "name": "Точность"})
    assert own.status_code == 201, own.text


@pytest.mark.parametrize("name_key, alias", [("Наменование", "Наименование"), ("Наименование", "Наменование")])
@pytest.mark.parametrize("blank_present", [False, True])
def test_unrelated_edit_with_untouched_blank_alias_preserves_name(references_api, name_key, alias, blank_present):
    fields = {"Тип": "Оборудование", name_key: "Станок", "Артикул": "A1"}
    if blank_present:
        fields[alias] = ""
    base = "/api/references/tech-operations"
    created = references_api.post(base, json={"tab": "ob", "fields": fields})
    assert created.status_code == 201, created.text
    before = created.json()
    response = references_api.patch(f"{base}/{before['id']}", json={"edits": [
        {"field": alias, "old": "", "value": ""},
        {"field": "Артикул", "old": "A1", "value": "A2"},
    ]})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["fields"][name_key] == "Станок" and saved["fields"]["Артикул"] == "A2"
    assert saved["equipment_model_id"] == before["equipment_model_id"]
    # An actual deletion of the populated name remains invalid.
    cleared = references_api.patch(f"{base}/{before['id']}", json={"edits": [
        {"field": name_key, "old": "Станок", "value": ""},
    ]})
    assert cleared.status_code == 422, cleared.text


@pytest.mark.parametrize("name_key, alias", [("Наменование", "Наименование"), ("Наименование", "Наменование")])
@pytest.mark.parametrize("via_import", [False, True])
@pytest.mark.parametrize("both_aliases", [False, True])
def test_single_name_alias_edit_synchronizes_existing_aliases(references_api, via_import, both_aliases, name_key, alias):
    fields = {"Тип": "Оборудование", name_key: "Станок"}
    if both_aliases:
        fields[alias] = "Станок"
    response = references_api.post("/api/references/tech-operations", json={"tab": "ob", "fields": fields})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    if via_import:
        response = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [{"fields": {
            "equipmentKey": "id:" + identifier, alias: "Станок новый"}}]})
        assert response.status_code == 200 and len(response.json()["updated"]) == 1, response.text
    else:
        response = references_api.patch("/api/references/tech-operations/" + identifier, json={"edits": [
            {"field": alias, "old": "Станок" if both_aliases else "", "value": "Станок новый"}]})
        assert response.status_code == 200, response.text
    saved = references_api.get("/api/references/tech-operations?tab=ob").json()["records"][0]
    assert saved["fields"]["Наменование"] == saved["fields"]["Наименование"] == "Станок новый"


def test_import_refuses_explicit_conflicting_alias_even_when_one_is_unchanged(references_api):
    created = references_api.post("/api/references/tech-operations", json={"tab": "ob", "fields": {
        "Тип": "Оборудование", "Наменование": "Станок", "Наименование": "Станок"}}).json()
    response = references_api.post("/api/references/import/ob?dry_run=0", json={"rows": [
        {"fields": {"equipmentKey": "id:" + created["id"], "Наменование": "Станок", "Наименование": "Другое"}},
        {"fields": {"Наименование": "Без ключа"}},
    ]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert not result["updated"] and len(result["refused"]) == 2, result
    assert "разные значения" in result["refused"][0]["reason"]
    assert result["refused"][1]["key"] == "#2"
    saved = references_api.get("/api/references/tech-operations?tab=ob").json()["records"][0]
    assert saved["fields"]["Наменование"] == saved["fields"]["Наименование"] == "Станок"


def test_deleted_live_unit_does_not_leave_archive_only_models_in_live_reports(cutover_db):
    from app.modules.references.infrastructure import (
        EquipmentModelRow, EquipmentOperationLinkRow, EquipmentTemplateToolRow, TechOperationRecordRow,
    )

    path, config, ids = cutover_db
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    with Session(engine) as session:
        records = SqlAlchemyTechOperationRepository(session)
        equipment = SqlAlchemyEquipmentRepository(session)
        model_id = records.get(ids[7]).equipment_model_id
        records.delete(ids[7])
        session.commit()
        assert session.get(TechOperationRecordRow, ids[7]).equipment_model_id == model_id
        assert session.get(EquipmentModelRow, model_id) is not None
        assert str(model_id) not in {model["id"] for model in equipment.models()}
        assert str(model_id) not in {model["id"] for model in equipment.report()["roles_unconfirmed"]}

        # True orphans and either kind of live model reference must remain diagnosable.
        orphan = EquipmentModelRow(id=uuid4(), name="Orphan")
        session.add(orphan)
        session.flush()
        assert str(orphan.id) in {model["id"] for model in equipment.report()["models_without_units"]}
        template = EquipmentTemplateToolRow(template_id="T2", position=0, tool_name="Missing unit",
                                           model_id=model_id, candidates=1)
        session.add(template)
        session.flush()
        assert str(model_id) in {model["id"] for model in equipment.report()["models_without_units"]}
        session.delete(template)
        session.flush()
        operation_id = session.scalar(select(EquipmentOperationLinkRow.operation_id).limit(1))
        session.add(EquipmentOperationLinkRow(operation_id=operation_id, op_column="Инструмент", position=0,
                                             model_id=model_id))
        session.flush()
        assert str(model_id) in {model["id"] for model in equipment.report()["models_without_units"]}
    engine.dispose()
