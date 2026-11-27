"""0222_production_serial_factory_number: заводской номер FRX.<проект>.<изделие>.<k> на изделии (владелец 27.11.2026)."""

import sqlite3
from uuid import uuid4

from alembic import command
from sqlalchemy import create_engine
import pytest

from tests.migrations.seed_any_revision import seed
from tests.production.test_box_luhn_migration import _config

PARENT = "0221_vna_calibration_only_check"
REVISION = "0222_production_serial_factory_number"


def _rows(db):
    return db.execute("SELECT id, serial_number, task_id, position, status FROM production_serials ORDER BY id").fetchall()


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "serials.db"
    config = _config(path)
    command.upgrade(config, PARENT)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        seed(engine, rows=3)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as db:
        before = _rows(db)
    assert before
    command.upgrade(config, REVISION)
    return config, path, before


def _number(db, row_id, k, product, number):
    db.execute("UPDATE production_serials SET item_serial = ?, product_designation = ?, factory_number = ? WHERE id = ?",
               (k, product, number, row_id))


def _serial(db, position):
    task = db.execute("SELECT task_id FROM production_serials LIMIT 1").fetchone()[0]
    row_id = uuid4().hex
    db.execute("INSERT INTO production_serials (id, serial_number, task_id, position, status) VALUES (?, ?, ?, ?, ?)",
               (row_id, f"ПЗ-9999-{position:04d}", task, 100 + position, "Изготовлено"))
    return row_id


def test_existing_serials_survive_with_empty_factory_numbers(database):
    _config_, path, before = database
    with sqlite3.connect(path) as db:
        assert _rows(db) == before
        assert db.execute("SELECT count(*) FROM production_serials WHERE item_serial IS NOT NULL "
                          "OR product_designation IS NOT NULL OR factory_number IS NOT NULL").fetchone() == (0,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(("k", "product", "number"), [
    (3, "КБ1", "FRX.831К.КБ1.3"),
    (7, "34.226", "FRX.831К.34.226.7"),
    (12, "120907/AA", "FRX.708К.4.120907/AA.12"),
    (1, "A-1", "FRX.1.A-1.1"),
])
def test_both_designation_forms_are_accepted(database, k, product, number):
    _config_, path, _before = database
    with sqlite3.connect(path) as db:
        _number(db, _serial(db, 1), k, product, number)


@pytest.mark.parametrize(("k", "product", "number"), [
    (3, "КБ1", "FRX.831К.КБ1.03"),        # k с нулями
    (3, "КБ1", "FRX.831К.КБ1.4"),         # k не тот
    (3, "КБ2", "FRX.831К.КБ1.3"),         # изделие не то
    (0, "КБ1", "FRX.831К.КБ1.0"),         # k ≥ 1
    (3, "КБ1", "FRX..КБ1.3"),             # пустой проект
    (3, "КБ1", "FRX.КБ1.3"),
    (3, "КБ1", "ABC.831К.КБ1.3"),         # не FRX
    (3, "", "FRX.831К..3"),               # пустое изделие
    (3, "34 226", "FRX.831К.34 226.3"),   # пробел в изделии
    (3, ".КБ1", "FRX.831К..КБ1.3"),       # точка по краю
    (3, "КБ1", None),                     # не все три
    (None, "КБ1", "FRX.831К.КБ1.3"),
    (3, None, "FRX.831К.КБ1.3"),
])
def test_malformed_factory_numbers_are_refused(database, k, product, number):
    _config_, path, _before = database
    with sqlite3.connect(path) as db, pytest.raises(sqlite3.IntegrityError):
        _number(db, _serial(db, 1), k, product, number)


def test_a_factory_number_and_a_task_k_are_unique(database):
    _config_, path, _before = database
    with sqlite3.connect(path) as db:
        first, second = _serial(db, 1), _serial(db, 2)
        _number(db, first, 3, "КБ1", "FRX.831К.КБ1.3")
        with pytest.raises(sqlite3.IntegrityError):     # один номер — одно изделие
            _number(db, second, 3, "КБ1", "FRX.831К.КБ1.3")
        with pytest.raises(sqlite3.IntegrityError):     # то же задание, тот же k
            _number(db, second, 3, "34.226", "FRX.831К.34.226.3")
        _number(db, second, 4, "КБ1", "FRX.831К.КБ1.4")


def test_empty_downgrade_roundtrips_and_issued_numbers_refuse_it(database):
    config, path, before = database
    command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        assert _rows(db) == before
    command.upgrade(config, REVISION)
    with sqlite3.connect(path) as db:
        _number(db, before[0][0], 1, "КБ1", "FRX.831К.КБ1.1")
    with pytest.raises(RuntimeError, match="заводских номеров"):
        command.downgrade(config, PARENT)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT factory_number FROM production_serials WHERE id = ?", (before[0][0],)).fetchone() \
            == ("FRX.831К.КБ1.1",)
