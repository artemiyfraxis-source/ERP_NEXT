"""Миграция 0084: у штучных строк набора прежние 5 % брака становятся 0, метраж и ручные значения не трогаются."""

import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BEFORE = "0083_procurement_sheet_fields"
AFTER = "0084_kit_piece_scrap_zero"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def test_old_piece_default_scrap_becomes_zero(tmp_path):
    database_path = tmp_path / "kits.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    assemblies = [{"index": "КБ1", "name": "Жгут", "run": "10", "components": [
        {"art": "43025-0400", "type": "Разъём", "name": "Корпус", "link": "", "per": "2", "scrap": "5", "lot": None},
        {"art": "UL1007", "type": "Провод", "name": "", "link": "", "per": "1.5", "scrap": "5", "lot": None},
        {"art": "39-00-0039", "type": "Терминал", "name": "", "link": "", "per": "4", "scrap": "3", "lot": None},
    ]}]
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO techcard_kitsets (project, project_key, version, customer, calc_source, assemblies, created_at,"
            " updated_at, updated_by) VALUES ('708К.4', '708К.4', 1, '', '', ?, '2026-10-01 10:00:00',"
            " '2026-10-01 10:00:00', 'Технолог')",
            (json.dumps(assemblies, ensure_ascii=False),),
        )
    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        stored = json.loads(connection.execute("SELECT assemblies FROM techcard_kitsets").fetchone()[0])
    assert [item["scrap"] for item in stored[0]["components"]] == ["0", "5", "3"]
