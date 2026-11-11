"""Additive schema, constraints and populated upgrade/downgrade roundtrip."""

from pathlib import Path
from hashlib import sha256

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import CHAR, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.modules.warehouse.infrastructure import SupplierItemRuleRow


def test_supplier_learning_migration_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{(tmp_path / 'learning.db').as_posix()}")
    command.upgrade(config, "0173_shipment_line_lots")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO warehouse_receipts (id,version,warehouse,created_at,updated_at,created_by) "
                              "VALUES (:id,1,'ЦАР','2026-11-11','2026-11-11','test')"), {"id": "1" * 32})
        command.upgrade(config, "head")
        assert "warehouse_supplier_item_rules" in inspect(engine).get_table_names()
        constraints = inspect(engine)
        assert {tuple(c["column_names"]) for c in constraints.get_unique_constraints("warehouse_supplier_item_rules")} == {
            ("supplier_key", "supplier_code_key", "name_hash"),
        }
        table = SupplierItemRuleRow.__table__
        assert {tuple(c.columns.keys()) for c in table.constraints if isinstance(c, UniqueConstraint)} == {
            ("supplier_key", "supplier_code_key", "name_hash"),
        }
        hash_column = next(c for c in constraints.get_columns("warehouse_supplier_item_rules") if c["name"] == "name_hash")
        for column_type, nullable in ((table.c.name_hash.type, table.c.name_hash.nullable),
                                      (hash_column["type"], hash_column["nullable"])):
            assert isinstance(column_type, CHAR) and column_type.length == 64
            assert not nullable
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT supplier_inn FROM warehouse_receipts")) == ""
            assert conn.scalar(text("SELECT count(*) FROM warehouse_receipt_line_confirmations")) == 0
        insert = text("INSERT INTO warehouse_supplier_item_rules "
                      "(id,supplier_key,supplier_code_key,name_key,name_hash,article,manufacturer,stock_unit,pack_size,quantity_basis,"
                      "version,active,created_at,updated_at,created_by,updated_by) "
                      "VALUES (:id,'inn:7707083893',:code,:name,:hash,'A','','шт',:pack,:basis,1,1,'2026-11-11','2026-11-11','t','t')")
        name = "я" * 1500 + "б" * 500
        values = {"id": "2" * 32, "code": "S", "name": name, "hash": sha256(name.encode("utf-8")).hexdigest(), "pack": 100, "basis": "packs"}
        with engine.begin() as conn:
            conn.execute(insert, values)
        for patch in ({"id": "3" * 32}, {"id": "3" * 32, "name": "other", "pack": 0},
                      {"id": "3" * 32, "code": "", "name": ""},
                      {"id": "3" * 32, "name": "other", "pack": None}):
            with pytest.raises(IntegrityError), engine.begin() as conn:
                candidate = {**values, **patch}
                candidate["hash"] = sha256(candidate["name"].encode("utf-8")).hexdigest()
                conn.execute(insert, candidate)
        other_name = "я" * 1500 + "в" * 500
        with engine.begin() as conn:
            conn.execute(insert, {**values, "id": "4" * 32, "name": other_name,
                                  "hash": sha256(other_name.encode("utf-8")).hexdigest()})
            assert conn.scalar(text("SELECT count(*) FROM warehouse_supplier_item_rules")) == 2
        constraints = inspect(engine)
        assert {tuple(c["column_names"]) for c in constraints.get_unique_constraints("warehouse_receipt_line_confirmations")} == {
            ("acceptance_id",), ("lot_id",),
        }
        assert len(constraints.get_foreign_keys("warehouse_receipt_line_confirmations")) == 4
        assert not constraints.get_indexes("warehouse_receipt_line_confirmations")[0]["unique"]
        command.downgrade(config, "0173_shipment_line_lots")
        assert "warehouse_supplier_item_rules" not in inspect(engine).get_table_names()
        assert "supplier_inn" not in {col["name"] for col in inspect(engine).get_columns("warehouse_receipts")}
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM warehouse_receipts")) == 1
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM warehouse_supplier_item_rules")) == 0
    finally:
        engine.dispose()
