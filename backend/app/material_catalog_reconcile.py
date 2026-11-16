"""Read-only cutover evidence for a local/restored SQLite or PostgreSQL database.

With writers stopped, before upgrading to 0181:
  python -m app.material_catalog_reconcile snapshot --database-url URL --output before.json
After upgrading, before allowing new writes:
  python -m app.material_catalog_reconcile verify --database-url URL --baseline before.json --output after.json

The files contain counts and hashes, not customer/stock payloads. Database URLs are never printed.
Verification covers every pre-existing table, complete material payloads and UUIDs, stock journal
targets, metadata copies, and frozen archive membership. An application edit after cutover is
expected to fail this before/after comparison: take evidence before reopening the database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import sqlalchemy as sa

TABLES = {"heat_shrink": "heat_shrink_records", "cov": "covering_records"}
MATERIAL_COLUMNS = {"material_archived", "material_group", "classification_status"}
PAYLOAD = ("id", "display_text", "normalized_search", "source_sheet", "sort_key", "fields",
           "mirror_key", "edited_in_erp", "equipment_model_id")
TARGETS = {"record_id": "tech_operation_records", "equipment_unit_id": "equipment_units",
           "heat_shrink_id": "heat_shrink_records", "covering_id": "covering_records"}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _rows(connection, name):
    table = sa.Table(name, sa.MetaData(), autoload_with=connection)
    return [dict(row) for row in connection.execute(sa.select(table)).mappings()]


def _payload(row):
    return _hash({key: row[key] for key in PAYLOAD})


def _normalized(name, row):
    values = dict(row)
    if name == "tech_operation_records":
        for key in MATERIAL_COLUMNS:
            values.pop(key, None)
    if name == "reference_stock_links":
        moved = values.pop("heat_shrink_id", None) or values.pop("covering_id", None)
        values.pop("covering_id", None)
        if moved:
            values.update(record_id=moved, record_tab="mat")
    return values


def snapshot(connection):
    names = sa.inspect(connection).get_table_names()
    tables, materials, entries = {}, {}, {}
    for name in names:
        rows = _rows(connection, name)
        tables[name] = {"count": len(rows), "sha256": _hash(sorted(_json(_normalized(name, row)) for row in rows))}
        if name == "tech_operation_records":
            materials = {str(row["id"]): _payload(row) for row in rows if row["tab"] == "mat" and not row.get("material_archived")}
        if name == "reference_entries":
            entries = {str(row["id"]): _hash(row) for row in rows}
    return {"version": 1, "tables": tables, "materials": materials, "entries": entries}


def verify(connection, before):
    after = snapshot(connection)
    errors = []
    counts = {name: {"before": before["tables"].get(name, {}).get("count", 0), "after": data["count"]}
              for name, data in after["tables"].items()}
    for name, expected in before["tables"].items():
        if name in ("alembic_version", "reference_entries"):
            continue
        if after["tables"].get(name) != expected:
            errors.append(f"Existing table changed: {name}")
    for identifier, digest in before["entries"].items():
        if after["entries"].get(identifier) != digest:
            errors.append(f"Original column/reference metadata changed: {identifier}")
    entries = _rows(connection, "reference_entries")
    by_key = {(row["kind"], row["key"]): row for row in entries}
    for row in entries:
        separator = ":" if row["kind"] in ("custom_column", "removed_column") else "|" if row["kind"] == "column_view" else None
        if not separator or not row["key"].startswith("mat" + separator):
            continue
        for tab in TABLES:
            copied = by_key.get((row["kind"], tab + row["key"][3:]))
            if copied is None or (str(copied["id"]) not in before["entries"] and
                    (copied["value"], copied["position"], copied["normalized_key"]) !=
                    (row["value"], row["position"], tab + row["normalized_key"][3:])):
                errors.append(f"Independent column metadata copy missing or changed: {tab}/{row['key']}")
    names = set(after["tables"])
    if not set(TABLES.values()) <= names:
        errors.append("Missing physical material tables")
        return {"ok": False, "table_counts": counts, "errors": errors}
    legacy = _rows(connection, "tech_operation_records")
    archive = {str(row["id"]): row for row in legacy if row.get("material_archived")}
    active = {str(row["id"]): row for row in legacy if row["tab"] == "mat" and not row.get("material_archived")}
    partition = {"mat": len(active)}
    copied = {}
    for tab, name in TABLES.items():
        rows = _rows(connection, name)
        partition[tab] = len(rows)
        for row in rows:
            identifier = str(row["id"])
            if identifier in active:
                errors.append(f"UUID has multiple active owners: {identifier}")
            if row["tab"] != tab:
                errors.append(f"Wrong storage tab: {identifier}")
            if identifier not in archive or _payload(row) != _payload(archive[identifier]):
                errors.append(f"Archive/payload mismatch: {identifier}")
            active[identifier] = row
            copied[identifier] = row
    if set(archive) != set(copied):
        errors.append("Frozen archive UUID set differs from copied UUID set")
    if {identifier: _payload(row) for identifier, row in active.items()} != before["materials"]:
        errors.append("Material coverage or complete payload equality failed")
    for identifier, row in active.items():
        if row.get("classification_status") not in ("classified", "unknown"):
            errors.append(f"Missing classification flag: {identifier}")
    existing = {column: {str(row["id"]) for row in _rows(connection, table)} for column, table in TARGETS.items()}
    links = _rows(connection, "reference_stock_links")
    for row in links:
        targets = [(column, str(row[column])) for column in TARGETS if row.get(column)]
        if len(targets) > 1 or any(identifier not in existing[column] for column, identifier in targets):
            errors.append(f"Invalid stock-link target: {row['id']}")
        if row.get("record_id") and str(row["record_id"]) in archive:
            errors.append(f"Stock link still targets material archive: {row['id']}")
        for column, tab in (("heat_shrink_id", "heat_shrink"), ("covering_id", "cov")):
            if row.get(column) and row["record_tab"] != tab:
                errors.append(f"Stock link tab differs from target: {row['id']}")
    if connection.dialect.name == "sqlite":
        triggers = set(connection.execute(sa.text("SELECT name FROM sqlite_master WHERE type='trigger'")).scalars())
        frozen = {"freeze_legacy_material_update", "freeze_legacy_material_delete"} <= triggers
    else:
        frozen = bool(connection.execute(sa.text("SELECT count(*) FROM pg_trigger WHERE tgname='freeze_legacy_material' "
                                                 "AND NOT tgisinternal")).scalar_one())
    if not frozen:
        errors.append("Legacy material freeze triggers missing")
    return {"ok": not errors, "table_counts": counts, "active_partition": partition,
            "source_materials": len(before["materials"]), "archived_materials": len(archive),
            "stock_links": len(links), "invariants": {
                "complete_payloads_and_ids": not any("payload" in error or "coverage" in error for error in errors),
                "all_existing_tables_unchanged": not any("table changed" in error for error in errors),
                "archive_frozen": frozen, "disjoint_active_owners": len(active) == sum(partition.values()),
                "stock_targets_valid": not any("Stock" in error or "stock" in error for error in errors),
            }, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("snapshot", "verify"))
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    if args.action == "verify" and args.baseline is None:
        parser.error("verify requires --baseline")
    engine = sa.create_engine(args.database_url)
    with engine.connect() as connection:
        result = snapshot(connection) if args.action == "snapshot" else verify(
            connection, json.loads(args.baseline.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    if args.action == "snapshot":
        print(f"Snapshot: {len(result['tables'])} tables, {len(result['materials'])} materials; {args.output}")
    else:
        print(json.dumps({key: value for key, value in result.items() if key != "table_counts"}, ensure_ascii=False))
        print(f"Before/after counts for all {len(result['table_counts'])} tables: {args.output}")
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
