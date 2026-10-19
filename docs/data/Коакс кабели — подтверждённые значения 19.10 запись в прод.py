"""Запись в прод 19.10.2026 — «Коаксиальные кабели»: подтверждённые значения поверх заполненных ячеек (владелец 19.10:
«всё заполнял ты; если нашёл подтверждённые — лучше ставить их»; решения главной сессии 19.10).

  A. Заполненная ячейка расходится с карточкой своего артикула строки / даташитом изготовителя — перезапись
     (5D-FB Cu, LMR-100, RG-174, RG-400, RG-6, SAT-703, TZC500-97, MFC-240 UF).
  B. Своя карточка кабеля описывает другой кабель, чем артикул строки — значение продавца своего артикула пишется
     в ячейку БД.ПРОВ (ячейка важнее карточки): 5D-FB, 10D-FB, RG-174, RG-402, RG-405.

Журнал «было → стало» и источник каждой ячейки — «Коакс кабели — подтверждённые значения 19.10.csv»; «было» —
значение, прочитанное с прода 19.10 после записи дозаполнения. Ячейка пишется, только если в ней всё ещё это
значение (иначе — отказ, не перезапись); уже записанное печатается как «уже так».

  • ячейки БД.ПРОВ: столбцы листа — загрузкой import_rows("prov") по «Марке» со старым значением; свои столбцы —
    правкой записи со старым значением;
  • D1–D3 — тем же путём, что форма «Коаксиальные кабели» (set_coax_diameters_in), старые D — как прочитаны.

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
from app.modules.references.application import (EditRecordCommand, FieldEdit, SetCoaxDiametersCommand, edit_record,
                                                 set_coax_diameters_in)
from app.modules.references.cables import diameter_index
from app.modules.references.domain import (BASE_COLUMNS, ReferenceValidationError, StaleRecordError, TechOperationTab,
                                           cable_key)
from app.modules.references.importers import import_rows
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 19.10.2026", department="System")
REASON = "подтверждённые значения коаксиальных кабелей 19.10.2026 (карточка своего артикула / даташит изготовителя)"
DIAMETERS = ("D1", "D2", "D3")
# [марка, поле (D1/D2/D3 — таблица диаметров), было, стало]
CELLS = json.loads(r'''[
 [
  "5D-FB Cu",
  "D2",
  "4,9",
  "5,1"
 ],
 [
  "5D-FB Cu",
  "D3",
  "5,64",
  "5,9"
 ],
 [
  "LMR-100",
  "D3",
  "2,11",
  "1,8"
 ],
 [
  "LMR-100",
  "Наружный диаметр, мм",
  "2,79",
  "2,9"
 ],
 [
  "RG-174",
  "D3",
  "2,23",
  "1,9"
 ],
 [
  "RG-400",
  "D3",
  "4,2",
  "3,95"
 ],
 [
  "RG-6",
  "D3",
  "5,38",
  "5,2"
 ],
 [
  "SAT-703",
  "D1",
  "1,13",
  "1,02"
 ],
 [
  "SAT-703",
  "D2",
  "4,80",
  "4,6"
 ],
 [
  "SAT-703",
  "D3",
  "5,30",
  "5,1"
 ],
 [
  "TZC500-97",
  "Материал диэлектрика",
  "ПЭ",
  "ПЭ вспененный"
 ],
 [
  "MFC-240 UF",
  "Материал экрана",
  "алюминий, фольга",
  "алюминий, фольга + медь лужёная, оплётка"
 ],
 [
  "5D-FB",
  "Радиус изгиба неподвижно, мм",
  "",
  "50"
 ],
 [
  "5D-FB",
  "Радиус изгиба многократно, мм",
  "",
  "150"
 ],
 [
  "10D-FB",
  "Погонная ёмкость, пФ/м",
  "",
  "82"
 ],
 [
  "RG-174",
  "Радиус изгиба многократно, мм",
  "",
  "50"
 ],
 [
  "RG-402",
  "Погонная ёмкость, пФ/м",
  "",
  "95,1"
 ],
 [
  "RG-402",
  "Радиус изгиба неподвижно, мм",
  "",
  "8"
 ],
 [
  "RG-405",
  "Погонная ёмкость, пФ/м",
  "",
  "95,1"
 ],
 [
  "RG-405",
  "Радиус изгиба неподвижно, мм",
  "",
  "4"
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
missing = sorted({mark for mark, *_ in CELLS if cable_key(mark) not in records})
if missing:
    print("СТОП — нет строк в БД.ПРОВ (ничего не записано):", ", ".join(missing))
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
            if field in BASE_COLUMNS[TechOperationTab.WIRE]:
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
    written = sum(len(item["changes"]) for item in data["updated"])
    refused_keys = {cable_key(item["key"]) for item in data["refused"]}
    refused = sum(len(row.fields) - 1 for row in base_rows if cable_key(row.fields["Марка"]) in refused_keys)
    for item in data["refused"]:
        print(f"   отказ (загрузка) «{item['key']}»: {item['reason']}")
    total["к записи"] += written
    total["отказ"] += refused

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
                todo.append(f"{n} {old} → {new}")
            else:
                total["отказ"] += 1
                print(f"   отказ «{mark}» {n}: в таблице {have[n]}, ждали {old}")
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
