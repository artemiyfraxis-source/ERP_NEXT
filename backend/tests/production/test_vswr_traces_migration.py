import sqlite3
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

PARENT = "0181_vna_sessions"
REVISION = "0182_vswr_traces"
TABLE = "production_vswr_measurements"
TRACES = "production_vswr_traces"
FILES = "production_vswr_files"


def test_migration_preserves_historical_evidence_and_protects_new_rows(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "vswr-traces.db"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    command.upgrade(config, PARENT)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=2)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as db:
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({TABLE})")]
        before = db.execute(f"SELECT {','.join(columns)} FROM {TABLE} ORDER BY id").fetchall()
        counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in (TABLE, "production_serials", "production_instruments", "warehouse_scan_jobs")}
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        assert db.execute(f"SELECT {','.join(columns)} FROM {TABLE} ORDER BY id").fetchall() == before
        assert db.execute(f"SELECT count(*) FROM {TRACES}").fetchone() == (0,)
        assert db.execute(f"SELECT count(*) FROM {FILES}").fetchone() == (0,)
        assert db.execute(f"SELECT count(*) FROM {TABLE} WHERE complete IS NOT NULL OR evidence_key IS NOT NULL").fetchone() == (0,)
        assert {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in counts} == counts
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        assert db.execute(f"SELECT * FROM {TABLE} ORDER BY id").fetchall() == before
    command.upgrade(config, REVISION)
    identifier = before[0][columns.index("id")]
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        sql = f"INSERT INTO {FILES} (measurement_id,assembly_end,file_name,content,file_meta) VALUES (?,?,?,?,?)"
        good = (identifier, "A", "a.s1p", b"original evidence", "{}")
        for index, value in ((0, uuid4().hex), (1, "C"), (1, None), (2, " "), (3, b""), (3, b"x" * (2 * 1024 * 1024 + 1)), (4, None)):
            bad = list(good)
            bad[index] = value
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql, bad)
        db.execute(sql, good)
        db.execute(sql, (identifier, "B", "b.s1p", b"other evidence", "{}"))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, good)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"DELETE FROM {TABLE} WHERE id=?", (identifier,))
    # Files alone must protect evidence even before trace/evaluation rows are present.
    with pytest.raises(RuntimeError, match="многотрассовые"):
        command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        sql = f"INSERT INTO {TRACES} (measurement_id,name,quantity,points,trace,samples,complex_samples) VALUES (?,?,?,?,?,?,?)"
        good = (identifier, "S11", "vswr", 2, "[]", "[[1000000000,1.1],[3000000000,1.2]]", "[]")
        for index, value in ((0, uuid4().hex), (1, "S33"), (2, "magnitude"), (3, 1), (4, None), (5, None), (6, None)):
            bad = list(good)
            bad[index] = value
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql, bad)
        db.execute(sql, good)
        db.execute(sql, (identifier, "A:S11", *good[2:]))
        db.execute(sql, (identifier, "B:S11", *good[2:]))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, good)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"DELETE FROM {TABLE} WHERE id=?", (identifier,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"UPDATE {TABLE} SET complete=0, verdict='pass' WHERE id=?", (identifier,))
        # An incomplete result can fail below the numerical limit without becoming a manual verdict.
        db.execute(f"UPDATE {TABLE} SET complete=0, verdict='fail', verdict_manual=0, "
                   "evidence_key=?, port_mapping=?, required_ends=?, end_results=? WHERE id=?",
                   ("a" * 64, '{"1":"A","2":"B"}', '["A","B"]', '[]', identifier))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"UPDATE {TABLE} SET verdict='pass' WHERE id=?", (identifier,))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"UPDATE {TABLE} SET evidence_key='short' WHERE id=?", (identifier,))
        # A different attempt cannot reuse the same file fingerprint for the same serial.
        insert_columns = [r[1] for r in db.execute(f"PRAGMA table_info({TABLE})")]
        values = list(db.execute(f"SELECT {','.join(insert_columns)} FROM {TABLE} WHERE id=?", (identifier,)).fetchone())
        values[insert_columns.index("id")] = uuid4().hex
        values[insert_columns.index("attempt")] += 10
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"INSERT INTO {TABLE} ({','.join(insert_columns)}) VALUES ({','.join('?' for _ in values)})", values)
    with pytest.raises(RuntimeError, match="многотрассовые"):
        command.downgrade(config, PARENT)
