"""Дозаполнение по исследованиям 09.11; карта сверена с выгрузкой 12.11.2026.

Только существующие строки по естественному ключу; никаких сохранённых ID.
Параметры заполняются только при old="". Единственное дополнение заполненного
поля — источник: прежний текст сохраняется целиком, old сверяется со снимком.
Одна транзакция на таблицу, отказ любой строки отменяет всю таблицу вместе
с аудитом. dry выполняет тот же путь с откатом; повторный apply ничего не пишет.
CSV рядом — журнал каждой ячейки, включая terStrip и дополнение источника.

Запускает оркестратор после репетиции на копии БД. Порядок: dry → backup-now → apply → dry.
Команды на сервере (скрипт передаётся через stdin, соседний CSV не требуется):
  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry < '../docs/data/БД — данные производителей 12.11 запись в прод.py'
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - apply < '../docs/data/БД — данные производителей 12.11 запись в прод.py'
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry < '../docs/data/БД — данные производителей 12.11 запись в прод.py'
Последний dry: 72 ячейки «уже так», новых правок нет. При отказе код выхода 2.
PostgreSQL: краткая блокировка чтения и записи tech_operation_records, чтобы
параллельная правка не вернула старый JSON; ожидание блокировки не более 10 с.
SQLite: BEGIN IMMEDIATE. Схема не создаётся и не меняется.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from uuid import UUID

from sqlalchemy import text

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import ReferenceValidationError, TechOperationTab
from app.modules.references.technology import sync_schema_fields
from app.shared.clock import moscow_now
from app.shared.identity import Actor


TABLE_NAMES = {"lug": "Наконечники", "coax": "БД.КОАКС"}
KEY_FIELDS = {
    "lug": ("Производитель", "Product Name", "Series", "От мм2", "До мм2",
            "Артикул контакта (REEL)", "Артикул контакта (BAG)"),
    "coax": ("Артикул", "Провод"),
}
STRIP = "Длина зачистки, мм"
SOURCE_FIELD = "Источник высоты обжима"
SOURCE_URL = "https://elr-systems.ru/upload/shop_1/1/4/6/item_146075/shop_property_file_146075_11813.pdf"
SOURCE_PAGE = "PDF 1 / печатная 11"
OLD_SOURCE = "нет данных производителя: КВТ не публикует высоту обжима — наконечники обжимаются матрицей/шестигранником по сечению (kvt.su)"
ACTOR = Actor(id=UUID(int=0), name="Данные производителей 12.11.2026", department="System")
REASON = "Дозаполнение пустых ячеек по исследованиям 09.11, сверка 12.11.2026"

# Exact DB names, section bands and sizes; no article/family inference at runtime.
# Each item is (natural key in KEY_FIELDS order, strip range, research CSV line).
LUG_ROWS = [
    [["КВТ", "НВИ", "НВИ 1.5", "0,5", "1,5", "НВИ 1.5-3", ""], "5–6", 5],
    [["КВТ", "НВИ", "НВИ 1.5", "0,5", "1,5", "НВИ 1.5-4", ""], "5–6", 5],
    [["КВТ", "НВИ", "НВИ 1.5", "0,5", "1,5", "НВИ 1.5-5", ""], "5–6", 5],
    [["КВТ", "НВИ", "НВИ 2.5", "1,5", "2,5", "НВИ 2.5-4", ""], "5–6", 6],
    [["КВТ", "НВИ", "НВИ 2.5", "1,5", "2,5", "НВИ 2.5-5", ""], "5–6", 6],
    [["КВТ", "НВИ", "НВИ 2.5", "1,5", "2,5", "НВИ 2.5-6", ""], "5–6", 6],
    [["КВТ", "НВИ", "НВИ 6.0", "4", "6", "НВИ 6.0-4", ""], "6–7", 7],
    [["КВТ", "НВИ", "НВИ 6.0", "4", "6", "НВИ 6.0-5", ""], "6–7", 7],
    [["КВТ", "НВИ", "НВИ 6.0", "4", "6", "НВИ 6.0-6", ""], "6–7", 7],
    [["КВТ", "НКИ", "НКИ 1.5", "0,5", "1,5", "НКИ 1.5-3", ""], "5–6", 2],
    [["КВТ", "НКИ", "НКИ 1.5", "0,5", "1,5", "НКИ 1.5-4", ""], "5–6", 2],
    [["КВТ", "НКИ", "НКИ 1.5", "0,5", "1,5", "НКИ 1.5-5", ""], "5–6", 2],
    [["КВТ", "НКИ", "НКИ 1.5", "0,5", "1,5", "НКИ 1.5-6", ""], "5–6", 2],
    [["КВТ", "НКИ", "НКИ 2.5", "1,5", "2,5", "НКИ 2.5-4", ""], "5–6", 3],
    [["КВТ", "НКИ", "НКИ 2.5", "1,5", "2,5", "НКИ 2.5-5", ""], "5–6", 3],
    [["КВТ", "НКИ", "НКИ 2.5", "1,5", "2,5", "НКИ 2.5-6", ""], "5–6", 3],
    [["КВТ", "НКИ", "НКИ 6.0", "4", "6", "НКИ 6.0-4", ""], "6–7", 4],
    [["КВТ", "НКИ", "НКИ 6.0", "4", "6", "НКИ 6.0-5", ""], "6–7", 4],
    [["КВТ", "НКИ", "НКИ 6.0", "4", "6", "НКИ 6.0-6", ""], "6–7", 4],
    [["КВТ", "НКИ", "НКИ 6.0", "4", "6", "НКИ 6.0-8", ""], "6–7", 4],
    [["КВТ", "РПИ-М", "РПИ-М (2.8)", "0,5", "1,5", "РПИ-М 1.5 (2.8)", ""], "5–6", 8],
    [["КВТ", "РПИ-М", "РПИ-М (4.8)", "0,5", "1,5", "РПИ-М 1.5 (4.8)", ""], "5–6", 8],
    [["КВТ", "РПИ-М", "РПИ-М (6.3)", "0,5", "1,5", "РПИ-М 1.5 (6.3)", ""], "5–6", 8],
    [["КВТ", "РПИ-М", "РПИ-М (6.3)", "4", "6", "РПИ-М 6.0 (6.3)", ""], "6–7", 10],
]


def natural_key(tab, fields):
    return tuple(fields.get(field, "") for field in KEY_FIELDS[tab])


def key_text(tab, key):
    return json.dumps(dict(zip(KEY_FIELDS[tab], key)), ensure_ascii=False, separators=(",", ":"))


def edits_for(item):
    key, value, research_line = item
    note = (f"12.11.2026: {STRIP} = {value} (семейная норма КВТ; "
            f"{key[1]}, {key[3]}–{key[4]} мм², {key[5] or key[6]}); "
            f"{SOURCE_URL}; {SOURCE_PAGE}, общая таблица; "
            f"Наконечники — длины зачистки и L± 09.11.csv, строка {research_line}. "
            "Отдельный допуск зачистки и L± не найдены; высота обжима не назначается")
    return (
        FieldEdit(STRIP, "", value),
        FieldEdit("terStrip", "", value),
        FieldEdit(SOURCE_FIELD, OLD_SOURCE, OLD_SOURCE + "; " + note),
    )


def journal_rows():
    for item in LUG_ROWS:
        for edit in edits_for(item):
            yield (TABLE_NAMES["lug"], key_text("lug", item[0]), edit.field,
                   edit.old, edit.value, SOURCE_URL, SOURCE_PAGE)


def value_changes(before, after):
    """Missing empty derived keys are equivalent to empty cells, never to filled cells."""
    return {key: (before.get(key, ""), after.get(key, ""))
            for key in before.keys() | after.keys() if before.get(key, "") != after.get(key, "")}


def lock_references(uow):
    # _edit_in reads and replaces the whole JSON without CAS. Wait for prior
    # readers and block new ones, or their later save could undo this fill.
    # The same lock protects natural-key uniqueness, including inserts.
    dialect = uow.session.get_bind().dialect.name
    if dialect == "postgresql":
        uow.session.execute(text("SET LOCAL lock_timeout = '10s'"))
        uow.session.execute(text("LOCK TABLE tech_operation_records IN ACCESS EXCLUSIVE MODE"))
    elif dialect == "sqlite":
        uow.session.execute(text("BEGIN IMMEDIATE"))
    else:
        raise ReferenceValidationError(f"Неподдерживаемая СУБД: {dialect}")


def fill_table(factory, tab, items, dry):
    """Preflight all cells, then edit/audit inside one UoW; log success only after commit."""
    cells = [dict(key=tuple(item[0]), edit=edit, status="ожидает")
             for item in items for edit in edits_for(item)]
    if not cells:
        print(f"{TABLE_NAMES[tab]}: 0 ячеек — безопасных заполнений по исследованию нет")
        return True
    failed = None
    try:
        with factory() as uow:
            lock_references(uow)
            index = defaultdict(list)
            # No fixed list limit: a later catalog growth must not hide duplicates.
            total = uow.tech_operations.counts().get(TechOperationTab(tab), 0)
            for record in uow.tech_operations.list(tab=TechOperationTab(tab), query="", limit=total + 1):
                index[natural_key(tab, record.fields)].append(record)
            groups = defaultdict(list)
            for cell in cells:
                groups[cell["key"]].append(cell)
            if len(groups) != len(items):
                raise ReferenceValidationError("Дубли естественных ключей в карте записи")
            plans = []
            for key, group in groups.items():
                matches = index.get(key, [])
                if len(matches) != 1:
                    for cell in group:
                        cell["status"] = f"отказ: найдено строк {len(matches)}, требуется одна"
                    continue
                record = matches[0]
                todo = []
                for cell in group:
                    edit = cell["edit"]
                    current = record.fields.get(edit.field, "")
                    if current == edit.value:
                        cell["status"] = "уже так"
                    elif current == edit.old:
                        cell["status"] = "готово"
                        todo.append(edit)
                    else:
                        cell["status"] = f"отказ: старое значение изменилось, сейчас «{current}»"
                expected = dict(record.fields, **{edit.field: edit.value for edit in todo})
                # Refuse legacy card drift instead of silently wiping a ter* value.
                drift = value_changes(expected, sync_schema_fields(tab, expected))
                if drift:
                    for cell in group:
                        cell["status"] = "отказ: несогласованные столбцы и ключи: " + ", ".join(sorted(drift))
                plans.append((record, todo, expected))
            if any(cell["status"].startswith("отказ") for cell in cells):
                raise ReferenceValidationError("Вся таблица отменена: проверка ключей, old или синхронизации не пройдена")
            now = moscow_now()
            for record, todo, expected in plans:
                if not todo:
                    continue
                before, after = _edit_in(record.id, tuple(todo), now, uow, siblings=False)
                if value_changes(expected, after.fields):
                    raise ReferenceValidationError("Правка изменила ячейки вне журнала")
                _audit("references.tech_record_edited", ACTOR, now, {
                    "record_id": str(before.id), "tab": tab, "reason": REASON,
                    "key": dict(zip(KEY_FIELDS[tab], natural_key(tab, before.fields))),
                    "fields": [edit.field for edit in todo],
                    "cells": [{"field": edit.field, "old": edit.old, "value": edit.value} for edit in todo],
                    "source": SOURCE_URL, "page": SOURCE_PAGE,
                }, uow)
            # Exercise constraints in dry as well, then roll back through UoW.__exit__.
            uow.session.flush()
            if not dry:
                uow.commit()
    except Exception as error:
        # Transaction boundary: the UoW has already rolled back, and CLI must fail.
        failed = f"{type(error).__name__}: {error}"
    for cell in cells:
        if failed and cell["status"] in ("ожидает", "готово"):
            cell["status"] = "отменено: вся таблица откачена"
        elif cell["status"] == "готово":
            cell["status"] = "проверено (откат)" if dry else "записано"
        edit = cell["edit"]
        print(f"{cell['status']}: {TABLE_NAMES[tab]} | {key_text(tab, cell['key'])} | "
              f"{edit.field}: «{edit.old}» → «{edit.value}»")
    print(TABLE_NAMES[tab] + ": " + json.dumps(dict(Counter(cell["status"] for cell in cells)), ensure_ascii=False))
    if failed:
        print(f"ОШИБКА {TABLE_NAMES[tab]}: {failed}")
    return failed is None


def run(factory, mode):
    if mode not in ("dry", "apply"):
        raise ValueError("Режим: dry | apply")
    outcomes = [fill_table(factory, tab, items, mode == "dry")
                for tab, items in (("lug", LUG_ROWS), ("coax", ()))]
    return 0 if all(outcomes) else 2


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Дозаполнение пустых ячеек по данным производителей")
    parser.add_argument("mode", choices=("dry", "apply"), nargs="?", default="dry")
    args = parser.parse_args()
    url = os.environ.get("ERP_DATABASE_URL")
    if not url:
        parser.error("Задайте ERP_DATABASE_URL; база по умолчанию не используется")
    engine = build_engine(url)
    try:
        return run(build_references_uow_factory(engine), args.mode)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
