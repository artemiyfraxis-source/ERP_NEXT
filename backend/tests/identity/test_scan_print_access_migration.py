"""Миграция 0159 (владелец 28.10.2026): «Сканирование документов» — уровням с правкой «Склада» или «Приёмки»,
«Печать документов» — каждому уровню с записанной строкой; только недостающие клетки, повторный запуск ничего не
меняет; сканер, выбранный человеком, — у задания с адресатом-агентом; откат убирает всё это."""

import importlib.util
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).parents[2]
AFTER = "0159_scan_print_access"
MIGRATION = BACKEND / "migrations" / "versions" / f"{AFTER}.py"


def _module():
    spec = importlib.util.spec_from_file_location("migration_0159", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BEFORE = _module().down_revision  # the revision it follows (re-parented on merge)


def _config(path: Path) -> Config:
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _matrix(path: Path) -> dict[str, dict[str, str]]:
    with sqlite3.connect(path) as connection:
        result: dict[str, dict[str, str]] = {}
        for level, section, mode in connection.execute("SELECT level, section, mode FROM identity_level_access"):
            result.setdefault(level, {})[section] = mode
        return result


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    command.upgrade(_config(path), BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM identity_level_access")
        connection.executemany("INSERT INTO identity_level_access (level, section, mode) VALUES (?, ?, ?)", [
            ("storekeeper", "warehouse", "edit"), ("storekeeper", "receiving", "edit"),
            ("foreman", "receiving", "edit"), ("foreman", "warehouse", "view"),
            ("operator", "warehouse", "view"),
            ("pm", "projects", "edit"), ("pm", "scan", "view"),  # a cell already stored stays as stored
            ("commercial", "print", "view"),
            ("tech_lead", "shipping", "edit"),  # the shipment УПД scan (#747) sat behind shipping edit
        ])
        user = uuid4().hex
        connection.execute(
            "INSERT INTO identity_users (id, login, email, name, department, role, site, active, job_title, "
            "qualification, access_level, shop_worker, site_master) VALUES (?, 'ivan', 'ivan@example.test', 'Иван', "
            "'', 'staff', NULL, 1, '', '', 'operator', 0, 0)", (user,))
        connection.execute("INSERT INTO identity_user_level_access (user_id, section, mode) VALUES (?, 'warehouse', "
                           "'edit')", (user,))
    return path


def test_upgrade_adds_scan_and_print_without_taking_anything_and_downgrade_removes_them(database, capsys):
    before = _matrix(database)
    command.upgrade(_config(database), AFTER)
    out = capsys.readouterr().out
    assert "добавлено клеток scan 3, print 5" in out
    assert "только личным исключением (scan не получили): 1" in out, "Иван scanned by his exception: reported"

    after = _matrix(database)
    assert after["storekeeper"] == {**before["storekeeper"], "scan": "edit", "print": "edit"}
    assert after["foreman"] == {**before["foreman"], "scan": "edit", "print": "edit"}, "receiving edit scanned too"
    assert after["operator"] == {**before["operator"], "print": "edit"}, "warehouse view never scanned"
    assert after["pm"] == {"projects": "edit", "scan": "view", "print": "edit"}
    assert after["commercial"] == {"print": "view"}, "a stored cell is not touched"
    assert after["tech_lead"] == {"shipping": "edit", "scan": "edit", "print": "edit"}, "shipping edit scanned too"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT section, mode FROM identity_user_level_access").fetchall() == [
            ("warehouse", "edit")], "no personal exception is added"

    command.downgrade(_config(database), BEFORE)
    assert _matrix(database) == {level: {s: m for s, m in row.items() if s not in ("scan", "print")}
                                 for level, row in before.items() if level != "commercial"}
    command.upgrade(_config(database), AFTER)  # again after a downgrade: the same cells
    assert {level: row for level, row in _matrix(database).items() if level not in ("pm", "commercial")} == {
        level: row for level, row in after.items() if level not in ("pm", "commercial")}


def test_a_chosen_scanner_sits_only_on_a_job_for_an_agent(database):
    command.upgrade(_config(database), AFTER)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("INSERT INTO warehouse_scan_agents (machine_id, first_seen, last_seen) "
                           "VALUES ('m-1', '2026-10-28', '2026-10-28')")
        job = ("INSERT INTO warehouse_scan_jobs (id, status, requested_by, created_at, target_machine_id, "
               "target_scanner) VALUES (?, 'pending', 'Кладовщик', '2026-10-28', ?, ?)")
        connection.execute(job, (uuid4().hex, "m-1", "{wia-2}"))
        connection.execute(job, (uuid4().hex, "m-1", None))
        connection.execute(job, (uuid4().hex, None, None))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(job, (uuid4().hex, None, "{wia-2}"))  # no agent — no scanner
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(job, (uuid4().hex, "m-1", ""))  # a chosen scanner has a name
    assert _module().DESTRUCTIVE is False
