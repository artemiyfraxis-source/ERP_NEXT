"""Миграция 0124 (владелец 14.10.2026): уровни «Менеджер по проектам» и «Кладовщик» получают строки матрицы из
LEVEL_SEED (только недостающие клетки), «Администратор» упразднён — его носители переходят в superadmin, его строки
матрицы удаляются; откат убирает новые уровни и возвращает людей и `admin`."""

import importlib.util
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.modules.identity.domain import LEVEL_SEED

BACKEND = Path(__file__).parents[2]
BEFORE, AFTER = "0123_ru_holidays", "0124_levels_pm_storekeeper"
MIGRATION = BACKEND / "migrations" / "versions" / f"{AFTER}.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0124", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(database_path: Path) -> Config:
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _rows(path: Path) -> dict[str, dict[str, str]]:
    with sqlite3.connect(path) as connection:
        result: dict[str, dict[str, str]] = {}
        for level, section, mode in connection.execute("SELECT level, section, mode FROM identity_level_access"):
            result.setdefault(level, {})[section] = mode
        return result


def _levels(path: Path) -> dict[str, str]:
    with sqlite3.connect(path) as connection:
        return dict(connection.execute("SELECT login, access_level FROM identity_users"))


def _events(path: Path) -> list[dict]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT details FROM audit_events WHERE event_type = 'identity.admin_level_retired'"
        )
        return [json.loads(details) for (details,) in rows]


def _add_user(path: Path, login: str, level: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO identity_users (id, login, email, name, department, role, site, active, job_title, "
            "qualification, access_level, shop_worker, site_master) VALUES (?, ?, ?, ?, '', 'staff', NULL, 1, '', '', "
            "?, 0, 0)",
            (uuid4().hex, login, f"{login}@example.test", login, level),
        )


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "levels.db"
    command.upgrade(_config(path), BEFORE)
    return path


def test_frozen_rows_are_the_level_seed_of_the_two_new_levels():
    frozen = _module().NEW_LEVEL_ROWS
    assert frozen == {level: {section: mode.value for section, mode in LEVEL_SEED[level].items()}
                      for level in ("pm", "storekeeper")}
    assert _module().DESTRUCTIVE is False


def test_upgrade_adds_the_new_rows_moves_admin_and_downgrade_restores(database, capsys):
    _add_user(database, "boss", "superadmin")
    _add_user(database, "stray", "admin")
    _add_user(database, "worker", "operator")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO identity_level_access (level, section, mode) VALUES ('admin', 'projects', 'edit')"
        )
        # A cell already stored for a new level stays as stored.
        connection.execute("INSERT INTO identity_level_access (level, section, mode) VALUES ('pm', 'projects', 'view')")
    assert "admin" in _rows(database)
    other_before = {level: row for level, row in _rows(database).items() if level not in ("pm", "storekeeper", "admin")}

    command.upgrade(_config(database), AFTER)
    out = capsys.readouterr().out
    assert "было" in out and "стало" in out and "переведено admin → superadmin: 1" in out

    rows = _rows(database)
    assert "admin" not in rows
    assert rows["pm"] == {**_module().NEW_LEVEL_ROWS["pm"], "projects": "view"}
    assert rows["storekeeper"] == _module().NEW_LEVEL_ROWS["storekeeper"]
    assert {level: row for level, row in rows.items() if level not in ("pm", "storekeeper")} == other_before
    assert _levels(database) == {"boss": "superadmin", "stray": "superadmin", "worker": "operator"}
    assert len(_events(database)) == 1

    # The owner then sets people on the new levels (on prod — through «Сотрудники»).
    _add_user(database, "ulyana", "pm")
    _add_user(database, "alexey", "storekeeper")
    command.downgrade(_config(database), BEFORE)
    rows = _rows(database)
    assert "pm" not in rows and "storekeeper" not in rows
    assert {level: row for level, row in rows.items() if level != "admin"} == other_before
    assert _levels(database) == {
        "boss": "superadmin", "stray": "admin", "worker": "operator", "ulyana": "operator", "alexey": "foreman",
    }
    assert _events(database) == []
    assert "возвращено admin: 1" in capsys.readouterr().out


def test_upgrade_without_admin_holders_writes_no_event_and_is_idempotent_on_rows(database):
    command.upgrade(_config(database), AFTER)
    assert _events(database) == []
    first = _rows(database)
    command.downgrade(_config(database), BEFORE)
    command.upgrade(_config(database), AFTER)
    assert _rows(database) == first
