"""Оборудование связано по ID, а не по названию (владелец 05.11.2026: «связка должна быть не через название, а через
идентификатор»).

- `equipment_models` — модель оборудования (единицы «ОБ.070.1…8» — одна модель): ID, наименование, артикул,
  производитель (правятся, ссылки идут на ID) и роль: «операционное» (`operational`, делает операции) или
  «обеспечивающее» (`supporting`: компрессор, чиллер… — обслуживает станки, машиной операции не бывает);
- `tech_operation_records.equipment_model_id` — модель единицы БД.ОБ (FK RESTRICT; CHECK: у строки БД.ОБ модель есть,
  у других строк — нет). Первое заполнение повторяет группировку экрана: одно наименование + артикул + производитель
  (без учёта регистра и лишних пробелов, производитель «-» = пусто); единица без наименования — модель сама по себе;
- `equipment_operation_links` — ячейка БД.ОП «Машина» / «Инструмент» / «Апликаторы / модули» (каждая ссылка ячейки,
  разделённые «;» или переносом строки) → модель. Связь ставится только при однозначном совпадении: текст ячейки =
  «Для базы» единицы модели или «артикул/наименование» модели (регистр и пробелы вокруг «/» не важны), затем тот же
  текст без «(R)», затем инв. номер единицы, затем номер модели расчётной строки («ОБ.071.X» → единицы ОБ.071.k);
  несколько моделей или ни одной — связи нет, ячейка в отчёте `equipment_links_report`;
- `equipment_unit_links` — единица → единица: «tool» — инструмент матрицы («Для инструмента» = инв. номер ровно одной
  единицы), «serves» — что обслуживает обеспечивающее оборудование («Обслуживает», пока пусто);
- `equipment_template_tools` — строки «Оборудование» шаблонов карт операций (БД.ТК) → модель при единственном
  совпадении (инв. номер, затем «артикул | наименование»); нерешённые записаны без модели (в отчёте);
- роль: модель, на которую ссылаются операции или карты, — операционная (подтверждено); без ссылок, со словом
  «компрессор» / «чиллер» / «охладител…» в наименовании или типе — обеспечивающая (подтверждено); остальное —
  операционная по умолчанию, не подтверждена (в отчёте, владелец подтверждает); слово есть, но операции ссылаются —
  операционная, не подтверждена.
Тексты ячеек и строки не меняются. Печатает сверку: строк ob / op, моделей, связей, нерешённых.

DESTRUCTIVE = False: ничего не удаляется и не переписывается; откат снимает новые таблицы и столбец.
"""

from collections import Counter, defaultdict
import json
import re
from typing import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0171_equipment_identity"
down_revision: str | None = "0170_equipment_setup_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "tech_operation_records"
TEMPLATES = "techcard_operation_templates"
# Literal copies of app.modules.references (equipment.py, equipment_identity.py, technology.py) on 05.11.2026.
OB_MODEL_CHECK_NAME = "ck_tech_operation_records_ob_model"
OB_MODEL_CHECK = "(tab = 'ob' AND equipment_model_id IS NOT NULL) OR (tab <> 'ob' AND equipment_model_id IS NULL)"
MODEL_FK = "fk_tech_operation_records_equipment_model"
MODEL_INDEX = "ix_tech_operation_records_equipment_model_id"
OP_LINK_COLUMNS = ("Машина", "Инструмент", "Апликаторы / модули")
NAME_KEYS = ("Наменование", "Наименование")
SUPPORTING_WORDS = re.compile(r"компрессор|чиллер|охладител", re.IGNORECASE)
_INV = re.compile(r"^\s*([A-Za-zА-Яа-яЁё]+)\.\s*(\d+)(?:\.(\d+|[A-Za-zА-Яа-яЁё]))?\s*$")
_CALC_UNIT_SUFFIX = re.compile(r"\(R\)(?=\s*/)")
_SEPARATORS = re.compile(r"([;\n])")


def _field(fields, *keys) -> str:
    for key in keys:
        value = str((fields or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def _norm(text) -> str:
    return " ".join(str(text or "").casefold().split())


def _ref_key(text) -> str:
    return re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", str(text or ""))).strip().casefold()


def _inv_key(text) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def _parse_inventory(value):
    match = _INV.match(str(value or ""))
    if not match:
        return None
    return match.group(1).upper() + ".", int(match.group(2)), (match.group(3) or "").upper()


def _inventory_order(value) -> tuple:
    parsed = _parse_inventory(value)
    if parsed is None:
        return (1, "", 0, 0, str(value or ""))
    prefix, base, suffix = parsed
    return (0, prefix, base, 0 if not suffix else int(suffix) if suffix.isdigit() else 9999, "")


def _attrs(fields) -> tuple[str, str, str]:
    maker = _field(fields, "Производитель")
    return _field(fields, *NAME_KEYS), _field(fields, "Артикул"), "" if re.fullmatch(r"-+", maker) else maker


def _base(fields, display) -> str:
    return _field(fields, "Для базы", "baseValue") or str(display or "").strip()


def _pieces(text) -> list[str]:
    return [part.strip() for index, part in enumerate(_SEPARATORS.split(str(text or ""))) if not index % 2 and part.strip()]


def _fields(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _records() -> sa.Table:
    return sa.table(TABLE, sa.column("id", sa.Uuid()), sa.column("tab", sa.String()), sa.column("display_text", sa.String()),
                    sa.column("fields", sa.JSON()), sa.column("equipment_model_id", sa.Uuid()))


def _models() -> sa.Table:
    return sa.table("equipment_models", sa.column("id", sa.Uuid()), sa.column("name", sa.String()),
                    sa.column("article", sa.String()), sa.column("maker", sa.String()), sa.column("role", sa.String()),
                    sa.column("role_confirmed", sa.Boolean()))


class _Index:
    """The texts, inventory numbers and model numbers that name each model (the rules of `equipment_identity`)."""

    def __init__(self, units: list[dict], models: dict) -> None:
        self.texts, self.inventories, self.families = defaultdict(set), defaultdict(set), defaultdict(set)
        for unit in units:
            model = unit["model"]
            if key := _ref_key(_base(unit["fields"], unit["display"])):
                self.texts[key].add(model)
            number = _field(unit["fields"], "Инв. номер")
            if number:
                self.inventories[_inv_key(number)].add(model)
                if (parsed := _parse_inventory(number)) is not None:
                    self.families[parsed[:2]].add(model)
        for model, (name, article, _maker) in models.items():
            if name and (key := _ref_key(f"{article}/{name}")):
                self.texts[key].add(model)

    def by_text(self, piece: str) -> set:
        for found in (self.texts.get(_ref_key(piece)), self.texts.get(_ref_key(_CALC_UNIT_SUFFIX.sub("", piece)))):
            if found:
                return set(found)
        return set()

    def by_inventory(self, piece: str) -> set:
        if found := self.inventories.get(_inv_key(piece)):
            return set(found)
        parsed = _parse_inventory(piece)
        return set(self.families.get(parsed[:2], ())) if parsed is not None else set()

    def candidates(self, piece: str) -> set:
        return self.by_text(piece) or self.by_inventory(piece)


def _template_tools(bind) -> list[tuple[str, int, str, str]]:
    """(template id, position in its tooling, name, inventory number) of each «Оборудование» row: the ERP edit when
    there is one, else the imported document."""
    if not sa.inspect(bind).has_table(TEMPLATES):
        return []
    rows = []
    for template_id, document, edited in bind.execute(sa.text(f"SELECT id, document, edited FROM {TEMPLATES}")):
        source = _fields(edited) or _fields(document)
        tooling = source.get("tooling") if isinstance(source.get("tooling"), list) else []
        for position, tool in enumerate(tooling):
            if isinstance(tool, dict) and str(tool.get("section") or "") == "Оборудование" and str(tool.get("name") or "").strip():
                rows.append((str(template_id), position, str(tool.get("name") or "").strip()[:2000],
                             str(tool.get("inventory_number") or "").strip()[:255]))
    return rows


def _template_candidates(index: _Index, name: str, inventory: str) -> set:
    named = set()
    if " | " in name:
        named = index.by_text(name.replace(" | ", "/"))
    else:
        named = index.by_text(name) or index.by_text(f"/{name}")
    found = index.by_inventory(inventory) if inventory else set()
    if not found:
        return named
    if len(found) > 1 and named and len(found & named) == 1:
        return found & named
    return found


def upgrade() -> None:
    bind = op.get_bind()
    dialect = "postgresql" if bind.dialect.name == "postgresql" else "sqlite"
    op.create_table(
        "equipment_models",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(512), nullable=False, server_default=""),
        sa.Column("article", sa.String(512), nullable=False, server_default=""),
        sa.Column("maker", sa.String(512), nullable=False, server_default=""),
        sa.Column("role", sa.String(16), nullable=False, server_default="operational"),
        sa.Column("role_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("role IN ('operational', 'supporting')", name="ck_equipment_models_role"),
    )
    op.add_column(TABLE, sa.Column("equipment_model_id", sa.Uuid(), nullable=True))
    records, models_table = _records(), _models()
    total = bind.execute(sa.select(sa.func.count()).select_from(records)).scalar_one()

    # 1. Models: the screen's grouping (name + article + maker); a nameless unit stands alone.
    units = [{"id": row.id, "display": row.display_text, "fields": _fields(row.fields)} for row in bind.execute(
        sa.select(records.c.id, records.c.display_text, records.c.fields).where(records.c.tab == "ob"))]
    groups: dict = defaultdict(list)
    for unit in units:
        name, article, maker = _attrs(unit["fields"])
        key = f"{_norm(name)}|{_norm(article)}|{_norm(maker)}" if _norm(name) else f"#{unit['id']}"
        groups[key].append(unit)
    models: dict = {}  # id -> (name, article, maker)
    kinds: dict = {}  # id -> the types of its units (for the role)
    for items in groups.values():
        items.sort(key=lambda unit: _inventory_order(_field(unit["fields"], "Инв. номер")))
        model_id = uuid4()
        models[model_id] = _attrs(items[0]["fields"])
        kinds[model_id] = " ".join(_field(unit["fields"], "Тип", "obType") for unit in items)
        for unit in items:
            unit["model"] = model_id
    if models:
        bind.execute(models_table.insert(), [{"id": model_id, "name": name, "article": article, "maker": maker,
                                              "role": "operational", "role_confirmed": False}
                                             for model_id, (name, article, maker) in models.items()])
    for unit in units:
        bind.execute(records.update().where(records.c.id == unit["id"]).values(equipment_model_id=unit["model"]))

    # 2. The record checks: a БД.ОБ unit has a model, any other record none.
    if dialect == "postgresql":
        op.create_foreign_key(MODEL_FK, TABLE, "equipment_models", ["equipment_model_id"], ["id"], ondelete="RESTRICT")
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {OB_MODEL_CHECK_NAME} CHECK ({OB_MODEL_CHECK})"))
    else:
        _sqlite_recreate(lambda batch: (
            batch.create_foreign_key(MODEL_FK, "equipment_models", ["equipment_model_id"], ["id"], ondelete="RESTRICT"),
            batch.create_check_constraint(OB_MODEL_CHECK_NAME, OB_MODEL_CHECK)))
    op.create_index(MODEL_INDEX, TABLE, ["equipment_model_id"])

    # 3. The link tables.
    op.create_table(
        "equipment_operation_links",
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("op_column", sa.String(32), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], [f"{TABLE}.id"], ondelete="CASCADE",
                                name="fk_equipment_operation_links_operation"),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="RESTRICT",
                                name="fk_equipment_operation_links_model"),
        sa.CheckConstraint("op_column IN ('Машина', 'Инструмент', 'Апликаторы / модули')",
                           name="ck_equipment_operation_links_column"),
        sa.CheckConstraint("position >= 0", name="ck_equipment_operation_links_position"),
    )
    op.create_index("ix_equipment_operation_links_model_id", "equipment_operation_links", ["model_id"])
    op.create_table(
        "equipment_unit_links",
        sa.Column("unit_id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("target_id", sa.Uuid(), primary_key=True),
        sa.ForeignKeyConstraint(["unit_id"], [f"{TABLE}.id"], ondelete="CASCADE", name="fk_equipment_unit_links_unit"),
        sa.ForeignKeyConstraint(["target_id"], [f"{TABLE}.id"], ondelete="RESTRICT", name="fk_equipment_unit_links_target"),
        sa.CheckConstraint("kind IN ('tool', 'serves')", name="ck_equipment_unit_links_kind"),
    )
    op.create_index("ix_equipment_unit_links_target_id", "equipment_unit_links", ["target_id"])
    op.create_index("ux_equipment_unit_links_one_tool", "equipment_unit_links", ["unit_id"], unique=True,
                    sqlite_where=sa.text("kind = 'tool'"), postgresql_where=sa.text("kind = 'tool'"))
    op.create_table(
        "equipment_template_tools",
        sa.Column("template_id", sa.String(255), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("tool_name", sa.String(2000), nullable=False),
        sa.Column("inventory_number", sa.String(255), nullable=False, server_default=""),
        sa.Column("candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["model_id"], ["equipment_models.id"], ondelete="RESTRICT",
                                name="fk_equipment_template_tools_model"),
        sa.CheckConstraint("candidates >= 0", name="ck_equipment_template_tools_candidates"),
    )
    op.create_index("ix_equipment_template_tools_model_id", "equipment_template_tools", ["model_id"])

    # 4. БД.ОП cells → models, only on an unambiguous match.
    index = _Index(units, models)
    cells = Counter()
    op_links, linked_models = [], set()
    operations = bind.execute(sa.select(records.c.id, records.c.fields).where(records.c.tab == "op")).all()
    for row in operations:
        fields = _fields(row.fields)
        for column in OP_LINK_COLUMNS:
            for position, piece in enumerate(_pieces(fields.get(column, ""))):
                found = index.candidates(piece)
                cells["linked" if len(found) == 1 else "ambiguous" if found else "unmatched"] += 1
                if len(found) == 1:
                    model_id = next(iter(found))
                    op_links.append({"operation_id": row.id, "op_column": column, "position": position,
                                     "model_id": model_id})
                    linked_models.add(model_id)
    if op_links:
        bind.execute(sa.table("equipment_operation_links", sa.column("operation_id", sa.Uuid()),
                              sa.column("op_column", sa.String()), sa.column("position", sa.Integer()),
                              sa.column("model_id", sa.Uuid())).insert(), op_links)

    # 5. «Для инструмента» of a matrix → the one unit of that inventory number.
    by_inventory = defaultdict(list)
    for unit in units:
        if number := _field(unit["fields"], "Инв. номер"):
            by_inventory[_inv_key(number)].append(unit["id"])
    tools, tool_counts = [], Counter()
    for unit in units:
        text = _field(unit["fields"], "Для инструмента")
        if not text:
            continue
        found = [item for item in by_inventory.get(_inv_key(text), []) if item != unit["id"]]
        tool_counts["linked" if len(found) == 1 else "ambiguous" if found else "unmatched"] += 1
        if len(found) == 1:
            tools.append({"unit_id": unit["id"], "kind": "tool", "target_id": found[0]})
    if tools:
        bind.execute(sa.table("equipment_unit_links", sa.column("unit_id", sa.Uuid()), sa.column("kind", sa.String()),
                              sa.column("target_id", sa.Uuid())).insert(), tools)

    # 6. «Оборудование» rows of the card templates → a model on a single match; the rest kept without one.
    template_rows, template_counts = [], Counter()
    for template_id, position, name, inventory in _template_tools(bind):
        found = _template_candidates(index, name, inventory)
        template_counts["linked" if len(found) == 1 else "ambiguous" if found else "unmatched"] += 1
        model_id = next(iter(found)) if len(found) == 1 else None
        if model_id is not None:
            linked_models.add(model_id)
        template_rows.append({"template_id": template_id, "position": position, "tool_name": name,
                              "inventory_number": inventory, "candidates": len(found), "model_id": model_id})
    if template_rows:
        bind.execute(sa.table("equipment_template_tools", sa.column("template_id", sa.String()),
                              sa.column("position", sa.Integer()), sa.column("tool_name", sa.String()),
                              sa.column("inventory_number", sa.String()), sa.column("candidates", sa.Integer()),
                              sa.column("model_id", sa.Uuid())).insert(), template_rows)

    # 7. Roles: only the certain ones are confirmed.
    roles = Counter()
    for model_id, (name, _article, _maker) in models.items():
        worded = bool(SUPPORTING_WORDS.search(name) or SUPPORTING_WORDS.search(kinds[model_id]))
        if model_id in linked_models:
            role, confirmed = "operational", not worded
        elif worded:
            role, confirmed = "supporting", True
        else:
            role, confirmed = "operational", False
        roles[(role, confirmed)] += 1
        if (role, confirmed) != ("operational", False):
            bind.execute(models_table.update().where(models_table.c.id == model_id)
                         .values(role=role, role_confirmed=confirmed))

    # 8. Reconciliation.
    total_after = bind.execute(sa.select(sa.func.count()).select_from(records)).scalar_one()
    without = bind.execute(sa.select(sa.func.count()).select_from(records).where(
        records.c.tab == "ob", records.c.equipment_model_id.is_(None))).scalar_one()
    if total_after != total or without:
        raise RuntimeError(f"{revision}: сверка не сошлась: строк {total} -> {total_after}, единиц без модели {without}")
    print(f"{revision}: строк всего {total} = {total_after}; БД.ОБ единиц {len(units)} -> моделей {len(models)}"
          f" (из них несколько единиц — {sum(1 for items in groups.values() if len(items) > 1)}); БД.ОП строк"
          f" {len(operations)}.")
    print(f"{revision}: ссылки БД.ОП: связано {cells['linked']}, неоднозначно {cells['ambiguous']}, не найдено"
          f" {cells['unmatched']}; «Для инструмента»: связано {tool_counts['linked']}, неоднозначно"
          f" {tool_counts['ambiguous']}, не найдено {tool_counts['unmatched']}; шаблоны карт «Оборудование»: связано"
          f" {template_counts['linked']}, неоднозначно {template_counts['ambiguous']}, не найдено"
          f" {template_counts['unmatched']}.")
    print(f"{revision}: роли: операционное подтверждено {roles[('operational', True)]}, обеспечивающее подтверждено"
          f" {roles[('supporting', True)]}, операционное по умолчанию (подтвердить) {roles[('operational', False)]}."
          " Нерешённое — python -m app.modules.references.equipment_links_report.")


def _sqlite_recreate(change) -> None:
    """SQLite copies the table to add a FK and a CHECK; rows 0115 left outside the card checks are copied as they are."""
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            change(batch)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))


def downgrade() -> None:
    bind = op.get_bind()
    records = _records()
    total = bind.execute(sa.select(sa.func.count()).select_from(records)).scalar_one()
    for name in ("equipment_template_tools", "equipment_unit_links", "equipment_operation_links"):
        op.drop_table(name)
    op.drop_index(MODEL_INDEX, table_name=TABLE)
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {OB_MODEL_CHECK_NAME}"))
        op.drop_constraint(MODEL_FK, TABLE, type_="foreignkey")
        op.drop_column(TABLE, "equipment_model_id")
    else:
        def change(batch) -> None:
            batch.drop_constraint(OB_MODEL_CHECK_NAME, type_="check")
            batch.drop_constraint(MODEL_FK, type_="foreignkey")
            batch.drop_column("equipment_model_id")

        _sqlite_recreate(change)
    op.drop_table("equipment_models")
    total_after = bind.execute(sa.select(sa.func.count()).select_from(sa.table(TABLE))).scalar_one()
    if total_after != total:
        raise RuntimeError(f"{revision}: откат: строк {total} -> {total_after}")
    print(f"{revision}: откат — строк {total_after}, модели и связи оборудования сняты.")
