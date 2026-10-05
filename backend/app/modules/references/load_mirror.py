"""REF-MIRROR-LOAD / REF-MIRROR-ALL CLI: the «Технология» bases mirror (`*.tsv`) → references snapshot.

    ERP_DATABASE_URL=... python -m app.modules.references.load_mirror --dry-run
    ERP_DATABASE_URL=... python -m app.modules.references.load_mirror [--source <папка>]

The default source is the snapshot bundled with the repo, `backend/data/reference-mirror/` (the SUPPLYMAP mirror
`_local/db` as of 29.09.2026, copied once, owner 05.10: nothing in the ERP needs the sibling SUPPLYMAP checkout).
`--source` takes a folder with the `.tsv` files, or a checkout that keeps them under `_local/db`.

Reads the bases of the «Технология» workbook: БД.ОБ, БД.ОП, БД.ТЕР, БД.КОАКС (records built exactly as the GAS
exporter builds them — `sheet_snapshot_records` → `import_supplymap_snapshot`, the same path as
`PUT /tech-operations/supplymap-snapshot`), БД.ПРОВ and БД.МАТ (header-keyed tables), all required; `СПР.КАБ.tsv`
(REF-SPRKAB: coax D1–D3 and AWG tables, view-only) is optional, but a present file that does not parse is an error.
Everything goes through one `replace_snapshot` transaction: the loaded tabs are replaced whole, a tab whose file is
absent (only СПР.КАБ may be) stays as it is. Records edited or created in ERP (`edited_in_erp`) are never overwritten
or deleted (as in SUPPLYMAP, where the base sheet is the single source and portal edits persist); the report counts
the mirror rows skipped because of that. The snapshot's source date is the mirror's latest `pulledAt`
(`_meta.json`). Exit code: 0 — done; 2 — a file is missing, unreadable or not a valid table, or ERP_DATABASE_URL is
not set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.shared.identity import Actor

from .application import ReplaceSnapshotCommand, TechRecordInput, UnitOfWork, replace_snapshot_detailed
from .domain import ReferenceValidationError, TechOperationTab
from .technology import (
    KAB_TABLES, SHEET_TABLES, SPR_KAB_SHEET, TECH_SHEETS, TECH_SNAPSHOT_SCHEMA_VERSION, import_sheet_table,
    import_supplymap_snapshot, parse_mirror_rows, parse_mirror_tsv, parse_spr_kab_tsv, sheet_snapshot_records,
)

ACTOR = Actor(id=UUID(int=0), name="ERP Integration", department="System")
MIRROR_SUBDIR = Path("_local") / "db"
TAB_LABELS = {**{tab: sheet for tab, (sheet, _row) in TECH_SHEETS.items()}, **SHEET_TABLES, **KAB_TABLES}


class MirrorLoadError(Exception):
    pass


@dataclass(frozen=True)
class MirrorSnapshot:
    tables: dict[str, list[TechRecordInput]]  # tab → records; every tab here is replaced by the load
    source_updated_at: datetime | None  # latest `pulledAt` of the loaded sheets, None when unknown


@dataclass(frozen=True)
class MirrorLoadResult:
    counts: dict[str, int]  # tab → rows read from the mirror
    current: dict[str, int]  # tab → rows in the snapshot before the load
    written: bool
    source_updated_at: datetime | None = None
    skipped_edited: dict[str, int] | None = None  # tab → mirror rows not applied because edited in ERP


BUNDLED_MIRROR = Path(__file__).resolve().parents[3] / "data" / "reference-mirror"


def default_source() -> Path:
    """The snapshot bundled with the repo — no sibling checkout, no env var."""
    return BUNDLED_MIRROR


def mirror_dir(source: Path) -> Path:
    """`source/_local/db` when the source is a checkout that keeps the mirror there, else the folder itself."""
    nested = Path(source) / MIRROR_SUBDIR
    return nested if nested.is_dir() else Path(source)


def _inputs(records) -> list[TechRecordInput]:
    return [TechRecordInput(
        tab=TechOperationTab(item.tab), display_text=item.display_text, search_text=item.search_text,
        source_sheet=item.source_sheet, sort_key=item.sort_key, fields=item.fields,
    ) for item in records]


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise MirrorLoadError(f"Нет файла зеркала {path}: укажите --source (папка с .tsv)")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise MirrorLoadError(f"{path.name}: {error}") from error


def _pulled_at(db: Path, sheets: list[str]) -> str:
    """Latest `pulledAt` of the sheets in `_meta.json` (db.mjs writes UTC `YYYY-MM-DDTHH:MM`) as ISO UTC;
    '' when the file or the stamps are missing or unreadable — the date is informative, not a gate."""
    try:
        meta = json.loads((db / "_meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    stamps = []
    for sheet in sheets:
        entry = meta.get(sheet) if isinstance(meta, dict) else None
        stamp = str(entry.get("pulledAt") or "").strip() if isinstance(entry, dict) else ""
        try:
            stamps.append(datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return max(stamps).isoformat() if stamps else ""


def read_mirror(source: Path) -> MirrorSnapshot:
    """ОБ/ОП/ТЕР/КОАКС and ПРОВ/МАТ files are required; a missing one is an error, not a silent empty base. СПР.КАБ
    is optional (absent → its two tabs are not in the result and stay as they are in the snapshot)."""
    db = mirror_dir(source)
    headers: dict[str, list[str]] = {}
    raw_records: list[dict[str, object]] = []
    for tab, (sheet, _row) in TECH_SHEETS.items():
        path = db / f"{sheet}.tsv"
        headers[tab], records = sheet_snapshot_records(tab, parse_mirror_rows(_read_text(path)))
        if not records:
            # A base with no record would wipe the whole tab: the header row is missing or not recognised.
            raise MirrorLoadError(f"{path.name}: ни одной записи — нет строки шапки или шапка не распознана")
        raw_records.extend(records)
    try:
        snapshot = import_supplymap_snapshot({"meta": {
            "schemaVersion": TECH_SNAPSHOT_SCHEMA_VERSION,
            "updatedAt": _pulled_at(db, [sheet for sheet, _row in TECH_SHEETS.values()]),
            "columnHeadersByTab": headers,
        }, "records": raw_records})
    except ValueError as error:
        raise MirrorLoadError(str(error)) from error
    tables: dict[str, list[TechRecordInput]] = {tab: [] for tab in TECH_SHEETS}
    for record in _inputs(snapshot.records):
        tables[record.tab.value].append(record)

    for tab, sheet in SHEET_TABLES.items():
        path = db / f"{sheet}.tsv"
        text = _read_text(path)
        try:
            table = parse_mirror_tsv(text)
            if not table["header"]:
                raise ValueError("нет строки шапки")
            tables[tab] = _inputs(import_sheet_table(tab, table))
        except ValueError as error:
            raise MirrorLoadError(f"{path.name}: {error}") from error
    kab_path = db / f"{SPR_KAB_SHEET}.tsv"
    if kab_path.is_file():
        try:
            for tab, table in parse_spr_kab_tsv(_read_text(kab_path)).items():
                tables[tab] = _inputs(import_sheet_table(tab, table))
        except ValueError as error:
            raise MirrorLoadError(f"{kab_path.name}: {error}") from error
    return MirrorSnapshot(tables=tables, source_updated_at=snapshot.source_updated_at)


def load_mirror(source: Path, uow: UnitOfWork, *, dry_run: bool, now: datetime) -> MirrorLoadResult:
    mirror = read_mirror(source)
    tables = mirror.tables
    with uow:
        metadata = uow.tech_operations.metadata()
        current = {tab: len(uow.tech_operations.list(tab=TechOperationTab(tab), query="", limit=100_000)) for tab in tables}
    counts = {tab: len(records) for tab, records in tables.items()}
    source_updated_at = mirror.source_updated_at or (metadata["source_updated_at"] if metadata else None)
    skipped: dict[str, int] = {}
    if not dry_run:
        try:
            _count, skipped = replace_snapshot_detailed(ReplaceSnapshotCommand(
                schema_version=TECH_SNAPSHOT_SCHEMA_VERSION,
                source_updated_at=source_updated_at,
                records=tuple(record for records in tables.values() for record in records),
                actor=ACTOR, now=now,
                declared_tabs=tuple(TechOperationTab(tab) for tab in tables),
                keep_other_tabs=True, preserve_erp_edits=True,
            ), uow)
        except ReferenceValidationError as error:
            raise MirrorLoadError(str(error)) from error
    return MirrorLoadResult(counts=counts, current=current, written=not dry_run, source_updated_at=source_updated_at,
                            skipped_edited={tab: skipped.get(tab, 0) for tab in tables})


def format_report(result: MirrorLoadResult) -> str:
    lines = ["Загрузка зеркала баз «Технологии»: " + ("записано" if result.written else "пробный прогон, ничего не записано")]
    if result.source_updated_at:
        lines.append(f"  дата выгрузки зеркала: {result.source_updated_at.isoformat()}")
    for tab, count in result.counts.items():
        lines.append(f"  {TAB_LABELS[tab]}: строк в зеркале {count}, сейчас в снимке {result.current[tab]}"
                     + (f", пропущено (правлено в ERP): {result.skipped_edited[tab]}" if result.skipped_edited and result.written else ""))
    if result.skipped_edited and result.written:
        lines.append(f"  всего пропущено, правлено в ERP: {sum(result.skipped_edited.values())}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Загрузка баз «Технологии» (БД.ОБ, БД.ОП, БД.ТЕР, БД.КОАКС, БД.ПРОВ, БД.МАТ, СПР.КАБ) из зеркала в снимок справочников (REF-MIRROR-ALL)")
    parser.add_argument("--source", default=None, help="папка с .tsv зеркала или репозиторий с ними в _local/db (по умолчанию — снимок в backend/data/reference-mirror)")
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
