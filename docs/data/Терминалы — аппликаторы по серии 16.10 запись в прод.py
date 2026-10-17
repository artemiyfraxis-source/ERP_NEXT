"""Запись в прод 16.10.2026 — аппликаторы внутри серии (владелец 16.10: «внутри одной серии у всех контактов должны быть
все аппликаторы, потому что они смежные — тут спокойно можно дописать A2», пример — JST XH: SXH-001T-P0.6 S5/M5/A2,
SXH-002T-P0.6 S5/M5).

Правило (серия = производитель + «Серия» строки БД.ТЕР):
  1. Аппликаторы A* серии (объединение) дописываются каждому контакту серии в конец ячейки «Аппликатор».
  2. Пустая ячейка получает значение серии, если у остальных контактов серии оно одно и то же.
  3. S* и M* (полуавтомат и ручной инструмент) не смешиваются: в базе они идут по диапазону сечений (Micro-Fit S8/M8 на
     20–24 AWG и S18/M9 на 26–30 AWG, КВТ M31/M32/M33 по размеру, у TE — свой инструмент на каждый контакт).
  Приставка «возможно» у ячейки сохраняется.

Запись — правка строки `_edit_in` (как «Изменить» в «Базе данных») по столбцу «Аппликатор» со старым значением;
ключ terApplicator идёт следом. Порядок: dry → `erp.sh backup-now` → apply → dry (всё «уже так»)."""
import os
import re
import sys
from collections import defaultdict
from uuid import UUID

from app.database import build_engine, build_references_uow_factory
from app.modules.references.application import FieldEdit, _audit, _edit_in
from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.identity import Actor

sys.stdout.reconfigure(encoding="utf-8")
ACTOR = Actor(id=UUID(int=0), name="Исправление данных 16.10.2026", department="System")
REASON = "аппликаторы внутри серии, 16.10.2026 (владелец: «у всех контактов серии все аппликаторы»)"
COLUMN = "Аппликатор"
mode = (sys.argv[1] if len(sys.argv) > 1 else "dry").strip()
if mode not in ("dry", "apply"):
    raise SystemExit("режим: dry | apply")
dry = mode == "dry"
factory = build_references_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
now = moscow_now()
MAYBE = "возможно "


def s(value):
    return str(value or "").strip()


def tokens(value):
    body = value[len(MAYBE):] if value.startswith(MAYBE) else value
    return [t.strip() for t in body.split("/") if t.strip()]


def is_applicator(token):
    return re.fullmatch(r"A\d+", token) is not None


with factory() as uow:
    rows = uow.tech_operations.list(tab=TechOperationTab.TERMINAL, query="", limit=100_000)
series = defaultdict(list)
for row in rows:
    key = (s(row.fields.get("terManufacturer")), s(row.fields.get("terSeries")))
    if key[1]:
        series[key].append(row)

plan = []
for key, members in sorted(series.items()):
    values = [s(r.fields.get(COLUMN) or r.fields.get("terApplicator")) for r in members]
    applicators = sorted({t for v in values for t in tokens(v) if is_applicator(t)}, key=lambda t: int(t[1:]))
    filled = {v for v in values if v}
    for row, value in zip(members, values):
        if not value:
            new = next(iter(filled)) if len(filled) == 1 else "/".join(applicators)
        else:
            missing = [t for t in applicators if t not in tokens(value)]
            new = value + "".join(f"/{t}" for t in missing)
        if new != value:
            plan.append((key, row, value, new))

print(f"== серий {len(series)}, к записи {len(plan)}")
written = 0
for (maker, name), row, old, new in plan:
    label = f"{maker} {name} «{s(row.fields.get('terArticle'))}»"
    with factory() as uow:
        live = uow.tech_operations.get(row.id)
        current = s(live.fields.get(COLUMN)) if live else None
        if current == new:
            print(f"   уже так: {label} {new}")
            continue
        try:
            _edit_in(row.id, (FieldEdit(COLUMN, old, new),), now, uow)
        except Exception as error:  # noqa: BLE001 — a stale or refused row is reported, the rest goes on
            print(f"   отказ: {label}: {error}")
            continue
        _audit("references.tech_record_edited", ACTOR, now, {
            "record_id": str(row.id), "tab": "ter", "fields": [COLUMN], "reason": REASON}, uow)
        if not dry:
            uow.commit()
        written += 1
        print(f"   {'проверено' if dry else 'записано'}: {label}: «{old}» → «{new}»")
print(f"== {'проверено' if dry else 'записано'} {written}")
