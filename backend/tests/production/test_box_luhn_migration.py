"""PX-2: migration 0043 moves box and defect numbers issued with the 3/1 check digit to Luhn."""

import sqlite3
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config

from app.modules.production.traceability import format_box_number, validate_box_number

BEFORE = "0042_shipping_project_addressees"
AFTER = "0043_production_box_luhn_check_digits"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _ean(sequence: int) -> str:
    body = f"{sequence:06d}"
    weighted = sum(int(char) * (3 if offset % 2 == 0 else 1) for offset, char in enumerate(reversed(body)))
    return f"{body}-{(10 - weighted % 10) % 10}"


def _insert(connection: sqlite3.Connection, table: str, values: dict) -> str:
    """Insert a row filling every other NOT NULL column with a placeholder of its type (FKs are off)."""
    row_id = uuid4().hex
    row = {"id": row_id}
    for _, name, kind, not_null, _, _ in connection.execute(f"PRAGMA table_info({table})"):
        if name in row or not not_null:
            continue
        kind = kind.upper()
        row[name] = 1 if kind.startswith(("INT", "NUMERIC", "BOOL", "FLOAT")) else (
            "2026-09-25 10:00:00" if kind.startswith(("DATE", "TIME")) else uuid4().hex if kind.startswith("CHAR(32)") else "x"
        )
    row.update(values)
    columns = ", ".join(row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    return row_id


def test_luhn_migration_rewrites_only_numbers_with_the_old_check_digit(tmp_path):
    # 000005: 3/1 gave 5, Luhn gives 9. 000010: both give the same digit, nothing to change.
    old, same, fresh = 5, 10, 42
    assert _ean(old) != format_box_number(old) and _ean(same) == format_box_number(same)
    database_path = tmp_path / "boxes.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        old_box = _insert(connection, "production_boxes", {"number": _ean(old)})
        same_box = _insert(connection, "production_boxes", {"number": _ean(same)})
        fresh_box = _insert(connection, "production_boxes", {"number": format_box_number(fresh)})
        open_box = _insert(connection, "production_boxes", {"number": None})
        broken_box = _insert(connection, "production_boxes", {"number": "123456-0"})
        defect = _insert(connection, "production_defects", {"number": f"Б-{_ean(old)}"})
        inspection = _insert(connection, "production_qc_inspections", {"defect_number": f"Б-{_ean(old)}"})

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        boxes = dict(connection.execute("SELECT id, number FROM production_boxes"))
        defects = dict(connection.execute("SELECT id, number FROM production_defects"))
        inspections = dict(connection.execute("SELECT id, defect_number FROM production_qc_inspections"))
    assert boxes[old_box] == format_box_number(old) == "000005-9"
    assert validate_box_number(boxes[old_box]) == boxes[old_box]
    assert boxes[same_box] == format_box_number(same)
    assert boxes[fresh_box] == format_box_number(fresh)
    assert boxes[open_box] is None
    # Neither scheme: not ours to guess, left for a human.
    assert boxes[broken_box] == "123456-0"
    assert defects[defect] == inspections[inspection] == "Б-000005-9"

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT number FROM production_boxes WHERE id = ?", (old_box,)).fetchone() == ("000005-9",)
