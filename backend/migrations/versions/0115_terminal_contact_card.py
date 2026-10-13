"""Карточка контакта БД.ТЕР по протоколу v5.2: проверки базы (владелец 12–13.10.2026).

Карточка контакта (пол, артикул BAG, обжим и диаметр изоляции, материал / покрытие, ток, даташит) хранится в `fields`
записи «Терминалы» (`tech_operation_records`, tab = 'ter'), как остальные поля БД.ТЕР. Миграция ставит на неё проверки
базы, чтобы плохое значение не записалось ни загрузкой, ни правкой, ни скриптом:
- `ck_tech_operation_records_ter_gender` — пол контакта `terGender`: Male, Female или пусто;
- `ck_tech_operation_records_ter_ins_od` — диаметр изоляции от / до (`terInsOdFrom` / `terInsOdTo`): пусто или число
  больше нуля (точка или запятая), «от» ≤ «до»;
- `ck_tech_operation_records_ter_datasheet` — даташит `terDatasheet`: пусто или ссылка http(s)://.
Пустое значение разрешено всегда (null-spec протокола: чего нет в спецификации производителя, то пусто).

Перед проверками записанный пол, который не Male / Female (прежняя загрузка оставляла непонятный «Тип контакта» как
есть), переводится по тем же словам, что `technology.contact_gender`: «Гнездо», «Socket» → Female, «Штырь», «Pin» →
Male, иначе пусто — сам «Тип контакта» в записи остаётся как был. Строки, которые и после этого нарушают проверки
(диаметр, даташит), остаются как есть и печатаются в лог: на PostgreSQL проверки ставятся `NOT VALID`, на SQLite таблица
копируется при `PRAGMA ignore_check_constraints`. Новые и изменённые строки проверяются. Повторный запуск ничего не
меняет. «Обязательных» полей протокол не добавляет: в базе есть строки без артикула REEL (корпуса, наконечники), их не
трогаем.

DESTRUCTIVE = False: ничего не удаляется. Downgrade снимает проверки (переведённый пол остаётся).
"""

import json
import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0115_terminal_contact_card"
down_revision: str | None = "0112_harness_route_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "tech_operation_records"
TAB = "ter"
GENDERS = ("Male", "Female")
# Literal copies of app.modules.references.technology (a migration never imports app code that moves on).
_FEMALE = re.compile(r"^(female|socket|receptacle|гнезд|мама)|\(socket\)|\(гнездо\)")
_MALE = re.compile(r"^(male|pin|tab|plug|штыр|папа)|\(pin\)|\(tab\)|\(штырь\)")


def _gender(value: object) -> str:
    folded = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    if _FEMALE.search(folded):
        return "Female"
    if _MALE.search(folded):
        return "Male"
    return ""


# Literal copy of app.modules.references.infrastructure.terminal_card_checks.
def _checks(dialect: str) -> dict[str, str]:
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
        "ck_tech_operation_records_ter_gender": f"tab <> 'ter' OR {gender} IN ('', 'Male', 'Female')",
        "ck_tech_operation_records_ter_ins_od": (
            f"tab <> 'ter' OR (({positive(od_from)}) AND ({positive(od_to)}) AND (CASE WHEN {number(od_from)} AND"
            f" {number(od_to)} THEN {cast(od_from)} <= {cast(od_to)} ELSE TRUE END))"
        ),
        "ck_tech_operation_records_ter_datasheet": (
            f"tab <> 'ter' OR {sheet} = '' OR {sheet} LIKE 'http://%' OR {sheet} LIKE 'https://%'"
        ),
    }


def _records() -> sa.Table:
    return sa.table(TABLE, sa.column("id", sa.Uuid()), sa.column("tab", sa.String()), sa.column("fields", sa.JSON()))


def _normalize_genders(bind) -> int:
    records = _records()
    changed = 0
    for row in bind.execute(sa.select(records.c.id, records.c.fields).where(records.c.tab == TAB)):
        fields = json.loads(row.fields or "{}") if isinstance(row.fields, str) else row.fields
        if not isinstance(fields, dict) or "terGender" not in fields:
            continue
        stored = str(fields["terGender"] or "")
        if stored in ("", *GENDERS):
            continue
        bind.execute(records.update().where(records.c.id == row.id).values(fields={**fields, "terGender": _gender(stored)}))
        changed += 1
    return changed


def _outside(bind, checks: dict[str, str]) -> list[str]:
    """Rows the checks would refuse after the gender is brought over: they stay as written, unchecked."""
    where = " OR ".join(f"NOT ({sql})" for sql in checks.values())
    return [str(item) for item in bind.execute(sa.text(f"SELECT display_text FROM {TABLE} WHERE {where}")).scalars()]


def upgrade() -> None:
    bind = op.get_bind()
    dialect = "postgresql" if bind.dialect.name == "postgresql" else "sqlite"
    checks = _checks(dialect)
    changed = _normalize_genders(bind)
    if changed:
        print(f"{revision}: {TABLE} — пол контакта переведён в Male / Female / пусто у {changed} записей «Терминалы».")
    outside = _outside(bind, checks)
    if outside:
        print(f"{revision}: {TABLE} — {len(outside)} записей «Терминалы» нарушают проверки карточки и оставлены как "
              f"есть (новые правки проверяются): " + "; ".join(outside[:20]))
    if dialect == "postgresql":
        for name, sql in checks.items():
            op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name}"))
            op.execute(sa.text(f"ALTER TABLE {TABLE} ADD CONSTRAINT {name} CHECK ({sql})" + (" NOT VALID" if outside else "")))
        return
    op.execute(sa.text("PRAGMA ignore_check_constraints = ON"))
    try:
        with op.batch_alter_table(TABLE, recreate="always") as batch:
            for name, sql in checks.items():
                batch.create_check_constraint(name, sql)
    finally:
        op.execute(sa.text("PRAGMA ignore_check_constraints = OFF"))


def downgrade() -> None:
    bind = op.get_bind()
    names = tuple(_checks("sqlite"))
    if bind.dialect.name == "postgresql":
        for name in names:
            op.execute(sa.text(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {name}"))
        return
    present = {check["name"] for check in sa.inspect(bind).get_check_constraints(TABLE)}
    with op.batch_alter_table(TABLE, recreate="always") as batch:
        for name in names:
            if name in present:
                batch.drop_constraint(name, type_="check")
