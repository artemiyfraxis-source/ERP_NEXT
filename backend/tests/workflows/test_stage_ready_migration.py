"""Миграция 0164 (владелец 29.10.2026): проверки журнала плана принимают этапы «Готово» и «ОТК пройден»; строки
журнала не переписываются, журнал остаётся только дополняемым, откат возвращает проверки 0161."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.modules.production.infrastructure import PLAN_EVENT_CHECKS

BACKEND = Path(__file__).resolve().parents[2]
BEFORE = "0163_approval_pin"
AFTER = "0164_stage_ready_qc_passed"
PROJECT = "708К.4"

_EVENT = (
    "INSERT INTO production_plan_events (id, project, sequence, occurred_at, who, kind, blocker, text, item_index,"
    " value, field, from_value, reason) VALUES (?, ?, ?, '2026-10-29 09:00:00', 'РП', 'Этап КБ', '', '', 'КБ1', ?, '',"
    " ?, '')"
)


def _config(path: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


@pytest.fixture(scope="module")
def at_0163(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("stage-ready") / "0163.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(_config(path), BEFORE)
    return path


@pytest.fixture()
def database(at_0163, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(at_0163, path)
    return path


def _stage(connection, sequence, value, from_value=""):
    connection.execute(_EVENT, (uuid4().hex, PROJECT, sequence, value, from_value))


def _rows(path: Path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT sequence, kind, value, from_value FROM production_plan_events ORDER BY sequence"
        ).fetchall()


def _table_sql(connection) -> str:
    return connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'production_plan_events'"
    ).fetchone()[0]


def test_before_0164_the_new_words_are_refused(database):
    with sqlite3.connect(database) as connection:
        for word in ("Готово", "ОТК пройден"):
            with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_stage_value"):
                _stage(connection, 1, word)


def test_upgrade_accepts_ready_and_qc_passed_keeps_rows_and_the_append_only_journal(database):
    with sqlite3.connect(database) as connection:
        _stage(connection, 1, "ОТК", "Новый")
    before = _rows(database)

    command.upgrade(_config(database), AFTER)

    assert _rows(database) == before  # журнал не переписан
    with sqlite3.connect(database) as connection:
        assert all(name in _table_sql(connection) for name in PLAN_EVENT_CHECKS)
        # Любой порядок (владелец 29.10: «пока что гибкая система»): назад и через этапы — проверка знает только слова.
        _stage(connection, 2, "Готово", "ОТК")
        _stage(connection, 3, "ОТК пройден", "Готово")
        _stage(connection, 4, "Новый", "ОТК пройден")
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_stage_value"):
            _stage(connection, 5, "Готов")
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_from_value"):
            _stage(connection, 5, "ОТК", "ОТК пройдено")
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'правка'")


def test_downgrade_returns_the_0161_checks_and_keeps_the_new_rows(database, capsys):
    command.upgrade(_config(database), AFTER)
    with sqlite3.connect(database) as connection:
        _stage(connection, 1, "Готово", "Новый")
        _stage(connection, 2, "ОТК пройден", "Готово")

    command.downgrade(_config(database), BEFORE)

    assert "«Этап КБ» со словом «Готово»: 2 строк(и) оставлены как есть." in capsys.readouterr().out
    assert [row[2] for row in _rows(database)] == ["Готово", "ОТК пройден"]
    with sqlite3.connect(database) as connection:
        assert "'ОТК пройден'" not in _table_sql(connection)
        with pytest.raises(sqlite3.IntegrityError, match="ck_production_plan_events_stage_value"):
            _stage(connection, 3, "Готово")
        with pytest.raises(sqlite3.DatabaseError, match="только дополняется"):
            connection.execute("UPDATE production_plan_events SET text = 'правка'")
    command.upgrade(_config(database), AFTER)  # повторный проход по строкам с новыми словами не падает
