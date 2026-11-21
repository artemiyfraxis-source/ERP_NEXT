"""«Группа на экране» (owner 21.11.2026): the screen «Производство → Оборудование» groups machines by purpose, not by
«С управлением / Без управления»; the group is set per equipment MODEL in БД.ОБ.

`equipment_models.screen_group` — VARCHAR(16) NULL, CHECK (NULL or one of the nine codes, the screen's order: cut
«Резка», cut_crimp «Резка и опрессовка», strip «Зачистка», crimp «Опрессовка», solder «Пайка и усадка», marking
«Маркировка», analyzers «Анализаторы цепей», printers «Принтеры и МФУ», other «Прочее»). NULL — the keyword rule of
`production.machines.screen_group_of` decides on read.

Backfill: only models with units in `equipment_machines` (the tab «Оборудование»); every other model stays NULL. Each
unit is classified by a FROZEN copy of the keyword rule as of this revision (no app imports — the live rule may change
later); a model whose units disagree takes the earliest rule (RULE_ORDER). One line per model is printed for the
rehearsal log: inventory numbers → group.

One plain ALTER on both dialects (a nullable column without a default: metadata-only on PostgreSQL), then UPDATEs by
primary key. Not destructive: the downgrade drops only the new column (its CHECK goes with it)."""

import json
import re

from alembic import op
import sqlalchemy as sa

revision = "0216_equipment_screen_group"
down_revision = "0213_equipment_setup_by"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

CODES = ("cut", "cut_crimp", "strip", "crimp", "solder", "marking", "analyzers", "printers", "other")
# Frozen copy of the keyword rule (production/machines.py `screen_group_of`, 21.11.2026). First match wins.
RULE_ORDER = ("printers", "analyzers", "cut_crimp", "cut", "strip", "crimp", "solder", "marking", "other")
NAME_KEYS = ("Наменование", "Наименование")
ARTICLE_KEY = "Артикул"
MAKER_KEY = "Производитель"
OPERATIONS_KEY = "Операции"
INV_KEY = "Инв. номер"
GROUP_STEMS = (
    ("cut", ("резк",)),
    ("strip", ("разделк", "зачист", "изоляци", "свивк", "скрутк")),
    ("crimp", ("опрессовк", "обжим")),
    ("solder", ("пайк", "лужен", "усадк")),
    ("marking", ("маркир", "маркер", "этикет")),
)
PRINTER_WORDS = re.compile(r"принтер|\bмфу\b")
PRINTER_MAKERS = re.compile(r"supvan|bixolon|godex|\btsc\b|pantum")
NOT_PRINTER = re.compile(r"3d|3д")
ANALYSER_WORDS = re.compile(r"анализатор\w*\s+цеп|\bвац\b|\bvna\b|стоячей\s+волны")
CRIMP_STEMS = ("опрессовк", "обжим")


def _field(fields: dict, *keys: str) -> str:
    for key in keys:
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return ""


def group_of(operations: list[str], name: str, article: str, maker: str) -> str:
    label = " ".join((name, article, maker)).casefold()
    if not NOT_PRINTER.search(label) and (PRINTER_WORDS.search(label) or PRINTER_MAKERS.search(maker.casefold())):
        return "printers"
    operations_text = ", ".join(operations).casefold()
    if ANALYSER_WORDS.search(" ".join((operations_text, name, article)).casefold()):
        return "analyzers"
    for text in (operations_text, name.casefold()):
        if "резк" in text and any(stem in text for stem in CRIMP_STEMS):
            return "cut_crimp"
        for group, stems in GROUP_STEMS:
            if any(stem in text for stem in stems):
                return group
    return "other"


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE equipment_models ADD COLUMN screen_group VARCHAR(16) "
        "CONSTRAINT ck_equipment_models_screen_group CHECK (screen_group IS NULL OR screen_group IN ("
        + ", ".join(f"'{code}'" for code in CODES) + "))"))
    connection = op.get_bind()
    units: dict[object, list[tuple[str, str]]] = {}  # keyed by the id as the driver returns it (UUID / hex text)
    for model_id, display_text, fields, article, maker in connection.execute(sa.text(
            "SELECT m.equipment_model_id, m.display_text, m.fields, em.article, em.maker FROM equipment_machines m "
            "JOIN equipment_models em ON em.id = m.equipment_model_id")):
        fields = json.loads(fields) if isinstance(fields, str) else dict(fields or {})
        operations = [item.strip() for item in _field(fields, OPERATIONS_KEY).split(",") if item.strip()]
        name = _field(fields, *NAME_KEYS) or str(display_text or "")
        group = group_of(operations, name, _field(fields, ARTICLE_KEY) or str(article or ""),
                         _field(fields, MAKER_KEY) or str(maker or ""))
        units.setdefault(model_id, []).append((_field(fields, INV_KEY), group))
    for model_id, found in sorted(units.items(), key=lambda item: sorted(number for number, _ in item[1])):
        group = min((group for _, group in found), key=RULE_ORDER.index)
        connection.execute(sa.text("UPDATE equipment_models SET screen_group = :group WHERE id = :id"),
                           {"group": group, "id": model_id})
        numbers = ", ".join(sorted(number or "—" for number, _ in found))
        print(f"0216 screen_group: {numbers} → {group}")


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE equipment_models DROP COLUMN screen_group"))
