"""Запись в прод 16.10.2026 (ночь) — структура монтажных проводов: «Материал жилы» и «Толщина изоляции, мм» только там,
где в структуре пусто (журнал ячеек с источниками — docs/data/«БД — дозаполнение 16.10 ночь.csv»).

Пишется через `save_hookup_in` (как форма «Монтажные провода»): все прочие поля структуры передаются как есть
(old = текущая структура, иначе отказ «уже поменяли»). Перед записью каждый провод проверяется в незакоммиченном
юните: если запись поменяла бы хоть одну ячейку БД.ПРОВ — провод не пишется (отказ). Порядок: dry → backup-now →
apply → dry (всё «уже записано»).
"""
import json
import os
import sys

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import SaveHookupCommand, save_hookup_in
from app.modules.references.cables import cable_mark
from app.modules.references.domain import TechOperationTab, normalized_cable_key
from app.modules.references.load_mirror import ACTOR
from app.shared.clock import moscow_now

sys.stdout.reconfigure(encoding="utf-8")
CELLS = json.loads(r'''[
 {
  "mark": "ПВАМ 0,35",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПВАМ 0,75",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПВАМ 1,50",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПВАМ 2,5",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПВАМ 4",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПВАМ 6",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПГВА 0,75",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПГВА 1,0",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПГВА 1,5",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПГВА 2,5",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПГВА 4,0",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПГВА 6,0",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПуГВнг(А)-LS 1х0,5",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПуГВнг(А)-LS 1х0,75",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПуГВнг(А)-LS 1х1,5",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПуГВнг(А)-LS 1х2,5",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "ПуГВнг(А)-LS 1х4,0",
  "field": "conductor_material",
  "new": "медь"
 },
 {
  "mark": "Силиконовый провод 12AWG",
  "field": "conductor_material",
  "new": "медь лужёная"
 },
 {
  "mark": "Силиконовый провод 14AWG",
  "field": "conductor_material",
  "new": "медь лужёная"
 },
 {
  "mark": "Силиконовый провод 26AWG",
  "field": "conductor_material",
  "new": "медь лужёная"
 },
 {
  "mark": "Силиконовый провод 30AWG",
  "field": "conductor_material",
  "new": "медь лужёная"
 }
]''')

mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
by_mark = {}
for cell in CELLS:
    by_mark.setdefault(cell["mark"], {})[cell["field"]] = cell["new"]


def shown(value):
    return "" if value is None else str(value)


stats = {"записано": 0, "уже записано": 0, "отказ": 0}
for mark, values in by_mark.items():
    with factory() as uow:
        records = [r for r in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100_000)
                   if cable_mark(r) == mark]
        current = uow.cable_structure.hookup(normalized_cable_key(mark))
    if len(records) != 1 or current is None:
        stats["отказ"] += 1
        print(f"   отказ «{mark}»: строк БД.ПРОВ {len(records)}, структура {'есть' if current else 'нет'}")
        continue
    record = records[0]
    material = values.get("conductor_material")
    thickness = values.get("insulation_thickness_mm")
    todo = {}
    if material is not None:
        if current.conductor_material.strip() == "":
            todo["conductor_material"] = material
        elif current.conductor_material.strip() == material:
            stats["уже записано"] += 1
        else:
            stats["отказ"] += 1
            print(f"   отказ «{mark}»: материал уже «{current.conductor_material}»")
    if thickness is not None:
        if current.insulation_thickness_mm is None:
            todo["insulation_thickness_mm"] = thickness
        elif str(current.insulation_thickness_mm).rstrip("0").rstrip(".") == thickness.replace(",", ".").rstrip("0").rstrip("."):
            stats["уже записано"] += 1
        else:
            stats["отказ"] += 1
            print(f"   отказ «{mark}»: толщина уже {current.insulation_thickness_mm}")
    if not todo:
        continue
    command = SaveHookupCommand(
        record_id=record.id, series=current.series, gauge_id=current.gauge_id, insulation=current.insulation,
        outer_d_mm=shown(current.outer_d_mm), voltage_v=shown(current.voltage_v), temp_min_c=shown(current.temp_min_c),
        temp_max_c=shown(current.temp_max_c), old=current, actor=ACTOR, now=now,
        conductor_material=todo.get("conductor_material"), insulation_thickness_mm=todo.get("insulation_thickness_mm"),
    )
    # probe in a unit of work that is never committed: the БД.ПРОВ row must stay exactly as it is
    with factory() as uow:
        wire, after = save_hookup_in(command, uow, {"source": "ночное дозаполнение 16.10"})
    changed = {k: (record.fields.get(k), after.fields.get(k)) for k in set(record.fields) | set(after.fields)
               if record.fields.get(k) != after.fields.get(k)}
    if changed:
        stats["отказ"] += 1
        print(f"   отказ «{mark}»: запись поменяла бы ячейки БД.ПРОВ {changed}")
        continue
    if not dry:
        with factory() as uow:
            save_hookup_in(command, uow, {"source": "ночное дозаполнение 16.10"})
            uow.commit()
    stats["записано"] += len(todo)
    print(f"   {'записано' if not dry else 'будет записано'} «{mark}»: {todo}")
print("== структура монтажных:", stats, "(проверка, ничего не записано)" if dry else "(записано)")
