"""«Монтажные провода»: каждый цвет — своя строка (владелец 20.10.2026: «имелось ввиду каждый цвет своя строка»).

До миграции строка монтажного провода БД.ПРОВ (`cable_kind = 'wire'`, миграция 0129) держала все цвета марки списком в
ячейке «Цвета» («чёрный, красный, белый»), одна строка на марку. Теперь у строки монтажного провода один цвет — ячейка
«Цвет», ключ строки — марка + цвет. Миграция:
- у каждой строки монтажного провода ячейка «Цвета» становится «Цвет» на том же месте (порядок столбцов сохранён);
- строка с N > 1 цветами: сама запись получает ПЕРВЫЙ цвет списка — её ID, ссылки `reference_stock_links`, признак
  «правлено в ERP», ключ зеркала остаются; на каждый следующий цвет создаётся новая запись с теми же полями, кроме
  цвета (тот же список, текст, ключ сортировки, ключ зеркала, признак «правлено в ERP»). ID новой записи — uuid5 от
  ID исходной и номера цвета: откат по нему узнаёт добавленные строки;
- строки с 0 или 1 цветом остаются одной строкой (только «Цвета» → «Цвет»);
- повтор цвета в списке («чёрный, Чёрный») даёт одну строку; написание цвета не меняется.
Коаксиальные и многожильные строки не трогаются («Цвета» многожильного — цвета жил, список). Структура монтажного
провода (`hookup_wires`) — по марке, одна на все её цвета; её миграция не трогает. Запись идёт через Core (не через
сессию ORM), поэтому хук etk_refresh `before_flush` не ставит запросов на пересборку ЭТК; значения, которые читает
генератор ЭТК (марка, сечение, AWG, Ø, экран, число жил), у всех строк марки те же, что были у одной.

DESTRUCTIVE = False: ни одно значение не теряется. Откат сводит строки одной марки обратно в одну со списком «Цвета»
(сначала цвет оставшейся записи, затем добавленных по порядку); удаляет только строки без ссылок на склад, иначе
отказывается с перечнем таких строк.
"""

import json
import re
from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from alembic import op
import sqlalchemy as sa


revision: str = "0132_prov_wire_color_rows"
down_revision: str | None = "0131_prov_cable_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "tech_operation_records"
LIST, ONE = "Цвета", "Цвет"
MARK = "Марка"
# The namespace of the ids of the rows this migration adds (uuid5(namespace, "<source id>:<colour number>")).
NAMESPACE = uuid5(NAMESPACE_URL, "erp-next:0132_prov_wire_color_rows")


def _records() -> sa.Table:
    return sa.table(
        TABLE, sa.column("id", sa.Uuid()), sa.column("tab", sa.String()), sa.column("cable_kind", sa.String()),
        sa.column("display_text", sa.String()), sa.column("normalized_search", sa.Text()),
        sa.column("source_sheet", sa.String()), sa.column("sort_key", sa.String()), sa.column("fields", sa.JSON()),
        sa.column("mirror_key", sa.String()), sa.column("edited_in_erp", sa.Boolean()),
    )


def _links() -> sa.Table:
    return sa.table("reference_stock_links", sa.column("record_id", sa.Uuid()))


def _fields(raw) -> dict:
    value = json.loads(raw or "{}") if isinstance(raw, str) else raw
    return dict(value) if isinstance(value, dict) else {}


# Literal copies of app.modules.references.domain.cable_key / color_key / split_colors on 20.10.2026.
def _cable_key(value: object) -> str:
    return re.sub(r"[\s-]", "", str(value or "").lower())


def _color_key(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _split(text: object) -> list[str]:
    seen: set[str] = set()
    colors: list[str] = []
    for piece in re.split(r"[,;]", str(text or "")):
        color = " ".join(piece.split())
        if color and _color_key(color) not in seen:
            seen.add(_color_key(color))
            colors.append(color)
    return colors


def _renamed(fields: dict, old: str, new: str, value: str) -> dict:
    """`old` → `new` in place, holding `value`; without `old` — `new` set where it is, else added at the end."""
    if old not in fields:
        return {**fields, new: value}
    return {(new if key == old else key): (value if key == old else item) for key, item in fields.items() if key != new}


def copy_id(source: UUID, number: int) -> UUID:
    return uuid5(NAMESPACE, f"{source}:{number}")


def _wire_rows(bind) -> list:
    records = _records()
    return list(bind.execute(sa.select(records).where(records.c.tab == "prov", records.c.cable_kind == "wire")))


def upgrade() -> None:
    bind = op.get_bind()
    records = _records()
    rows = _wire_rows(bind)
    conflicts = []
    for row in rows:
        fields = _fields(row.fields)
        if str(fields.get(ONE) or "").strip() and str(fields.get(LIST) or "").strip():
            conflicts.append(f"{fields.get(MARK) or row.display_text}: «{LIST}» = «{fields[LIST]}», «{ONE}» = «{fields[ONE]}»")
    if conflicts:
        raise RuntimeError(f"{revision}: у монтажных проводов заполнены и «{LIST}», и «{ONE}» — не знаю, какой цвет "
                           "верный; поправьте строки и запустите снова: " + "; ".join(conflicts))
    split = added = 0
    fresh = []
    for row in rows:
        fields = _fields(row.fields)
        colors = _split(fields.get(LIST)) or _split(fields.get(ONE)) or [""]
        first = _renamed(fields, LIST, ONE, colors[0])
        if first != fields:
            bind.execute(records.update().where(records.c.id == row.id).values(fields=first))
        if len(colors) > 1:
            split += 1
        for number, color in enumerate(colors[1:], start=1):
            fresh.append({
                "id": copy_id(row.id, number), "tab": row.tab, "cable_kind": row.cable_kind,
                "display_text": row.display_text, "normalized_search": row.normalized_search,
                "source_sheet": row.source_sheet, "sort_key": row.sort_key, "fields": {**first, ONE: color},
                "mirror_key": row.mirror_key, "edited_in_erp": row.edited_in_erp,
            })
    if fresh:
        bind.execute(records.insert(), fresh)
        added = len(fresh)
    print(f"{revision}: монтажных проводов {len(rows)}, со списком цветов {split}; добавлено строк по цветам {added}, "
          f"стало {len(rows) + added}.")
    # A custom column «Цвет» of the монтажные (none on 20.10.2026) would now repeat the list's own column.
    entries = sa.table("reference_entries", sa.column("kind", sa.String()), sa.column("normalized_key", sa.String()))
    bind.execute(entries.delete().where(entries.c.kind == "custom_column", entries.c.normalized_key == "prov:wire:цвет"))


def downgrade() -> None:
    bind = op.get_bind()
    records = _records()
    rows = sorted(_wire_rows(bind), key=lambda row: (row.sort_key or "", str(row.id)))
    linked = {row.record_id for row in bind.execute(sa.select(_links().c.record_id)) if row.record_id is not None}
    by_mark: dict[str, list] = {}
    for row in rows:
        fields = _fields(row.fields)
        by_mark.setdefault(_cable_key(fields.get(MARK) or row.display_text), []).append(row)
    plans = []
    refused = []
    for group in by_mark.values():
        ids = {row.id for row in group}
        # the row the upgrade kept: the one whose copies (uuid5 of its id) are in the group; else one with stock links
        keeper = next((row for row in group if copy_id(row.id, 1) in ids), None) \
            or next((row for row in group if row.id in linked), group[0])
        copies = [copy_id(keeper.id, number) for number in range(1, len(group))]
        order = {row_id: index for index, row_id in enumerate(copies)}
        others = sorted((row for row in group if row.id != keeper.id),
                        key=lambda row: (order.get(row.id, len(order)), row.sort_key or "", str(row.id)))
        blocked = [row for row in others if row.id in linked]
        if blocked:
            refused += [f"{_fields(row.fields).get(MARK) or row.display_text} {_fields(row.fields).get(ONE) or '(без цвета)'}"
                        for row in blocked]
            continue
        colors = []
        for row in (keeper, *others):
            color = str(_fields(row.fields).get(ONE) or "").strip()
            if color and _color_key(color) not in {_color_key(item) for item in colors}:
                colors.append(color)
        plans.append((keeper, others, colors))
    if refused:
        raise RuntimeError(f"{revision}: откат не сводит цвета в одну строку — к этим строкам привязаны партии склада, "
                           "удалить их нельзя: " + "; ".join(refused) + ". Перепривяжите партии к строке марки и "
                           "запустите откат снова.")
    deleted = 0
    for keeper, others, colors in plans:
        fields = _renamed(_fields(keeper.fields), ONE, LIST, ", ".join(colors))
        bind.execute(records.update().where(records.c.id == keeper.id).values(fields=fields))
        if others:
            bind.execute(records.delete().where(records.c.id.in_([row.id for row in others])))
            deleted += len(others)
    print(f"{revision}: откат — марок монтажных проводов {len(plans)}, удалено строк по цветам {deleted}.")
