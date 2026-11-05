"""What of the equipment is not linked by id — read-only (owner 05.11.2026: ambiguous links are not guessed, they are
listed for the owner).

    ERP_DATABASE_URL=... python -m app.modules.references.equipment_links_report [--json]

Prints the БД.ОП cells («Машина» / «Инструмент» / «Апликаторы / модули») whose text names no model, several models
or only supporting equipment; matrices whose «Для инструмента» names no unit; «Оборудование» rows of the card templates
migration 0171 could not resolve; models without units, models whose units disagree on name / article / maker, and
roles («операционное» / «обеспечивающее») the owner has not confirmed. The same as GET
/api/references/equipment-links/report. Writes nothing (the transaction is rolled back). Exit code 2 — no
ERP_DATABASE_URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REASONS = {"ambiguous": "несколько моделей", "unmatched": "ни одной модели", "supporting": "обеспечивающее оборудование"}
ROLES = {"operational": "операционное", "supporting": "обеспечивающее"}


def format_report(report: dict) -> str:
    counts = report["counts"]
    lines = [
        f"Оборудование: единиц {counts['units']}, моделей {counts['models']}, связей операций {counts['operation_links']}",
        f"БД.ОП — не связано ячеек: {counts['operations_unresolved']}",
    ]
    lines += [f"  {item['number']} · {item['column']}: «{item['text']}» — {REASONS[item['reason']]}"
              + (": " + "; ".join(candidate["display"] for candidate in item["candidates"]) if item["candidates"] else "")
              for item in report["operations"]]
    lines.append(f"«Для инструмента» — не связано: {counts['tools_unresolved']}")
    lines += [f"  {item['inventory']}: «{item['text']}» — {REASONS[item['reason']]}" for item in report["tools"]]
    lines.append(f"Шаблоны карт, «Оборудование» — не связано: {counts['templates_unresolved']}")
    lines += [f"  {item['template_id']} #{item['position']}: «{item['tool_name']}» {item['inventory_number']}"
              f" — {REASONS[item['reason']]}" for item in report["templates"]]
    lines.append(f"Модели без единиц (на них ссылаются операции): {counts['models_without_units']}")
    lines += [f"  {item['display']} — операций {item['operations']}" for item in report["models_without_units"]]
    lines.append(f"Модели, у единиц которых разные наименование / артикул / производитель: {counts['models_units_disagree']}")
    lines += [f"  {item['display']}: {', '.join(item['inventory_numbers'])}" for item in report["models_units_disagree"]]
    lines.append(f"Роль не подтверждена владельцем: {counts['roles_unconfirmed']}")
    lines += [f"  {ROLES[item['role']]}: {item['display'] or item['name']} ({', '.join(item['inventory_numbers']) or 'без единиц'})"
              for item in report["roles_unconfirmed"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Несвязанные по ID ссылки на оборудование (только чтение)")
    parser.add_argument("--json", action="store_true", help="вывести отчёт как JSON")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine, build_references_uow_factory
    from .application import equipment_links_report
    engine = build_engine(database_url)
    try:
        report = equipment_links_report(build_references_uow_factory(engine)())
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
