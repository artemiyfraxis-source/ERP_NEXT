import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parents[2]
BEFORE, AFTER = "0165_split_lug_cable_tabs", "0166_kb_progress"
BASE_INSERT = ("INSERT INTO production_plan_events (id,project,sequence,occurred_at,who,kind,blocker,text,item_index,value,field,from_value,reason) "
               "VALUES (?, '708К.4', ?, '2026-11-02 12:00:00', 'Мастер', ?, '', 'история', 'КБ1', '', '', '', '')")


def config(path):
    result = Config(str(BACKEND / "alembic.ini"))
    result.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return result


@pytest.fixture(scope="module")
def before(tmp_path_factory):
    path = tmp_path_factory.mktemp("kb-progress") / "before.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(config(path), BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(BASE_INSERT, (uuid4().hex, 1, "Комментарий"))
    return path


@pytest.fixture()
def database(before, tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(before, path)
    return path


def test_upgrade_keeps_old_history_unknown_and_append_only(database):
    with sqlite3.connect(database) as connection:
        old = connection.execute("SELECT * FROM production_plan_events").fetchall()
        assert "progress_run" not in {row[1] for row in connection.execute("PRAGMA table_info(production_plan_events)")}
    command.upgrade(config(database), AFTER)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id,project,sequence,occurred_at,who,kind,blocker,text,deadline,item_index,value,field,from_value,reason FROM production_plan_events").fetchall() == old
        assert connection.execute("SELECT progress_run,progress_started,progress_done,progress_qc,progress_passed FROM production_plan_events").fetchone() == (None,) * 5
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'новое'")
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [(AFTER,)]


def test_progress_constraints_allow_lowering_inconsistency_but_refuse_junk(database):
    command.upgrade(config(database), AFTER)
    sql = BASE_INSERT.replace("value,field,from_value,reason)", "value,field,from_value,reason,progress_run,progress_started,progress_done,progress_qc)").replace("'', '', '', '')", "'', '', '', '', ?, ?, ?, ?)")
    with sqlite3.connect(database) as connection:
        # Lowering done below qc is deliberately valid; stage order is a warning, not a CHECK.
        connection.execute(sql, (uuid4().hex, 2, "Количество КБ", 200, 150, 20, 150))
        for run, started, done, qc in [(200, -1, 0, None), (200, 201, 0, None), (200, 1.5, 0, None), (None, 1, 0, None), (200, None, None, None)]:
            with pytest.raises(sqlite3.IntegrityError, match="progress"):
                connection.execute(sql, (uuid4().hex, 3, "Количество КБ", run, started, done, qc))
    with pytest.raises(RuntimeError, match="истории"):
        command.downgrade(config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT progress_done,progress_qc FROM production_plan_events WHERE sequence=2").fetchone() == (20, 150)


def test_downgrade_on_a_copy_without_progress_rows_keeps_history_and_trigger(database):
    command.upgrade(config(database), AFTER)
    command.downgrade(config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT text FROM production_plan_events").fetchone() == ("история",)
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'новое'")
    command.upgrade(config(database), AFTER)
