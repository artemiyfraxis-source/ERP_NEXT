"""«Оболочки» dry run: what the classifier does with the БД.МАТ rows, writing nothing.

    python -m app.modules.references.coverings_report                 (the bundled mirror)
    ERP_DATABASE_URL=... python -m app.modules.references.coverings_report   (the live snapshot, read only)

Prints the rows per group, the «Прочее» rows and, per row, what the screen takes from the name (the sheet columns do
not hold it) and what is still empty. Sheet values are never changed: the parsing is done on read, the only stored
field is the manual «Раздел оболочек»."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .coverings import GROUP_ORDER, GROUP_TITLES, build
from .domain import TechOperationTab
from .load_mirror import default_source, read_mirror

WANTED = {"tut": ("before_mm", "after_mm", "ratio", "material", "temp_min_c", "color"),
          "ttk": ("before_mm", "after_mm", "ratio", "material", "temp_min_c", "color"),
          "braid": ("nominal_mm", "min_mm", "material", "temp_min_c", "color"),
          "corrugated": ("inner_mm", "outer_mm", "split", "material", "temp_min_c"),
          "other": ()}


def load_rows(source: Path | None) -> list[tuple[str, dict[str, str]]]:
    url = os.getenv("ERP_DATABASE_URL", "").strip()
    if url:
        from app.database import build_engine, build_references_uow_factory
        engine = build_engine(url)
        try:
            with build_references_uow_factory(engine)() as uow:
                return [(str(item.id), dict(item.fields)) for item in uow.tech_operations.list(tab=TechOperationTab.MATERIAL, query="", limit=10_000)]
        finally:
            engine.dispose()
    records = read_mirror(source or default_source()).tables["mat"]
    return [(str(index), dict(item.fields)) for index, item in enumerate(records)]


def report(records: list[tuple[str, dict[str, str]]]) -> str:
    result = build(records)
    lines = [f"Всего в БД.МАТ: {len(records)}; оболочек: {len(result['rows'])}; не оболочки (остаются в «Материалах»): {len(records) - len(result['rows'])}"]
    for group in GROUP_ORDER:
        rows = [row for row in result["rows"] if row["group"] == group]
        lines.append(f"\n{GROUP_TITLES[group]} · {len(rows)}")
        for row in rows:
            empty = [key for key in WANTED[group] if row[key] in (None, "")]
            taken = f" [из названия: {', '.join(row['notes'])}]" if row["notes"] else ""
            gaps = f" — пусто: {', '.join(empty)}" if empty else ""
            lines.append(f"  {row['name']}{taken}{gaps}")
    lines.append("\nЗаписи в базу: нет (dry run). Значения листа не меняются.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Оболочки: сухой прогон классификатора по БД.МАТ")
    parser.add_argument("--source", default=None, help="папка с .tsv зеркала (по умолчанию — снимок в репозитории)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(report(load_rows(Path(args.source) if args.source else None)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
