"""Fill every table of a migrated database with a few rows, at whatever revision it stands (test/CI helper only).

    cd backend; ERP_DATABASE_URL=… python -m tests.migrations.seed_any_revision [--rows 6]

The migrations job of CI seeds the database at head-1 with this, then runs the last migration over real rows. The
demo seeder and the API can't do it: their code is at head, the schema one revision behind. So the schema is
reflected and filled column by column: foreign keys take rows of their parent, CHECK `col IN (…)` takes its first
value, and the stock tables get consistent quantities (ПРИХОД > СПИСАНО + РЕЗЕРВ ≥ what kitting, techcards and
«Со склада» hold on the lot), so the integrity snapshot of a seeded database is clean.
Never point it at a real database: it only inserts, but the rows are made up.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Column, MetaData, Table, create_engine, inspect, select
from sqlalchemy import types as sqltypes
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import sort_tables

ROWS = 6
# `status IN ('open', …)` on SQLite, `((status)::text = ANY ((ARRAY['open'::character varying, …` on PostgreSQL: the
# first word is the column, the first quoted literal an allowed value.
_CHECK_IN = re.compile(r"(\w+).*?'([^']*)'", re.DOTALL)
_IN_LIST = re.compile(r"\bIN\s*\(|=\s*ANY\s*\(", re.IGNORECASE)
# A one-value `source IN ('kaiten')` (0137) PostgreSQL keeps as `((source)::text = 'kaiten'::text)` — no IN, no ANY.
_SINGLE_EQ = re.compile(r"^[\s(]*(\w+)\)?(?:::[\w ]+?)?\s*=\s*'[^']*'(?:::[\w ]+)?[\s)]*$")
_CHECK_NULL_OR = re.compile(r"\s*\(*\s*(\w+)\s+IS\s+NULL\s*\)?\s+OR\b", re.IGNORECASE)
# `(tab = 'prov' AND cable_kind IN (…)) OR (tab <> 'prov' AND cable_kind IS NULL)` (0131): the column stays empty on a
# row of any other value of the first column (the seeder never picks the one value that needs it).
_CHECK_NULL_BRANCH = re.compile(r"\bOR\b.*?<>.*?\bAND\s*\(?\s*(\w+)\s+IS\s+NULL", re.IGNORECASE | re.DOTALL)
_WAREHOUSES = ("ЦАР", "ЖУК")
# Stock quantities per lot row, and what each holder takes of one lot: 3 holders × 1 ≤ РЕЗЕРВ 20 ≤ balance.
_LOT_QUANTITIES = {
    "inbound": lambda i: Decimal(100 + i), "written_off": lambda i: Decimal(10), "reserved": lambda i: Decimal(20),
}
_HOLDER_QUANTITY = {
    ("fulfillment_lines", "reserved"),
    ("fulfillment_kit_reserves", "quantity"),
    ("procurement_stock_covers", "quantity"),
}


def seed(engine: Engine, rows: int = ROWS) -> dict[str, int]:
    """Insert `rows` rows into every empty table; returns rows inserted per table."""
    metadata = MetaData()
    metadata.reflect(bind=engine)
    inserted: dict[str, int] = {}
    with engine.begin() as connection:
        checks = _check_values(connection, metadata)
        for table in _fill_order(metadata):
            if table.name == "alembic_version" or _has_rows(connection, table):
                continue
            parents = _parent_values(connection, table)
            lots = _lot_ids(connection, metadata) if "lot_id" in table.c and not table.c.lot_id.foreign_keys else []
            count = 1 if table.name in _SINGLE_ROW_TABLES else rows
            values = [_row(table, index, parents, lots, checks, engine.dialect.name) for index in range(count)]
            connection.execute(table.insert(), values)
            inserted[table.name] = len(values)
    return inserted


def _fill_order(metadata: MetaData) -> list[Table]:
    """Parents before children; the lots also before the tables that hold reserves on them by a plain `lot_id`."""
    lots = metadata.tables.get("warehouse_lots")
    extra = [
        (lots, table) for table in metadata.tables.values()
        if lots is not None and table is not lots and "lot_id" in table.c and not table.c.lot_id.foreign_keys
    ]
    return sort_tables(metadata.tables.values(), extra_dependencies=extra)


def _has_rows(connection: Connection, table: Table) -> bool:
    return connection.execute(select(1).select_from(table).limit(1)).first() is not None


def _check_values(connection: Connection, metadata: MetaData) -> dict[tuple[str, str], str | None]:
    """CHECK `status IN ('open', …)` → ('table', 'status'): 'open'; CHECK `col IS NULL OR (…)` on a nullable column →
    None (the column is left empty, e.g. «restored from» of a plain save)."""
    inspector = inspect(connection)
    found: dict[tuple[str, str], str | None] = {}
    for table in metadata.tables.values():
        for check in inspector.get_check_constraints(table.name):
            text = str(check.get("sqltext", ""))
            empty = _CHECK_NULL_OR.match(text) or _CHECK_NULL_BRANCH.search(text)
            if empty and empty.group(1) in table.c and table.c[empty.group(1)].nullable:
                found[(table.name, empty.group(1))] = None
                continue
            match = _CHECK_IN.search(text)
            if match and match.group(1) in table.c and (_IN_LIST.search(text) or _SINGLE_EQ.match(text)):
                found[(table.name, match.group(1))] = match.group(2)
    return found


def _parent_values(connection: Connection, table: Table) -> dict[str, list[Any]]:
    """column → the parent values a row may point at (each foreign key, composite ones as aligned columns)."""
    values: dict[str, list[Any]] = {}
    for key in table.foreign_key_constraints:
        parent = key.referred_table
        if parent is table:
            continue  # self reference (a correction of an earlier row): left NULL
        referred = [element.column for element in key.elements]
        rows = connection.execute(select(*referred).order_by(*referred)).all()
        for position, element in enumerate(key.elements):
            values[element.parent.name] = [row[position] for row in rows]
    return values


def _lot_ids(connection: Connection, metadata: MetaData) -> list[Any]:
    lots = metadata.tables.get("warehouse_lots")
    if lots is None:
        return []
    return list(connection.execute(select(lots.c.id).order_by(lots.c.id)).scalars())


def _row(table: Table, index: int, parents: dict[str, list[Any]], lots: list[Any],
         checks: dict[tuple[str, str], str | None], dialect: str) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for column in table.columns:
        if _database_assigns(table, column, dialect):
            continue
        name = column.name
        if checks.get((table.name, name), "") is None:  # `col IS NULL OR …` wins over a parent to point at
            row[name] = None
            continue
        if name in parents:
            choices = parents[name]
            if not choices:
                if not column.nullable:
                    raise RuntimeError(f"{table.name}.{name}: the parent table has no rows to point at")
                row[name] = None
            else:
                row[name] = choices[index % len(choices)]
            continue
        if column.foreign_keys:  # self reference
            row[name] = None
            continue
        if name == "lot_id" and lots:
            row[name] = lots[index % len(lots)]
            continue
        if (table.name, name) in checks:
            row[name] = checks[(table.name, name)]
            continue
        row[name] = _value(table.name, column, index)
    return row


def _database_assigns(table: Table, column: Column, dialect: str) -> bool:
    """An integer surrogate key the database numbers itself (SERIAL / IDENTITY / the SQLite rowid)."""
    if not column.primary_key or not isinstance(column.type, sqltypes.Integer):
        return False
    if column.identity is not None or column.server_default is not None:
        return True
    return dialect == "sqlite" and len(table.primary_key.columns) == 1


# Pairs a CHECK ties together that the column-by-column fill can't see: identity_documents.scope takes 'all' from its
# `IN (…)` list, and `scope = 'all'` demands an empty scope_value (0100_identity_documents); fulfillment_lines.absent_at
# is left empty (`absent_at IS NULL OR …`), and «who» goes with «when» (0106_fulfillment_line_absent).
# finished_goods.before_erp is 0: the acts of a КБ (0128) sum to its «принято» moves less what was shipped before ERP,
# and row i of the moves and of the acts both take the i-th КБ and i + 1 pieces, so the sums meet only without it.
_COUPLED_VALUES: dict[tuple[str, str], Any] = {
    ("identity_documents", "scope_value"): "",
    ("fulfillment_lines", "absent_by"): None,
    ("finished_goods", "before_erp"): Decimal(0),
    # A VSWR verdict without a norm (the seeder leaves norm_vswr_max empty) is a manual one (0142_production_vswr).
    ("production_vswr_measurements", "verdict_manual"): True,
}
# Values a CHECK shapes beyond `IN (…)`: an internal project number is «ВП-…» (0135_internal_projects), unique; the
# label printer settings are bounded ranges and a numeric `dpi IN (203, 300)` (0138_warehouse_labels).
_LABEL_SETTINGS = {"width_mm": 58, "height_mm": 30, "dpi": 300, "gap_mm": 2, "density": 12, "speed": 2,
                   "offset_x_mm": 0, "offset_y_mm": 0}
_SHAPED_VALUES: dict[tuple[str, str], Any] = {
    ("purchasing_internal_projects", "number"): lambda i: f"ВП-{i + 1:03d}",
    **{("warehouse_label_settings", name): (lambda value: lambda i: value)(value) for name, value in _LABEL_SETTINGS.items()},
    # A VSWR band is from < to and its peak lies inside (0142_production_vswr_measurements).
    ("production_vswr_measurements", "band_from_mhz"): lambda i: Decimal(1),
    ("production_vswr_measurements", "band_to_mhz"): lambda i: Decimal(8800),
    ("production_vswr_measurements", "max_vswr_mhz"): lambda i: Decimal(1000),
}
# One row per installation (CHECK `id = 1`): seeded with a single row.
_SINGLE_ROW_TABLES = frozenset({"warehouse_label_settings"})


def _value(table: str, column: Column, index: int) -> Any:
    name, kind = column.name, column.type
    if (table, name) in _COUPLED_VALUES:
        return _COUPLED_VALUES[(table, name)]
    if (table, name) in _SHAPED_VALUES:
        return _SHAPED_VALUES[(table, name)](index)
    if table == "warehouse_lots" and name in _LOT_QUANTITIES:
        return _LOT_QUANTITIES[name](index)
    if (table, name) in _HOLDER_QUANTITY:
        return Decimal(1)
    if isinstance(kind, sqltypes.Boolean):
        return False
    if isinstance(kind, sqltypes.Uuid):
        return uuid.uuid4() if kind.as_uuid else uuid.uuid4().hex
    if isinstance(kind, sqltypes.DateTime):
        return datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc) + timedelta(days=index)
    if isinstance(kind, sqltypes.Date):
        return date(2026, 9, 1) + timedelta(days=index)
    if isinstance(kind, sqltypes.Time):
        return time(9, index % 60)
    if isinstance(kind, sqltypes.Interval):
        return timedelta(minutes=index + 1)
    if isinstance(kind, sqltypes.JSON):
        return [] if name.endswith(("ids", "s")) else {}
    if isinstance(kind, sqltypes.Enum) and kind.enums:
        return kind.enums[0]
    if isinstance(kind, sqltypes.Integer):
        return 1 if name == "version" else index + 1
    if isinstance(kind, sqltypes.Float):
        return float(index + 1)
    if isinstance(kind, sqltypes.Numeric):
        return Decimal(index + 1)
    if isinstance(kind, sqltypes.LargeBinary):
        return f"bytes-{index}".encode()
    if isinstance(kind, sqltypes.String):
        length = getattr(kind, "length", None)
        if name == "warehouse" and (length is None or length >= 3):
            return _WAREHOUSES[index % len(_WAREHOUSES)]
        if length == 32 and (name == "id" or name.endswith("_id")):
            return uuid.uuid4().hex  # a Uuid column as SQLite reflects it (CHAR(32))
        if length == 64 and name.endswith("sha256"):
            return hashlib.sha256(f"{table}.{name}-{index}".encode()).hexdigest()  # CHECK length(…) = 64
        return _fit(f"{name}-{index}", f"-{index}", length)
    raise RuntimeError(f"{table}.{name}: no seed value for type {kind!r}")


def _fit(value: str, suffix: str, length: int | None) -> str:
    if length is None or len(value) <= length:
        return value
    return (value[: max(length - len(suffix), 0)] + suffix)[-length:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=ROWS)
    parser.add_argument("--database-url", default=os.getenv("ERP_DATABASE_URL", ""))
    args = parser.parse_args(argv)
    if not args.database_url:
        print("Set ERP_DATABASE_URL", file=sys.stderr)
        return 2
    engine = create_engine(args.database_url)
    try:
        inserted = seed(engine, args.rows)
    finally:
        engine.dispose()
    print(f"seeded {len(inserted)} tables, {sum(inserted.values())} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
