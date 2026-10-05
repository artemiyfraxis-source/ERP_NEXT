"""Database integrity check around a migration: a snapshot before, a snapshot after, a comparison.

    ERP_DATABASE_URL=… python -m app.db_integrity snapshot --out before.json
    ERP_DATABASE_URL=… alembic upgrade head
    ERP_DATABASE_URL=… python -m app.db_integrity snapshot --out after.json
    python -m app.db_integrity compare before.json after.json [--migrations-dir backend/migrations/versions]

`compare` exits 0 when nothing got worse and 1 with one Russian line per violation:
- a table lost rows, unless every migration between the two revisions declares a module-level `DESTRUCTIVE = True`;
- the stock sum (ПРИХОД − СПИСАНО of warehouse_lots, in total and per warehouse) or the РЕЗЕРВ sum changed, under
  the same exception;
- a counted defect grew: negative quantities, РЕЗЕРВ over the lot balance, holders (kit lines, techcard reserves,
  «Со склада») over the lot РЕЗЕРВ, holders on a missing lot, duplicate lot keys (warehouse, ГРН, упаковка), the
  lot key constraint missing, rows whose foreign key points nowhere, foreign keys left NOT VALID (PostgreSQL);
- the revision after is not the head of the migrations directory.
New tables and new rows are fine. Works on PostgreSQL (production) and SQLite (tests); every check reads the real
schema first, so a snapshot can be taken at any revision.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine

FORMAT = 1
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"
_QUANT = Decimal("0.000001")
_EPS = "0.000001"

LOTS = "warehouse_lots"
LOT_KEY_CONSTRAINT = "uq_warehouse_lot_key"
LOT_KEY_COLUMNS = ("warehouse", "grn", "package")

# Quantities that must never go below zero: (table, column, what the owner calls it).
NON_NEGATIVE = (
    (LOTS, "inbound", "ПРИХОД партии"),
    (LOTS, "written_off", "СПИСАНО партии"),
    (LOTS, "reserved", "РЕЗЕРВ партии"),
    ("fulfillment_lines", "requirement", "потребность строки комплектации"),
    ("fulfillment_lines", "reserved", "резерв строки комплектации"),
    ("fulfillment_lines", "issued", "выдано по строке комплектации"),
    ("fulfillment_kit_reserves", "quantity", "резерв техкарты"),
    ("fulfillment_kit_reserves", "consumed", "израсходованный резерв техкарты"),
    ("procurement_stock_covers", "quantity", "резерв «Со склада»"),
    ("production_boxes", "quantity", "количество в коробке"),
    ("production_boxes", "consumed", "израсходовано из коробки"),
    ("production_boxes", "reserved", "резерв коробки"),
)

# Who holds a part of a lot's РЕЗЕРВ: (table, quantity column, label). The lot's РЕЗЕРВ must cover their sum.
RESERVE_HOLDERS = (
    ("fulfillment_lines", "reserved", "строки комплектации"),
    ("fulfillment_kit_reserves", "quantity", "резервы техкарт"),
    ("procurement_stock_covers", "quantity", "резервы «Со склада»"),
)

_COUNT_LABELS = {
    "lots_reserved_over_balance": "партии с РЕЗЕРВОМ больше остатка (ПРИХОД − СПИСАНО)",
    "lots_over_reserved":
        "партии, где держатели резерва (комплектация, техкарты, «Со склада») держат больше РЕЗЕРВА партии",
    "duplicate_lot_keys": "повторы ключа партии (склад, ГРН, упаковка)",
    "lot_key_constraint_missing": f"нет ограничения уникальности {LOT_KEY_CONSTRAINT}",
    "fk_not_validated": "внешние ключи без проверки (NOT VALID)",
}
_SUM_LABELS = {
    "stock_total": "сумма остатков по партиям (ПРИХОД − СПИСАНО)",
    "reserved_total": "сумма РЕЗЕРВА по партиям",
}


# ── snapshot ────────────────────────────────────────────────────────────────────────────────────────────────


def take_snapshot(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        schema = _Schema(connection)
        tables = {name: _count(connection, schema, name) for name in schema.tables}
        counts, sums, details = _invariants(connection, schema)
    return {
        "format": FORMAT,
        "dialect": engine.dialect.name,
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "revision": _revision(engine),
        "tables": tables,
        "counts": counts,
        "sums": sums,
        "details": details,
    }


class _Schema:
    def __init__(self, connection: Connection):
        self.connection = connection
        self.inspector = inspect(connection)
        self.tables = sorted(
            name for name in self.inspector.get_table_names() if not name.startswith("sqlite_")
        )
        self._columns: dict[str, set[str]] = {}

    def has(self, table: str, *columns: str) -> bool:
        if table not in self.tables:
            return False
        if table not in self._columns:
            self._columns[table] = {column["name"] for column in self.inspector.get_columns(table)}
        return set(columns) <= self._columns[table]

    def q(self, name: str) -> str:
        return self.connection.dialect.identifier_preparer.quote(name)


def _count(connection: Connection, schema: _Schema, table: str) -> int:
    return int(connection.execute(text(f"SELECT count(*) FROM {schema.q(table)}")).scalar_one())


def _revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        if "alembic_version" not in inspect(connection).get_table_names():
            return None
        rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    return ",".join(sorted(rows)) or None


def _invariants(connection: Connection, schema: _Schema) -> tuple[dict[str, int], dict[str, str], dict[str, Any]]:
    counts: dict[str, int] = {}
    sums: dict[str, str] = {}
    details: dict[str, Any] = {}
    q = schema.q

    def scalar(sql: str, **params) -> Any:
        return connection.execute(text(sql), params).scalar()

    for table, column, _ in NON_NEGATIVE:
        if schema.has(table, column):
            counts[f"negative:{table}.{column}"] = int(scalar(
                f"SELECT count(*) FROM {q(table)} WHERE {q(column)} < 0"
            ))

    lots_ok = schema.has(LOTS, "id", "inbound", "written_off", "reserved")
    if lots_ok:
        balance = f"({q('inbound')} - {q('written_off')})"
        counts[f"negative:{LOTS}.balance"] = int(scalar(f"SELECT count(*) FROM {q(LOTS)} WHERE {balance} < 0"))
        counts["lots_reserved_over_balance"] = int(scalar(
            f"SELECT count(*) FROM {q(LOTS)} WHERE {q('reserved')} > {balance} + {_EPS}"
        ))
        sums["stock_total"] = _decimal(scalar(f"SELECT sum({balance}) FROM {q(LOTS)}"))
        sums["reserved_total"] = _decimal(scalar(f"SELECT sum({q('reserved')}) FROM {q(LOTS)}"))
        if schema.has(LOTS, "warehouse"):
            rows = connection.execute(text(
                f"SELECT {q('warehouse')}, sum({balance}) FROM {q(LOTS)} GROUP BY {q('warehouse')}"
            )).all()
            for warehouse, total in rows:
                sums[f"stock:{warehouse}"] = _decimal(total)

        holders = [
            (table, column) for table, column, _ in RESERVE_HOLDERS if schema.has(table, "lot_id", column)
        ]
        if holders:
            held = " + ".join(
                f"COALESCE((SELECT sum(h.{q(column)}) FROM {q(table)} h WHERE h.{q('lot_id')} = l.{q('id')}), 0)"
                for table, column in holders
            )
            counts["lots_over_reserved"] = int(scalar(
                f"SELECT count(*) FROM {q(LOTS)} l WHERE l.{q('reserved')} + {_EPS} < ({held})"
            ))
    for table, column, _ in RESERVE_HOLDERS:
        if schema.has(table, "lot_id", column) and lots_ok:
            counts[f"orphan_lot:{table}"] = int(scalar(
                f"SELECT count(*) FROM {q(table)} h WHERE h.{q('lot_id')} IS NOT NULL AND h.{q(column)} > 0"
                f" AND NOT EXISTS (SELECT 1 FROM {q(LOTS)} l WHERE l.{q('id')} = h.{q('lot_id')})"
            ))

    if schema.has(LOTS, *LOT_KEY_COLUMNS):
        key = ", ".join(q(column) for column in LOT_KEY_COLUMNS)
        counts["duplicate_lot_keys"] = int(scalar(
            f"SELECT count(*) FROM (SELECT 1 FROM {q(LOTS)} GROUP BY {key} HAVING count(*) > 1) d"
        ))
        counts["lot_key_constraint_missing"] = 0 if _has_lot_key(schema) else 1

    orphans = _foreign_key_orphans(connection, schema)
    counts.update(orphans)

    if connection.dialect.name == "postgresql":
        rows = connection.execute(text(
            "SELECT t.relname, c.conname FROM pg_constraint c"
            " JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace"
            " WHERE c.contype = 'f' AND NOT c.convalidated AND n.nspname = current_schema()"
            " ORDER BY t.relname, c.conname"
        )).all()
        counts["fk_not_validated"] = len(rows)
        details["fk_not_validated"] = [f"{table}.{name}" for table, name in rows]
    return counts, sums, details


def _has_lot_key(schema: _Schema) -> bool:
    wanted = set(LOT_KEY_COLUMNS)
    for constraint in schema.inspector.get_unique_constraints(LOTS):
        if constraint.get("name") == LOT_KEY_CONSTRAINT or set(constraint["column_names"]) == wanted:
            return True
    for index in schema.inspector.get_indexes(LOTS):
        if index.get("unique") and set(index["column_names"]) == wanted:
            return True
    return False


def _foreign_key_orphans(connection: Connection, schema: _Schema) -> dict[str, int]:
    """Rows whose foreign key points at a missing row (SQLite does not enforce keys; PostgreSQL keeps a NOT VALID
    one unchecked): e.g. a production operation whose task is gone."""
    q = schema.q
    found: dict[str, int] = {}
    for table in schema.tables:
        for key in schema.inspector.get_foreign_keys(table):
            columns, parent, referred = key["constrained_columns"], key["referred_table"], key["referred_columns"]
            if not columns or key.get("referred_schema") not in (None, schema.inspector.default_schema_name):
                continue
            present = " AND ".join(f"c.{q(column)} IS NOT NULL" for column in columns)
            if parent in schema.tables:
                match = " AND ".join(f"p.{q(r)} = c.{q(column)}" for column, r in zip(columns, referred))
                missing = f" AND NOT EXISTS (SELECT 1 FROM {q(parent)} p WHERE {match})"
            else:
                missing = ""
            sql = f"SELECT count(*) FROM {q(table)} c WHERE {present}{missing}"
            found[f"fk_orphans:{table}({','.join(columns)})->{parent}"] = int(
                connection.execute(text(sql)).scalar_one()
            )
    return found


def _decimal(value: Any) -> str:
    if value is None:
        return "0"
    try:
        amount = Decimal(str(value)).quantize(_QUANT)
    except InvalidOperation:
        return str(value)
    return format(amount.normalize(), "f") if amount else "0"


# ── migrations ──────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Migration:
    revision: str
    down_revisions: tuple[str, ...]
    destructive: bool
    file: str


def read_migrations(directory: Path) -> dict[str, Migration]:
    """revision → its file's `revision`, `down_revision` and module-level `DESTRUCTIVE`, read without importing."""
    found: dict[str, Migration] = {}
    for path in sorted(Path(directory).glob("*.py")):
        values: dict[str, Any] = {}
        for node in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision", "DESTRUCTIVE"}:
                    try:
                        values[target.id] = ast.literal_eval(value)
                    except ValueError:
                        values[target.id] = None
        revision = values.get("revision")
        if not isinstance(revision, str):
            continue
        down = values.get("down_revision")
        downs = tuple(down) if isinstance(down, (tuple, list)) else ((down,) if isinstance(down, str) else ())
        found[revision] = Migration(revision, downs, values.get("DESTRUCTIVE") is True, path.name)
    return found


def heads(migrations: dict[str, Migration]) -> list[str]:
    referenced = {down for migration in migrations.values() for down in migration.down_revisions}
    return sorted(revision for revision in migrations if revision not in referenced)


def migrations_between(
    migrations: dict[str, Migration], before: str | None, after: str | None,
) -> list[Migration] | None:
    """The migrations applied to go from `before` to `after` (after's ancestors that are not before's), walking the
    down_revision chain; None when `after` is unknown or `before` is not its ancestor."""
    if after is None:
        return [] if before is None else None
    reach = _ancestors(migrations, after)
    if any(revision not in migrations for revision in reach):
        return None
    if before is None:
        applied = reach
    elif before in reach:
        applied = reach - _ancestors(migrations, before)
    else:
        return None
    return sorted((migrations[revision] for revision in applied), key=lambda migration: migration.file)


def _ancestors(migrations: dict[str, Migration], revision: str) -> set[str]:
    found: set[str] = set()
    pending = [revision]
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        migration = migrations.get(current)
        if migration is not None:
            pending.extend(migration.down_revisions)
    return found


# ── compare ─────────────────────────────────────────────────────────────────────────────────────────────────


def compare(before: dict[str, Any], after: dict[str, Any], migrations_dir: Path | None = None) -> list[str]:
    """Russian lines, one per violation; empty when the database came through intact."""
    violations: list[str] = []
    directory = Path(migrations_dir) if migrations_dir is not None else DEFAULT_MIGRATIONS_DIR
    migrations = read_migrations(directory) if directory.is_dir() else {}
    old, new = before.get("revision"), after.get("revision")

    if not migrations:
        violations.append(f"Не нашёл миграций в {directory}: не могу проверить ревизию и DESTRUCTIVE.")
        chain = None
    else:
        tops = heads(migrations)
        if len(tops) != 1:
            violations.append(f"В {directory} несколько последних миграций: {', '.join(tops)}.")
        elif new != tops[0]:
            violations.append(f"Ревизия базы после обновления — {new or 'нет'}, а последняя миграция — {tops[0]}.")
        chain = migrations_between(migrations, old, new)
        if chain is None:
            violations.append(
                f"Не могу построить цепочку миграций от {old or 'пустой базы'} до {new or 'нет'} по down_revision."
            )
    destructive = bool(chain) and all(migration.destructive for migration in chain)
    why = _why_not_destructive(old, new, chain)

    for table, rows_before in sorted(before.get("tables", {}).items()):
        rows_after = after.get("tables", {}).get(table)
        lost = rows_before - (rows_after or 0)
        if lost <= 0 or destructive:
            continue
        if rows_after is None:
            violations.append(f"Таблица {table} удалена вместе с {rows_before} строками, {why}.")
        else:
            violations.append(
                f"Таблица {table} потеряла строки: было {rows_before}, стало {rows_after} (−{lost}), {why}."
            )

    counts_before, counts_after = before.get("counts", {}), after.get("counts", {})
    for key in sorted(counts_after):
        was, now = counts_before.get(key, 0), counts_after[key]
        if now > was:
            violations.append(f"Стало хуже — {_count_label(key)}: было {was}, стало {now}.")
    if counts_after.get("fk_not_validated", 0) > counts_before.get("fk_not_validated", 0):
        names = after.get("details", {}).get("fk_not_validated", [])
        if names:
            violations.append(f"Внешние ключи без проверки: {', '.join(names)}.")

    sums_before, sums_after = before.get("sums", {}), after.get("sums", {})
    # Only when warehouse_lots exists on both sides: a table that appeared or vanished is judged by its row count.
    if not destructive and "stock_total" in sums_before and "stock_total" in sums_after:
        for key in sorted(set(sums_before) | set(sums_after)):
            was, now = sums_before.get(key, "0"), sums_after.get(key, "0")
            if Decimal(was) != Decimal(now):
                violations.append(f"Изменилась {_sum_label(key)}: было {was}, стало {now}, {why}.")
    return violations


def _why_not_destructive(old: str | None, new: str | None, chain: list[Migration] | None) -> str:
    if chain is None:
        return "а цепочку миграций построить не удалось"
    if not chain:
        return "а миграций между снимками не было"
    plain = [migration.revision for migration in chain if not migration.destructive]
    if len(plain) == 1:
        return f"а миграция {plain[0]} не объявляет DESTRUCTIVE = True"
    return f"а миграции {', '.join(plain)} не объявляют DESTRUCTIVE = True"


def _count_label(key: str) -> str:
    if key in _COUNT_LABELS:
        return _COUNT_LABELS[key]
    kind, _, rest = key.partition(":")
    if kind == "negative":
        table, _, column = rest.partition(".")
        if column == "balance":
            return f"отрицательный остаток (ПРИХОД − СПИСАНО) в {table}"
        label = next((name for t, c, name in NON_NEGATIVE if t == table and c == column), column)
        return f"отрицательное значение «{label}» ({table}.{column})"
    if kind == "orphan_lot":
        label = next((name for t, _, name in RESERVE_HOLDERS if t == rest), rest)
        return f"{label} на несуществующей партии ({rest})"
    if kind == "fk_orphans":
        child, _, parent = rest.partition("->")
        return f"строки {child} ссылаются на отсутствующие строки {parent}"
    return key


def _sum_label(key: str) -> str:
    if key in _SUM_LABELS:
        return _SUM_LABELS[key]
    if key.startswith("stock:"):
        return f"сумма остатков склада {key.partition(':')[2]}"
    return key


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────────────────


def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="python -m app.db_integrity", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="write a snapshot of the database at ERP_DATABASE_URL")
    snap.add_argument("--out", required=True)
    snap.add_argument("--database-url", default=os.getenv("ERP_DATABASE_URL", ""))
    comp = commands.add_parser("compare", help="compare two snapshots; exit 1 on any violation")
    comp.add_argument("before")
    comp.add_argument("after")
    comp.add_argument("--migrations-dir", default=str(DEFAULT_MIGRATIONS_DIR))
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        if not args.database_url:
            print("Задайте ERP_DATABASE_URL (или --database-url).", file=sys.stderr)
            return 2
        engine = create_engine(args.database_url)
        try:
            data = take_snapshot(engine)
        finally:
            engine.dispose()
        with open(args.out, "w", encoding="utf-8", newline="") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        rows = sum(data["tables"].values())
        print(f"Снимок {args.out}: ревизия {data['revision']}, таблиц {len(data['tables'])}, строк {rows}, "
              f"остаток по партиям {data['sums'].get('stock_total', '—')}.")
        return 0

    violations = compare(_load(args.before), _load(args.after), Path(args.migrations_dir))
    if violations:
        for line in violations:
            print(line)
        return 1
    print("Целостность в порядке: строки не потеряны, остатки и резервы сходятся, ревизия последняя.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
