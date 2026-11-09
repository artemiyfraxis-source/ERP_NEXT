"""Миграция 0173 (владелец 06.11.2026): уже набранным строкам отправок — партии по актам приёмки ГП, FIFO в порядке
заведения отправок; непокрытое актами — на первый ГРН КБ и в список сверки; набранное не меняется; откат обратим."""

from __future__ import annotations

import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from app.db_integrity import take_snapshot

BACKEND = Path(__file__).resolve().parents[2]
BEFORE = "0172_resource_unit_ids"
AFTER = "0173_shipment_line_lots"


def _config(path: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


@pytest.fixture(scope="module")
def at_before(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("line-lots") / "before.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(_config(path), BEFORE)
    return path


@pytest.fixture()
def database(at_before, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(at_before, path)
    return path


_GOOD = (
    "INSERT INTO finished_goods (id, version, project, item_index, customer, article, name, ordered, unit, accepted,"
    " shipped, received_at, grn, note, sent, before_erp, project_key, location)"
    " VALUES (?, 3, ?, ?, '', '', 'Жгут', 2000, 'шт', 0, 0, '2026-10-15', ?, '', 0, 0, ?, '')"
)
_MOVE = (
    "INSERT INTO finished_goods_moves (id, finished_good_id, project_key, item_index, kind, quantity, from_state,"
    " to_state, shipment_id, actor, actor_id, occurred_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'Кладовщик',"
    " NULL, ?, ?)"
)
_ACT = (
    "INSERT INTO finished_goods_acts (id, finished_good_id, move_id, grn, quantity, received_at, location, actor,"
    " created_at) VALUES (?, ?, ?, ?, ?, ?, '', 'Кладовщик', ?)"
)
_SHIPMENT = (
    "INSERT INTO shipments (id, number, version, created_at, status, customer, receiver, shipping_point, contact, phone,"
    " carrier, upd_number, upd_url, pl_number, dn_number, track_number, manager, note, tk_note, inherited_fields,"
    " no_upd_approved_by) VALUES (?, ?, 2, ?, ?, '', 'Получатель', '', '', '', '', '', '', '', '', '', 'Менеджер', '',"
    " '', '', '')"
)
_LINE = (
    "INSERT INTO shipment_lines (id, shipment_id, finished_good_id, position, project, item_index, article, name,"
    " requested, picked, unit, grn, project_key) VALUES (?, ?, ?, ?, ?, ?, '', 'Жгут', ?, ?, 'шт', ?, ?)"
)


def _good(connection, project, item, grn, acts=(), rollbacks=()) -> str:
    good_id = uuid4().hex
    connection.execute(_GOOD, (good_id, project, item, grn, project))
    for number, quantity, when in acts:
        move = uuid4().hex
        connection.execute(_MOVE, (move, good_id, project, item, "accept", quantity, "production", "accepted", when, ""))
        connection.execute(_ACT, (uuid4().hex, good_id, move, number, quantity, when[:10], when))
    for quantity, when in rollbacks:
        connection.execute(_MOVE, (
            uuid4().hex, good_id, project, item, "correction", quantity, "accepted", "production", when, "ошибка приёмки",
        ))
    return good_id


def _shipment(connection, number, when, status, lines) -> list[str]:
    shipment = uuid4().hex
    connection.execute(_SHIPMENT, (shipment, number, when, status))
    ids = []
    for position, (good_id, project, item, picked, grn) in enumerate(lines):
        line = uuid4().hex
        connection.execute(_LINE, (line, shipment, good_id, position, project, item, max(picked, 1), picked, grn, project))
        ids.append(line)
    return ids


def _seed(database: Path) -> dict[str, str]:
    with sqlite3.connect(database) as connection:
        a = _good(connection, "533К.12", "КБ1", "ГП.26.0000003", acts=(
            ("ГП.26.0000003", 100, "2026-10-15 07:00:00.000000"),
            ("ГП.26.0000007", 350, "2026-10-16 07:00:00.000000"),
            ("ГП.26.0000008", 350, "2026-10-17 07:00:00.000000"),
        ), rollbacks=((50, "2026-10-17 12:00:00.000000"),))  # откат снимает 50 с нового акта ГП…8
        b = _good(connection, "591К.4", "КБ2", "ГП.26.0000005", acts=(("ГП.26.0000005", 50, "2026-10-18 07:00:00.000000"),))
        c = _good(connection, "180К.11", "КБ1", "ГП.26.0000009")  # принято до актов: актов нет
        ids = {}
        # Порядок заведения отправок, а не порядок вставки, решает FIFO: ОТГ-02 заведена позже ОТГ-01.
        ids["s2"], = _shipment(connection, "ОТГ-20261020-01", "2026-10-20 09:00:00.000000", "Отгружена", [
            (a, "533К.12", "КБ1", 460, "ГП.26.0000003"),
        ])
        ids["s1"], ids["b"] = _shipment(connection, "ОТГ-20261019-01", "2026-10-19 09:00:00.000000", "Закрыта", [
            (a, "533К.12", "КБ1", 300, "ГП.26.0000003"), (b, "591К.4", "КБ2", 80, "ГП.26.0000005"),
        ])
        ids["new"], ids["c"] = _shipment(connection, "ОТГ-20261021-01", "2026-10-21 09:00:00.000000", "Собрана", [
            (a, "533К.12", "КБ1", 0, None), (c, "180К.11", "КБ1", 5, "ГП.26.0000009"),
        ])
    return ids


def _lots(database: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT p.line_id, p.grn, CAST(p.quantity AS TEXT), a.grn FROM shipment_line_lots p"
            " LEFT JOIN finished_goods_acts a ON a.id = p.act_id ORDER BY p.line_id, p.position"
        ).fetchall()
    found: dict[str, list[tuple]] = {}
    for line_id, grn, quantity, act_grn in rows:
        found.setdefault(line_id, []).append((grn, Decimal(quantity), act_grn))
    return found


def _lines(database: Path) -> list[tuple]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT id, CAST(picked AS TEXT), grn FROM shipment_lines ORDER BY id"
        ).fetchall()


def test_backfill_allocates_fifo_in_shipment_order_and_lists_the_uncovered(database, capsys):
    ids = _seed(database)
    lines_before = _lines(database)
    capsys.readouterr()

    command.upgrade(_config(database), AFTER)

    lots = _lots(database)
    assert lots[ids["s1"]] == [
        ("ГП.26.0000003", Decimal(100), "ГП.26.0000003"), ("ГП.26.0000007", Decimal(200), "ГП.26.0000007"),
    ]
    # ГП…7: 150 осталось; ГП…8: 350 − откат 50 = 300; 10 сверх актов — на первый ГРН КБ.
    assert lots[ids["s2"]] == [
        ("ГП.26.0000007", Decimal(150), "ГП.26.0000007"), ("ГП.26.0000008", Decimal(300), "ГП.26.0000008"),
        ("ГП.26.0000003", Decimal(10), "ГП.26.0000003"),
    ]
    assert lots[ids["b"]] == [("ГП.26.0000005", Decimal(80), "ГП.26.0000005")]
    assert lots[ids["c"]] == [("ГП.26.0000009", Decimal(5), None)]  # без актов — ГРН КБ, акта нет
    assert ids["new"] not in lots
    assert _lines(database) == lines_before  # набранное и ГРН строк не меняются

    out = capsys.readouterr().out
    assert ("0173_shipment_line_lots: до — строк отправок с набранным 4, штук 845; после — партий 7, штук 845; "
            "строк из нескольких актов 2; не покрыто актами строк 3.") in out
    assert "не покрыто: ОТГ-20261020-01 533К.12 КБ1: 10 из 460 шт не покрыто актами — записано на ГП.26.0000003" in out
    assert "не покрыто: ОТГ-20261019-01 591К.4 КБ2: 30 из 80 шт не покрыто актами — записано на ГП.26.0000005" in out
    assert ("не покрыто: ОТГ-20261021-01 180К.11 КБ1: 5 из 5 шт не покрыто актами — записано на ГП.26.0000009 "
            "(у КБ нет актов)") in out

    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        assert take_snapshot(engine)["counts"]["shipment_line_lots_mismatch"] == 0
    finally:
        engine.dispose()


def test_downgrade_drops_the_lots_and_upgrade_rebuilds_them(database):
    ids = _seed(database)
    command.upgrade(_config(database), AFTER)
    built = _lots(database)
    lines = _lines(database)

    command.downgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        tables = {name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "shipment_line_lots" not in tables
    assert _lines(database) == lines

    command.upgrade(_config(database), AFTER)
    assert _lots(database) == built and len(built[ids["s2"]]) == 3


def test_empty_database_migrates_cleanly(database, capsys):
    command.upgrade(_config(database), AFTER)
    assert "до — строк отправок с набранным 0, штук 0; после — партий 0, штук 0" in capsys.readouterr().out
