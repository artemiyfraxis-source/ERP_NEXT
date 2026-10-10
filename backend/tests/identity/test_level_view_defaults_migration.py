"""ID-6 on the level matrix: migration 0089 puts back the «просмотр» floor on «Склад», «Лист закупки», «Приёмка»
without touching a stored cell or a cell an administrator has ever saved."""

import importlib.util
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

from app.modules.identity.domain import FULL_ACCESS_LEVELS, LEVEL_SEED

BACKEND = Path(__file__).parents[2]
BEFORE, AFTER = "0088_api_tokens_assembly_drafts", "0089_level_view_defaults"
SECTIONS = {"procurement", "receiving", "warehouse"}


def _migration():
    path = BACKEND / "migrations" / "versions" / "0089_level_view_defaults.py"
    spec = importlib.util.spec_from_file_location("level_view_defaults", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(database_path: Path) -> Config:
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _cells(database_path: Path) -> dict[tuple[str, str], str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT level, section, mode FROM identity_level_access WHERE section IN "
            "('procurement', 'receiving', 'warehouse')"
        )
        return {(level, section): mode for level, section, mode in rows}


def _events(database_path: Path, event_type: str) -> list[dict]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT details FROM audit_events WHERE event_type = ?", (event_type,))
        return [json.loads(details) for (details,) in rows]


def test_frozen_defaults_are_the_level_seed_cells_of_the_three_sections():
    seeded = {
        (level, section)
        for level, row in LEVEL_SEED.items()
        for section in row
        if section in SECTIONS and level not in FULL_ACCESS_LEVELS
    }
    assert set(_migration().VIEW_DEFAULTS) == seeded


def test_view_floor_fills_empty_cells_only_and_keeps_the_administrators_choices(tmp_path):
    database_path = tmp_path / "levels.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        # The 0086 intersection left gaps; an administrator cleared foreman × procurement and raised operator × warehouse.
        connection.execute(
            "DELETE FROM identity_level_access WHERE section IN ('procurement', 'receiving', 'warehouse') "
            "AND level IN ('commercial', 'foreman', 'operator')"
        )
        connection.execute("INSERT INTO identity_level_access VALUES ('operator', 'warehouse', 'edit')")
        connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, 'identity.level_access_saved', ?, 'Админ', 'Дирекция', "
            "'2026-10-08 10:00:00', ?)",
            (uuid4().hex, uuid4().hex, uuid4().hex, json.dumps({
                "level": "foreman", "changes": json.dumps({"procurement": ["view", ""]}),
            })),
        )
        tech_lead_before = {
            section: mode for (level, section), mode in _cells(database_path).items() if level == "tech_lead"
        }

    command.upgrade(config, AFTER)
    cells = _cells(database_path)

    assert ("foreman", "procurement") not in cells  # the administrator closed it
    assert cells[("operator", "warehouse")] == "edit"  # a stored cell stays
    for cell in [("commercial", "procurement"), ("commercial", "warehouse"), ("foreman", "receiving"),
                 ("foreman", "warehouse"), ("operator", "receiving")]:
        assert cells[cell] == "view", cell
    assert ("commercial", "receiving") not in cells  # not in the owner's plan
    assert {s: m for (level, s), m in cells.items() if level == "tech_lead"} == tech_lead_before
    for level in FULL_ACCESS_LEVELS:
        assert {cells[(level, section)] for section in SECTIONS} == {"edit"}
    [event] = _events(database_path, "identity.level_view_defaults_seeded")
    assert sorted(map(tuple, json.loads(event["cells"]))) == sorted([
        ("commercial", "procurement"), ("commercial", "warehouse"), ("foreman", "receiving"),
        ("foreman", "warehouse"), ("operator", "receiving"),
    ])

    with sqlite3.connect(database_path) as connection:
        # Raised by an administrator after the migration: the downgrade leaves it.
        connection.execute(
            "UPDATE identity_level_access SET mode = 'edit' WHERE level = 'foreman' AND section = 'warehouse'"
        )
    command.downgrade(config, BEFORE)
    after_downgrade = _cells(database_path)
    assert ("commercial", "procurement") not in after_downgrade
    assert ("operator", "receiving") not in after_downgrade
    assert after_downgrade[("foreman", "warehouse")] == "edit"
    assert after_downgrade[("operator", "warehouse")] == "edit"
    assert _events(database_path, "identity.level_view_defaults_seeded") == []


def test_a_full_seeded_database_gets_nothing_and_no_event(tmp_path):
    database_path = tmp_path / "fresh.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    before = _cells(database_path)

    command.upgrade(config, AFTER)

    assert _cells(database_path) == before
    assert _events(database_path, "identity.level_view_defaults_seeded") == []
