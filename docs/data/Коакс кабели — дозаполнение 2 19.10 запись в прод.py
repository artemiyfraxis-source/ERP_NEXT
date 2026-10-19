"""Запись в прод 19.10.2026 — «Коаксиальные кабели»: дозаполнение пустых ячеек, второй проход (владелец 19.10: «данных
нет нихера»; «если нашёл подтверждённые — лучше ставить их»).

Погонная ёмкость, рабочее напряжение, масса, радиусы изгиба — только в пустые ячейки:
  • по стандарту MIL-DTL-17 — у строк, чьи материалы совпадают с конструкцией своего типа RG (RG-58 C/U, RG-213,
    RG-179B/U, RG-180, RG-303, RG-316S, RG-393/U); значения слэш-листов — из справочника Times Microwave и каталога
    Harbour Industries (QPL-изготовители);
  • по даташитам изготовителя своего артикула — Huber+Suhner SUCOFORM 141, SUCOFORM 86 FEP, ENVIROFLEX_316_D (RG-316D);
  • по карточке продавца своего артикула — RG-174 A/U (olmitec, Radiolab).

Значения и источник каждой ячейки — «Коакс кабели — дозаполнение 2 19.10.csv» (журнал для отката: до записи все эти
ячейки пустые), сводка — «Коакс кабели — дозаполнение 2 19.10.md».

  • строка ищется по «Марке» (cable_key), не по ID;
  • столбцы листа БД.ПРОВ («Напряжение, В», «Масса, г/м») — загрузкой import_rows("prov") по «Марке» со старым
    значением ""; свои столбцы (ёмкость, радиусы) — правкой записи со старым значением "". Заполненную за это время
    ячейку скрипт не перезапишет: отказ.
Повторный запуск идемпотентен: записанное печатается как «уже так».

  cd /opt/erp/app/deploy
  sudo -n docker compose --env-file /opt/erp/config/.env -f docker-compose.yml run --rm -T api python - dry   < этот файл
  sudo -n bash /opt/erp/app/deploy/erp.sh backup-now
  sudo -n docker compose ... run --rm -T api python - apply < этот файл
  sudo -n docker compose ... run --rm -T api python - dry   < этот файл   → к записи 0, отказ 0, остальное «уже так»
"""
import json
import os
import sys
from collections import defaultdict
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import EditRecordCommand, FieldEdit, custom_columns, edit_record
from app.modules.references.domain import (BASE_COLUMNS, ReferenceValidationError, StaleRecordError, TechOperationTab,
                                           cable_key)
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 19.10.2026", department="System")
# [марка, поле, было, стало]
CELLS = json.loads(r'''[
 [
  "RG-58 C/U TC PE TC PVC",
  "Погонная ёмкость, пФ/м",
  "",
  "101,1"
 ],
 [
  "RG-58 C/U TC PE TC PVC",
  "Напряжение, В",
  "",
  "1900"
 ],
 [
  "RG-58 C/U TC PE TC PVC",
  "Масса, г/м",
  "",
  "39"
 ],
 [
  "RG-213 CU PE CU PVC",
  "Напряжение, В",
  "",
  "5000"
 ],
 [
  "RG-213 CU PE CU PVC",
  "Масса, г/м",
  "",
  "165"
 ],
 [
  "RG-179B/U",
  "Погонная ёмкость, пФ/м",
  "",
  "64"
 ],
 [
  "RG-179B/U",
  "Напряжение, В",
  "",
  "1200"
 ],
 [
  "RG-179B/U",
  "Масса, г/м",
  "",
  "16,1"
 ],
 [
  "RG-179B/U",
  "Радиус изгиба неподвижно, мм",
  "",
  "10,2"
 ],
 [
  "RG-180",
  "Напряжение, В",
  "",
  "1500"
 ],
 [
  "RG-180",
  "Масса, г/м",
  "",
  "29,5"
 ],
 [
  "RG-180",
  "Радиус изгиба неподвижно, мм",
  "",
  "17,8"
 ],
 [
  "RG-303",
  "Погонная ёмкость, пФ/м",
  "",
  "96,5"
 ],
 [
  "RG-303",
  "Напряжение, В",
  "",
  "1900"
 ],
 [
  "RG-303",
  "Масса, г/м",
  "",
  "46,1"
 ],
 [
  "RG-303",
  "Радиус изгиба неподвижно, мм",
  "",
  "22,9"
 ],
 [
  "RG-316S",
  "Погонная ёмкость, пФ/м",
  "",
  "96,5"
 ],
 [
  "RG-316S",
  "Напряжение, В",
  "",
  "1200"
 ],
 [
  "RG-316S",
  "Масса, г/м",
  "",
  "18,2"
 ],
 [
  "RG-316S",
  "Радиус изгиба неподвижно, мм",
  "",
  "12,7"
 ],
 [
  "RG-393/U",
  "Погонная ёмкость, пФ/м",
  "",
  "96,5"
 ],
 [
  "RG-393/U",
  "Радиус изгиба неподвижно, мм",
  "",
  "50,8"
 ],
 [
  "SUCOFORM 141",
  "Погонная ёмкость, пФ/м",
  "",
  "92"
 ],
 [
  "SUCOFORM 141",
  "Напряжение, В",
  "",
  "1900"
 ],
 [
  "SUCOFORM 141",
  "Масса, г/м",
  "",
  "40"
 ],
 [
  "SUCOFORM 141",
  "Радиус изгиба неподвижно, мм",
  "",
  "8"
 ],
 [
  "SUCOFORM 141",
  "Радиус изгиба многократно, мм",
  "",
  "40"
 ],
 [
  "SUCOFORM 86 FEP",
  "Погонная ёмкость, пФ/м",
  "",
  "95"
 ],
 [
  "SUCOFORM 86 FEP",
  "Напряжение, В",
  "",
  "1500"
 ],
 [
  "SUCOFORM 86 FEP",
  "Масса, г/м",
  "",
  "18"
 ],
 [
  "SUCOFORM 86 FEP",
  "Радиус изгиба неподвижно, мм",
  "",
  "6"
 ],
 [
  "SUCOFORM 86 FEP",
  "Радиус изгиба многократно, мм",
  "",
  "20"
 ],
 [
  "RG-316D",
  "Погонная ёмкость, пФ/м",
  "",
  "94,5"
 ],
 [
  "RG-316D",
  "Масса, г/м",
  "",
  "21"
 ],
 [
  "RG-316D",
  "Радиус изгиба неподвижно, мм",
  "",
  "5"
 ],
 [
  "RG-316D",
  "Радиус изгиба многократно, мм",
  "",
  "30"
 ],
 [
  "RG-174 A/U",
  "Погонная ёмкость, пФ/м",
  "",
  "92,16"
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
no_column = sorted({field for _, field, *_ in CELLS if field not in base_columns and field not in own_columns})
if no_column:
    print("СТОП — нет своих столбцов БД.ПРОВ (ничего не записано):", ", ".join(no_column))
    raise SystemExit(1)

print("== БД.ПРОВ")
by_mark = defaultdict(list)
for mark, field, old, new in CELLS:
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
print("ИТОГО", total, "(проверка, ничего не записано)" if dry else "(записано)")
