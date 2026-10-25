"""БД.ПРОВ: новый список кабелей «ETHERNET» (`cable_kind = 'lan'`) — LAN-кабели уходят из многожильных (владелец
25.10.2026, глядя на «Многожильные» — 366 строк, из них 230 витых пар Cat5e/Cat6…: «интернетные вынести вообще в другую
вкладку — новая вкладка, новая БД»; название — «ETHERNET», «не витая пара»).

Миграция:
- расширяет проверку `ck_tech_operation_records_cable_kind` до coax | wire | multi | lan;
- переносит в 'lan' строки БД.ПРОВ из 'multi', у которых «Тип провода» или «Марка» называют кабель Ethernet (правило
  `ETHERNET` — буквальная копия `app.modules.references.cables.ETHERNET` на 25.10.2026): категория Cat5…Cat8, код экрана
  по ISO/IEC 11801 (U/UTP, F/UTP, SF/UTP, U/FTP, S/FTP и магазинные SFTP / SSTP / FTP / UTP), слово LAN, Industrial
  Ethernet / PROFINET / EtherCAT. Витые пары не для Ethernet — PROFIBUS (6XV1830-0EH10), DIN 47100 (PAAR-TRONIC-CY),
  телефонные ШТЛЭн / ШТСПЭн, контрольные КДВВГ / КСПВГ / LiYCY — ничего из этого не называют и остаются в многожильных;
- у перенесённой строки заполняет из типа, марки и «Число жил» пустые «Категория» (Cat5e…), «Число пар» («8 (4 пары)»
  → 4), «Прокладка» (внутренняя / наружная / внутренняя и наружная), а «Экран» ставит кодом (U/UTP, F/UTP…) — прежний
  текст («нет», «общий — фольга»…) не теряется: он записан в «Источник данных» строки («…Экран «U/UTP» (было «нет»)»),
  откат возвращает его оттуда; пустые столбцы списка ETHERNET строке добавляются пустыми (правка в каждой ячейке);
- ручные переносы по марке (`reference_entries` kind 'cable_kind') 'multi' перенесённых марок становятся 'lan'.
Число строк БД.ПРОВ не меняется (проверка: всего до = всего после, lan = перенесённые, multi = было − перенесённые),
ID строк, ссылки `reference_stock_links` и имена кабелей не меняются. Запись идёт через Core, хук etk_refresh не
срабатывает. Список перенесённых марок на проде 25.10.2026 (230) — `docs/data/ETHERNET — перенос из многожильных
25.10.csv`.

DESTRUCTIVE = False: ни одна строка и ни одно непустое значение не удаляются. Откат возвращает строки 'lan' в 'multi',
прежний «Экран» и поля, которые заполнила миграция (по её записи в «Источник данных»), ручные переносы 'lan' → 'multi'
и сужает проверку обратно.
"""

import json
import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0150_prov_lan_kind"
down_revision: str | None = "0149_receiving_defect_quantity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "tech_operation_records"
TAB = "prov"
CHECK_NAME = "ck_tech_operation_records_cable_kind"
# Literal copies of app.modules.references.infrastructure.CABLE_KIND_CHECK before and after this revision.
CHECK_OLD = (
    "(tab = 'prov' AND cable_kind IS NOT NULL AND cable_kind IN ('coax', 'wire', 'multi'))"
    " OR (tab <> 'prov' AND cable_kind IS NULL)"
)
CHECK_NEW = (
    "(tab = 'prov' AND cable_kind IS NOT NULL AND cable_kind IN ('coax', 'wire', 'multi', 'lan'))"
    " OR (tab <> 'prov' AND cable_kind IS NULL)"
)
# Literal copies of app.modules.references.cables (ETHERNET, the parsers) and domain._LAN_COLUMNS on 25.10.2026.
ETHERNET = re.compile(
    r"ethernet|profinet|ethercat|\bLAN\b|\bcat(?:egory)?\.?\s?(?:5e?|6a?|7a?|8)(?![0-9])|категори[яи]\s?(?:5e?|6a?|7a?|8)(?![0-9])"
    r"|(?<![A-Za-z])(?:[USF]{1,2}/[UF]TP|SFTP|SSTP|FTP|UTP)(?![A-Za-z])",
    re.IGNORECASE,
)
CATEGORY = re.compile(r"\bcat(?:egory)?\.?\s?(5e|5|6a|6|7a|7|8)(?![0-9])|категори[яи]\s?(5e|5|6a|6|7a|7|8)(?![0-9])", re.IGNORECASE)
SCREEN = re.compile(r"(?<![A-Za-z])(SF/UTP|S/FTP|F/FTP|U/FTP|F/UTP|U/UTP|S/UTP|SFTP|SSTP|FTP|UTP)(?![A-Za-z])", re.IGNORECASE)
SCREEN_CODES = {"SFTP": "SF/UTP", "SSTP": "S/FTP", "FTP": "F/UTP", "UTP": "U/UTP"}
PAIRS = re.compile(r"\(\s*(\d+)\s*пар")
LAN_COLUMNS = (
    "Марка", "Тип провода", "Категория", "Стандарт", "Производители",
    "AWG", "Сечение, мм2", "Число пар", "Жила: проволок × диаметр, мм", "Материал жилы", "Материал изоляции жилы", "Экран",
    "Материал оболочки", "Прокладка", "Наружный диаметр, мм",
    "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Ссылка на datasheet",
)
SOURCE = "Источник данных"
SCREEN_FIELD = "Экран"
FILLED = ("Категория", "Число пар", "Прокладка")  # filled only where empty
JOURNAL = "25.10.2026 (0150): в список ETHERNET"
_JOURNAL_ITEM = re.compile(r"(Категория|Число пар|Прокладка|Экран) «([^»]*)»(?: \(было «([^»]*)»\))?")


def _records() -> sa.Table:
    return sa.table(
        TABLE, sa.column("id", sa.Uuid()), sa.column("tab", sa.String()), sa.column("display_text", sa.String()),
        sa.column("fields", sa.JSON()), sa.column("cable_kind", sa.String()),
    )


def _entries() -> sa.Table:
    return sa.table(
        "reference_entries", sa.column("id", sa.Uuid()), sa.column("kind", sa.String()),
        sa.column("normalized_key", sa.String()), sa.column("value", sa.String()),
    )


def _fields(raw) -> dict:
    value = json.loads(raw or "{}") if isinstance(raw, str) else raw
    return dict(value) if isinstance(value, dict) else {}


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def cable_key(value) -> str:
    """Literal copy of app.modules.references.domain.cable_key — the key of the `cable_kind` map."""
    return re.sub(r"[\s-]", "", str(value or "").lower())


def _mark(fields: dict, display: str) -> str:
    return _text(fields.get("Марка") or display)


def is_ethernet(fields: dict, mark: str) -> bool:
    return bool(ETHERNET.search(f"{_text(fields.get('Тип провода'))} {mark}"))


def _category(text: str) -> str:
    match = CATEGORY.search(text or "")
    return f"Cat{(match.group(1) or match.group(2)).upper().replace('E', 'e')}" if match else ""


def _screen(text: str) -> str:
    match = SCREEN.search(text or "")
    if not match:
        return ""
    code = match.group(1).upper()
    return SCREEN_CODES.get(code, code)


def _pairs(cores: str) -> str:
    match = PAIRS.search(cores)
    if match:
        return match.group(1)
    bare = re.fullmatch(r"\s*(\d+)\s*", cores)
    return str(int(bare.group(1)) // 2) if bare and int(bare.group(1)) % 2 == 0 and int(bare.group(1)) > 0 else ""


def _laying(text: str) -> str:
    lowered = (text or "").casefold()
    inner, outer = "внутрен" in lowered, "наруж" in lowered or "внешн" in lowered
    return "внутренняя и наружная" if inner and outer else "внутренняя" if inner else "наружная" if outer else ""


def derived(fields: dict, mark: str) -> dict:
    """What the type, the mark and «Число жил» say: «Категория», «Экран» (code), «Число пар», «Прокладка»."""
    kind_type = _text(fields.get("Тип провода"))
    found = {
        "Категория": _category(kind_type) or _category(mark),
        SCREEN_FIELD: _screen(kind_type) or _screen(mark),
        "Число пар": _pairs(_text(fields.get("Число жил"))),
        "Прокладка": _laying(kind_type),
    }
    return {name: value for name, value in found.items() if value}


def lan_row(fields: dict, mark: str) -> tuple[dict, str]:
    """The moved row's fields and its journal entry: the empty derived cells filled, «Экран» as its code (the old text
    in the entry), the list's empty columns padded. A filled value other than «Экран» is never changed."""
    result = dict(fields)
    said = []
    for name, value in derived(fields, mark).items():
        old = _text(fields.get(name))
        if name == SCREEN_FIELD and old and old != value:
            said.append(f"{name} «{value}» (было «{old}»)")
            result[name] = value
        elif not old:
            said.append(f"{name} «{value}»")
            result[name] = value
    for name in LAN_COLUMNS:
        result.setdefault(name, "")
    entry = JOURNAL + (" — из типа, марки и числа жил: " + ", ".join(said) if said else "")
    source = _text(result.get(SOURCE))
    result[SOURCE] = f"{source}; {entry}" if source else entry
    return result, entry


def multi_row(fields: dict) -> dict:
    """Downgrade of `lan_row`: the journal entry removed, «Экран» and the cells it filled restored."""
    result = dict(fields)
    source = _text(result.get(SOURCE))
    start = source.find(JOURNAL)
    if start < 0:
        return result
    end = source.find("; ", start)
    entry = source[start:] if end < 0 else source[start:end]
    for name, value, old in _JOURNAL_ITEM.findall(entry):
        if _text(result.get(name)) != value:
            continue  # changed after the move: the newer value stays
        if old:
            result[name] = old
        else:
            result.pop(name, None)
    rest = (source[:start] + ("" if end < 0 else source[end + 2:])).strip()
    rest = rest[:-1].strip() if rest.endswith(";") else rest
    if rest:
        result[SOURCE] = rest
    else:
        result.pop(SOURCE, None)
    for name in FILLED:
        if name in result and not _text(result[name]):
            del result[name]
    return result


def _counts(bind) -> dict:
    records = _records()
    rows = bind.execute(sa.select(records.c.cable_kind, sa.func.count()).where(records.c.tab == TAB)
                        .group_by(records.c.cable_kind))
    return {kind: count for kind, count in rows}


def _set_check(sql: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CHECK_NAME}"))
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {CHECK_NAME} CHECK ({sql})"))
        return
    # SQLite copies the table to change a CHECK; rows 0115 left outside the БД.ТЕР card checks are copied as they are
    # (the same PRAGMA 0115 and 0131 used). The new check holds for every row.
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            batch.drop_constraint(CHECK_NAME, type_="check")
            batch.create_check_constraint(CHECK_NAME, sql)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))


def _move_overrides(bind, keys: set, source: str, target: str) -> int:
    entries = _entries()
    query = sa.select(entries.c.id, entries.c.normalized_key).where(entries.c.kind == "cable_kind", entries.c.value == source)
    ids = [row.id for row in bind.execute(query) if keys is None or row.normalized_key in keys]
    if ids:
        bind.execute(entries.update().where(entries.c.id.in_(ids)).values(value=target))
    return len(ids)


def upgrade() -> None:
    _set_check(CHECK_NEW)
    bind = op.get_bind()
    records = _records()
    before = _counts(bind)
    moved: list[str] = []
    keys: set = set()
    query = sa.select(records.c.id, records.c.fields, records.c.display_text).where(
        records.c.tab == TAB, records.c.cable_kind == "multi")
    for row in list(bind.execute(query)):
        fields = _fields(row.fields)
        mark = _mark(fields, row.display_text)
        if not is_ethernet(fields, mark):
            continue
        fresh, _entry = lan_row(fields, mark)
        bind.execute(records.update().where(records.c.id == row.id).values(cable_kind="lan", fields=fresh))
        moved.append(mark)
        keys.add(cable_key(mark))
    overrides = _move_overrides(bind, keys, "multi", "lan")
    after = _counts(bind)
    total = sum(before.values())
    if (sum(after.values()) != total or after.get("lan", 0) != len(moved)
            or after.get("multi", 0) != before.get("multi", 0) - len(moved)
            or any(after.get(kind, 0) != before.get(kind, 0) for kind in ("coax", "wire"))):
        raise RuntimeError(f"{revision}: сверка строк БД.ПРОВ не сошлась: до {before}, после {after}, перенесено {len(moved)}")
    print(f"{revision}: строк БД.ПРОВ {total} = {sum(after.values())}; до {before}; после {after}; "
          f"в ETHERNET перенесено {len(moved)}, ручных переносов по марке {overrides}.")
    if moved:
        print(f"{revision}: марки: " + " | ".join(sorted(moved)))


def downgrade() -> None:
    bind = op.get_bind()
    records = _records()
    before = _counts(bind)
    query = sa.select(records.c.id, records.c.fields).where(records.c.tab == TAB, records.c.cable_kind == "lan")
    rows = list(bind.execute(query))
    for row in rows:
        bind.execute(records.update().where(records.c.id == row.id)
                     .values(cable_kind="multi", fields=multi_row(_fields(row.fields))))
    _move_overrides(bind, None, "lan", "multi")
    after = _counts(bind)
    if sum(after.values()) != sum(before.values()) or after.get("lan", 0):
        raise RuntimeError(f"{revision}: откат: сверка строк БД.ПРОВ не сошлась: до {before}, после {after}")
    print(f"{revision}: откат — {len(rows)} строк ETHERNET возвращены в многожильные; после {after}.")
    _set_check(CHECK_OLD)
