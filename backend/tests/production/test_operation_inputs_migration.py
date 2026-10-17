"""Миграция 0081: входы операций существующих заданий — ровно сегодняшняя цепочка по номерам этапов."""

import sqlite3
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.production.domain import OperationInput
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork

BEFORE = "0080_production_plan_marks"
AFTER = "0081_production_operation_inputs"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _insert(connection: sqlite3.Connection, table: str, values: dict) -> str:
    """Insert a row filling every other NOT NULL column with a placeholder of its type (FKs are off)."""
    row = {"id": uuid4().hex}
    for _, name, kind, not_null, _, _ in connection.execute(f"PRAGMA table_info({table})"):
        if name in row or name in values or not not_null:
            continue
        kind = kind.upper()
        row[name] = 1 if kind.startswith(("INT", "NUMERIC", "BOOL", "FLOAT")) else (
            "2026-09-25 10:00:00" if kind.startswith(("DATE", "TIME")) else uuid4().hex if kind.startswith("CHAR(32)") else "x"
        )
    row.update(values)
    connection.execute(
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()),
    )
    return row["id"]


def _task(connection: sqlite3.Connection, number: str, positions: tuple[int, ...]) -> tuple[str, dict[int, str]]:
    task = _insert(connection, "production_tasks", {
        "number": number, "source_idempotency_key": number, "status": "Запущено", "quantity": 5, "version": 1,
        "handed_to": "",
    })
    return task, {
        position: _insert(connection, "production_operations", {"task_id": task, "position": position})
        # Вставка вразнобой: порядок строк в базе не должен влиять на цепочку.
        for position in sorted(positions, reverse=True)
    }


def test_backfill_reproduces_the_position_chain_and_downgrade_drops_the_graph(tmp_path):
    database_path = tmp_path / "graph.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        chain_task, chain = _task(connection, "ПЗ-0001", (1, 2, 3))
        _, single = _task(connection, "ПЗ-0002", (1,))
        gap_task, gap = _task(connection, "ПЗ-0003", (2, 5, 9))

    command.upgrade(config, AFTER)
    with sqlite3.connect(database_path) as connection:
        edges = {
            (operation, source): (per_unit, batch)
            for operation, source, per_unit, batch in connection.execute(
                "SELECT operation_id, input_operation_id, per_unit, transfer_batch FROM production_operation_inputs"
            )
        }
        stage_keys = {row[0] for row in connection.execute("SELECT stage_key FROM production_operations")}
    assert edges == {
        (chain[2], chain[1]): (1, None), (chain[3], chain[2]): (1, None),
        (gap[5], gap[2]): (1, None), (gap[9], gap[5]): (1, None),
    }
    assert stage_keys == {None}

    # Текущий код читает перенесённый граф: финал — последняя операция, входы — предыдущий номер. Код знает и колонки
    # поздних миграций (0089: план задания) — читаем на голове цепочки.
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    with SqlAlchemyProductionUnitOfWork(sessionmaker(bind=engine, class_=Session)) as uow:
        task = uow.production.get(UUID(chain_task))
        gapped = uow.production.get(UUID(gap_task))
    engine.dispose()
    assert [operation.inputs for operation in task.operations] == [
        (), (OperationInput(UUID(chain[1])),), (OperationInput(UUID(chain[2]), Decimal(1), None),),
    ]
    assert task.final_operation.id == UUID(chain[3])
    assert gapped.final_operation.id == UUID(gap[9])
    assert single  # одна операция — без входов, рёбер нет

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(production_operations)")}
        positions = sorted(row[0] for row in connection.execute("SELECT position FROM production_operations"))
    assert "production_operation_inputs" not in tables
    assert "stage_key" not in columns
    assert positions == [1, 1, 2, 2, 3, 5, 9]
