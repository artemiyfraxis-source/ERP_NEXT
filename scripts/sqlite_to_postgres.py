"""Move a whole local ERP database (SQLite, e.g. the owner's real.db) into the server's PostgreSQL.

Owner 01.10.2026: the local ERP held data that is not in Google (project 719К.2 with its ТК, hand-fixed lots,
requests), so the server starts from a copy of it instead of a fresh Google import.
    python ../scripts/sqlite_to_postgres.py --sqlite /app/data/import/real.db          # report only
    python ../scripts/sqlite_to_postgres.py --sqlite /app/data/import/real.db --yes    # replace server data
The target is ERP_DATABASE_URL (PostgreSQL). Both databases must be at the same Alembic revision: upgrade the
SQLite copy first (`ERP_DATABASE_URL=sqlite:///<copy> alembic upgrade head`; deploy/move-local-db.ps1 does it).
With --yes every PostgreSQL table is emptied and refilled in ONE transaction — foreign keys are DEFERRABLE on
PostgreSQL (migrations/env.py), so table order does not matter — and the row counts are compared before COMMIT:
any difference rolls everything back. Sequences are moved past the copied ids afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import MetaData, create_engine, func, select, text
from sqlalchemy import types as sqltypes

from app.shared.clock import MOSCOW

BATCH = 500
SKIP = {"alembic_version"}


def converter(column):
    """SQLite stores booleans as 0/1, dates and JSON as text: turn them into what the PostgreSQL column takes."""
    kind = column.type
    if isinstance(kind, sqltypes.Boolean):
        return lambda v: None if v is None else bool(v)
    if isinstance(kind, sqltypes.JSON):
        return lambda v: json.loads(v) if isinstance(v, (str, bytes)) else v
    if isinstance(kind, sqltypes.DateTime):
        aware = bool(getattr(kind, "timezone", False))

        def to_datetime(v):
            if v is None or isinstance(v, datetime):
                return v
            value = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            # SQLAlchemy drops tzinfo when writing SQLite. Everything people do is stamped with moscow_now(), so a naive
            # value is Moscow wall time — what the local ERP shows today. (Import stamps were UTC; they shift by 3 h.)
            return value.replace(tzinfo=MOSCOW) if aware and value.tzinfo is None else value

        return to_datetime
    if isinstance(kind, sqltypes.Date):
        return lambda v: v if v is None or isinstance(v, date) else date.fromisoformat(str(v)[:10])
    if isinstance(kind, sqltypes.Time):
        return lambda v: v if v is None or isinstance(v, time) else time.fromisoformat(str(v))
    if isinstance(kind, sqltypes.Numeric) and not isinstance(kind, sqltypes.Float):
        return lambda v: v if v is None or isinstance(v, Decimal) else Decimal(str(v))
    if isinstance(kind, sqltypes.Integer):
        return lambda v: v if v is None or isinstance(v, int) else int(v)
    if isinstance(kind, (sqltypes.String, sqltypes.Uuid)):
        return lambda v: v if v is None or isinstance(v, str) else str(v)
    return lambda v: v


def revision(rows) -> str:
    return ",".join(sorted(r[0] for r in rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--yes", action="store_true", help="replace the PostgreSQL data (without it: report only)")
    args = parser.parse_args()

    url = os.environ.get("ERP_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        print(f"ERP_DATABASE_URL is not PostgreSQL: {url!r}", file=sys.stderr)
        return 2
    source = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    source.text_factory = lambda b: b.decode("utf-8", errors="replace")
    engine = create_engine(url)

    with engine.connect() as conn:
        target_rev = revision(conn.execute(text("SELECT version_num FROM alembic_version")))
    source_rev = revision(source.execute("SELECT version_num FROM alembic_version"))
    if source_rev != target_rev:
        print(f"Разные версии схемы: SQLite {source_rev}, PostgreSQL {target_rev}. Сначала alembic upgrade head.",
              file=sys.stderr)
        return 2

    meta = MetaData()
    meta.reflect(engine)
    source_tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables = [t for t in meta.sorted_tables if t.name not in SKIP]
    missing = sorted(source_tables - {t.name for t in tables} - SKIP - {"sqlite_sequence"})
    if missing:
        print(f"!! В SQLite есть таблицы, которых нет на сервере (не переносятся): {', '.join(missing)}")

    counts = {}
    for table in tables:
        counts[table.name] = (
            source.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0] if table.name in source_tables else 0
        )
    total = sum(counts.values())
    print(f"В SQLite {total} строк в {sum(1 for c in counts.values() if c)} таблицах из {len(tables)}.")
    if not args.yes:
        for name, count in sorted(counts.items()):
            if count:
                print(f"  {name}: {count}")
        print("Это была сверка без записи. Перенести: --yes")
        return 0

    with engine.begin() as conn:
        names = ", ".join(f'"{t.name}"' for t in tables)
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        for table in tables:
            if not counts[table.name]:
                continue
            source_cols = [r[1] for r in source.execute(f'PRAGMA table_info("{table.name}")')]
            cols = [c for c in table.columns if c.name in source_cols]
            dropped = sorted(set(source_cols) - {c.name for c in cols})
            if dropped:
                print(f"!! {table.name}: колонки только в SQLite, не переносятся: {', '.join(dropped)}")
            convert = [converter(c) for c in cols]
            cursor = source.execute(f'SELECT {", ".join(chr(34) + c.name + chr(34) for c in cols)} FROM "{table.name}"')
            while rows := cursor.fetchmany(BATCH):
                conn.execute(
                    table.insert(),
                    [{c.name: f(v) for c, f, v in zip(cols, convert, row)} for row in rows],
                )
        conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # FK errors surface here, before the count check
        wrong = []
        for table in tables:
            got = conn.execute(select(func.count()).select_from(table)).scalar_one()
            if got != counts[table.name]:
                wrong.append(f"{table.name}: SQLite {counts[table.name]}, PostgreSQL {got}")
        if wrong:
            raise SystemExit("Расхождение, ничего не записано:\n  " + "\n  ".join(wrong))
        for table in tables:
            for column in table.columns:
                seq = conn.execute(
                    text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": column.name}
                ).scalar()
                if seq:
                    conn.execute(
                        text(f'SELECT setval(:s, COALESCE((SELECT MAX("{column.name}") FROM "{table.name}"), 0) + 1, false)'),
                        {"s": seq},
                    )
    print(f"Перенесено {total} строк, сверка по всем {len(tables)} таблицам совпала.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
