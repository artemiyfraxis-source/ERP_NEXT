"""«Наконечники» и виды кабелей — отдельные базы (владелец 28–29.10.2026: «каждая вкладка в БД — это отдельная таблица
БД»; «наконечники и контакты хранятся в одной базе БД.ТЕР — так не должно быть»; БД.ПРОВ: «все виды проводов лежат в
одной таблице, разделённые по виду — я же просил как раз наоборот»).

До этой миграции наконечники лежали в базе «Терминалы» (tab = 'ter') с пометкой `ter_kind = 'lug'` (0155), а кабели
всех видов — в БД.ПРОВ (tab = 'prov') с пометкой `cable_kind` coax | wire | multi | lan (0131, 0150). Каждая база
«Базы данных» — своё значение `tab` (у него свои столбцы, свои «+ Столбец», счётчики, файл и поиск), поэтому:
- строки с `ter_kind = 'lug'` переходят в базу `lug`; кабели — `cable_kind` coax → `cab_coax`, multi → `cab_multi`,
  lan → `cab_lan`; монтажные провода (wire) остаются в `prov`. ID строк, их поля и число не меняются;
- столбцы `ter_kind` и `cable_kind` и их проверки `ck_tech_operation_records_ter_kind` / `_cable_kind` снимаются —
  вид строки теперь её база; новая проверка `ck_tech_operation_records_tab` — база строки одна из известных;
- проверки карточки контакта (0115: пол, диаметр изоляции, даташит) теперь держат и «Наконечники» (`tab NOT IN
  ('ter', 'lug')`); строки, которые их нарушали ещё до этой миграции, остаются как есть (PostgreSQL — `NOT VALID`,
  SQLite — копия таблицы при `PRAGMA ignore_check_constraints`), как в 0115;
- свои столбцы владельца (`reference_entries`, kind `custom_column`): ключ `prov:<вид>:<имя>` → `<база>:<имя>`
  (`prov:wire:` → `prov:`, `prov:coax:` → `cab_coax:`…), `ter:lug:<имя>` → `lug:<имя>`; общие наборы столбцов
  (`column_view`, ключ `<таблица>|<столбец>`) с таким ключом столбца — так же (имена таблиц экрана не трогаются);
- журнал «Склад → база данных» (`reference_stock_links.record_tab`): у записи, ушедшей в новую базу, — её база;
  проверка `ck_reference_stock_links_tab` расширена новыми базами.
Запись идёт через Core (не через сессию ORM) — хук etk_refresh не ставит запросов на пересборку ЭТК. Печатает сверку
по базам: строк до / после, совпадение ID, свои столбцы, связи «Склад → база».

DESTRUCTIVE = False: ни одна строка и ни одно значение не удаляются (снятые столбцы `ter_kind` / `cable_kind`
восстанавливаются из базы строки). Откат возвращает строки в 'ter' / 'prov' с их видом, столбцы и проверки, ключи
своих столбцов и журнал связей.
"""

from collections import Counter
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0165_split_lug_cable_tabs"
down_revision: str | None = "0164_stage_ready_qc_passed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "tech_operation_records"
LINKS = "reference_stock_links"
ENTRIES = "reference_entries"

# (old tab, its kind column, kind) -> the new base. Монтажные провода (prov / wire) stay in 'prov'.
MOVES = {
    ("ter", "ter_kind", "lug"): "lug",
    ("prov", "cable_kind", "coax"): "cab_coax",
    ("prov", "cable_kind", "multi"): "cab_multi",
    ("prov", "cable_kind", "lan"): "cab_lan",
}
# The kind every row of an old tab carries after the downgrade when it is no moved one.
STAYING = {"ter": ("ter_kind", "contact"), "prov": ("cable_kind", "wire")}
NEW_TABS = tuple(MOVES.values())

# Literal copies of app.modules.references.infrastructure on 30.10.2026 (a migration does not follow later changes).
TAB_CHECK_NAME = "ck_tech_operation_records_tab"
TAB_CHECK = ("tab IN ('ter', 'ob', 'op', 'lug', 'coax', 'prov', 'cab_coax', 'cab_multi', 'cab_lan', 'mat', 'kab_coax',"
             " 'kab_awg')")
KNOWN_TABS = ("ter", "ob", "op", "lug", "coax", "prov", "cab_coax", "cab_multi", "cab_lan", "mat", "kab_coax", "kab_awg")
CABLE_KIND_CHECK_NAME = "ck_tech_operation_records_cable_kind"
CABLE_KIND_CHECK = (  # 0150
    "(tab = 'prov' AND cable_kind IS NOT NULL AND cable_kind IN ('coax', 'wire', 'multi', 'lan'))"
    " OR (tab <> 'prov' AND cable_kind IS NULL)"
)
TER_KIND_CHECK_NAME = "ck_tech_operation_records_ter_kind"
TER_KIND_CHECK = (  # 0155
    "(tab = 'ter' AND ter_kind IS NOT NULL AND ter_kind IN ('contact', 'lug'))"
    " OR (tab <> 'ter' AND ter_kind IS NULL)"
)
LINK_CHECK_NAME = "ck_reference_stock_links_tab"
LINK_CHECK_OLD = "record_tab IN ('coax', 'mat', 'ob', 'op', 'prov', 'ter')"  # 0116
LINK_CHECK_NEW = "record_tab IN ('cab_coax', 'cab_lan', 'cab_multi', 'coax', 'lug', 'mat', 'ob', 'op', 'prov', 'ter')"
CARD_OTHER_OLD = "tab <> 'ter'"  # 0115
CARD_OTHER_NEW = "tab NOT IN ('ter', 'lug')"

# Custom column keys: the list prefix -> the base prefix.
KEY_PREFIXES = (
    ("prov:coax:", "cab_coax:"), ("prov:multi:", "cab_multi:"), ("prov:lan:", "cab_lan:"), ("prov:wire:", "prov:"),
    ("ter:lug:", "lug:"),
)


def _card_checks(dialect: str, other: str) -> dict[str, str]:
    """Literal copy of app.modules.references.infrastructure.terminal_card_checks (0115) with the «other tab» part."""
    def value(key: str) -> str:
        raw = f"json_extract(fields, '$.{key}')" if dialect == "sqlite" else f"(fields ->> '{key}')"
        return f"COALESCE({raw}, '')"

    def number(text: str) -> str:
        if dialect == "sqlite":
            return (f"({text} GLOB '[0-9]*' AND {text} NOT GLOB '*[^0-9.,]*' AND {text} NOT GLOB '*[.,]*[.,]*'"
                    f" AND {text} NOT GLOB '*[.,]')")
        return f"({text} ~ '^[0-9]+([.,][0-9]+)?$')"

    def cast(text: str) -> str:
        return f"CAST(REPLACE({text}, ',', '.') AS {'REAL' if dialect == 'sqlite' else 'NUMERIC'})"

    def positive(text: str) -> str:
        return f"CASE WHEN {text} = '' THEN TRUE WHEN {number(text)} THEN {cast(text)} > 0 ELSE FALSE END"

    gender, od_from, od_to, sheet = (value(key) for key in ("terGender", "terInsOdFrom", "terInsOdTo", "terDatasheet"))
    return {
        "ck_tech_operation_records_ter_gender": f"{other} OR {gender} IN ('', 'Male', 'Female')",
        "ck_tech_operation_records_ter_ins_od": (
            f"{other} OR (({positive(od_from)}) AND ({positive(od_to)}) AND (CASE WHEN {number(od_from)} AND"
            f" {number(od_to)} THEN {cast(od_from)} <= {cast(od_to)} ELSE TRUE END))"
        ),
        "ck_tech_operation_records_ter_datasheet": (
            f"{other} OR {sheet} = '' OR {sheet} LIKE 'http://%' OR {sheet} LIKE 'https://%'"
        ),
    }


def _dialect(bind) -> str:
    return "postgresql" if bind.dialect.name == "postgresql" else "sqlite"


def _records(*kinds: str) -> sa.Table:
    return sa.table(TABLE, sa.column("id", sa.Uuid()), sa.column("tab", sa.String()), sa.column("fields", sa.JSON()),
                    *(sa.column(kind, sa.String()) for kind in kinds))


def _links() -> sa.Table:
    return sa.table(LINKS, sa.column("id", sa.Uuid()), sa.column("record_id", sa.Uuid()),
                    sa.column("record_tab", sa.String()))


def _entries() -> sa.Table:
    return sa.table(ENTRIES, sa.column("id", sa.Uuid()), sa.column("kind", sa.String()), sa.column("key", sa.String()),
                    sa.column("normalized_key", sa.String()))


def _folded(key: str) -> str:
    return " ".join(str(key).casefold().split())


def _ids(bind, records: sa.Table, *where) -> set:
    return set(bind.execute(sa.select(records.c.id).where(*where)).scalars())


def _tab_counts(bind) -> dict[str, int]:
    records = _records()
    return dict(bind.execute(sa.select(records.c.tab, sa.func.count()).group_by(records.c.tab)).all())


def _link_counts(bind) -> dict[str, int]:
    links = _links()
    return dict(bind.execute(sa.select(links.c.record_tab, sa.func.count()).group_by(links.c.record_tab)).all())


def _custom_count(bind) -> int:
    entries = _entries()
    return bind.execute(sa.select(sa.func.count()).select_from(entries).where(entries.c.kind == "custom_column")).scalar_one()


def _rekey(bind, pairs: Sequence[tuple[str, str]]) -> tuple[int, int]:
    """Custom column keys and column-view column keys: a key starting with an old prefix gets the new one. Returns
    (custom columns rewritten, column-view entries rewritten); a key that would collide is refused loudly."""
    entries = _entries()
    rows = list(bind.execute(sa.select(entries.c.id, entries.c.kind, entries.c.key)
                             .where(entries.c.kind.in_(("custom_column", "column_view")))))
    taken = {(row.kind, _folded(row.key)) for row in rows}
    counts = Counter()
    for row in rows:
        if row.kind == "custom_column":
            view, column = "", row.key
        else:
            view, _, column = row.key.partition("|")
            view += "|"
        for old, new in pairs:
            if column.startswith(old):
                key = view + new + column[len(old):]
                if (row.kind, _folded(key)) in taken:
                    raise RuntimeError(f"{revision}: ключ «{key}» ({row.kind}) уже есть — «{row.key}» не переименовать")
                taken.discard((row.kind, _folded(row.key)))
                taken.add((row.kind, _folded(key)))
                bind.execute(entries.update().where(entries.c.id == row.id)
                             .values(key=key, normalized_key=_folded(key)))
                counts[row.kind] += 1
                break
    return counts["custom_column"], counts["column_view"]


def _set_link_check(sql: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE {LINKS} DROP CONSTRAINT IF EXISTS {LINK_CHECK_NAME}"))
        op.execute(sa.text(f"ALTER TABLE {LINKS} ADD CONSTRAINT {LINK_CHECK_NAME} CHECK ({sql})"))
        return
    with op.batch_alter_table(LINKS, recreate="always") as batch:
        batch.drop_constraint(LINK_CHECK_NAME, type_="check")
        batch.create_check_constraint(LINK_CHECK_NAME, sql)


def _relink(bind, mapping: dict[str, str], *, upgrade: bool) -> int:
    """`record_tab` of the journal rows of a moved record: its new base (upgrade) / its old tab (downgrade)."""
    links, records = _links(), _records()
    changed = 0
    if upgrade:
        query = (sa.select(links.c.id, records.c.tab).select_from(links.join(records, links.c.record_id == records.c.id))
                 .where(records.c.tab.in_(NEW_TABS), links.c.record_tab != records.c.tab))
        for link_id, tab in list(bind.execute(query)):
            bind.execute(links.update().where(links.c.id == link_id).values(record_tab=tab))
            changed += 1
        return changed
    for new, old in mapping.items():
        changed += bind.execute(links.update().where(links.c.record_tab == new).values(record_tab=old)).rowcount or 0
    return changed


def _outside(bind, checks: dict[str, str]) -> int:
    where = " OR ".join(f"NOT ({sql})" for sql in checks.values())
    return bind.execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE {where}")).scalar_one()


def _reconcile(before: dict, after: dict, title: str) -> None:
    lines = [f"{revision}: {title} — сверка по базам (строк до -> после):"]
    for tab in sorted(set(before) | set(after)):
        lines.append(f"  {tab}: {before.get(tab, 0)} -> {after.get(tab, 0)}")
    print("\n".join(lines))


def upgrade() -> None:
    bind = op.get_bind()
    dialect = _dialect(bind)
    records = _records("ter_kind", "cable_kind")
    unknown = sorted(set(_tab_counts(bind)) - set(KNOWN_TABS))
    if unknown:
        raise RuntimeError(f"{revision}: в {TABLE} есть строки неизвестных баз: {unknown}")
    total = bind.execute(sa.select(sa.func.count()).select_from(records)).scalar_one()
    tabs_before = _tab_counts(bind)
    links_before, customs_before = _link_counts(bind), _custom_count(bind)
    moving = {target: _ids(bind, records, records.c.tab == tab, records.c[column] == kind)
              for (tab, column, kind), target in MOVES.items()}
    staying = {tab: _ids(bind, records, records.c.tab == tab, records.c[column] == kind)
               for tab, (column, kind) in STAYING.items()}

    # 1. Each moved row to its base; its kind cleared in the same write, so the 0131 / 0150 / 0155 checks hold until
    #    they go (a row of another tab has no kind).
    for (tab, column, kind), target in MOVES.items():
        bind.execute(records.update().where(records.c.tab == tab, records.c[column] == kind)
                     .values(tab=target, **{column: None}))
    # 2. The journal of «Склад -> база данных»: the new bases allowed, a moved record's rows follow it.
    _set_link_check(LINK_CHECK_NEW)
    relinked = _relink(bind, {}, upgrade=True)
    # 3. The owner's columns and the column-view keys: list prefixes -> base prefixes.
    customs, views = _rekey(bind, KEY_PREFIXES)
    # 4. The kind columns and their checks go; the card checks hold «Наконечники» too; the tab check comes.
    old_cards, new_cards = _card_checks(dialect, CARD_OTHER_OLD), _card_checks(dialect, CARD_OTHER_NEW)
    outside = _outside(bind, new_cards)
    if dialect == "postgresql":
        for name in (CABLE_KIND_CHECK_NAME, TER_KIND_CHECK_NAME, *old_cards):
            op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name}"))
        op.drop_column(TABLE, "cable_kind")
        op.drop_column(TABLE, "ter_kind")
        for name, sql in new_cards.items():
            op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {name} CHECK ({sql})" + (" NOT VALID" if outside else "")))
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {TAB_CHECK_NAME} CHECK ({TAB_CHECK})"))
    else:
        def change(batch) -> None:
            for name in (CABLE_KIND_CHECK_NAME, TER_KIND_CHECK_NAME, *old_cards):
                batch.drop_constraint(name, type_="check")
            batch.drop_column("cable_kind")
            batch.drop_column("ter_kind")
            for name, sql in new_cards.items():
                batch.create_check_constraint(name, sql)
            batch.create_check_constraint(TAB_CHECK_NAME, TAB_CHECK)

        _sqlite_recreate(change)

    # 5. Reconciliation: every row and id where it should be, the links and the owner's columns all kept.
    plain = _records()
    tabs_after = _tab_counts(bind)
    total_after = bind.execute(sa.select(sa.func.count()).select_from(plain)).scalar_one()
    wrong = [target for target, ids in moving.items() if _ids(bind, plain, plain.c.tab == target) != ids]
    wrong += [tab for tab, ids in staying.items() if _ids(bind, plain, plain.c.tab == tab) != ids]
    links_after, customs_after = _link_counts(bind), _custom_count(bind)
    if (total_after != total or wrong or sum(links_after.values()) != sum(links_before.values())
            or customs_after != customs_before):
        raise RuntimeError(f"{revision}: сверка не сошлась: строк {total} -> {total_after}, базы с другими ID {wrong}, "
                           f"связи {links_before} -> {links_after}, свои столбцы {customs_before} -> {customs_after}")
    _reconcile(tabs_before, tabs_after, "перенос в отдельные базы")
    print(f"{revision}: строк всего {total} = {total_after}; ID на месте: "
          + ", ".join(f"{target} {len(ids)}" for target, ids in moving.items())
          + ", " + ", ".join(f"{tab} {len(ids)}" for tab, ids in staying.items()) + ".")
    print(f"{revision}: связи «Склад -> база» {sum(links_before.values())} = {sum(links_after.values())} "
          f"(перенесено в новую базу {relinked}; по базам {links_after}); свои столбцы {customs_before} = "
          f"{customs_after} (ключей переименовано {customs}); общих наборов столбцов с ключом списка — {views}; "
          f"строк вне проверок карточки (оставлены как есть) — {outside}.")


def _sqlite_recreate(change) -> None:
    """SQLite copies the table to change its CHECKs and columns; rows 0115 left outside the card checks are copied as
    they are (the PRAGMA 0115, 0131, 0150 and 0155 used)."""
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            change(batch)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))


def downgrade() -> None:
    bind = op.get_bind()
    dialect = _dialect(bind)
    plain = _records()
    total = bind.execute(sa.select(sa.func.count()).select_from(plain)).scalar_one()
    tabs_before = _tab_counts(bind)
    links_before, customs_before = _link_counts(bind), _custom_count(bind)
    ids_before = {tab: _ids(bind, plain, plain.c.tab == tab) for tab in (*NEW_TABS, *STAYING)}

    back = {target: tab for (tab, _column, _kind), target in MOVES.items()}
    _relink(bind, back, upgrade=False)
    _set_link_check(LINK_CHECK_OLD)
    customs, _views = _rekey(bind, tuple((new, old) for old, new in KEY_PREFIXES))

    old_cards, new_cards = _card_checks(dialect, CARD_OTHER_OLD), _card_checks(dialect, CARD_OTHER_NEW)
    if dialect == "postgresql":  # the checks of 0165 go first: a row outside the card checks is written back too
        for name in (TAB_CHECK_NAME, *new_cards):
            op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name}"))
    op.add_column(TABLE, sa.Column("cable_kind", sa.String(8), nullable=True))
    op.add_column(TABLE, sa.Column("ter_kind", sa.String(8), nullable=True))
    records = _records("ter_kind", "cable_kind")
    if dialect == "sqlite":  # SQLite keeps its checks until the copy below: a row 0115 left outside them is written too
        op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        for tab, (column, kind) in STAYING.items():
            bind.execute(records.update().where(records.c.tab == tab).values(**{column: kind}))
        for (tab, column, kind), target in MOVES.items():
            bind.execute(records.update().where(records.c.tab == target).values(tab=tab, **{column: kind}))
    finally:
        if dialect == "sqlite":
            op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))
    outside = _outside(bind, old_cards)
    if dialect == "postgresql":
        for name, sql in old_cards.items():
            op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {name} CHECK ({sql})" + (" NOT VALID" if outside else "")))
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {CABLE_KIND_CHECK_NAME} CHECK ({CABLE_KIND_CHECK})"))
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {TER_KIND_CHECK_NAME} CHECK ({TER_KIND_CHECK})"))
    else:
        def change(batch) -> None:
            batch.drop_constraint(TAB_CHECK_NAME, type_="check")
            for name in new_cards:
                batch.drop_constraint(name, type_="check")
            for name, sql in old_cards.items():
                batch.create_check_constraint(name, sql)
            batch.create_check_constraint(CABLE_KIND_CHECK_NAME, CABLE_KIND_CHECK)
            batch.create_check_constraint(TER_KIND_CHECK_NAME, TER_KIND_CHECK)

        _sqlite_recreate(change)

    tabs_after = _tab_counts(bind)
    total_after = bind.execute(sa.select(sa.func.count()).select_from(records)).scalar_one()
    wrong = [tab for (tab, column, kind), target in MOVES.items()
             if _ids(bind, records, records.c.tab == tab, records.c[column] == kind) != ids_before[target]]
    wrong += [tab for tab, (column, kind) in STAYING.items()
              if _ids(bind, records, records.c.tab == tab, records.c[column] == kind) != ids_before[tab]]
    links_after, customs_after = _link_counts(bind), _custom_count(bind)
    if (total_after != total or wrong or set(tabs_after) & set(NEW_TABS)
            or sum(links_after.values()) != sum(links_before.values()) or customs_after != customs_before):
        raise RuntimeError(f"{revision}: откат: сверка не сошлась: строк {total} -> {total_after}, виды с другими ID "
                           f"{wrong}, связи {links_before} -> {links_after}, свои столбцы {customs_before} -> {customs_after}")
    _reconcile(tabs_before, tabs_after, "откат")
    print(f"{revision}: откат — строк {total_after}, ключей своих столбцов возвращено {customs}, связи "
          f"{sum(links_after.values())}.")

