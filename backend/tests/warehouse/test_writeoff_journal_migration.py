"""SK-13: migration 0033 creates the СПИСАНИЕ journal and carries over the movements made before it."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config

BEFORE = "0032_receiving_vk_revisions"
AFTER = "0033_warehouse_writeoff_journal"
LOT, OTHER_LOT, GONE_LOT = "a" * 32, "b" * 32, "c" * 32
REQUEST, ISSUED_LINE, EMPTY_LINE = "d" * 32, "e" * 32, "f" * 32
ACTOR = "1" * 32


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _event(connection, number: int, aggregate: str, event_type: str, occurred_at: str, details: dict) -> str:
    event_id = f"{number:032d}"
    connection.execute(
        "INSERT INTO audit_events (id, aggregate_id, event_type, actor_id, actor_name, actor_department,"
        " occurred_at, details) VALUES (?, ?, ?, ?, 'Кладовщик', 'Склад', ?, ?)",
        (event_id, aggregate, event_type, ACTOR, occurred_at, json.dumps(details, ensure_ascii=False)),
    )
    return event_id


def _seed(connection) -> dict[str, str]:
    for lot_id, grn, name in ((LOT, "OLD.26.0000001", "Вилка"), (OTHER_LOT, "FRX.26.0000002", "Разъём")):
        connection.execute(
            "INSERT INTO warehouse_lots (id, version, warehouse, grn, package, article, name, item_type, purpose,"
            " unit, storage_location, inbound, written_off, reserved, source_receiving_ids)"
            " VALUES (?, 3, 'ЦАР', ?, 'П1', 'A-1', ?, 'Комплектующие', 'Склад', 'шт', '1F4', 10, 4, 0, '[]')",
            (lot_id, grn, name),
        )
    connection.execute(
        "INSERT INTO fulfillment_requests (id, number, source_idempotency_key, source_fingerprint, version,"
        " created_at, requester, purpose, status) VALUES (?, 'REQ.26.0000007', 'kitset:1', 'f', 4,"
        " '2026-09-20 10:00:00', 'Технолог', '591К.3 / КБ-1', 'ISSUED')",
        (REQUEST,),
    )
    for line_id, position, name, issued, lot_id, grn in (
        (ISSUED_LINE, 0, "Разъём в заявке", "2", OTHER_LOT, "FRX.26.0000002"),
        (EMPTY_LINE, 1, "Нулевая позиция", "0", OTHER_LOT, "FRX.26.0000002"),
    ):
        connection.execute(
            "INSERT INTO fulfillment_lines (id, request_id, position, requirement_id, article, name, unit,"
            " requirement, reserved, issued, lot_id, lot_warehouse, lot_grn, lot_package)"
            " VALUES (?, ?, ?, ?, 'КД-1', ?, 'шт', 2, 0, ?, ?, 'ЦАР', ?, 'П1')",
            (line_id, REQUEST, position, "9" * 32, name, issued, lot_id, grn),
        )
    return {
        "issued": _event(connection, 1, REQUEST, "fulfillment.issued", "2026-09-21 09:00:00", {"lines": 2}),
        "written": _event(connection, 2, OTHER_LOT, "warehouse.written_off", "2026-09-22 10:00:00",
                          {"quantity": "1.5", "reason": "Брак", "purpose": "Иванов"}),
        "returned": _event(connection, 3, OTHER_LOT, "warehouse.writeoff_returned", "2026-09-23 11:00:00",
                           {"quantity": "0.5", "reason": "Возврат", "purpose": "FRX.001"}),
        "legacy": _event(connection, 4, LOT, "warehouse.legacy_writeoff_history", "2026-09-28 12:00:00", {
            "source": "SUPPLYMAP 2026-09-28", "row": "5", "date": "01.09.2026", "quantity": "3", "unit": "шт",
            "reason": "Выдача в производство", "purpose": "999К.1", "author": "Тестовый Кладовщик",
            "posted": "Да", "remaining_after": "7",
        }),
        "legacy_zero": _event(connection, 5, LOT, "warehouse.legacy_writeoff_history", "2026-09-28 12:00:00", {
            "source": "SUPPLYMAP 2026-09-28", "row": "6", "date": "не дата", "quantity": "0",
        }),
        "gone": _event(connection, 6, GONE_LOT, "warehouse.written_off", "2026-09-22 10:00:00",
                       {"quantity": "1", "reason": "Выдача", "purpose": "X"}),
    }


def test_writeoff_journal_migration_carries_over_past_movements_and_downgrades(tmp_path):
    database_path = tmp_path / "journal.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        events = _seed(connection)

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT kind, lot_id, warehouse, grn, package, name, quantity, unit, reason, purpose, author, posted,"
            " remaining_after, substr(occurred_at, 1, 10), actor_name, source_key"
            " FROM warehouse_writeoffs ORDER BY occurred_at"
        ).fetchall()
        stock = connection.execute("SELECT grn, inbound, written_off, reserved FROM warehouse_lots ORDER BY grn").fetchall()

    assert [(r[0], r[3], r[5], Decimal(str(r[6])), r[8], r[9], r[10], r[11], r[12], r[13], r[15]) for r in rows] == [
        ("legacy", "OLD.26.0000001", "Вилка", Decimal("3"), "Выдача в производство", "999К.1", "Тестовый Кладовщик",
         1, 7, "2026-09-01", "supplymap-writeoff:SUPPLYMAP 2026-09-28:5"),
        ("issue", "FRX.26.0000002", "Разъём в заявке", Decimal("2"), "Выдача REQ.26.0000007", "", "выдача по заявке",
         1, None, "2026-09-21", f"fulfillment-issue:{UUID(ISSUED_LINE)}"),
        ("writeoff", "FRX.26.0000002", "Разъём", Decimal("1.5"), "Брак", "Иванов", "Кладовщик",
         1, None, "2026-09-22", f"audit:{UUID(events['written'])}"),
        ("return", "FRX.26.0000002", "Разъём", Decimal("0.5"), "Возврат", "FRX.001", "Кладовщик",
         1, None, "2026-09-23", f"audit:{UUID(events['returned'])}"),
    ]
    assert {(r[2], r[4], r[7], r[14]) for r in rows} == {("ЦАР", "П1", "шт", "Кладовщик")}
    assert {r[1] for r in rows} == {LOT, OTHER_LOT}
    # History only: the migration moves no stock.
    assert stock == [("FRX.26.0000002", 10, 4, 0), ("OLD.26.0000001", 10, 4, 0)]

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "warehouse_writeoffs" not in tables
    assert "audit_events" in tables

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT count(*) FROM warehouse_writeoffs").fetchone() == (4,)
