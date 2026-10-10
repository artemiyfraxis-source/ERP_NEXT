"""Ключ № проекта рядом с текстом проекта — трек и «Снабжение» читают один проект по индексу (аудит U06).

Раньше трек проекта читал до 100 000 записей каждого модуля, «Снабжение» — первые 10 000, и фильтровал проект в
Python: старые записи за границей пропадали, а чтение росло со всей ERP. Теперь у каждой таблицы, где проект записан
текстом, есть ключ для точного сравнения (`app.shared.project_number`: пробелы прочь, верхний регистр, латинские
двойники → кириллица, «2000К» ≡ «2000К.1»; «719К.2» ≠ «719К.21») и индекс по нему:

- `production_tasks.project_key`, `finished_goods.project_key`, `shipment_lines.project_key`,
  `techcard_projects.project_key` — из `project`;
- `procurement_lines.project_key` — из `project_reference` («» — без проекта);
- `requests.project_key` / `purpose_project_key` / `designation_project_key` — из № ПРОЕКТА, ЦЕЛИ и ОБОЗНАЧЕНИЯ
  «проект.NNN» (заявка проекта — по любому из трёх, как `request_matches_project`);
- `receiving_lines.purpose_project_key` / `goal_project_key` — из НАЗНАЧЕНИЯ и ЦЕЛИ, плюс обычный индекс
  `procurement_line_id` (уникальный индекс там частичный — только живые строки).

`fulfillment_requests` не трогается: ключ туда заводит ветка «одна заявка на КБ» (#254); до неё запросы
комплектации отбираются по цифрам номера в НАЗНАЧЕНИИ.

Колонки nullable: NULL — строка вставлена мимо ORM (ORM ставит ключ сам при записи текста проекта); такие строки
чтение берёт и сверяет в Python, так что ключ никогда не прячет запись. Здесь все существующие строки получают ключ.
Не разрушающая: строки и их значения не меняются, только добавляются колонки и индексы; откат их снимает.
"""

from __future__ import annotations

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0098_project_keys"
down_revision: str | None = "0097_fulfillment_kit_request_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

# Нормализация на момент миграции (копия `app.shared.project_number`: миграция не зависит от будущего кода).
_HOMOGLYPHS = str.maketrans("KCEOPAHTMBXY", "КСЕОРАНТМВХУ")


def _number(value: object) -> str:
    normalized = re.sub(r"\s+", "", str(value or "").strip().upper()).translate(_HOMOGLYPHS)
    if re.fullmatch(r"[1-9]\d*(?:К[А-Я]?|МФ)(?:\.[А-Я]{1,3})?\.?", normalized):
        return normalized.rstrip(".") + ".1"
    return normalized


def _from_purpose(value: object) -> str:
    purpose = re.sub(r"[\s·,;:\-—]*КБ\s*\d+[\s\S]*$", "", str(value or "").strip(), flags=re.I)
    token = re.split(r"[\s·,;]+", purpose)[0] if purpose else ""
    token = re.sub(r"\.\d{3,}$", "", token)
    return _number(token) if token else ""


def _reference(value: object) -> str:
    return _number(value) if value else ""


# (таблица, [(колонка ключа, колонка текста, функция)]); ключ — String(255): верхний регистр может удлинить текст.
_KEYS = (
    ("production_tasks", (("project_key", "project", _number),)),
    ("finished_goods", (("project_key", "project", _number),)),
    ("shipment_lines", (("project_key", "project", _number),)),
    ("techcard_projects", (("project_key", "project", _number),)),
    ("procurement_lines", (("project_key", "project_reference", _reference),)),
    ("requests", (
        ("project_key", "project_reference", _reference),
        ("purpose_project_key", "purpose", _from_purpose),
        ("designation_project_key", "designation", _from_purpose),
    )),
    ("receiving_lines", (
        ("purpose_project_key", "purpose", _from_purpose),
        ("goal_project_key", "goal", _from_purpose),
    )),
)
_RECEIVING_LINE_INDEX = "ix_receiving_lines_procurement_line_id"
_BATCH = 1000


def _backfill(connection, table: str, keys) -> None:
    sources = ", ".join(source for _key, source, _function in keys)
    rows = connection.execute(sa.text(f"SELECT id, {sources} FROM {table}")).fetchall()
    assignments = ", ".join(f"{key} = :{key}" for key, _source, _function in keys)
    update = sa.text(f"UPDATE {table} SET {assignments} WHERE id = :id")
    params = [
        {"id": row[0], **{key: function(row[index + 1]) for index, (key, _source, function) in enumerate(keys)}}
        for row in rows
    ]
    for start in range(0, len(params), _BATCH):
        connection.execute(update, params[start:start + _BATCH])


def upgrade() -> None:
    connection = op.get_bind()
    for table, keys in _KEYS:
        for key, _source, _function in keys:
            op.add_column(table, sa.Column(key, sa.String(255), nullable=True))
        _backfill(connection, table, keys)
        for key, _source, _function in keys:
            op.create_index(f"ix_{table}_{key}", table, [key])
    op.create_index(_RECEIVING_LINE_INDEX, "receiving_lines", ["procurement_line_id"])


def downgrade() -> None:
    # SQLite: родной DROP COLUMN (3.35+) — пересборка таблицы (batch) потеряла бы частичные индексы receiving_lines.
    sqlite = op.get_bind().dialect.name == "sqlite"
    op.drop_index(_RECEIVING_LINE_INDEX, table_name="receiving_lines")
    for table, keys in reversed(_KEYS):
        for key, _source, _function in keys:
            op.drop_index(f"ix_{table}_{key}", table_name=table)
            if sqlite:
                op.execute(f"ALTER TABLE {table} DROP COLUMN {key}")
            else:
                op.drop_column(table, key)
