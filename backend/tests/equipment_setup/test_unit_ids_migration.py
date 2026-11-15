"""Migration 0172: setup recipes, tasks, machine claims and allocation machines name the БД.ОБ unit by its id (FK),
not the inventory number; only an exact single-unit number converts, anything else stops the migration with a list;
the downgrade gives the numbers back."""

import json
import sqlite3
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import CheckConstraint, create_engine, inspect

from app.modules.equipment_setup import infrastructure, recipe_infrastructure
from app.modules.production import resource_assignment_infrastructure, resource_infrastructure
from app.shared.persistence import Base
from tests.shipping.test_pack_decision_migration import _config

BEFORE, AFTER = "0171_equipment_identity", "0172_resource_unit_ids"
TABLES = {table.name for module in (resource_assignment_infrastructure, resource_infrastructure, infrastructure, recipe_infrastructure)
    for item in vars(module).values() if isinstance(item, type) and (table := getattr(item, "__table__", None)) is not None}
NOW = "2026-11-05 09:00:00.000000"


def test_schema_matches_models(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "schema.db"
    command.upgrade(_config(path), "head")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert len(TABLES) == 13 and TABLES <= set(inspector.get_table_names())
        for name in TABLES:
            model = Base.metadata.tables[name]
            assert {column["name"]: column["nullable"] for column in inspector.get_columns(name)} == {
                column.name: column.nullable for column in model.columns}, name
            assert set(inspector.get_pk_constraint(name)["constrained_columns"]) == {column.name for column in model.primary_key}
            assert {index["name"] for index in inspector.get_indexes(name)} == {index.name for index in model.indexes}, name
            assert {check["name"] for check in inspector.get_check_constraints(name)} == {
                check.name for check in model.constraints if isinstance(check, CheckConstraint)}, name
            assert {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]), fk["options"].get("ondelete"))
                for fk in inspector.get_foreign_keys(name)} == {
                (tuple(element.parent.name for element in fk.elements), fk.referred_table.name,
                    tuple(element.column.name for element in fk.elements), fk.ondelete) for fk in model.foreign_key_constraints}, name
    finally:
        engine.dispose()


def _unit(connection, number: str) -> UUID:
    unit, model = uuid4(), uuid4()
    connection.execute("INSERT INTO equipment_models (id, name, article, maker, role, role_confirmed)"
                       " VALUES (?, 'Автомат резки', 'EW', '', 'operational', 1)", (model.hex,))
    connection.execute(
        "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields,"
        " mirror_key, edited_in_erp, equipment_model_id) VALUES (?, 'ob', ?, '', 'БД.ОБ', ?, ?, '', 0, ?)",
        (unit.hex, number, number, json.dumps({"Инв. номер": number, "Тип": "Оборудование", "Наменование": "Автомат резки",
                                               "Расположение": "ЦАР"}, ensure_ascii=False), model.hex))
    return unit


def _setup_rows(connection, machine: str, other: str) -> dict:
    recipe, task, allocation, person = uuid4(), uuid4(), uuid4(), str(uuid4())
    connection.execute("INSERT INTO equipment_setup_recipe_versions VALUES (?, 1, ?, 'Рецепт', '[]', '[]', 'Панель', ?, ?)",
                       (recipe.hex, machine, uuid4().hex, NOW))
    snapshot = {"id": str(task), "machine_id": machine, "version": 1}
    connection.execute("INSERT INTO equipment_setup_tasks VALUES (?, 1, 'New', ?, ?, ?, 1, ?, ?, ?)",
                       (task.hex, uuid4().hex, machine, recipe.hex, NOW, NOW, json.dumps(snapshot, ensure_ascii=False)))
    connection.execute("INSERT INTO equipment_setup_commands VALUES ('assign-1', 'f', ?)", (json.dumps(snapshot, ensure_ascii=False),))
    for kind, key in (("machine", machine), ("person", person)):
        connection.execute("INSERT INTO production_resource_reservations VALUES ('setup', ?, ?, ?, ?, ?)",
                           (task.hex, kind, key, NOW, "2026-11-05 10:00:00.000000"))
    connection.execute("INSERT INTO production_active_resources VALUES ('machine', ?, 'setup', ?, ?, ?)",
                       (machine, task.hex, task.hex, NOW))
    connection.execute("INSERT INTO production_resource_allocations VALUES (?, ?, ?, 0, ?)", (
        allocation.hex, uuid4().hex, uuid4().hex,
        json.dumps([{"etk_position": 1, "machine_id": machine}, {"etk_position": 3, "machine_id": other}], ensure_ascii=False)))
    return {"recipe": recipe, "task": task, "allocation": allocation, "person": person}


def _state(path) -> dict:
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        state = {
            "recipes": connection.execute("SELECT machine_id FROM equipment_setup_recipe_versions").fetchall(),
            "tasks": [(machine, json.loads(snapshot)) for machine, snapshot in connection.execute(
                "SELECT machine_id, snapshot FROM equipment_setup_tasks")],
            "commands": [json.loads(row[0]) for row in connection.execute("SELECT result FROM equipment_setup_commands")],
            "reservations": sorted(connection.execute(
                "SELECT resource_kind, resource_key FROM production_resource_reservations").fetchall()),
            "active": connection.execute("SELECT resource_kind, resource_key FROM production_active_resources").fetchall(),
        }
        if "production_resource_allocation_machines" in tables:
            state["allocation_machines"] = connection.execute(
                "SELECT etk_position, machine_id FROM production_resource_allocation_machines ORDER BY 1").fetchall()
            state["claim_units"] = sorted(connection.execute(
                "SELECT resource_kind, machine_id FROM production_resource_reservations").fetchall(), key=str)
        else:
            state["allocations"] = [json.loads(row[0]) for row in connection.execute("SELECT machines FROM production_resource_allocations")]
    return state


def test_upgrade_converts_exact_numbers_to_unit_ids_and_downgrade_gives_them_back(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "convert.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        machine, other = _unit(connection, "ОБ.027.2"), _unit(connection, "ОБ.098.1")
        rows = _setup_rows(connection, "ОБ.027.2", "ОБ.098.1")
    before = _state(path)

    command.upgrade(config, AFTER)
    assert "рецептов 1, заданий наладки 1, план станков 1, занятость станков 1, станков назначений 2" in capsys.readouterr().out
    state = _state(path)
    assert state["recipes"] == [(machine.hex,)]
    assert state["tasks"][0][0] == machine.hex and state["tasks"][0][1]["machine_id"] == str(machine)
    assert state["commands"][0]["machine_id"] == str(machine)
    assert state["reservations"] == sorted([("machine", str(machine)), ("person", rows["person"])])
    assert state["claim_units"] == sorted([("machine", machine.hex), ("person", None)], key=str)
    assert state["active"] == [("machine", str(machine))]
    assert state["allocation_machines"] == [(1, machine.hex), (3, other.hex)]
    with sqlite3.connect(path) as connection:  # a machine claim names its unit, a person claim none
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE production_resource_reservations SET machine_id = NULL WHERE resource_kind = 'machine'")

    # Renumbering the unit afterwards changes nothing that points at it; the downgrade returns the current number.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE tech_operation_records SET fields = json_set(fields, '$.\"Инв. номер\"', 'ОБ.027.7')"
                           " WHERE id = ?", (machine.hex,))
    assert _state(path) == state
    command.downgrade(config, BEFORE)
    renumbered = _state(path)
    assert renumbered["recipes"] == [("ОБ.027.7",)] and renumbered["active"] == [("machine", "ОБ.027.7")]
    command.upgrade(config, AFTER)
    assert _state(path) == state
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE tech_operation_records SET fields = json_set(fields, '$.\"Инв. номер\"', 'ОБ.027.2')"
                           " WHERE id = ?", (machine.hex,))
    command.downgrade(config, BEFORE)
    assert _state(path) == before


@pytest.mark.parametrize("case", ["unknown", "duplicate"])
def test_a_number_that_names_no_single_unit_stops_the_migration_with_a_list(tmp_path, monkeypatch, case):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "stop.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        _unit(connection, "ОБ.027.2")
        if case == "duplicate":
            _unit(connection, "ОБ.027.2")
        _setup_rows(connection, "ОБ.027.2" if case == "duplicate" else "ОБ.027.9", "ОБ.027.2")
    before = _state(path)
    with pytest.raises(RuntimeError, match="не переведены на ID единицы БД.ОБ") as error:
        command.upgrade(config, AFTER)
    expected = "этот инв. номер у 2 записей БД.ОБ" if case == "duplicate" else "«ОБ.027.9» — нет записи БД.ОБ"
    assert expected in str(error.value) and "рецепт" in str(error.value) and "задание наладки" in str(error.value)
    assert _state(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (BEFORE,)


def test_rows_seeded_at_0171_go_through_0172_and_back(tmp_path, monkeypatch):
    """The CI migrations job: seed head-1, upgrade, downgrade -1, upgrade (tests.migrations.seed_any_revision)."""
    from tests.migrations.seed_any_revision import seed

    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "seeded.db"
    config = _config(path)
    command.upgrade(config, BEFORE)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inserted = seed(engine, rows=2)
    finally:
        engine.dispose()
    assert inserted["production_resource_reservations"] == 2 and "equipment_setup_tasks" not in inserted
    command.upgrade(config, AFTER)
    command.downgrade(config, BEFORE)
    command.upgrade(config, AFTER)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM production_resource_reservations WHERE machine_id IS NULL").fetchone() == (2,)
    # Seeded at 0172 (head-1 of the next migration): the setup tables point at records by FK again.
    fresh = tmp_path / "seeded-0172.db"
    command.upgrade(_config(fresh), AFTER)
    engine = create_engine(f"sqlite+pysqlite:///{fresh.as_posix()}")
    try:
        inserted = seed(engine, rows=2)
    finally:
        engine.dispose()
    assert inserted["equipment_setup_tasks"] == 2 and inserted["production_resource_allocation_machines"] == 2
