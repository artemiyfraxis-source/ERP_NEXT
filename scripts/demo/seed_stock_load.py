"""Load-test data for the warehouse screen: N stock lots (default 10 000) inserted straight into a demo base.

    ERP_DATABASE_URL=sqlite+pysqlite:///demo.db python scripts/demo/seed_stock_load.py [10000]

Only for a throw-away demo/preview base (never the server): lots are marked with the GRN prefix LOAD.26.
Running it again first removes the earlier LOAD.26. lots, so the count is exact.
"""
import os
import random
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[2] / "backend"
DB_URL = os.environ.get("ERP_DATABASE_URL") or f"sqlite+pysqlite:///{BACKEND}/demo.db"
if "erp-server" in DB_URL or DB_URL.startswith("postgres"):
    sys.exit("Refusing to seed load data into a non-demo database.")

count = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
random.seed(42)
TYPES = ["Комплектующие", "Крепёж", "Провод", "Разъём", "Корпус"]
engine = create_engine(DB_URL)
rows = []
for i in range(count):
    article = f"ART-{i % 2500:04d}"
    rows.append({
        "id": uuid.uuid4().hex, "warehouse": "ЦАР" if i % 3 else "ЖУК", "grn": f"LOAD.26.{i:07d}", "package": "",
        "article": article, "series": f"LOT-{i % 400}", "name": f"Позиция {article}", "item_type": TYPES[i % len(TYPES)],
        "purpose": "", "unit": "шт", "storage_location": f"{1 + i % 20}{'ABCDEF'[i % 6]}{1 + i % 30}",
        "inbound": str(Decimal(random.randint(10, 500))), "written_off": "0", "reserved": "0", "src": "[]",
    })
with engine.begin() as connection:
    connection.execute(text("DELETE FROM warehouse_lots WHERE grn LIKE 'LOAD.26.%'"))
    connection.execute(text(
        "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, series, name, item_type, purpose, unit,"
        " storage_location, inbound, written_off, reserved, source_receiving_ids, manufacturer, description,"
        " storage_conditions, datasheet, certificate) VALUES (:id, 1, :warehouse, :grn, :package, :article, :series,"
        " :name, :item_type, :purpose, :unit, :storage_location, :inbound, :written_off, :reserved, :src, '', '', '', '', '')"
    ), rows)
print(f"Seeded {count} lots (LOAD.26.*) into {DB_URL}")
