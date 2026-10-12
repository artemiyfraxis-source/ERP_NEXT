"""БД.ОБ cleanup (owner 12.10.2026): `python -m app.workflows.equipment_cleanup [--apply]`.

- the type «Оборудование (ПР)» is a Google-era leftover: those rows become «Оборудование» (inventory numbers stay);
- the «.X» rows (article «…(R)», a calculation stand-in of a model) go away; first every БД.ОП cell that names one
  («T21(R)/Станция паяльная») is re-pointed to the model's own text («T21/Станция паяльная», the «Для базы» of its
  numbered units). An X row whose model has no numbered unit is kept and reported — nothing would be left to point to.

Without `--apply` it only prints the plan; with it everything is written in ONE transaction, each change audited and
marked `edited_in_erp` (a mirror reload keeps them). Idempotent: a second run finds nothing to do. Issued ЭТК
snapshots and the techcard templates are not touched: the autoplan reads the machine family from the inventory prefix
(`ОБ.071.X` → `ОБ.071`), so their old text still resolves.

    ERP_DATABASE_URL=... python -m app.workflows.equipment_cleanup            # plan
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

from app.modules.references.application import UnitOfWork, _audit, _search_text
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.equipment import field as pick
from app.modules.references.technology import RETIRED_EQUIPMENT_TYPES, repoint_calc_unit, retired_unit
from app.shared.clock import moscow_now
from app.shared.identity import Actor

BASE_KEY = "Для базы"
_CALC_BASE = re.compile(r"^[^/]*\(R\)\s*/")


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


def _is_retired(record: TechOperationRecord) -> bool:
    """«.X» number or «(R)» article; a record imported without sheet columns is known by its «Для базы» text."""
    return retired_unit(pick(record.fields, "Инв. номер"), pick(record.fields, "Артикул"))         or bool(_CALC_BASE.match(_base(record)))


def _model_text(record: TechOperationRecord) -> str:
    """«T21(R)/Станция» → «T21/Станция»: what the numbered units of the model carry in «Для базы»."""
    return repoint_calc_unit(_base(record))


def _swap(piece: str, replacements: dict[str, str]) -> str:
    """A cell holds one reference or several split by «;» / line breaks; only a whole reference is re-pointed."""
    stripped = piece.strip()
    return piece.replace(stripped, replacements[stripped]) if stripped in replacements else piece


def build_plan(equipment: list[TechOperationRecord], operations: list[TechOperationRecord]) -> Plan:
    plan = Plan()
    live = [item for item in equipment if not _is_retired(item)]
    live_bases = {_base(item).casefold() for item in live}
    plan.retyped = [item for item in equipment if pick(item.fields, "Тип", "obType") in RETIRED_EQUIPMENT_TYPES
                    and not _is_retired(item)]
    replacements: dict[str, str] = {}
    for item in equipment:
        if not _is_retired(item):
            continue
        old, new = _base(item), _model_text(item)
        if new.casefold() not in live_bases:
            plan.kept.append((item, f"у модели «{new}» нет пронумерованных единиц — указывать не на что"))
            continue
        plan.deleted.append(item)
        if old and old != new:
            replacements[old] = new
    for operation in operations:
        for name, value in operation.fields.items():
            text = str(value or "")
            after = "".join(_swap(piece, replacements) for piece in re.split(r"([;\n])", text))
            if after != text:
                plan.repointed.append((operation, name, text, after))
    return plan


def format_plan(plan: Plan, *, applied: bool) -> str:
    done = "сделано" if applied else "будет"
    lines = [f"Тип «Оборудование (ПР)» → «Оборудование»: {done} у {len(plan.retyped)} записей"]
    lines += [f"  {pick(item.fields, 'Инв. номер') or '—'}  {item.display_text}" for item in plan.retyped]
    lines.append(f"Операции БД.ОП, ссылка «…(R)/…» → модель: {len(plan.repointed)} ячеек")
    lines += [f"  {op.display_text} · {name}: «{before}» → «{after}»" for op, name, before, after in plan.repointed]
    lines.append(f"Строки «.X» удалить: {len(plan.deleted)}")
    lines += [f"  {pick(item.fields, 'Инв. номер')}  {item.display_text}" for item in plan.deleted]
    lines += [f"Оставлена {pick(item.fields, 'Инв. номер')}: {why}" for item, why in plan.kept]
    if plan.empty:
        lines.append("Делать нечего: база уже приведена в порядок.")
    elif not applied:
        lines.append("Проверка без записи. Записать — та же команда с --apply.")
    return "\n".join(lines)


def _edited(record: TechOperationRecord, fields: dict[str, str]) -> TechOperationRecord:
    updated = replace(record, fields=fields, edited_in_erp=True)
    return replace(updated, normalized_search=_search_text(updated))


def run(uow: UnitOfWork, actor: Actor, now: datetime, *, apply: bool) -> Plan:
    with uow:
        equipment = list(uow.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=100_000))
        operations = list(uow.tech_operations.list(tab=TechOperationTab.OPERATION, query="", limit=100_000))
        plan = build_plan(equipment, operations)
        if not apply or plan.empty:
            return plan
        for item in plan.retyped:
            fields = {**item.fields, **{key: "Оборудование" for key in ("Тип", "obType") if key in item.fields}}
            uow.tech_operations.save(_edited(item, fields))
            _audit("references.equipment_retyped", actor, now, {"record_id": str(item.id), "inventory": pick(item.fields, "Инв. номер")}, uow)
        changed: dict[uuid.UUID, tuple[TechOperationRecord, dict[str, str]]] = {}
        for operation, name, _before, after in plan.repointed:
            record, fields = changed.setdefault(operation.id, (operation, dict(operation.fields)))
            fields[name] = after
        for record, fields in changed.values():
            uow.tech_operations.save(_edited(record, fields))
            _audit("references.operation_repointed", actor, now, {"record_id": str(record.id), "number": pick(record.fields, "Номер", "opNumber")}, uow)
        for item in plan.deleted:
            uow.tech_operations.delete(item.id)
            _audit("references.equipment_removed", actor, now, {"record_id": str(item.id), "inventory": pick(item.fields, "Инв. номер")}, uow)
        uow.commit()
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="БД.ОБ: убрать «Оборудование (ПР)» и строки «.X», перенаправить операции БД.ОП. "
                                                 "Без --apply только печатает план.")
    parser.add_argument("--apply", action="store_true", help="записать (одной транзакцией); без флага — проверка без записи")
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
