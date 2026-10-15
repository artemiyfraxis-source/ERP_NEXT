"""Запись в прод 16.10.2026 (ночь) — пустые ячейки БД.ПРОВ по источникам (журнал ячеек с URL —
docs/data/«БД — дозаполнение 16.10 ночь.csv»): import_rows("prov") по «Марке», old="" у каждой ячейки, так что
заполненная ячейка возвращается отказом; марки и названия не меняются, строки не создаются (если хоть одна марка не
найдена — СТОП, ничего не пишется). Порядок (live-data protocol): dry → backup-now → apply → dry (всё «отказ»).
"""
import json
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import EditRecordCommand, FieldEdit, edit_record
from app.modules.references.cables import cable_mark
from app.modules.references.domain import TechOperationTab
from app.modules.references.importers import import_rows
from app.modules.references.load_mirror import ACTOR
from app.shared.bulk_import import ImportRowIn
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
ROWS = json.loads(r'''[
 {
  "fields": {
   "Марка": "ParLan combi F/UTP2 Cat5e PE 2х0,75",
   "Темп. от, °C": "-60",
   "Темп. до, °C": "70"
  },
  "old": {
   "Темп. от, °C": "",
   "Темп. до, °C": ""
  }
 },
 {
  "fields": {
   "Марка": "ParLan combi F/UTP2 Cat5e PE 2х1,50",
   "Темп. от, °C": "-60",
   "Темп. до, °C": "70"
  },
  "old": {
   "Темп. от, °C": "",
   "Темп. до, °C": ""
  }
 },
 {
  "fields": {
   "Марка": "ParLan combi F/UTP4 Cat5e PE 2х0,75",
   "Темп. от, °C": "-60",
   "Темп. до, °C": "70"
  },
  "old": {
   "Темп. от, °C": "",
   "Темп. до, °C": ""
  }
 },
 {
  "fields": {
   "Марка": "ParLan complex F/UTP2 Cat5e PVC/PEtr 2х0,75",
   "Темп. от, °C": "-50",
   "Темп. до, °C": "70"
  },
  "old": {
   "Темп. от, °C": "",
   "Темп. до, °C": ""
  }
 },
 {
  "fields": {
   "Марка": "ParLan complex F/UTP4 Cat5e PVC/PEtr 2х0,50",
   "Темп. от, °C": "-50",
   "Темп. до, °C": "70"
  },
  "old": {
   "Темп. от, °C": "",
   "Темп. до, °C": ""
  }
 },
 {
  "fields": {
   "Марка": "ParLan complex F/UTP4 Cat5e PVC/PEtr 2х0,75",
   "Темп. от, °C": "-50",
   "Темп. до, °C": "70"
  },
  "old": {
   "Темп. от, °C": "",
   "Темп. до, °C": ""
  }
 }
]''')
# owner's own columns («Материал экрана» …) are not import fields: edit_record with old = "" per cell
EDITS = json.loads(r'''[]''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()


def call(dry_run):
    return import_rows("prov", factory(), [ImportRowIn(fields=r["fields"], old=r["old"]) for r in ROWS], ACTOR, now,
                       dry_run=dry_run, source="erp").model_dump()


data = call(True)
if data["created"]:
    print("== СТОП — марки не найдены, ничего не записано:", data["created"])
    raise SystemExit(1)
if mode == "apply":
    data = call(False)
print(f"== БД.ПРОВ: строк {len(ROWS)}, ячеек {sum(len(r['old']) for r in ROWS)} → обновлено {len(data['updated'])}, "
      f"без изменений {data['unchanged']}, отказ {len(data['refused'])}")
for item in data["updated"][:400]:
    print("   обновлено:", item)
for item in data["refused"]:
    print("   отказ:", item)
done = {"записано": 0, "уже записано": 0, "отказ": 0}
for item in EDITS:
    with factory() as uow:
        found = [r for r in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)
                 if cable_mark(r) == item["mark"]]
    if len(found) != 1:
        done["отказ"] += 1
        print(f"   отказ «{item['mark']}»: строк {len(found)}")
        continue
    current = str(found[0].fields.get(item["field"]) or "")
    if current == item["new"]:
        done["уже записано"] += 1
        continue
    if current.strip():
        done["отказ"] += 1
        print(f"   отказ «{item['mark']}», {item['field']}: в ячейке «{current}»")
        continue
    if mode == "apply":
        edit_record(EditRecordCommand(found[0].id, (FieldEdit(item["field"], "", item["new"]),), ACTOR, now), factory())
    done["записано"] += 1
print("== свои столбцы БД.ПРОВ:", done)
print("ИТОГО", "(проверка, ничего не записано)" if mode == "dry" else "(записано)")
