"""Миграция 0128 (владелец 19.10.2026): на каждый ход «принято» — акт со своим ГРН, первый — ГРН КБ; отгруженное до
ERP акта не получает; сдача коробки получает ГРН своего акта; последовательность уходит за последний номер."""

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
BEFORE = "0127_etk_inputs_overrides"
AFTER = "0128_finished_goods_acts"


def _config(path: Path) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


@pytest.fixture(scope="module")
def at_0127(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("goods-acts") / "0127.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("ERP_DATABASE_URL", raising=False)
        command.upgrade(_config(path), BEFORE)
    return path


@pytest.fixture()
def database(at_0127, tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "erp.db"
    shutil.copyfile(at_0127, path)
    return path


_GOOD = (
    "INSERT INTO finished_goods (id, version, project, item_index, customer, article, name, ordered, unit, accepted,"
    " shipped, received_at, grn, note, sent, before_erp, project_key, location)"
    " VALUES (?, 3, ?, ?, '', '', 'Жгут', 2000, 'шт', ?, ?, '2026-10-15', ?, '', ?, ?, ?, ?)"
)
_MOVE = (
    "INSERT INTO finished_goods_moves (id, finished_good_id, project_key, item_index, kind, quantity, from_state,"
    " to_state, shipment_id, actor, actor_id, occurred_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'Кладовщик',"
    " NULL, ?, ?)"
)
_ROUTE = {"accept": ("production", "accepted"), "pack": ("accepted", "packed"), "ship": ("packed", "shipped")}


def _good(connection, project, item, accepted, grn, *, before=0, location="", moves=()) -> str:
    good_id = uuid4().hex
    connection.execute(_GOOD, (good_id, project, item, accepted, before, grn, before, before, project, location))
    for kind, quantity, when, reason in moves:
        connection.execute(_MOVE, (uuid4().hex, good_id, project, item, kind, quantity, *_ROUTE[kind], when, reason))
    return good_id


def _seed(database: Path) -> dict[str, str]:
    legacy = "перенос 0111: отгружено до ERP"
    with sqlite3.connect(database) as connection:
        ids = {
            "a": _good(connection, "533К.11", "КБ1", 1000, "ГП.26.0000001", location="Стеллаж 3", moves=(
                ("accept", 400, "2026-10-15 07:00:00.000000", "КБ без заданий в ERP: принято до тиража"),
                ("accept", 600, "2026-10-16 08:00:00.000000", "коробка К-0002"),
            )),
            "b": _good(connection, "533К.12", "КБ1", 800, "ГП.26.0000003", moves=(
                ("accept", 100, "2026-10-15 09:00:00.000000", ""),
                ("accept", 350, "2026-10-16 09:00:00.000000", ""),
                ("accept", 350, "2026-10-17 09:00:00.000000", ""),
            )),
            "c": _good(connection, "180К.11", "КБ1", 40, None, before=40, moves=tuple(
                (kind, 40, "2026-10-12 06:00:00.000000", legacy) for kind in ("accept", "pack", "ship")
            )),
            "d": _good(connection, "591К.4", "КБ2", 30, "ГП.26.0000005", before=10, moves=(
                *((kind, 10, "2026-10-12 06:00:00.000000", legacy) for kind in ("accept", "pack", "ship")),
                ("accept", 20, "2026-10-18 06:00:00.000000", ""),
            )),
            # 31.12 22:30 по UTC — уже 2027 год по Москве: номер из 'grn-27'.
            "e": _good(connection, "796К.2", "КБ1", 46, "ГП.26.0000004", moves=(
                ("accept", 40, "2026-10-18 07:00:00.000000", ""),
                ("accept", 6, "2026-12-31 22:30:00.000000", ""),
            )),
        }
        connection.execute("INSERT INTO shipping_sequences (name, value) VALUES ('grn-26', 5)")
        box = uuid4().hex
        ids["receipt"] = uuid4().hex
        connection.execute(
            "INSERT INTO production_finished_goods_receipts (id, source_box_id, finished_good_id, grn, quantity,"
            " received_at) VALUES (?, ?, ?, 'ГП.26.0000001', 600, '2026-10-16 08:00:00.000000')",
            (ids["receipt"], box, ids["a"]),
        )
        connection.execute(
            "INSERT INTO production_serials (id, serial_number, task_id, position, status, current_box_id,"
            " finished_goods_grn) VALUES (?, 'S-1', ?, 1, 'finished_goods', ?, 'ГП.26.0000001')",
            (uuid4().hex, uuid4().hex, box),
        )
    return ids


def _acts(database: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT a.finished_good_id, a.grn, CAST(a.quantity AS TEXT), a.received_at, a.location, a.actor, m.kind"
            " FROM finished_goods_acts a JOIN finished_goods_moves m ON m.id = a.move_id ORDER BY a.created_at"
        ).fetchall()
    found: dict[str, list[tuple]] = {}
    for good_id, grn, quantity, received_at, location, actor, kind in rows:
        assert kind == "accept" and actor == "Кладовщик"
        found.setdefault(good_id, []).append((grn, Decimal(quantity), received_at, location))
    return found


def test_every_accept_move_becomes_an_act_and_later_ones_get_new_numbers(database, capsys):
    ids = _seed(database)
    command.upgrade(_config(database), AFTER)

    acts = _acts(database)
    assert acts[ids["a"]] == [
        ("ГП.26.0000001", Decimal(400), "2026-10-15", "Стеллаж 3"), ("ГП.26.0000006", Decimal(600), "2026-10-16", "Стеллаж 3"),
    ]
    assert [(grn, quantity) for grn, quantity, _day, _place in acts[ids["b"]]] == [
        ("ГП.26.0000003", Decimal(100)), ("ГП.26.0000007", Decimal(350)), ("ГП.26.0000008", Decimal(350)),
    ]
    assert ids["c"] not in acts  # всё отгружено до ERP
    assert [(grn, quantity) for grn, quantity, _day, _place in acts[ids["d"]]] == [("ГП.26.0000005", Decimal(20))]
    assert acts[ids["e"]] == [
        ("ГП.26.0000004", Decimal(40), "2026-10-18", ""), ("ГП.27.0000001", Decimal(6), "2027-01-01", ""),
    ]
    with sqlite3.connect(database) as connection:
        assert dict(connection.execute("SELECT name, value FROM shipping_sequences")) == {"grn-26": 8, "grn-27": 1}
        assert dict(connection.execute("SELECT project, grn FROM finished_goods")) == {
            "533К.11": "ГП.26.0000001", "533К.12": "ГП.26.0000003", "180К.11": None, "591К.4": "ГП.26.0000005",
            "796К.2": "ГП.26.0000004",
        }
        assert connection.execute("SELECT grn FROM production_finished_goods_receipts").fetchall() == [("ГП.26.0000006",)]
        assert connection.execute("SELECT finished_goods_grn FROM production_serials").fetchall() == [("ГП.26.0000006",)]

    out = capsys.readouterr().out
    assert ("0128_finished_goods_acts: до — ходов «принято» 10, штук 1916, из них отгружено до ERP 50; после — актов 8, "
            "штук 1866; КБ, принятых несколькими актами, 3; коробкам дан ГРН акта 1; последовательности: grn-26 = 8, "
            "grn-27 = 1.") in out
    assert "533К.12 КБ1: 800 = 100 ГП.26.0000003 + 350 ГП.26.0000007 + 350 ГП.26.0000008" in out

    engine = create_engine(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        assert take_snapshot(engine)["counts"]["finished_goods_acts_mismatch"] == 0
    finally:
        engine.dispose()


def test_downgrade_drops_the_acts_and_upgrade_writes_them_again(database):
    ids = _seed(database)
    command.upgrade(_config(database), AFTER)
    first = {good_id: [(quantity, day) for _grn, quantity, day, _place in items] for good_id, items in _acts(database).items()}
    command.downgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        assert not connection.execute("SELECT name FROM sqlite_master WHERE name = 'finished_goods_acts'").fetchall()
    command.upgrade(_config(database), AFTER)
    again = _acts(database)
    assert {good_id: [(quantity, day) for _grn, quantity, day, _place in items] for good_id, items in again.items()} == first
    assert again[ids["a"]][0][0] == "ГП.26.0000001"  # первый акт — всё так же ГРН КБ
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO finished_goods_acts (id, finished_good_id, move_id, grn, quantity, received_at, location, actor,"
            " created_at) SELECT ?, finished_good_id, move_id, 'ГП.26.0000099', 1, received_at, '', 'x', created_at"
            " FROM finished_goods_acts LIMIT 1", (uuid4().hex,),
        )
