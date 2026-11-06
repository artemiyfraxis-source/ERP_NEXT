"""Ресурсы наладки и станки — по ID физической единицы БД.ОБ, а не по инв. номеру (владелец 05.11.2026: «связка не
через название, а через идентификатор»; назначение ссылается на физический экземпляр).

0170 хранила станок инвентарным номером («ОБ.027.2»); теперь это id записи БД.ОБ (`tech_operation_records.id`,
с 0171 не меняется при перезагрузке), FK RESTRICT — единицу с рецептом, заданием или занятостью не удалить, а смена её
инв. номера ничего не ломает:
- `equipment_setup_recipe_versions.machine_id`, `equipment_setup_tasks.machine_id` — тип UUID + FK (снимок задания в
  `snapshot` и результат команды в `equipment_setup_commands.result` получают тот же id);
- `production_resource_reservations` / `production_active_resources`: ключ станка (`resource_key`) — канонический
  UUID единицы, плюс столбец `machine_id` (FK; CHECK: у станка есть, у сотрудника нет);
- `production_resource_allocations.machines` (JSON) → таблица `production_resource_allocation_machines`
  (назначение, номер операции ЭТК, единица — FK).
Переводятся только точные совпадения: сохранённый номер = инв. номер ровно одной записи БД.ОБ (как его писал 0170:
без пробелов, заглавными). Если хоть одно значение не переводится — миграция останавливается со списком (ничего не
меняя); решает репетиция на копии прода. Откат возвращает текущие инв. номера единиц и так же останавливается, если у
единицы номера нет.

DESTRUCTIVE = False: строки не удаляются; JSON назначений переносится в таблицу (откат возвращает его).
"""

from collections import defaultdict
import json
import re
from typing import Sequence
from uuid import UUID

from alembic import op
import sqlalchemy as sa


revision: str = "0172_resource_unit_ids"
down_revision: str | None = "0171_equipment_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

UNITS = "tech_operation_records"
RECIPES = "equipment_setup_recipe_versions"
TASKS = "equipment_setup_tasks"
COMMANDS = "equipment_setup_commands"
RESERVATIONS = "production_resource_reservations"
ACTIVE = "production_active_resources"
ALLOCATIONS = "production_resource_allocations"
ALLOCATION_MACHINES = "production_resource_allocation_machines"
RECIPE_CHECK = "ck_setup_recipe_machine"
RECIPE_FK, TASK_FK = "fk_setup_recipe_unit", "fk_setup_task_unit"
# Literal copies of app.modules.production.resource_infrastructure on 06.11.2026.
UNIT_CHECK = ("(resource_kind = 'machine' AND machine_id IS NOT NULL)"
              " OR (resource_kind <> 'machine' AND machine_id IS NULL)")
CLAIMS = {RESERVATIONS: ("ck_resource_reservation_unit", "fk_resource_reservation_unit"),
          ACTIVE: ("ck_active_resource_unit", "fk_active_resource_unit")}
COUNTED = (RECIPES, TASKS, COMMANDS, RESERVATIONS, ACTIVE, ALLOCATIONS)


def _fields(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _inventory(fields) -> str:
    """The inventory number as 0170 stored it (references.equipment.physical_equipment: no spaces, upper case)."""
    keys = {" ".join(str(key).split()).casefold(): value for key, value in (fields or {}).items()}
    for key in ("инв. номер", "inventorynumber"):
        if value := str(keys.get(key) or "").strip():
            return re.sub(r"\s+", "", value).casefold().upper().replace("OБ", "ОБ")
    return ""


def _units(bind) -> tuple[dict[str, list[UUID]], dict[UUID, str]]:
    table = sa.table(UNITS, sa.column("id", sa.Uuid()), sa.column("tab", sa.String()), sa.column("fields", sa.JSON()))
    by_number, numbers = defaultdict(list), {}
    for row in bind.execute(sa.select(table.c.id, table.c.fields).where(table.c.tab == "ob")):
        number = _inventory(_fields(row.fields))
        numbers[row.id] = number
        if number:
            by_number[number].append(row.id)
    return by_number, numbers


def _tables():
    recipes = sa.table(RECIPES, sa.column("recipe_id", sa.Uuid()), sa.column("version", sa.Integer()),
                       sa.column("machine_id", sa.String()))
    tasks = sa.table(TASKS, sa.column("id", sa.Uuid()), sa.column("machine_id", sa.String()), sa.column("snapshot", sa.JSON()))
    commands = sa.table(COMMANDS, sa.column("key", sa.String()), sa.column("result", sa.JSON()))
    claims = {name: _claims(name) for name in (RESERVATIONS, ACTIVE)}
    allocations = sa.table(ALLOCATIONS, sa.column("id", sa.Uuid()), sa.column("machines", sa.JSON()))
    return recipes, tasks, commands, claims, allocations


def _claims(name: str, *extra) -> sa.TableClause:
    return sa.table(name, sa.column("resource_kind", sa.String()), sa.column("resource_key", sa.String()),
                    *([sa.column("owner_kind", sa.String()), sa.column("owner_id", sa.Uuid())] if name == RESERVATIONS else []),
                    *extra)


def _claim_where(table, row):
    where = [table.c.resource_kind == row.resource_kind, table.c.resource_key == row.resource_key]
    if "owner_kind" in table.c:
        where += [table.c.owner_kind == row.owner_kind, table.c.owner_id == row.owner_id]
    return where


def _counts(bind) -> dict[str, int]:
    return {name: bind.execute(sa.text(f'SELECT count(*) FROM "{name}"')).scalar_one() for name in COUNTED}


def _sqlite_recreate(table: str, change) -> None:
    with op.batch_alter_table(table, recreate="always") as batch:
        change(batch)


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    before = _counts(bind)
    by_number, _numbers = _units(bind)
    recipes, tasks, commands, claims, allocations = _tables()
    problems: set[str] = set()

    def unit(where: str, value) -> UUID | None:
        found = by_number.get(str(value or ""), [])
        if len(found) == 1:
            return found[0]
        problems.add(f"{where}: «{value}» — " + (f"этот инв. номер у {len(found)} записей БД.ОБ" if found
                                                 else "нет записи БД.ОБ с этим инв. номером"))
        return None

    # 1. Every stored machine → its unit, before anything changes.
    recipe_units = {(row.recipe_id, row.version): unit(f"рецепт {row.recipe_id} v{row.version}", row.machine_id)
                    for row in bind.execute(sa.select(recipes))}
    task_rows = bind.execute(sa.select(tasks)).all()
    task_units = {row.id: unit(f"задание наладки {row.id}", row.machine_id) for row in task_rows}
    command_rows = [(row.key, _json(row.result)) for row in bind.execute(sa.select(commands))]
    for key, result in command_rows:
        if isinstance(result, dict) and "machine_id" in result:
            unit(f"команда наладки {key}", result["machine_id"])
    claim_rows = {name: bind.execute(sa.select(table).where(table.c.resource_kind == "machine")).all()
                  for name, table in claims.items()}
    claim_units = {name: [(row, unit(f"{'план' if name == RESERVATIONS else 'занятость'} станка", row.resource_key))
                          for row in rows] for name, rows in claim_rows.items()}
    bindings = []
    for row in bind.execute(sa.select(allocations)):
        items = _json(row.machines)
        if not isinstance(items, list):
            problems.add(f"назначение {row.id}: станки не списком")
            continue
        for item in items:
            position = item.get("etk_position") if isinstance(item, dict) else None
            if type(position) is not int or position <= 0:
                problems.add(f"назначение {row.id}: нет номера операции ЭТК")
                continue
            found = unit(f"назначение {row.id}, операция ЭТК {position}", item.get("machine_id"))
            bindings.append({"allocation_id": row.id, "etk_position": position, "machine_id": found})
    if problems:
        raise RuntimeError(f"{revision}: не переведены на ID единицы БД.ОБ ({len(problems)}) — исправьте инв. номера в"
                           " БД.ОБ (ровно одна запись на номер) или данные наладки и повторите:\n  "
                           + "\n  ".join(sorted(problems)))

    def stored(identifier: UUID) -> str:  # a Uuid column's text: PostgreSQL uuid text, SQLite CHAR(32) hex
        return str(identifier) if postgres else identifier.hex

    # 2. Recipes and setup tasks: the column becomes the unit's UUID with a FK.
    for (recipe_id, version), found in recipe_units.items():
        bind.execute(recipes.update().where(recipes.c.recipe_id == recipe_id, recipes.c.version == version)
                     .values(machine_id=stored(found)))
    for row in task_rows:
        found = task_units[row.id]
        snapshot = _json(row.snapshot)
        if isinstance(snapshot, dict) and "machine_id" in snapshot:
            snapshot = {**snapshot, "machine_id": str(found)}
        bind.execute(tasks.update().where(tasks.c.id == row.id).values(machine_id=stored(found), snapshot=snapshot))
    for key, result in command_rows:
        if isinstance(result, dict) and "machine_id" in result:
            bind.execute(commands.update().where(commands.c.key == key)
                         .values(result={**result, "machine_id": str(by_number[str(result["machine_id"])][0])}))
    if postgres:
        op.execute(sa.text(f"ALTER TABLE {RECIPES} DROP CONSTRAINT {RECIPE_CHECK}"))
        for table in (RECIPES, TASKS):
            op.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN machine_id TYPE uuid USING machine_id::uuid"))
        op.create_foreign_key(RECIPE_FK, RECIPES, UNITS, ["machine_id"], ["id"], ondelete="RESTRICT")
        op.create_foreign_key(TASK_FK, TASKS, UNITS, ["machine_id"], ["id"], ondelete="RESTRICT")
    else:
        def recipe_change(batch) -> None:
            batch.drop_constraint(RECIPE_CHECK, type_="check")
            batch.alter_column("machine_id", type_=sa.Uuid(), existing_nullable=False)
            batch.create_foreign_key(RECIPE_FK, UNITS, ["machine_id"], ["id"], ondelete="RESTRICT")

        def task_change(batch) -> None:
            batch.alter_column("machine_id", type_=sa.Uuid(), existing_nullable=False)
            batch.create_foreign_key(TASK_FK, UNITS, ["machine_id"], ["id"], ondelete="RESTRICT")

        _sqlite_recreate(RECIPES, recipe_change)
        _sqlite_recreate(TASKS, task_change)

    # 3. Machine claims: the key is the unit's canonical UUID, `machine_id` the same unit as a FK.
    for name, (check, foreign) in CLAIMS.items():
        op.add_column(name, sa.Column("machine_id", sa.Uuid(), nullable=True))
        table = _claims(name, sa.column("machine_id", sa.Uuid()))
        for row, found in claim_units[name]:
            bind.execute(table.update().where(*_claim_where(table, row)).values(resource_key=str(found), machine_id=found))
        if postgres:
            op.create_foreign_key(foreign, name, UNITS, ["machine_id"], ["id"], ondelete="RESTRICT")
            op.create_check_constraint(check, name, UNIT_CHECK)
        else:
            _sqlite_recreate(name, lambda batch, check=check, foreign=foreign: (
                batch.create_foreign_key(foreign, UNITS, ["machine_id"], ["id"], ondelete="RESTRICT"),
                batch.create_check_constraint(check, UNIT_CHECK)))
        op.create_index(f"ix_{name}_machine_id", name, ["machine_id"])

    # 4. Allocation machines: JSON → rows with a FK each.
    op.create_table(
        ALLOCATION_MACHINES,
        sa.Column("allocation_id", sa.Uuid(), sa.ForeignKey(f"{ALLOCATIONS}.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("etk_position", sa.Integer(), primary_key=True),
        sa.Column("machine_id", sa.Uuid(), sa.ForeignKey(f"{UNITS}.id", ondelete="RESTRICT"), nullable=False),
        sa.CheckConstraint("etk_position > 0", name="ck_resource_allocation_machine_position"),
    )
    op.create_index(f"ix_{ALLOCATION_MACHINES}_machine_id", ALLOCATION_MACHINES, ["machine_id"])
    if bindings:
        bind.execute(sa.table(ALLOCATION_MACHINES, sa.column("allocation_id", sa.Uuid()), sa.column("etk_position", sa.Integer()),
                              sa.column("machine_id", sa.Uuid())).insert(), bindings)
    if postgres:
        op.drop_column(ALLOCATIONS, "machines")
    else:
        _sqlite_recreate(ALLOCATIONS, lambda batch: batch.drop_column("machines"))

    after = _counts(bind)
    if after != before:
        raise RuntimeError(f"{revision}: сверка не сошлась: {before} -> {after}")
    print(f"{revision}: строк {before} = {after}; переведено на ID единиц: рецептов {len(recipe_units)}, заданий наладки"
          f" {len(task_units)}, план станков {len(claim_units[RESERVATIONS])}, занятость станков {len(claim_units[ACTIVE])},"
          f" станков назначений {len(bindings)}.")


def downgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    _by_number, numbers = _units(bind)
    problems: set[str] = set()

    def number(where: str, value) -> str:
        try:
            identifier = value if isinstance(value, UUID) else UUID(str(value))
        except ValueError:
            identifier = None
        found = numbers.get(identifier, "")
        if not found:
            problems.add(f"{where}: единица {value} — " + ("без инв. номера" if identifier in numbers else "не найдена в БД.ОБ"))
        return found

    typed = {name: sa.table(name, *columns) for name, columns in (
        (RECIPES, (sa.column("recipe_id", sa.Uuid()), sa.column("version", sa.Integer()), sa.column("machine_id", sa.Uuid()))),
        (TASKS, (sa.column("id", sa.Uuid()), sa.column("machine_id", sa.Uuid()), sa.column("snapshot", sa.JSON()))),
        (ALLOCATION_MACHINES, (sa.column("allocation_id", sa.Uuid()), sa.column("etk_position", sa.Integer()),
                               sa.column("machine_id", sa.Uuid()))))}
    before = _counts(bind)
    recipe_numbers = {(row.recipe_id, row.version): number(f"рецепт {row.recipe_id} v{row.version}", row.machine_id)
                      for row in bind.execute(sa.select(typed[RECIPES]))}
    task_rows = bind.execute(sa.select(typed[TASKS])).all()
    task_numbers = {row.id: number(f"задание наладки {row.id}", row.machine_id) for row in task_rows}
    _recipes, tasks, commands, claims, allocations = _tables()
    command_rows = [(row.key, _json(row.result)) for row in bind.execute(sa.select(commands))]
    command_numbers = {key: number(f"команда наладки {key}", result["machine_id"])
                       for key, result in command_rows if isinstance(result, dict) and "machine_id" in result}
    claim_numbers = {name: [(row, number("станок", row.resource_key)) for row in
                            bind.execute(sa.select(table).where(table.c.resource_kind == "machine")).all()]
                     for name, table in claims.items()}
    machines = defaultdict(list)
    for row in bind.execute(sa.select(typed[ALLOCATION_MACHINES]).order_by(
            typed[ALLOCATION_MACHINES].c.allocation_id, typed[ALLOCATION_MACHINES].c.etk_position)):
        machines[row.allocation_id].append({"etk_position": row.etk_position,
                                            "machine_id": number(f"назначение {row.allocation_id}", row.machine_id)})
    if problems:
        raise RuntimeError(f"{revision}: откат: нет инв. номера для ({len(problems)}):\n  " + "\n  ".join(sorted(problems)))

    # 1. Allocation machines back to JSON.
    op.add_column(ALLOCATIONS, sa.Column("machines", sa.JSON(), nullable=True))
    for allocation_id in bind.execute(sa.select(allocations.c.id)).scalars().all():
        bind.execute(allocations.update().where(allocations.c.id == allocation_id)
                     .values(machines=machines.get(allocation_id, [])))
    if postgres:
        op.alter_column(ALLOCATIONS, "machines", existing_type=sa.JSON(), nullable=False)
    else:
        _sqlite_recreate(ALLOCATIONS, lambda batch: batch.alter_column("machines", existing_type=sa.JSON(), nullable=False))
    op.drop_table(ALLOCATION_MACHINES)

    # 2. Machine claims: the key back to the inventory number.
    for name, (check, foreign) in CLAIMS.items():
        op.drop_index(f"ix_{name}_machine_id", table_name=name)
        if postgres:
            op.drop_constraint(check, name, type_="check")
            op.drop_constraint(foreign, name, type_="foreignkey")
            op.drop_column(name, "machine_id")
        else:
            _sqlite_recreate(name, lambda batch, check=check, foreign=foreign: (
                batch.drop_constraint(check, type_="check"), batch.drop_constraint(foreign, type_="foreignkey"),
                batch.drop_column("machine_id")))
        table = claims[name]
        for row, found in claim_numbers[name]:
            bind.execute(table.update().where(*_claim_where(table, row)).values(resource_key=found))

    # 3. Recipes and setup tasks: text inventory numbers again.
    if postgres:
        op.drop_constraint(RECIPE_FK, RECIPES, type_="foreignkey")
        op.drop_constraint(TASK_FK, TASKS, type_="foreignkey")
        for table in (RECIPES, TASKS):
            op.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN machine_id TYPE varchar(128) USING machine_id::text"))
    else:
        def recipe_change(batch) -> None:
            batch.drop_constraint(RECIPE_FK, type_="foreignkey")
            batch.alter_column("machine_id", type_=sa.String(128), existing_nullable=False)

        def task_change(batch) -> None:
            batch.drop_constraint(TASK_FK, type_="foreignkey")
            batch.alter_column("machine_id", type_=sa.String(128), existing_nullable=False)

        _sqlite_recreate(RECIPES, recipe_change)
        _sqlite_recreate(TASKS, task_change)
    recipes_text = sa.table(RECIPES, sa.column("recipe_id", sa.Uuid()), sa.column("version", sa.Integer()),
                            sa.column("machine_id", sa.String()))
    for (recipe_id, version), found in recipe_numbers.items():
        bind.execute(recipes_text.update().where(recipes_text.c.recipe_id == recipe_id, recipes_text.c.version == version)
                     .values(machine_id=found))
    for row in task_rows:
        snapshot = _json(row.snapshot)
        if isinstance(snapshot, dict) and "machine_id" in snapshot:
            snapshot = {**snapshot, "machine_id": task_numbers[row.id]}
        bind.execute(tasks.update().where(tasks.c.id == row.id).values(machine_id=task_numbers[row.id], snapshot=snapshot))
    for key, result in command_rows:
        if key in command_numbers:
            bind.execute(commands.update().where(commands.c.key == key)
                         .values(result={**result, "machine_id": command_numbers[key]}))
    op.create_check_constraint(RECIPE_CHECK, RECIPES, "length(machine_id) > 0") if postgres else _sqlite_recreate(
        RECIPES, lambda batch: batch.create_check_constraint(RECIPE_CHECK, "length(machine_id) > 0"))

    after = _counts(bind)
    if after != before:
        raise RuntimeError(f"{revision}: откат: сверка не сошлась: {before} -> {after}")
    print(f"{revision}: откат — строк {after}; станки снова по инв. номерам.")
