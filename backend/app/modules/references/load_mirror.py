"""REF-MIRROR-LOAD CLI: SUPPLYMAP mirror `_local/db/БД.ПРОВ.tsv` / `БД.МАТ.tsv` → references snapshot.

    ERP_DATABASE_URL=... python -m app.modules.references.load_mirror --dry-run
    ERP_DATABASE_URL=... python -m app.modules.references.load_mirror [--source ../SUPPLYMAP]

Goes through `replace_snapshot`; only the tabs whose file is present are replaced, every other tab of the
snapshot (and its schema version / source date) stays as it is. Exit code: 0 — done; 2 — a file is missing,
unreadable or not a valid table, or ERP_DATABASE_URL is not set.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.shared.identity import Actor

from .application import ReplaceSnapshotCommand, TechRecordInput, UnitOfWork, replace_snapshot
from .domain import ReferenceValidationError, TechOperationTab
from .technology import SHEET_TABLES, TECH_SNAPSHOT_SCHEMA_VERSION, import_sheet_table, parse_mirror_tsv

ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")
MIRROR_SUBDIR = Path("_local") / "db"


class MirrorLoadError(Exception):
    pass


@dataclass(frozen=True)
class MirrorLoadResult:
    counts: dict[str, int]  # tab → rows read from the mirror
    current: dict[str, int]  # tab → rows in the snapshot before the load
    written: bool


def default_source() -> Path:
    """SUPPLYMAP_SOURCE_REPOSITORY, else the nearest `SUPPLYMAP` sibling of this checkout or any parent
    (a Desktop worktree sits under `.claude/worktrees/`, so plain `../SUPPLYMAP` would miss it)."""
    override = os.getenv("SUPPLYMAP_SOURCE_REPOSITORY", "").strip()
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        if (parent.parent / "SUPPLYMAP").is_dir():
            return parent.parent / "SUPPLYMAP"
    return Path("..") / "SUPPLYMAP"


def read_mirror(source: Path) -> dict[str, list[TechRecordInput]]:
    """Both mirror files are required; a missing one is an error, not a silent empty table."""
    tables: dict[str, list[TechRecordInput]] = {}
    for tab, sheet in SHEET_TABLES.items():
        path = Path(source) / MIRROR_SUBDIR / f"{sheet}.tsv"
        if not path.is_file():
            raise MirrorLoadError(f"Нет файла зеркала {path}: задайте --source или SUPPLYMAP_SOURCE_REPOSITORY")
        try:
            table = parse_mirror_tsv(path.read_text(encoding="utf-8"))
            if not table["header"]:
                raise ValueError("нет строки шапки")
            records = import_sheet_table(tab, table)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise MirrorLoadError(f"{path.name}: {error}") from error
        tables[tab] = [TechRecordInput(
            tab=TechOperationTab(item.tab), display_text=item.display_text, search_text=item.search_text,
            source_sheet=item.source_sheet, sort_key=item.sort_key, fields=item.fields,
        ) for item in records]
    return tables


def load_mirror(source: Path, uow: UnitOfWork, *, dry_run: bool, now: datetime) -> MirrorLoadResult:
    tables = read_mirror(source)
    with uow:
        metadata = uow.tech_operations.metadata()
        current = {tab: len(uow.tech_operations.list(tab=TechOperationTab(tab), query="", limit=100_000)) for tab in tables}
    counts = {tab: len(records) for tab, records in tables.items()}
    if not dry_run:
        try:
            replace_snapshot(ReplaceSnapshotCommand(
                schema_version=TECH_SNAPSHOT_SCHEMA_VERSION,
                source_updated_at=metadata["source_updated_at"] if metadata else None,
                records=tuple(record for records in tables.values() for record in records),
                actor=ACTOR, now=now,
                declared_tabs=tuple(TechOperationTab(tab) for tab in tables),
                keep_other_tabs=True,
            ), uow)
        except ReferenceValidationError as error:
            raise MirrorLoadError(str(error)) from error
    return MirrorLoadResult(counts=counts, current=current, written=not dry_run)


def format_report(result: MirrorLoadResult) -> str:
    lines = ["Загрузка зеркала БД.ПРОВ / БД.МАТ: " + ("записано" if result.written else "пробный прогон, ничего не записано")]
    for tab, count in result.counts.items():
        lines.append(f"  {SHEET_TABLES[tab]}: строк в зеркале {count}, сейчас в снимке {result.current[tab]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Загрузка БД.ПРОВ и БД.МАТ из зеркала SUPPLYMAP в снимок справочников (REF-MIRROR-LOAD)")
    parser.add_argument("--source", default=None, help="корень репозитория SUPPLYMAP (по умолчанию SUPPLYMAP_SOURCE_REPOSITORY или соседняя папка)")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать строки, без записи в базу")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("Задайте ERP_DATABASE_URL (та же база, что у приложения)", file=sys.stderr)
        return 2
    from app.database import build_engine, build_references_uow_factory
    engine = build_engine(database_url)
    try:
        result = load_mirror(Path(args.source) if args.source else default_source(),
                             build_references_uow_factory(engine)(), dry_run=args.dry_run, now=datetime.now(timezone.utc))
    except MirrorLoadError as error:
        print(f"Зеркало не читается: {error}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print(format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
