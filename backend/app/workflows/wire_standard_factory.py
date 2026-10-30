"""БД.ПРОВ: завод, попавший в «Стандарт», переносится в «Производители» (owner 13.10.2026).

    ERP_DATABASE_URL=... python -m app.workflows.wire_standard_factory --dry-run   # «было → стало» по строкам, без записи
    ERP_DATABASE_URL=... python -m app.workflows.wire_standard_factory --apply     # записать одной транзакцией + аудит

После разделения «Стандарт / производитель» (`wire_standard_makers`) у ~10 строк в «Стандарт» остался завод в скобках:
«ГОСТ (Breks, Брянск)», «ГОСТ 3581-011-39793330-2009 (Паритет)»… Правило — таблица `KNOWN` ниже: значение ячейки
«Стандарт» целиком → (стандарт без завода, заводы). Ячейка меняется, только если в ней ещё ровно известный текст (без
учёта пробелов по краям); правленая руками ячейка не трогается. Заводы дописываются в «Производители» (уже названные
не повторяются). Записанные строки помечаются `edited_in_erp`, на каждую — событие аудита. Второй запуск ничего не меняет.
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
from app.modules.references.wire_makers import MAKERS_FIELD, STANDARD_FIELD, join_makers, parse_makers
from app.shared.clock import moscow_now
from app.shared.identity import Actor

# «Стандарт» as the split left it → (the standard alone, the factories). Found on the mirror БД.ПРОВ (13.10.2026).
KNOWN: dict[str, tuple[str, tuple[str, ...]]] = {
    "ГОСТ (Breks, Брянск)": ("ГОСТ", ("Breks (Брянск)",)),
    "ГОСТ 7399-97 (BREKS, Брянск)": ("ГОСТ 7399-97", ("Breks (Брянск)",)),
    "ГОСТ (Калужский кабельный завод)": ("ГОСТ", ("Калужский кабельный завод",)),
    "ГОСТ (Калужский кабельный завод, П7513)": ("ГОСТ", ("Калужский кабельный завод (П7513)",)),
    "ГОСТ 3581-011-39793330-2009 (Паритет)": ("ГОСТ 3581-011-39793330-2009", ("Паритет",)),
    "ГОСТ 3574-010-39793330-2009 (Паритет ParLan); IEC 61156-6; TIA/EIA-568-A": (
        "ГОСТ 3574-010-39793330-2009; IEC 61156-6; TIA/EIA-568-A", ("Паритет",)),
    "ГОСТ-based (Камкабель/ОКБ КП)": ("ГОСТ", ("Камкабель", "ОКБ КП")),
}


@dataclass
class Plan:
    rows: list[tuple[TechOperationRecord, dict[str, str]]] = field(default_factory=list)  # (record, new fields)
    total: int = 0

    @property
    def empty(self) -> bool:
        return not self.rows


def moved(fields: dict[str, str]) -> dict[str, str] | None:
    """The row's cells with the factory moved, or None when «Стандарт» does not hold a known text."""
    rule = KNOWN.get(str(fields.get(STANDARD_FIELD) or "").strip())
    if rule is None:
        return None
    standard, factories = rule
    makers = join_makers(parse_makers(fields.get(MAKERS_FIELD) or "") + list(factories))
    return {**fields, STANDARD_FIELD: standard, MAKERS_FIELD: makers}


def build_plan(records: list[TechOperationRecord]) -> Plan:
    plan = Plan(total=len(records))
    for record in records:
        fields = moved(dict(record.fields))
        if fields is not None and fields != dict(record.fields):
            plan.rows.append((record, fields))
    return plan


def format_plan(plan: Plan, *, applied: bool) -> str:
    lines = [f"БД.ПРОВ: записей {plan.total}, завод в «Стандарт» у {len(plan.rows)} ({'перенесено' if applied else 'будет перенесено'})"]
    for record, fields in plan.rows:
        lines.append(f"  {record.display_text}: Стандарт «{record.fields.get(STANDARD_FIELD, '')}» → «{fields[STANDARD_FIELD]}» · "
                     f"Производители «{record.fields.get(MAKERS_FIELD, '')}» → «{fields[MAKERS_FIELD]}»")
    if plan.empty:
        lines.append("Делать нечего: ни в одной ячейке «Стандарт» нет известного текста с заводом.")
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
            _audit("references.wire_standard_factory_moved", actor, now, {
                "record_id": str(record.id), "mark": record.display_text,
                "standard": [record.fields.get(STANDARD_FIELD, ""), fields[STANDARD_FIELD]],
                "makers": [record.fields.get(MAKERS_FIELD, ""), fields[MAKERS_FIELD]],
            }, uow)
        uow.commit()
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="БД.ПРОВ: перенести завод из «Стандарт» в «Производители».")
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
        plan = run(build_references_uow_factory(engine)(), Actor(id=uuid.uuid4(), name="завод из стандарта", department=""),
                   moscow_now(), apply=args.apply)
    finally:
        engine.dispose()
    print(format_plan(plan, applied=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
