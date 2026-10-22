import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from tests.migrations.seed_any_revision import seed

REVISION = "0142_production_vswr_measurements"
TABLE = "production_vswr_measurements"
COLUMNS = (
    "id", "serial_id", "attempt", "instrument_id", "band_from_mhz", "band_to_mhz", "max_vswr", "max_vswr_mhz",
    "norm", "norm_vswr_max", "verdict", "verdict_manual", "verdict_reason", "inspector_id", "inspector_name",
    "measured_at", "file_name", "content", "points", "file_meta", "trace",
)


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _row(serial: str, attempt: int = 1, **changes) -> tuple:
    values = {
        "id": uuid.uuid4().hex, "serial_id": serial, "attempt": attempt, "instrument_id": uuid.uuid4().hex,
        "band_from_mhz": 1000, "band_to_mhz": 3000, "max_vswr": 1.3, "max_vswr_mhz": 2400,
        "norm": json.dumps({"status": "ok", "vswrMax": 1.35}), "norm_vswr_max": 1.35, "verdict": "pass",
        "verdict_manual": 0, "verdict_reason": "", "inspector_id": uuid.uuid4().hex, "inspector_name": "ОТК",
        "measured_at": "2026-10-22 10:00:00", "file_name": "sn1.s1p", "content": b"# MHz S RI R 50\n",
        "points": 1000, "file_meta": "{}", "trace": "[]",
    }
    values.update(changes)
    return tuple(values[column] for column in COLUMNS)


def test_vswr_table_refuses_inconsistent_measurements(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "vswr.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    insert = f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"
    serial = uuid.uuid4().hex
    with sqlite3.connect(path) as connection:
        connection.execute(insert, _row(serial))
        connection.execute(insert, _row(serial, 2, max_vswr=1.5, verdict="fail"))
        connection.execute(insert, _row(serial, 3, norm_vswr_max=None, verdict_manual=1, verdict_reason="По заказчику"))
        for bad in (
            _row(serial, 1),  # та же попытка изделия
            _row(serial, 0),  # попытка с нуля
            _row(uuid.uuid4().hex, band_from_mhz=3000, band_to_mhz=1000),  # полоса наоборот
            _row(uuid.uuid4().hex, band_from_mhz=0),  # полоса от нуля
            _row(uuid.uuid4().hex, max_vswr=0.9),  # КСВН меньше 1
            _row(uuid.uuid4().hex, max_vswr_mhz=3500),  # максимум вне полосы
            _row(uuid.uuid4().hex, verdict="maybe"),  # вердикт не из списка
            _row(uuid.uuid4().hex, verdict="pass", max_vswr=1.5),  # автоматический «годно» выше нормы
            _row(uuid.uuid4().hex, verdict="fail", max_vswr=1.2),  # автоматический «не годно» в норме
            _row(uuid.uuid4().hex, norm_vswr_max=None),  # автоматический без нормы
            _row(uuid.uuid4().hex, norm_vswr_max=0.5, verdict_manual=1, verdict_reason="x"),  # норма меньше 1
            _row(uuid.uuid4().hex, verdict_manual=1, verdict_reason="  "),  # ручной без причины
            _row(uuid.uuid4().hex, file_name=" "),  # файл без имени
            _row(uuid.uuid4().hex, content=b""),  # пустой файл
            _row(uuid.uuid4().hex, content=b"x" * (2 * 1024 * 1024 + 1)),  # больше 2 МБ
            _row(uuid.uuid4().hex, points=0),  # без точек
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, bad)
        assert connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone() == (3,)

    command.downgrade(config, "-1")  # на родителя, каким бы он ни был после перестановки цепочки
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert TABLE not in tables


def test_ci_seeder_fills_the_vswr_table_when_it_is_head_minus_one(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "seeded.db"
    command.upgrade(_config(path), REVISION)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inserted = seed(engine)
    finally:
        engine.dispose()
    assert inserted[TABLE] > 0
