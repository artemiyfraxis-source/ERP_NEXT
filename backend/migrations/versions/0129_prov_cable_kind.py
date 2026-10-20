"""БД.ПРОВ: у каждой строки свой список кабелей и у каждого списка свои столбцы (владелец 20.10.2026: «у каждого своя
структура… нужен рефактор, чтобы развести по разным»).

Коаксиальные кабели, монтажные провода и многожильные кабели живут в одной таблице `tech_operation_records`
(tab = 'prov') с одним общим набором столбцов, поэтому правка одного списка добавляла столбцы другим, а список строки
нигде не хранился (его вычисляли при каждом чтении из правила `cables.auto_kind` и ручных переносов `cable_kind`).
Миграция:
- добавляет столбец `cable_kind` (coax | wire | multi) и проверку `ck_tech_operation_records_cable_kind`: у строки
  БД.ПРОВ ровно один список, у строк других вкладок — никакого;
- заполняет список каждой строки БД.ПРОВ так, как его показывал экран: ручной перенос по марке, иначе правило
  `cables.auto_kind`; «Прочие кабели» закрыты — строка, которая попала бы туда, идёт в многожильные, если жил больше
  одной, иначе в монтажные;
- делит свои столбцы владельца («+ Столбец», `prov:<имя>`) по спискам: столбец остаётся у тех списков, где в нём есть
  значение (ни у кого нет — у всех трёх), ключ становится `prov:<список>:<имя>`;
- из полей строки убирает только ПУСТЫЕ ключи, которых нет среди столбцов её списка (свои столбцы списка включая).
  Заполненное значение не убирается никогда, даже в чужом столбце.
ID строк, их число, все непустые значения, ссылки `reference_stock_links`, имена кабелей (по ним БД.КОАКС «Провод»
находит кабель) и читатели генератора ЭТК не меняются. Запись идёт через Core (не через сессию ORM), поэтому хук
etk_refresh `before_flush` не ставит запросов на пересборку ЭТК.

DESTRUCTIVE = False: удаляются только пустые ключи. Откат снимает проверку и столбец и сводит свои столбцы обратно в
`prov:<имя>`; убранные пустые ключи не возвращаются (в них ничего не было).
"""

import json
from typing import Sequence
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0129_prov_cable_kind"
down_revision: str | None = "0128_finished_goods_acts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "tech_operation_records"
TAB = "prov"
KINDS = ("coax", "wire", "multi")
CHECK_NAME = "ck_tech_operation_records_cable_kind"
# Literal copy of app.modules.references.infrastructure.CABLE_KIND_CHECK.
CHECK_SQL = (
    "(tab = 'prov' AND cable_kind IS NOT NULL AND cable_kind IN ('coax', 'wire', 'multi'))"
    " OR (tab <> 'prov' AND cable_kind IS NULL)"
)
# Literal copy of app.modules.references.domain._PROV_COLUMNS / _PROV_NEVER on 20.10.2026 (a migration does not follow
# later changes of the column lists).
PROV_COLUMNS = (
    "Марка", "Тип провода", "Стандарт", "Производители", "Сечение, мм2", "AWG", "Жила: проволок × диаметр, мм",
    "Число жил", "Цвета", "Наружный диаметр, мм", "Изоляция", "Экран", "Волновое сопротивление, Ом", "Темп. от, °C",
    "Темп. до, °C", "Напряжение, В", "Ток, А", "Масса, г/м", "Закупок", "Цена посл., ₽/м без НДС", "Поставщик",
    "Ссылка на datasheet", "Источник данных", "Категория",
)
NEVER = {
    "coax": ("AWG", "Ток, А", "Категория"),
    "wire": ("Волновое сопротивление, Ом", "Категория"),
    "multi": ("Ток, А",),
}
COLUMNS = {kind: frozenset(name for name in PROV_COLUMNS if name not in NEVER[kind]) for kind in KINDS}


def _records() -> sa.Table:
    return sa.table(
        TABLE, sa.column("id", sa.Uuid()), sa.column("tab", sa.String()), sa.column("display_text", sa.String()),
        sa.column("fields", sa.JSON()), sa.column("cable_kind", sa.String()),
    )


def _entries() -> sa.Table:
    return sa.table(
        "reference_entries", sa.column("id", sa.Uuid()), sa.column("kind", sa.String()), sa.column("key", sa.String()),
        sa.column("normalized_key", sa.String()), sa.column("value", sa.String()), sa.column("position", sa.Integer()),
    )


def _fields(raw) -> dict:
    value = json.loads(raw or "{}") if isinstance(raw, str) else raw
    return dict(value) if isinstance(value, dict) else {}


def _empty(value) -> bool:
    return not str(value if value is not None else "").strip()


def _folded(key: str) -> str:
    return " ".join(str(key).casefold().split())


def _cores(fields: dict) -> int | None:
    text = str(fields.get("Число жил") or "").strip()
    digits = ""
    for char in text:
        if not char.isdigit():
            break
        digits += char
    return int(digits) if digits else None


def _kinds(bind) -> dict:
    """Row id → its list, as the screen showed it: the manual move by mark, else `cables.auto_kind` (the same rule
    0096 used); «other» → multi with more than one core, else wire."""
    from app.modules.references.cables import auto_kind
    from app.modules.references.domain import cable_key

    records, entries = _records(), _entries()
    overrides = {row.normalized_key: row.value for row in bind.execute(
        sa.select(entries.c.normalized_key, entries.c.value).where(entries.c.kind == "cable_kind"))}
    dia_keys = set()
    for row in bind.execute(sa.select(records.c.fields, records.c.display_text).where(records.c.tab == "kab_coax")):
        fields = _fields(row.fields)
        first = next(iter(fields.values()), "") if fields else ""
        key = cable_key(fields.get("Кабель", first) or row.display_text)
        if key:
            dia_keys.add(key)
    kinds = {}
    for row in bind.execute(sa.select(records.c.id, records.c.fields, records.c.display_text).where(records.c.tab == TAB)):
        fields = {str(key): str(value if value is not None else "") for key, value in _fields(row.fields).items()}
        mark = str(fields.get("Марка") or row.display_text or "").strip()
        kind = overrides.get(cable_key(mark)) or auto_kind(fields, mark, frozenset(dia_keys))[0].value
        if kind not in KINDS:
            cores = _cores(fields)
            kind = "multi" if cores is not None and cores > 1 else "wire"
        kinds[row.id] = kind
    return kinds


def _split_custom_columns(bind, rows: dict, kinds: dict) -> dict:
    """`prov:<name>` → `prov:<kind>:<name>` for every list with a value in it (none → all three). Returns list → names."""
    entries = _entries()
    own = list(bind.execute(sa.select(entries).where(entries.c.kind == "custom_column", entries.c.key.like(f"{TAB}:%"))))
    names = {kind: set() for kind in KINDS}
    fresh = []
    for entry in own:
        name = entry.value
        filled = {kinds[row_id] for row_id, fields in rows.items() if not _empty(fields.get(name))}
        for kind in (kind for kind in KINDS if kind in filled or not filled):
            names[kind].add(name)
            key = f"{TAB}:{kind}:{name}"
            fresh.append({"id": uuid4(), "kind": "custom_column", "key": key, "normalized_key": _folded(key),
                          "value": name, "position": entry.position})
    if own:
        bind.execute(entries.delete().where(entries.c.id.in_([entry.id for entry in own])))
    if fresh:
        bind.execute(entries.insert(), fresh)
    print(f"{revision}: свои столбцы БД.ПРОВ разделены по спискам: {len(own)} → {len(fresh)} "
          f"({', '.join(f'{kind}: {len(names[kind])}' for kind in KINDS)}).")
    return names


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("cable_kind", sa.String(8), nullable=True))
    bind = op.get_bind()
    records = _records()
    kinds = _kinds(bind)
    rows = {row.id: _fields(row.fields) for row in bind.execute(sa.select(records.c.id, records.c.fields).where(records.c.tab == TAB))}
    own = _split_custom_columns(bind, rows, kinds)
    dropped = 0
    for row_id, fields in rows.items():
        kind = kinds[row_id]
        allowed = COLUMNS[kind] | own[kind]
        kept = {key: value for key, value in fields.items() if key in allowed or not _empty(value)}
        dropped += len(fields) - len(kept)
        values = {"cable_kind": kind} if kept == fields else {"cable_kind": kind, "fields": kept}
        bind.execute(records.update().where(records.c.id == row_id).values(**values))
    counts = {kind: sum(1 for value in kinds.values() if value == kind) for kind in KINDS}
    print(f"{revision}: строк БД.ПРОВ {len(rows)} ({', '.join(f'{kind}: {count}' for kind, count in counts.items())}); "
          f"убрано пустых ключей чужих столбцов: {dropped}.")
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {CHECK_NAME} CHECK ({CHECK_SQL})"))
        return
    _sqlite_recreate(lambda batch: batch.create_check_constraint(CHECK_NAME, CHECK_SQL))


def _sqlite_recreate(change) -> None:
    """SQLite copies the table to change its CHECKs. БД.ТЕР rows 0115 left outside its card checks are copied as they
    are (the same PRAGMA 0115 used); the new check itself holds for every row — the upgrade filled every list."""
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            change(batch)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))


def downgrade() -> None:
    bind = op.get_bind()
    entries = _entries()
    split = list(bind.execute(sa.select(entries).where(entries.c.kind == "custom_column", entries.c.key.like(f"{TAB}:%"))))
    merged: dict = {}
    for entry in split:
        if entry.value not in merged or entry.position < merged[entry.value]["position"]:
            key = f"{TAB}:{entry.value}"
            merged[entry.value] = {"id": uuid4(), "kind": "custom_column", "key": key, "normalized_key": _folded(key),
                                   "value": entry.value, "position": entry.position}
    if split:
        bind.execute(entries.delete().where(entries.c.id.in_([entry.id for entry in split])))
    if merged:
        bind.execute(entries.insert(), list(merged.values()))
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CHECK_NAME}"))
        op.drop_column(TABLE, "cable_kind")
        return
    def drop(batch) -> None:
        batch.drop_constraint(CHECK_NAME, type_="check")
        batch.drop_column("cable_kind")

    _sqlite_recreate(drop)
