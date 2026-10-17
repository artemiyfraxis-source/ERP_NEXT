"""Запись в прод 17.10.2026 — БД.МАТ «Расходники»: припои и флюсы ИЗАГРИ, РУСФЛЮС, Felder (владелец 17.10: «наполнить базу
припоев и флюсов от Изагри, Русфлюс, Felder с ссылками на покупку»). Данные — «Припои и флюсы — Изагри, Русфлюс, Felder
17.10.csv» (355 позиций, каждая карточка продавца открыта; как найдено — там же и в .md).

Что пишется: «Наименование» (+ производитель в скобках, как у уже заведённого Felder — иначе 4 имени совпадают у разных
марок), «Группа» = «припой/флюс», «Производитель», «Серия» = артикул производителя, «Материал» = сплав/состав и флюс
в припое, «Ед. изм.», «Поставщик» = продавец, «Ссылка на datasheet» = документация производителя, «Источник данных».
Новые столбцы (`add_column`, как «+ Столбец»): «Вид», «Диаметр, мм», «Фасовка», «Температура плавления, °C»,
«Ссылка на покупку», «Цена, ₽», «Дата цены».
Не пишется догадка: у 79 строк Felder тип флюса на карточке продавца не указан (агент вывел его из артикула) — флюс
в «Материал» и в наименовании для них не ставится, только «с флюсом».
Уже заведённый «Припой Sn60Pb39Cu1 1,0мм с флюсом RA05 (Felder)» (арт. 1864102005) — дополняется новыми столбцами.

Путь: строки — `import_rows("mat")` по «Наименованию», old="" у каждого поля (заполненная ячейка — отказ, не
перезапись); загрузка БД.МАТ берёт только столбцы листа, поэтому новые столбцы дописываются вторым шагом — `_edit_in`,
как правка строки в «Базе данных», old="" (стоит другое — отказ).
  ... python - columns < этот файл   (заводит пустые столбцы; после backup-now)
  ... python - dry     < этот файл
  ... python - apply   < этот файл
  ... python - dry     < этот файл   → всё «без изменений»
"""
import csv
import io
import os
import re
import sys
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import ColumnCommand, FieldEdit, _audit, _edit_in, add_column, custom_columns
from app.modules.references.domain import ReferenceValidationError, StaleRecordError
from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 17.10.2026", department="System")
NEW_COLUMNS = ("Вид", "Диаметр, мм", "Фасовка", "Температура плавления, °C", "Ссылка на покупку", "Цена, ₽", "Дата цены")
EXISTING_FELDER = ("1864102005", "Припой Sn60Pb39Cu1 1,0мм с флюсом RA05 (Felder)")
GUESS = "ТИП ФЛЮСА НЕ УКАЗАН НА КАРТОЧКЕ"
CSV_TEXT = r'''@@CSV@@'''

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("columns", "dry", "apply"):
    raise SystemExit("режим: columns | dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()

if mode == "columns":
    with factory() as uow:
        own = custom_columns(TechOperationTab.MATERIAL, uow)
        for name in NEW_COLUMNS:
            if name in own:
                print(f"   уже есть: «{name}»")
                continue
            print(f"   столбец «{name}» заведён: {add_column(ColumnCommand(tab=TechOperationTab.MATERIAL, name=name, actor=ACTOR, now=now), uow)} записей")
        uow.commit()
    raise SystemExit(0)

rows: list[ImportRowIn] = []
extras: dict[str, dict[str, str]] = {}
seen: set[str] = set()
for item in csv.DictReader(io.StringIO(CSV_TEXT.lstrip("﻿"))):
    guess = GUESS in item["Как найдено"]
    # Тип флюса в адресе карточки продавца («…iso-core-ra-05-1.0mm…») — это данные продавца, не догадка.
    token = item["Флюс в припое"].split(",")[0].strip().lower().replace("05", "-05")
    if guess and token and f"-{token}-" in item["Ссылка на покупку"].lower():
        guess = False
        item["Как найдено"] = item["Как найдено"].split("; ТИП ФЛЮСА")[0] + "; тип флюса — по адресу карточки продавца"
    name = item["Наименование"].strip()
    flux = item["Флюс в припое"].strip()
    if guess:
        name = re.sub(r"с флюсом [^,]+", "с флюсом", name)
        flux = ""
    source = item["Как найдено"].split("; ТИП ФЛЮСА")[0].strip() if guess else item["Как найдено"].strip()
    if guess:
        source += "; тип флюса на карточке продавца не указан"
    material = item["Сплав или состав"].strip() + (f", флюс в припое: {flux}" if flux else "")
    extra = {"Вид": item["Вид"], "Диаметр, мм": item["Диаметр, мм"], "Фасовка": item["Фасовка"],
             "Температура плавления, °C": item["Температура плавления, °C"], "Ссылка на покупку": item["Ссылка на покупку"],
             "Цена, ₽": item["Цена, ₽"], "Дата цены": item["Дата цены"]}
    if item["Артикул производителя"].strip() == EXISTING_FELDER[0]:
        fields = {"Наименование": EXISTING_FELDER[1], **extra}
    else:
        maker = item["Производитель"].strip()
        options = [name, f"{name}, {item['Фасовка'].strip()}" if item["Фасовка"].strip() not in name else "",
                   f"{name}, арт. {item['Артикул производителя'].strip()}" if item["Артикул производителя"].strip() else "",
                   f"{name}, {item['Продавец'].strip()} {item['Ссылка на покупку'].rstrip('/').rsplit('/', 1)[-1]}"]
        full = next(f"{option} ({maker})" for option in options if option and f"{option} ({maker})" not in seen)
        fields = {"Наименование": full, "Группа": "припой/флюс", "Производитель": item["Производитель"],
                  "Серия": item["Артикул производителя"], "Материал": material, "Ед. изм.": item["Ед. изм."],
                  "Поставщик": item["Продавец"], "Ссылка на datasheet": item["Ссылка на документацию"],
                  "Источник данных": source, **extra}
    fields = {key: str(value).strip() for key, value in fields.items() if str(value).strip()}
    if fields["Наименование"] in seen:
        print("   дубль имени, пропущен:", fields["Наименование"])
        continue
    seen.add(fields["Наименование"])
    extras[fields["Наименование"]] = {key: fields.pop(key) for key in NEW_COLUMNS if key in fields}
    if len(fields) > 1:
        rows.append(ImportRowIn(fields=fields, old={key: "" for key in fields if key != "Наименование"}))


def call(dry_run):
    return import_rows("mat", factory(), rows, ACTOR, now, dry_run=dry_run, source="erp").model_dump()


data = call(mode != "apply")
print(f"ИТОГО {len(rows)}: {'проверено' if mode == 'dry' else 'записано'} — новых {len(data['created'])}, обновлено {len(data['updated'])}, "
      f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["refused"][:40]:
    print("   отказ:", item)
for item in data["updated"][:5]:
    print("   обновлено:", item)

# Шаг 2: новые столбцы — правкой записи (в dry — только у строк, что уже есть).
done = same = refused = missing = 0
with factory() as uow:
    by_name = {str(r.fields.get("Наименование", "")).strip(): r.id for r in uow.tech_operations.list(tab=TechOperationTab.MATERIAL, query="", limit=100_000)}
for name, values in extras.items():
    record_id = by_name.get(name)
    if record_id is None:
        missing += 1
        continue
    with factory() as uow:
        record = uow.tech_operations.get(record_id)
        edits = tuple(FieldEdit(key, "", value) for key, value in values.items() if str(record.fields.get(key, "")).strip() != value)
        if not edits:
            same += 1
            continue
        try:
            before, _ = _edit_in(record_id, edits, now, uow)
        except (StaleRecordError, ReferenceValidationError, LookupError) as error:
            refused += 1
            print(f"   отказ (столбцы): {name} ({error})")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {"record_id": str(before.id), "tab": before.tab.value,
               "fields": [edit.field for edit in edits], "reason": "припои и флюсы: вид, фасовка, ссылка на покупку, цена (17.10.2026)"}, uow)
        if mode == "apply":
            uow.commit()
        done += 1
print(f"СТОЛБЦЫ {len(extras)}: {'проверено' if mode == 'dry' else 'записано'} {done}, уже так {same}, отказ {refused}, "
      f"строки ещё нет (появится при записи) {missing}")
