"""БД.ПРОВ: «Стандарт / производитель» → «Стандарт» + «Производители» (owner 12.10.2026).

    ERP_DATABASE_URL=... python -m app.workflows.wire_standard_makers --dry-run   # «было → стало» по строкам, без записи
    ERP_DATABASE_URL=... python -m app.workflows.wire_standard_makers --apply     # записать одной транзакцией + аудит

Правило — `references/wire_makers.py`. Идемпотентно: строка без старой ячейки не трогается; уже заполненные «Стандарт» и
«Производители» не затираются (производители дополняются). Записанные строки помечаются `edited_in_erp`.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime

from app.modules.references.application import UnitOfWork, _audit, _search_text
from app.modules.references.domain import CABLE_TABS, TechOperationRecord, TechOperationTab
from app.modules.references.wire_makers import LEGACY_FIELD, MAKERS_FIELD, STANDARD_FIELD, split_fields
from app.shared.clock import moscow_now
from app.shared.identity import Actor


@dataclass
class Plan:
    rows: list[tuple[TechOperationRecord, dict[str, str]]] = field(default_factory=list)  # (record, new fields)
    total: int = 0
    to_standard: int = 0
    to_makers: int = 0

    @property
    def empty(self) -> bool:
        return not self.rows


def build_plan(records: list[TechOperationRecord]) -> Plan:
    plan = Plan(total=len(records))
    for record in records:
        fields = split_fields(record.fields)
        if fields == dict(record.fields):
            continue
        plan.rows.append((record, fields))
        plan.to_standard += bool(fields.get(STANDARD_FIELD))
        plan.to_makers += bool(fields.get(MAKERS_FIELD))
    return plan


def format_plan(plan: Plan, *, applied: bool) -> str:
    lines = [f"БД.ПРОВ: записей {plan.total}, к записи: {len(plan.rows)} (из них со старой ячейкой «{LEGACY_FIELD}» — {sum(LEGACY_FIELD in r.fields for r, _ in plan.rows)}) "
             f"({'сделано' if applied else 'будет'})"]
    for record, fields in plan.rows:
        lines.append(f"  {record.display_text}: «{record.fields.get(LEGACY_FIELD, '')}» → "
                     f"Стандарт «{fields.get(STANDARD_FIELD, '')}» · Производители «{fields.get(MAKERS_FIELD, '')}»")
    lines.append(f"Итого: со стандартом {plan.to_standard}, с производителями {plan.to_makers}, "
                 f"без производителей {len(plan.rows) - plan.to_makers}, без стандарта {len(plan.rows) - plan.to_standard}")
    if plan.empty:
        lines.append("Делать нечего: все записи уже разделены.")
    elif not applied:
        lines.append("Проверка без записи. Записать — та же команда с --apply.")
    return "\n".join(lines)


def run(uow: UnitOfWork, actor: Actor, now: datetime, *, apply: bool) -> Plan:
    with uow:
        records = list(uow.tech_operations.list(tab=CABLE_TABS, query="", limit=100_000))
        plan = build_plan(records)
        if not apply or plan.empty:
            return plan
        for record, fields in plan.rows:
            updated = replace(record, fields=fields, edited_in_erp=True)
            uow.tech_operations.save(replace(updated, normalized_search=_search_text(updated)))
            _audit("references.wire_standard_makers_split", actor, now, {
                "record_id": str(record.id), "mark": record.display_text, "before": record.fields.get(LEGACY_FIELD, ""),
                "standard": fields.get(STANDARD_FIELD, ""), "makers": fields.get(MAKERS_FIELD, ""),
            }, uow)
        uow.commit()
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="БД.ПРОВ: разделить «Стандарт / производитель» на «Стандарт» и «Производители».")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="только напечатать «было → стало» (по умолчанию)")
    mode.add_argument("--apply", action="store_true", help="записать одной транзакцией с аудитом")
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
        plan = run(build_references_uow_factory(engine)(), Actor(id=uuid.uuid4(), name="разделение стандарта и производителей", department=""),
                   moscow_now(), apply=args.apply)
    finally:
        engine.dispose()
    print(format_plan(plan, applied=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
