"""Запись в прод 20.10.2026 — «Коаксиальные кабели»: спорные значения второго прохода, решены агентом (владелец 20.10:
«сам реши все»). Правило — документ изготовителя артикула строки или значение стандарта (QPL-изготовитель Times):
  • RG-316D — по даташиту Huber+Suhner ENVIROFLEX_316_D, её артикула: диэлектрик SPEX (сшитый вспененный ПЭ) 1,53 мм (D2 в таблице 1,52 — оставлен),
    оболочка RADOX, Ø 3,16 (было — по кабелю M17/152: ПТФЭ, ФЭП, 2,9);
  • RG-180 — ёмкость 50,5 пФ/м (Times: 15,4 pF/ft, M17/95; Harbour 57,1 — не взято);
  • RG-393/U — рабочее напряжение 5000 В и масса 261 г/м (Times M17/127; Harbour 1500 В / 245,6 г/м — не взято).
Старое значение — прочитанное с прода; изменённая с тех пор ячейка вернётся отказом. Механика — как у
«Коакс кабели — дозаполнение 2 19.10 запись в прод.py».

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл
"""
import json
import os
import sys
from collections import defaultdict
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import (EditRecordCommand, FieldEdit, SetCoaxDiametersCommand, custom_columns,
                                                 edit_record, set_coax_diameters_in)
from app.modules.references.cables import diameter_index
from app.modules.references.domain import (BASE_COLUMNS, ReferenceValidationError, StaleRecordError, TechOperationTab,
                                           cable_key)
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 19.10.2026", department="System")
REASON = "дозаполнение коаксиальных кабелей, второй проход 19.10.2026 (по стандарту MIL-DTL-17)"
DIAMETERS = ("D1", "D2", "D3")
# [марка, поле (D1/D2/D3 — таблица диаметров), было, стало]
CELLS = json.loads(r'''[
 [
  "RG-316D",
  "Материал диэлектрика",
  "ПТФЭ",
  "ПЭ вспененный сшитый"
 ],
 [
  "RG-316D",
  "Материал оболочки",
  "ФЭП",
  "RADOX"
 ],
 [
  "RG-316D",
  "Наружный диаметр, мм",
  "2,9",
  "3,16"
 ],
 [
  "RG-180",
  "Погонная ёмкость, пФ/м",
  "",
  "50,5"
 ],
 [
  "RG-393/U",
  "Напряжение, В",
  "",
  "5000"
 ],
 [
  "RG-393/U",
  "Масса, г/м",
  "",
  "261"
 ]
]''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
total = {"к записи": 0, "уже так": 0, "отказ": 0}

with factory() as uow:
    records = {cable_key(r.fields.get("Марка") or r.display_text): r
               for r in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)}
    own_columns = set(custom_columns(TechOperationTab.WIRE, uow))
missing = sorted({mark for mark, *_ in CELLS if cable_key(mark) not in records})
if missing:
    print("СТОП — нет строк в БД.ПРОВ (ничего не записано):", ", ".join(missing))
    raise SystemExit(1)
base_columns = set(BASE_COLUMNS[TechOperationTab.WIRE])
no_column = sorted({field for _, field, *_ in CELLS
                    if field not in DIAMETERS and field not in base_columns and field not in own_columns})
if no_column:
    print("СТОП — нет своих столбцов БД.ПРОВ (ничего не записано):", ", ".join(no_column))
    raise SystemExit(1)

print("== БД.ПРОВ")
by_mark = defaultdict(list)
for mark, field, old, new in CELLS:
    if field not in DIAMETERS:
        by_mark[mark].append((field, old, new))
base_rows = []
for mark, cells in by_mark.items():
    record = records[cable_key(mark)]
    own, base, base_old = [], {}, {}
    for field, old, new in cells:
        current = str(record.fields.get(field, "") or "").strip()
        if current == new:
            total["уже так"] += 1
        elif current == old:
            print(f"   {'к записи' if dry else 'пишу'}: «{mark}» {field}: «{old}» → «{new}»")
            if field in base_columns:
                base[field], base_old[field] = new, old
            else:
                own.append(FieldEdit(field, old, new))
        else:
            total["отказ"] += 1
            print(f"   отказ «{mark}» {field}: в ячейке «{current[:60]}», ждали «{old}»")
    if base:
        base_rows.append(ImportRowIn(fields={"Марка": record.fields.get("Марка") or mark, **base}, old=base_old))
    if own:
        if not dry:
            try:
                edit_record(EditRecordCommand(record.id, tuple(own), ACTOR, now), factory())
            except (StaleRecordError, ReferenceValidationError) as error:
                total["отказ"] += len(own)
                print(f"   отказ «{mark}»: {error}")
                continue
        total["к записи"] += len(own)
if base_rows:
    def load(dry_run):
        return import_rows("prov", factory(), base_rows, ACTOR, now, dry_run=dry_run, source="erp").model_dump()
    data = load(True)
    if data["created"]:
        print("СТОП — загрузка по «Марке» завела бы новые строки, столбцы листа не записаны:", data["created"])
        raise SystemExit(1)
    if not dry:
        data = load(False)
    cells = sum(len(row.fields) - 1 for row in base_rows)
    written = sum(len(item["changes"]) for item in data["updated"])
    refused_keys = {cable_key(item["key"]) for item in data["refused"]}
    refused = sum(len(row.fields) - 1 for row in base_rows if cable_key(row.fields["Марка"]) in refused_keys)
    for item in data["refused"]:
        print(f"   отказ (загрузка) «{item['key']}»: {item['reason']}")
    total["к записи"] += written
    total["отказ"] += refused
    if written + refused != cells:
        print(f"   ВНИМАНИЕ: ячеек {cells}, записано {written}, отказ {refused} — не сходится")

print("== СПР.КАБ D1–D3")
by_dia = defaultdict(list)
for mark, field, old, new in CELLS:
    if field in DIAMETERS:
        by_dia[mark].append((field, old, new))
for mark, cells in by_dia.items():
    with factory() as uow:
        current = diameter_index(uow.tech_operations.list(tab=TechOperationTab.KAB_COAX, query="", limit=100_000)).get(cable_key(mark))
        if current is None:
            total["отказ"] += len(cells)
            print(f"   отказ «{mark}»: нет строки в таблице диаметров")
            continue
        have = {n: str(current.fields.get(n, "") or "").strip() for n in DIAMETERS}
        merged, todo = dict(have), []
        for n, old, new in cells:
            if have[n] == new:
                total["уже так"] += 1
            elif have[n] == old:
                merged[n] = new
                todo.append(f"{n} «{old}» → «{new}»")
            else:
                total["отказ"] += 1
                print(f"   отказ «{mark}» {n}: в таблице «{have[n]}», ждали «{old}»")
        if not todo:
            continue
        try:
            set_coax_diameters_in(SetCoaxDiametersCommand(cable=mark, dia_id=current.id, old=have, value=merged,
                                                          actor=ACTOR, now=now), uow, {"reason": REASON})
        except (StaleRecordError, ReferenceValidationError) as error:
            total["отказ"] += len(todo)
            print(f"   отказ «{mark}»: {error}")
            continue
        if not dry:
            uow.commit()
        total["к записи"] += len(todo)
        print(f"   {'к записи' if dry else 'записано'}: «{mark}» " + "; ".join(todo))
print("ИТОГО", total, "(проверка, ничего не записано)" if dry else "(записано)")
