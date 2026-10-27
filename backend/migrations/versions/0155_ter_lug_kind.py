"""БД.ТЕР: «Наконечники» — свой список (владелец 26.10.2026: «тип наконечник, а не терминал… и в базе их бы тоже отдельно
сделать, в другую БД — наконечники»).

Наконечники (НШВ / НШВИ, НКИ / НК, НВИ / НВ, НШИ / ПМ, РПИ / РППИ и их импортные двойники FDD, FDFD, FDFN, FLVDDF,
«Female / Quick Disconnect», FASTON, трубчатые ТМ / ТМЛ / ТА / ТАМ) лежали в «Терминалах» вместе с обжимными контактами
разъёмов. Строки остаются в таблице `tech_operation_records` (tab = 'ter') — все читатели (поиск обжима генератора ЭТК,
каталог жгута, наименования склада, артикул партии, связи «Склад → база») находят наконечник как раньше. Миграция:
- добавляет столбец `ter_kind` (contact | lug) и проверку `ck_tech_operation_records_ter_kind`: у строки БД.ТЕР ровно
  один список, у строк других вкладок — никакого;
- заполняет список каждой строки БД.ТЕР правилом `lugs.ter_kind_of` (буквальная копия на 26.10.2026 ниже): первое слово
  наименования (Product Name), серии или артикула (REEL / BAG) — семейство наконечника, либо наименование / серия
  называют disconnect, FASTON или «наконечник». Гильзы «ГС» / «ГМЛ» — не наконечники, остаются в «Терминалах».
Число строк, их ID, поля и ссылки `reference_stock_links` не меняются (сверка: всего до = всего после = contact + lug).
Запись идёт через Core (не через сессию ORM) — хук etk_refresh не ставит запросов на пересборку ЭТК. Печатает сводку:
сколько строк в каждом списке и какие наименования ушли в «Наконечники».

DESTRUCTIVE = False: ничего не удаляется. Откат снимает проверку и столбец — строки снова один список «Терминалы».
"""

import json
import re
from collections import Counter
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0155_ter_lug_kind"
down_revision: str | None = "0154_user_ui_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "tech_operation_records"
TAB = "ter"
CHECK_NAME = "ck_tech_operation_records_ter_kind"
# Literal copy of app.modules.references.infrastructure.TER_KIND_CHECK.
CHECK_SQL = (
    "(tab = 'ter' AND ter_kind IS NOT NULL AND ter_kind IN ('contact', 'lug'))"
    " OR (tab <> 'ter' AND ter_kind IS NULL)"
)
# Literal copy of app.modules.references.lugs (LUG_FAMILY, LUG_WORDS, the fields read) on 26.10.2026 — a migration does
# not follow later changes of the rule.
LUG_FAMILY = re.compile(
    r"^(?:ншви|ншв|нши|ншп|ншк|нки|нк|нви|нв|пм|рппи|рпи|тмл|там|тм|та"
    r"|fdd|mdd|fdfd|mdfd|fdfn|mdfn|flvddf|flvdd)(?![a-zа-яё])",
    re.IGNORECASE,
)
LUG_WORDS = re.compile(r"disconnect|faston|наконечник", re.IGNORECASE)
PRODUCT = ("terComponent", "product name")
SERIES = ("terSeries", "series", "серия")
ARTICLES = ("terArticle", "terArticleBag", "артикул контакта (reel)", "артикул контакта (bag)", "артикул контакта")


def _folded(name) -> str:
    return " ".join(str(name or "").casefold().split())


def _values(fields: dict, names: tuple) -> list:
    wanted = {_folded(name) for name in names}
    return [" ".join(str(value or "").split()) for key, value in fields.items()
            if _folded(key) in wanted and str(value or "").strip()]


def is_lug(fields: dict) -> bool:
    names = _values(fields, PRODUCT) + _values(fields, SERIES)
    if any(LUG_WORDS.search(text) for text in names):
        return True
    return any(LUG_FAMILY.match(text) for text in names + _values(fields, ARTICLES))


def ter_kind_of(fields: dict) -> str:
    return "lug" if is_lug(fields) else "contact"


def _records() -> sa.Table:
    return sa.table(
        TABLE, sa.column("id", sa.Uuid()), sa.column("tab", sa.String()), sa.column("display_text", sa.String()),
        sa.column("fields", sa.JSON()), sa.column("ter_kind", sa.String()),
    )


def _fields(raw) -> dict:
    value = json.loads(raw or "{}") if isinstance(raw, str) else raw
    return dict(value) if isinstance(value, dict) else {}


def _product(fields: dict, display: str) -> str:
    found = _values(fields, PRODUCT) or _values(fields, SERIES)
    return found[0] if found else str(display or "").strip()


def _recreate(change) -> None:
    """SQLite copies the table to change its CHECKs; rows 0115 left outside the БД.ТЕР card checks are copied as they are
    (the same PRAGMA 0115, 0131 and 0150 used). The new check holds for every row."""
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            change(batch)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("ter_kind", sa.String(8), nullable=True))
    bind = op.get_bind()
    records = _records()
    total_before = bind.execute(sa.select(sa.func.count()).select_from(records)).scalar_one()
    rows = list(bind.execute(sa.select(records.c.id, records.c.fields, records.c.display_text).where(records.c.tab == TAB)))
    kinds = Counter()
    lugs = Counter()
    for row in rows:
        fields = _fields(row.fields)
        kind = ter_kind_of(fields)
        kinds[kind] += 1
        if kind == "lug":
            lugs[_product(fields, row.display_text)] += 1
        bind.execute(records.update().where(records.c.id == row.id).values(ter_kind=kind))
    total_after = bind.execute(sa.select(sa.func.count()).select_from(records)).scalar_one()
    stored = dict(bind.execute(sa.select(records.c.ter_kind, sa.func.count()).where(records.c.tab == TAB)
                               .group_by(records.c.ter_kind)).all())
    if (total_after != total_before or sum(kinds.values()) != len(rows) or stored.get(None)
            or stored.get("lug", 0) != kinds["lug"] or stored.get("contact", 0) != kinds["contact"]):
        raise RuntimeError(f"{revision}: сверка строк не сошлась: всего до {total_before}, после {total_after}; "
                           f"БД.ТЕР {len(rows)}, по правилу {dict(kinds)}, записано {stored}")
    print(f"{revision}: строк всего {total_before} = {total_after}; БД.ТЕР {len(rows)}: "
          f"«Терминалы» {kinds['contact']}, «Наконечники» {kinds['lug']}.")
    if lugs:
        print(f"{revision}: в «Наконечники»: " + ", ".join(f"{name} {count}" for name, count in sorted(lugs.items())))
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {CHECK_NAME} CHECK ({CHECK_SQL})"))
        return
    _recreate(lambda batch: batch.create_check_constraint(CHECK_NAME, CHECK_SQL))


def downgrade() -> None:
    bind = op.get_bind()
    total = bind.execute(sa.select(sa.func.count()).select_from(_records())).scalar_one()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CHECK_NAME}"))
        op.drop_column(TABLE, "ter_kind")
    else:
        def drop(batch) -> None:
            batch.drop_constraint(CHECK_NAME, type_="check")
            batch.drop_column("ter_kind")

        _recreate(drop)
    after = bind.execute(sa.select(sa.func.count()).select_from(sa.table(TABLE))).scalar_one()
    if after != total:
        raise RuntimeError(f"{revision}: откат: строк было {total}, стало {after}")
    print(f"{revision}: откат — список «Наконечники» снят, строк {after}.")
