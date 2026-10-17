"""Запись в прод 17.10.2026 — БД.КОАКС: ссылка на карточку товара у поставщика и цена (владелец 17.10: «нужно ссылку на
карточку на сайтах и цены»). Данные — «Коакс — ссылки и цены 17.10.csv» (каждая страница открыта, артикул сверен; как
найдено — там же и в .md).

Новые столбцы «Цена, ₽» и «Дата цены» заводятся тем же путём, что «+ Столбец» (`add_column`). Ячейки — `_edit_in`, как
правка строки в «Базе данных»: «Ссылка» пишется только в пустую (другая ссылка уже стоит — не трогаю, печатаю), цена и
дата — в пустые. Всё в одной транзакции: apply — commit в конце, dry — откат. У строк старого зеркала поля «Ссылка» нет
(правка строки его не заводит) — им ссылка идёт загрузкой `import_rows("coax")` по паре артикул + кабель, old="" (столбец
карточки КОАКС, загрузка его заводит).

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  ... python - apply < этот файл
  ... python - dry   < этот файл   → 0 к записи
"""
import csv
import io
import os
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import ColumnCommand, FieldEdit, _audit, _edit_in, add_column, custom_columns
from app.modules.references.domain import ReferenceValidationError, StaleRecordError, TechOperationTab
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
REASON = "ссылка на карточку и цена поставщика 17.10.2026 (владелец: «нужно ссылку на карточку на сайтах и цены»)"
NEW_COLUMNS = ("Цена, ₽", "Дата цены")
CSV_TEXT = r'''@@CSV@@'''

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
rows = [row for row in csv.DictReader(io.StringIO(CSV_TEXT.lstrip("﻿"))) if row["Ссылка"].strip() or row["Цена ₽"].strip()]
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
written = same = kept = refused = 0
links: list[ImportRowIn] = []
with factory() as uow:
    own = custom_columns(TechOperationTab.COAX, uow)
    for name in NEW_COLUMNS:
        if name not in own:
            count = add_column(ColumnCommand(tab=TechOperationTab.COAX, name=name, actor=ACTOR, now=now), uow)
            print(f"== столбец «{name}» заведён: {count} записей")
    for row in rows:
        record_id = UUID(row["id"])
        current = uow.tech_operations.get(record_id)
        if current is None:
            refused += 1
            print(f"   нет записи: {row['Артикул']} · {row['Кабель']}")
            continue
        have = {name: str(current.fields.get(name, "")).strip() for name in ("Ссылка", *NEW_COLUMNS)}
        want = {"Ссылка": row["Ссылка"].strip(), "Цена, ₽": row["Цена ₽"].strip(), "Дата цены": row["Дата цены"].strip() if row["Цена ₽"].strip() else ""}
        edits = []
        by_load = bool(want["Ссылка"]) and "Ссылка" not in current.fields
        if by_load:
            links.append(ImportRowIn(fields={"coaxArticle": current.fields.get("coaxArticle", ""), "coaxWire": current.fields.get("coaxWire", ""),
                                             "Ссылка": want.pop("Ссылка")}, old={"Ссылка": ""}))
        else:
            want = want if want["Ссылка"] else {k: v for k, v in want.items() if k != "Ссылка"}
        for name, value in want.items():
            if not value or have[name] == value:
                continue
            if have[name]:
                kept += 1
                print(f"   оставлено: {row['Артикул']} · {row['Кабель']} «{name}» стоит «{have[name]}», найдено «{value}»")
                continue
            edits.append(FieldEdit(name, "", value))
        if not edits:
            same += 0 if by_load else 1
            continue
        try:
            before, _ = _edit_in(record_id, tuple(edits), now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            refused += 1
            print(f"   отказ: {row['Артикул']} · {row['Кабель']} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(before.id), "tab": before.tab.value, "fields": [edit.field for edit in edits], "reason": REASON,
        }, uow)
        written += 1
    if mode == "apply":
        uow.commit()
if links:
    def call(dry_run):
        return import_rows("coax", factory(), links, ACTOR, now, dry_run=dry_run, source="erp").model_dump()
    data = call(True)
    if data["created"]:
        print("== СТОП — пары не найдены, ссылки загрузкой не записаны:", data["created"])
        raise SystemExit(1)
    if mode == "apply":
        data = call(False)
    print(f"== «Ссылка» загрузкой: {len(links)} → обновлено {len(data['updated'])}, без изменений {data['unchanged']}, отказ {len(data['refused'])}")
    for item in data["refused"]:
        print("   отказ:", item)
print(f"ИТОГО строк с данными {len(rows)}: {'проверено' if mode == 'dry' else 'записано'} {written}, уже так {same}, "
      f"оставлено своё {kept}, отказ {refused}")
