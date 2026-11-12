import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

PARENT = "0166_kb_progress"
REVISION = "0167_instrument_verification_pending"
TABLE = "production_instruments"
RELATED = (TABLE, "production_qc_instruments", "production_vswr_measurements")
COLUMNS = ("id", "version", "inventory_number", "name", "instrument_type", "verification_number", "verified_at", "verification_due", "status")
INSERT = f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})"


def _config(path):
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _row(**changes):
    values = dict(zip(COLUMNS, (uuid4().hex, 1, f"TEST-{uuid4().hex}", "Тестовый прибор", "Тестер", "TEST-PV", "2026-01-01 00:00:00", "2027-01-01 00:00:00", "Действует")))
    values.update(changes)
    return tuple(values[key] for key in COLUMNS)


def _snapshot(path):
    with sqlite3.connect(path) as connection:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in RELATED}


def test_upgrade_and_clean_downgrade_preserve_instruments_and_references(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "instruments.db"
    config = _config(path)
    command.upgrade(config, PARENT)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inserted = seed(engine, rows=2)
    finally:
        engine.dispose()
    assert all(inserted[table] == 2 for table in RELATED)
    with sqlite3.connect(path) as connection:
        connection.execute(INSERT, _row(status="Не используется"))
        before_fk = {table: connection.execute(f"PRAGMA foreign_key_list({table})").fetchall() for table in RELATED}
    before = _snapshot(path)
    command.upgrade(config, REVISION)
    assert _snapshot(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {table: connection.execute(f"PRAGMA foreign_key_list({table})").fetchall() for table in RELATED} == before_fk
        indexes = {row[1] for row in connection.execute(f"PRAGMA index_list({TABLE})")}
        assert {"ix_production_instruments_status", "ix_production_instruments_verification_due"} <= indexes
    command.downgrade(config, PARENT)
    assert _snapshot(path) == before
    with sqlite3.connect(path) as connection:
        columns = {row[1]: row[3] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
        assert all(columns[key] for key in ("verification_number", "verified_at", "verification_due"))


def test_constraints_and_blocked_downgrade_keep_pending_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "pending.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    pending = _row(status="Поверка не подтверждена", verification_number=None, verified_at=None, verification_due=None)
    with sqlite3.connect(path) as connection:
        connection.execute(INSERT, pending)
        for bad in (
            _row(status="unknown"), _row(verification_number=None), _row(verification_number=" "),
            _row(verified_at=None), _row(verification_due=None), _row(verification_due="2025-01-01 00:00:00"),
            _row(status="Поверка не подтверждена"),
            _row(status="Поверка не подтверждена", verification_number=None, verified_at=None),
            _row(inventory_number=pending[2]),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(INSERT, bad)
    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        command.downgrade(config, PARENT)
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT * FROM {TABLE}").fetchall() == [pending]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (REVISION,)
        columns = {row[1]: row[3] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
        assert all(not columns[key] for key in ("verification_number", "verified_at", "verification_due"))


def test_upgrade_refuses_invalid_old_data_without_rewriting_it(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "invalid.db"
    config = _config(path)
    command.upgrade(config, PARENT)
    invalid = _row(status="unknown")
    with sqlite3.connect(path) as connection:
        connection.execute(INSERT, invalid)
    with pytest.raises(RuntimeError, match="Existing instruments violate"):
        command.upgrade(config, REVISION)
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT * FROM {TABLE}").fetchall() == [invalid]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (PARENT,)


V2_PARENT = "0174_supplier_item_rules"
V2 = "0175_instrument_kinds_files"


def test_v2_upgrade_four_existing_rows_and_clean_downgrade(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "four.db"
    config = _config(path)
    command.upgrade(config, V2_PARENT)
    rows = [_row(inventory_number="ВАЦ-01"), *(
        _row(inventory_number=number, status="Поверка не подтверждена", verification_number=None, verified_at=None, verification_due=None)
        for number in ("ОБ.040", "ОБ.042", "ОБ.086")
    )]
    with sqlite3.connect(path) as connection:
        connection.executemany(INSERT, rows)
    command.upgrade(config, V2)
    with sqlite3.connect(path) as connection:
        actual = connection.execute(f"SELECT {', '.join(COLUMNS)} FROM {TABLE} ORDER BY inventory_number").fetchall()
        assert actual == sorted(rows, key=lambda row: row[2])
        assert connection.execute(f"SELECT kind, parent_id, quantity, serial_number, manufacturer, doc_designation, storage_location, warehouse_grn, warehouse_writeoff_id FROM {TABLE}").fetchall() == [
            ("Средство измерений", None, 1, "", "", "", "", "", None)
        ] * 4
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert "ix_production_instrument_files_instrument_id" in {row[1] for row in connection.execute("PRAGMA index_list(production_instrument_files)")}
    command.downgrade(config, V2_PARENT)
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT * FROM {TABLE} ORDER BY inventory_number").fetchall() == sorted(rows, key=lambda row: row[2])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v2_checks_foreign_keys_files_and_blocked_downgrade(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "checks-v2.db"
    config = _config(path)
    command.upgrade(config, V2)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seeded = seed(engine, rows=2)
        assert seeded[TABLE] == 2 and seeded["production_instrument_files"] == 2
    finally:
        engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        parent_id = connection.execute(f"SELECT id FROM {TABLE} LIMIT 1").fetchone()[0]
        user_id = connection.execute("SELECT id FROM identity_users LIMIT 1").fetchone()[0]
        def insert(**changes):
            row = dict(zip(COLUMNS, _row()))
            row.update(changes)
            connection.execute(f"INSERT INTO {TABLE} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
        accessory = {"kind": "Принадлежность", "parent_id": parent_id,
                     "verification_number": None, "verified_at": None, "verification_due": None}
        insert(**accessory, quantity=3)
        insert(**accessory, quantity=1, status="Не используется")
        self_id = uuid4().hex
        for changes in (
            {"kind": "Неизвестный"}, {"id": self_id, "parent_id": self_id}, {"parent_id": uuid4().hex},
            {"quantity": 0}, {"quantity": 2}, {"status": "Неизвестный"}, {"verification_number": None},
            {"verification_number": " "}, {"verified_at": None}, {"verification_due": None},
            {"status": "Поверка не подтверждена"}, {"verification_due": "2025-01-01"},
            {**accessory, "parent_id": None}, {**accessory, "verification_number": "ПВ"},
            {**accessory, "verified_at": "2026-01-01"}, {**accessory, "verification_due": "2027-01-01"},
            {**accessory, "status": "Поверка не подтверждена"},
        ):
            with pytest.raises(sqlite3.IntegrityError):
                insert(**changes)
        base_file = {"instrument_id": parent_id, "kind": "Паспорт", "file_name": "паспорт.pdf", "content": b"x",
                     "uploaded_at": "2026-11-12T10:00:00+03:00", "uploaded_by": user_id}
        for changes in ({"kind": "Неверный"}, {"file_name": " "}, {"content": b""},
                        {"content": b"x" * (20 * 1024 * 1024 + 1)}, {"instrument_id": uuid4().hex}, {"uploaded_by": uuid4().hex}):
            row = {"id": uuid4().hex, **base_file, **changes}
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(f"INSERT INTO production_instrument_files ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(f"DELETE FROM {TABLE} WHERE id = ?", (parent_id,))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(RuntimeError, match="Нельзя отменить"):
        command.downgrade(config, V2_PARENT)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (V2,)
        assert connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (4,)
        assert connection.execute("SELECT count(*) FROM production_instrument_files").fetchone() == (2,)


def test_v2_upgrade_seeded_references_survive_table_rebuild(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "references-v2.db"
    config = _config(path)
    command.upgrade(config, V2_PARENT)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    before = _snapshot(path)
    command.upgrade(config, V2)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in RELATED:
            columns = ', '.join(COLUMNS) if table == TABLE else '*'
            assert connection.execute(f"SELECT {columns} FROM {table} ORDER BY 1").fetchall() == before[table]


@pytest.mark.parametrize("column,value", [
    ("serial_number", "SN-123"), ("serial_number", " "), ("manufacturer", "Завод"),
    ("doc_designation", "ВАЦ"), ("storage_location", "ОТК"), ("warehouse_grn", "ГРН-1"),
    ("quantity", 2), ("parent_id", "parent"), ("warehouse_writeoff_id", "1234567890abcdef1234567890abcdef"),
])
def test_v2_downgrade_refuses_metadata_without_dropping_it(tmp_path, monkeypatch, column, value):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "metadata.db"
    config = _config(path)
    command.upgrade(config, V2)
    parent, child = _row(), _row()
    with sqlite3.connect(path) as connection:
        connection.executemany(INSERT, [parent, child])
        if column == "quantity":
            # Exercise the loss guard independently of the measuring quantity constraint.
            connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f"UPDATE {TABLE} SET {column} = ? WHERE id = ?", (parent[0] if column == "parent_id" else value, child[0]))
        before = connection.execute(f"SELECT * FROM {TABLE} ORDER BY id").fetchall()
    with pytest.raises(RuntimeError, match="сохранены дополнительные сведения или связи СИ"):
        command.downgrade(config, V2_PARENT)
    with sqlite3.connect(path) as connection:
        assert connection.execute(f"SELECT * FROM {TABLE} ORDER BY id").fetchall() == before
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (V2,)
        assert connection.execute("SELECT count(*) FROM production_instrument_files").fetchone() == (0,)
