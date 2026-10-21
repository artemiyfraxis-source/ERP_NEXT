"""Миграция 0111 (владелец 12.10.2026): журнал ГП заполняется из счётчиков и отправок так, что его сумма по КБ равна
счётчикам; строки с нарушенным порядком счётчиков останавливают миграцию; сеялка CI заполняет новую таблицу."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.shipping.domain import journal_balance
from app.modules.shipping.infrastructure import SqlAlchemyFinishedGoodsRepository
from tests.migrations.seed_any_revision import seed

BACKEND = Path(__file__).resolve().parents[2]
BEFORE = "0110_stage_warehouse_accepted"
AFTER = "0111_finished_goods_register"


def _config(path: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


@pytest.fixture(scope="module")
def at_0110(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("goods-register") / "0110.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(_config(path), BEFORE)
    return path


@pytest.fixture()
def database(at_0110, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(at_0110, path)
    return path


_GOOD = (
    "INSERT INTO finished_goods (id, version, project, item_index, customer, article, name, ordered, unit, accepted,"
    " shipped, received_at, grn, note, sent, before_erp, project_key)"
    " VALUES (?, 1, ?, ?, '', '', 'Жгут', ?, 'шт', ?, ?, ?, NULL, '', ?, ?, ?)"
)
_SHIPMENT = (
    "INSERT INTO shipments (id, number, version, created_at, status, customer, receiver, shipping_point, contact, phone,"
    " terms, carrier, deadline, upd_type, upd_number, upd_date, upd_url, pl_number, dn_number, track_number, packed_at,"
    " shipped_at, manager, manager_id, note, tk_note, inherited_fields) VALUES (?, ?, 1, '2026-10-01 09:00:00.000000', ?,"
    " '', 'П', 'Москва', '', '', NULL, '', NULL, NULL, '', NULL, '', '', '', '', ?, ?, 'М', NULL, '', '', '')"
)
_LINE = (
    "INSERT INTO shipment_lines (id, shipment_id, finished_good_id, position, project, item_index, article, name,"
    " requested, picked, unit, grn, project_key) VALUES (?, ?, ?, 0, ?, ?, '', 'Жгут', ?, ?, 'шт', NULL, ?)"
)


def _good(connection, project, item, ordered, accepted, packed, sent, before=0, received="2026-10-02") -> str:
    good_id = uuid4().hex
    connection.execute(_GOOD, (good_id, project, item, ordered, accepted, packed, received, sent, before, project))
    return good_id


def _shipment(connection, number, status, good_id, project, item, picked, packed_at=None, shipped_at=None) -> str:
    shipment_id = uuid4().hex
    connection.execute(_SHIPMENT, (shipment_id, number, status, packed_at, shipped_at))
    connection.execute(_LINE, (uuid4().hex, shipment_id, good_id, project, item, picked, picked, project))
    return shipment_id


def _seed(database: Path) -> dict[str, str]:
    with sqlite3.connect(database) as connection:
        a = _good(connection, "708К.4", "КБ1", 50, 30, 20, 12)
        closed = _shipment(connection, "ОТГ-1", "Закрыта", a, "708К.4", "КБ1", 12, "2026-10-03", "2026-10-04")
        packed = _shipment(connection, "ОТГ-2", "Упакована", a, "708К.4", "КБ1", 8, "2026-10-05")
        b = _good(connection, "70К.1", "КБ1", 5, 5, 5, 5, before=5, received=None)
        c = _good(connection, "71К.1", "КБ1", 10, 10, 6, 0)
        assembled = _shipment(connection, "ОТГ-3", "Собрана", c, "71К.1", "КБ1", 4)
        _shipment(connection, "ОТГ-4", "Отменена", c, "71К.1", "КБ1", 0)
        d = _good(connection, "72К.1", "КБ1", 10, 0, 0, 0)
    return {"a": a, "b": b, "c": c, "d": d, "closed": closed, "packed": packed, "assembled": assembled}


def _moves(database: Path):
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT finished_good_id, kind, CAST(quantity AS TEXT), from_state, to_state, shipment_id, reason, actor"
            " FROM finished_goods_moves ORDER BY project_key, occurred_at, kind"
        ).fetchall()


def test_backfill_follows_the_counters_and_the_shipments_and_prints_the_reconciliation(database, capsys):
    ids = _seed(database)
    command.upgrade(_config(database), AFTER)

    out = capsys.readouterr().out
    assert ("0111_finished_goods_register: КБ с движением ГП — 3; штук: принято складом (на складе) 14, "
            "упаковано (не уехало) 14, отгружено 17; ходов журнала записано 10; расхождений строк отправок со "
            "счётчиками 1.") in out
    moves = _moves(database)
    by_good: dict[str, list] = {}
    for good_id, kind, quantity, source, target, shipment, reason, actor in moves:
        by_good.setdefault(good_id, []).append((kind, Decimal(quantity), shipment, reason))
        assert actor == "миграция 0111" and reason.startswith("перенос 0111")
    assert sorted(by_good[ids["a"]], key=str) == sorted([
        ("accept", Decimal(30), None, "перенос 0111"),
        ("pack", Decimal(12), ids["closed"], "перенос 0111"), ("ship", Decimal(12), ids["closed"], "перенос 0111"),
        ("pack", Decimal(8), ids["packed"], "перенос 0111"),
    ], key=str)
    assert sorted(by_good[ids["b"]], key=str) == sorted([
        (kind, Decimal(5), None, "перенос 0111: отгружено до ERP") for kind in ("accept", "pack", "ship")
    ], key=str)
    assert sorted(by_good[ids["c"]], key=str) == sorted([
        ("accept", Decimal(10), None, "перенос 0111"), ("pack", Decimal(4), ids["assembled"], "перенос 0111"),
        ("pack", Decimal(2), None, "перенос 0111: без отправки"),
    ], key=str)
    assert ids["d"] not in by_good

    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        # База на 0111, а репозиторий — текущего кода: колонка поздней миграции (0122 — место хранения ГП).
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE finished_goods ADD COLUMN location VARCHAR(255) NOT NULL DEFAULT ''")
        with Session(engine) as session:
            goods = SqlAlchemyFinishedGoodsRepository(session)
            totals = goods.journal_totals()
            for good in goods.list():
                assert journal_balance(totals.get(good.id, {})) == (good.accepted, good.shipped, good.sent)
    finally:
        engine.dispose()


def test_downgrade_drops_the_journal_and_upgrade_rebuilds_the_same(database):
    _seed(database)
    command.upgrade(_config(database), AFTER)
    first = sorted((row[0], row[1], row[2], row[5]) for row in _moves(database))
    command.downgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name = 'finished_goods_moves'").fetchall()
        connection.execute("UPDATE finished_goods SET shipped = accepted + 1 WHERE project = '72К.1'")  # проверок нет
        connection.execute("UPDATE finished_goods SET shipped = 0 WHERE project = '72К.1'")
    command.upgrade(_config(database), AFTER)
    assert sorted((row[0], row[1], row[2], row[5]) for row in _moves(database)) == first
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError, match="ck_finished_goods"):
        connection.execute("UPDATE finished_goods SET sent = shipped + 1 WHERE project = '708К.4'")


def test_counters_out_of_order_stop_the_migration_with_the_kb_named(database):
    with sqlite3.connect(database) as connection:
        _good(connection, "708К.4", "КБ7", 10, 3, 5, 0)
    with pytest.raises(RuntimeError, match="708К.4 КБ7: принято 3"):
        command.upgrade(_config(database), AFTER)


def test_ci_seeder_fills_the_journal_at_head(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "head.db"
    command.upgrade(_config(path), AFTER)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        inserted = seed(engine)
    finally:
        engine.dispose()
    assert inserted["finished_goods_moves"] == 6 and inserted["finished_goods"] == 6
    with sqlite3.connect(path) as connection:
        assert set(connection.execute("SELECT kind, from_state, to_state FROM finished_goods_moves")) == {
            ("accept", "production", "accepted"),
        }
