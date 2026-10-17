"""БД.ОБ cleanup (owner 12.10.2026): `python -m app.workflows.equipment_cleanup --dry-run | --apply`.

- the type «Оборудование (ПР)» is a Google-era leftover: those rows become «Оборудование» (inventory numbers stay);
- the «.X» rows (article «…(R)», a calculation stand-in of a model) go away; first every БД.ОП cell that names one
  («T21(R)/Станция паяльная») is re-pointed to the model's own text («T21/Станция паяльная», the «Для базы» of its
  numbered units). An X row is deleted only when nothing references it after that; otherwise it is kept and the
  reason printed: its model has no numbered unit (nothing to point to), a БД.ОП cell still names it, a matrix names
  it in «Для инструмента», it alone owns a crimp code of БД.ТЕР, or a warehouse lot is linked to it.

`--dry-run` (the default) only prints the plan; `--apply` writes everything in ONE transaction, each change audited
(with its before/after) and marked `edited_in_erp` (a mirror reload keeps them), plus one summary audit event.
Idempotent: a second run finds nothing to do. Issued ЭТК snapshots and the techcard templates are not touched: the
autoplan reads the machine family from the inventory prefix (`ОБ.071.X` → `ОБ.071`), so their old text still resolves.

    ERP_DATABASE_URL=... python -m app.workflows.equipment_cleanup --dry-run
    ERP_DATABASE_URL=... python -m app.workflows.equipment_cleanup --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Mapping

from app.modules.references.application import UnitOfWork, _audit, _search_text
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.equipment import CODE_KEY, INV_KEY, TOOL_KEY, inv_key
from app.modules.references.equipment import field as pick
from app.modules.references.technology import RETIRED_EQUIPMENT_TYPES, repoint_calc_unit, retired_unit
from app.shared.clock import moscow_now
from app.shared.identity import Actor

BASE_KEY = "Для базы"
_CALC_BASE = re.compile(r"^[^/]*\(R\)\s*/")
_SPLIT = re.compile(r"([;\n])")


@dataclass
class Plan:
    retyped: list[TechOperationRecord] = field(default_factory=list)
    deleted: list[TechOperationRecord] = field(default_factory=list)
    kept: list[tuple[TechOperationRecord, str]] = field(default_factory=list)  # (X row, why it stays)
    repointed: list[tuple[TechOperationRecord, str, str, str]] = field(default_factory=list)  # (op, field, before, after)

    @property
    def empty(self) -> bool:
        return not (self.retyped or self.deleted or self.repointed)


def _base(record: TechOperationRecord) -> str:
    """«Для базы»; a record imported without sheet columns carries it as its display text."""
    return pick(record.fields, BASE_KEY, "baseValue") or record.display_text


def _key(text: str) -> str:
    """A reference compared as the reader sees it: case, extra spaces and spaces around «/» do not matter."""
    return re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", str(text or ""))).strip().casefold()


def _is_retired(record: TechOperationRecord) -> bool:
    """«.X» number or «(R)» article; a record imported without sheet columns is known by its «Для базы» text."""
    return retired_unit(pick(record.fields, INV_KEY), pick(record.fields, "Артикул")) or bool(_CALC_BASE.match(_base(record)))


def _model_text(record: TechOperationRecord) -> str:
    """«T21(R)/Станция» → «T21/Станция»: what the numbered units of the model carry in «Для базы»."""
    return repoint_calc_unit(_base(record))


def _pieces(text: str) -> list[str]:
    """A cell holds one reference or several split by «;» / line breaks (separators kept, so they join back)."""
    return _SPLIT.split(text)


def _swap(piece: str, replacements: dict[str, str]) -> str:
    """Only a whole reference is re-pointed; the spacing around it stays."""
    stripped = piece.strip()
    return piece.replace(stripped, replacements[_key(stripped)]) if stripped and _key(stripped) in replacements else piece


def _references(item: TechOperationRecord, equipment: list[TechOperationRecord],
                operations_after: list[tuple[TechOperationRecord, str, str]], live_bases: set[str],
                stock_links: Mapping[uuid.UUID, int]) -> list[str]:
    """What still points at the X row once the operations are re-pointed — each one keeps the row."""
    found: list[str] = []
    old, inventory = _key(_base(item)), inv_key(pick(item.fields, INV_KEY))
    names = {old} if old not in live_bases else set()  # the same text on a numbered unit resolves to that unit
    for operation, name, text in operations_after:
        for piece in _pieces(text):
            if piece.strip() and (_key(piece) in names or (inventory and inv_key(piece) == inventory)):
                found.append(f"операция {pick(operation.fields, 'Номер', 'opNumber') or operation.display_text} · {name}")
    for other in equipment:
        if other.id != item.id and inventory and inv_key(pick(other.fields, TOOL_KEY)) == inventory:
            found.append(f"матрица {pick(other.fields, INV_KEY) or other.display_text} («Для инструмента»)")
    code = pick(item.fields, CODE_KEY).upper()
    if code and not any(pick(other.fields, CODE_KEY).upper() == code for other in equipment
                        if other.id != item.id and not _is_retired(other)):
        found.append(f"код обжима {code} есть только у неё")
    if stock_links.get(item.id):
        found.append(f"привязок склада: {stock_links[item.id]}")
    return found


def build_plan(equipment: list[TechOperationRecord], operations: list[TechOperationRecord],
               stock_links: Mapping[uuid.UUID, int] | None = None) -> Plan:
    plan = Plan()
    live_bases = {_key(_base(item)) for item in equipment if not _is_retired(item)}
    candidates: list[TechOperationRecord] = []
    replacements: dict[str, str] = {}
    for item in equipment:
        if not _is_retired(item):
            continue
        old, new = _base(item), _model_text(item)
        if _key(new) not in live_bases:
            plan.kept.append((item, f"у модели «{new}» нет пронумерованных единиц — указывать не на что"))
            continue
        candidates.append(item)
        if old and _key(old) != _key(new):
            replacements[_key(old)] = new
    operations_after: list[tuple[TechOperationRecord, str, str]] = []
    for operation in operations:
        for name, value in operation.fields.items():
            text = str(value or "")
            after = "".join(_swap(piece, replacements) for piece in _pieces(text))
            if after != text:
                plan.repointed.append((operation, name, text, after))
            operations_after.append((operation, name, after))
    for item in candidates:
        found = _references(item, equipment, operations_after, live_bases, stock_links or {})
        if found:
            plan.kept.append((item, "на неё ещё ссылаются: " + "; ".join(found)))
        else:
            plan.deleted.append(item)
    gone = {item.id for item in plan.deleted}  # an X row that stays is retyped too: the type must not survive
    plan.retyped = [item for item in equipment
                    if pick(item.fields, "Тип", "obType") in RETIRED_EQUIPMENT_TYPES and item.id not in gone]
    return plan


def format_plan(plan: Plan, *, applied: bool) -> str:
    done = "сделано" if applied else "будет"
    lines = [f"Тип «Оборудование (ПР)» → «Оборудование»: {done} у {len(plan.retyped)} записей"]
    lines += [f"  {pick(item.fields, INV_KEY) or '—'}  {item.display_text}" for item in plan.retyped]
    lines.append(f"Операции БД.ОП, ссылка «…(R)/…» → модель: {len(plan.repointed)} ячеек"
                 f" в {len({op.id for op, *_rest in plan.repointed})} операциях")
    lines += [f"  {op.display_text} · {name}: «{before}» → «{after}»" for op, name, before, after in plan.repointed]
    lines.append(f"Строки «.X» удалить: {len(plan.deleted)}")
    lines += [f"  {pick(item.fields, INV_KEY)}  {item.display_text}" for item in plan.deleted]
    lines += [f"Оставлена {pick(item.fields, INV_KEY) or item.display_text}: {why}" for item, why in plan.kept]
    if plan.empty:
        lines.append("Делать нечего: база уже приведена в порядок.")
    elif not applied:
        lines.append("Проверка без записи (--dry-run). Записать — та же команда с --apply.")
    return "\n".join(lines)


def _edited(record: TechOperationRecord, fields: dict[str, str]) -> TechOperationRecord:
    updated = replace(record, fields=fields, edited_in_erp=True)
    return replace(updated, normalized_search=_search_text(updated))


def run(uow: UnitOfWork, actor: Actor, now: datetime, *, apply: bool) -> Plan:
    with uow:
        equipment = list(uow.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=100_000))
        operations = list(uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000))
        stock_links = {item.id: len(uow.stock_links.list(record_id=item.id, limit=10_000))
                       for item in equipment if _is_retired(item)}
        plan = build_plan(equipment, operations, stock_links)
        if not apply or plan.empty:
            return plan
        for item in plan.retyped:
            fields = {**item.fields, **{key: "Оборудование" for key in ("Тип", "obType") if key in item.fields}}
            uow.tech_operations.save(_edited(item, fields))
            _audit("references.equipment_retyped", actor, now, {
                "record_id": str(item.id), "inventory": pick(item.fields, INV_KEY),
                "before": pick(item.fields, "Тип", "obType"), "after": "Оборудование"}, uow)
        changed: dict[uuid.UUID, tuple[TechOperationRecord, dict[str, str], list[dict[str, str]]]] = {}
        for operation, name, before, after in plan.repointed:
            _record, fields, cells = changed.setdefault(operation.id, (operation, dict(operation.fields), []))
            fields[name] = after
            cells.append({"field": name, "before": before, "after": after})
        for record, fields, cells in changed.values():
            uow.tech_operations.save(_edited(record, fields))
            _audit("references.operation_repointed", actor, now, {
                "record_id": str(record.id), "number": pick(record.fields, "Номер", "opNumber"), "cells": cells}, uow)
        for item in plan.deleted:
            uow.tech_operations.delete(item.id)
            _audit("references.equipment_removed", actor, now, {
                "record_id": str(item.id), "inventory": pick(item.fields, INV_KEY), "display_text": item.display_text,
                "fields": dict(item.fields)}, uow)
        _audit("references.equipment_cleanup", actor, now, {
            "retyped": len(plan.retyped), "operations": len(changed), "cells": len(plan.repointed),
            "deleted": len(plan.deleted), "kept": [pick(item.fields, INV_KEY) for item, _why in plan.kept]}, uow)
        uow.commit()
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="БД.ОБ: убрать «Оборудование (ПР)» и строки «.X», перенаправить операции БД.ОП.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только напечатать план (так и без флагов)")
    mode.add_argument("--apply", action="store_true", help="записать одной транзакцией, каждое изменение — в журнал")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine, build_references_uow_factory

    engine = build_engine(url)
    try:
        plan = run(build_references_uow_factory(engine)(), Actor(id=uuid.uuid4(), name="чистка оборудования", department=""),
                   moscow_now(), apply=args.apply)
    finally:
        engine.dispose()
    print(format_plan(plan, applied=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
