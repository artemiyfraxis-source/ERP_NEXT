"""Миграция 0089 (план задания, «Выполнено», команда, «Сдано N шт»): существующие задания не меняются, новые значения
закреплены CHECK, откат возвращает прежнюю схему."""

import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.modules.production.domain import FlowMode, ProductionStatus
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from tests.production.test_operation_inputs_migration import _config, _insert

BEFORE = "0088_api_tokens_assembly_drafts"


def _after(config: Config) -> str:
    """Ревизия этой миграции: следующая за 0088 (номер может смениться при слиянии)."""
    script = ScriptDirectory.from_config(config)
    return next(item.revision for item in script.walk_revisions() if item.down_revision == BEFORE)


def test_existing_tasks_keep_their_data_get_the_default_plan_and_constraints_hold(tmp_path):
    database_path = tmp_path / "flow.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        task = _insert(connection, "production_tasks", {
            "number": "ПЗ-0001", "source_idempotency_key": "k1", "status": "Запущено", "quantity": 10, "version": 3,
            "handed_to": "ЖУК", "project": "591К.3", "item": "КБ1",
        })
        cancelled = _insert(connection, "production_tasks", {
            "number": "ПЗ-0002", "source_idempotency_key": "k2", "status": "Отменено", "quantity": 5, "version": 1,
            "handed_to": "",
        })
        _insert(connection, "production_operations", {"task_id": task, "position": 1, "code": "OP-1"})
        user = _insert(connection, "identity_users", {"login": "fitter", "email": "f@example.test", "active": 1})

    command.upgrade(config, _after(config))
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT number, status, version, quantity, handed_to, techcard_id, techcard_revision, flow_mode, "
            "transfer_batch, early_quantity FROM production_tasks ORDER BY number"
        ).fetchall()
        assert rows == [
            ("ПЗ-0001", "Запущено", 3, 10, "ЖУК", None, None, "mixed", None, None),
            ("ПЗ-0002", "Отменено", 1, 5, "", None, None, "mixed", None, None),
        ]
        # Новые значения — только допустимые: CHECK в базе, не только в коде.
        for statement in (
            "UPDATE production_tasks SET flow_mode = 'batch' WHERE number = 'ПЗ-0001'",
            "UPDATE production_tasks SET transfer_batch = 11 WHERE number = 'ПЗ-0001'",
            "UPDATE production_tasks SET early_quantity = 10 WHERE number = 'ПЗ-0001'",
            "UPDATE production_tasks SET techcard_revision = 0 WHERE number = 'ПЗ-0001'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO production_task_deliveries (id, task_id, site, quantity, delivered_at, delivered_by) "
                "VALUES (?, ?, 'ЖУК', 0, '2026-10-10 10:00:00', 'Мастер')", (uuid4().hex, task),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO production_task_deliveries (id, task_id, site, quantity, delivered_at, delivered_by, cancelled_at) "
                "VALUES (?, ?, 'ЖУК', 2, '2026-10-10 10:00:00', 'Мастер', '2026-10-10 11:00:00')", (uuid4().hex, task),
            )
        connection.execute(
            "UPDATE production_tasks SET status = 'Выполнено', flow_mode = 'parallel', transfer_batch = 2, "
            "early_quantity = 1, techcard_revision = 2 WHERE number = 'ПЗ-0001'"
        )
        connection.execute(
            "INSERT INTO production_task_deliveries (id, task_id, site, quantity, delivered_at, delivered_by) "
            "VALUES (?, ?, 'ЖУК', 10, '2026-10-10 10:00:00', 'Мастер')", (uuid4().hex, task),
        )
        connection.execute("INSERT INTO production_task_team (task_id, operator_id, position) VALUES (?, ?, 0)", (task, user))

    # Текущий код читает перенесённые задания.
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    with SqlAlchemyProductionUnitOfWork(sessionmaker(bind=engine, class_=Session)) as uow:
        stored = uow.production.get(UUID(task))
        other = uow.production.get(UUID(cancelled))
        delivered = uow.deliveries.totals([UUID(task), UUID(cancelled)])
    engine.dispose()
    assert (stored.status, stored.flow_mode, stored.transfer_batch, stored.early_quantity) == (
        ProductionStatus.DONE, FlowMode.PARALLEL, 2, 1,
    )
    assert stored.team == (UUID(user),)
    assert other.flow_mode is FlowMode.MIXED and other.team == ()
    assert delivered == {UUID(task): 10}

    command.downgrade(config, BEFORE)
    with sqlite3.connect(database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(production_tasks)")}
        statuses = dict(connection.execute("SELECT number, status FROM production_tasks"))
    assert not {"production_task_team", "production_task_deliveries"} & tables
    assert not {"techcard_id", "techcard_revision", "flow_mode", "transfer_batch", "early_quantity"} & columns
    # «Выполнено» прежняя схема не знает — задание возвращается в «Запущено».
    assert statuses == {"ПЗ-0001": "Запущено", "ПЗ-0002": "Отменено"}
