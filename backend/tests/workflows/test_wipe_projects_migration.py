"""Миграция 0078: разовый снос всех проектов (владелец 30.09.2026); склад, комплектация и КД целы, проект можно
завести снова кнопкой «Создать проект из расчёта»."""

import importlib.util
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

import app.database  # noqa: F401 — таблицы всех модулей в metadata
from app.main import app
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.production.application import LaunchProductionCommand, launch_production
from app.modules.production.domain import ProductionOperation
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.techcards.application import CreateProjectTechcardCommand, create_project_techcard
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import Base
from app.workflows.project_kitset import attach_document
from tests.factories import Actor
from tests.techcards.calc_sheets import FakeSheets
from tests.techcards.test_kitset_api import STATE, _load, _principal

BACKEND = Path(__file__).parents[2]
_spec = importlib.util.spec_from_file_location("wipe", BACKEND / "migrations" / "versions" / "0078_wipe_projects_once.py")
wipe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wipe)

BEFORE, WIPE = "0077_production_operation_shares", "0078_wipe_projects_once"
NOW = datetime(2026, 9, 30, 12, 0)
ACTOR = Actor(id=UUID("40000000-0000-0000-0000-000000000002"), name="Технолог", department="")


def _config(path: Path) -> Config:
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def _count(engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


@pytest.fixture()
def database(tmp_path, monkeypatch):
    """База на 0077: проект из расчёта, выданная комплектация с резервом партии, ТК, задание цеха, КД."""
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "real.db"
    command.upgrade(_config(path), BEFORE)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})
    # Колонки поздних миграций, которые читает ORM этих API: база остаётся на 0077, но запросы идут текущим кодом.
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE production_plan_events ADD COLUMN field VARCHAR(32) NOT NULL DEFAULT ''"))
        # 0100: история ручной смены этапа.
        connection.execute(text(
            "ALTER TABLE production_plan_events ADD COLUMN from_value VARCHAR(64) NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE production_plan_events ADD COLUMN reason TEXT NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE production_operations ADD COLUMN stage_key VARCHAR(128)"))
        # 0119: код коробки.
        connection.execute(text("ALTER TABLE production_boxes ADD COLUMN code VARCHAR(64)"))
        # 0120: операции ЭТК полуфабриката.
        connection.execute(text("ALTER TABLE production_operations ADD COLUMN etk_ops JSON"))
        # 0121: ссылка на папку проекта.
        connection.execute(text("ALTER TABLE techcard_kitsets ADD COLUMN folder_url VARCHAR(1024)"))
        # 0122: место хранения ГП и план отгрузки в карточке проекта.
        connection.execute(text("ALTER TABLE finished_goods ADD COLUMN location VARCHAR(255) NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE shipping_project_addressees ADD COLUMN plan_date DATE"))
        connection.execute(text("ALTER TABLE shipping_project_addressees ADD COLUMN note TEXT NOT NULL DEFAULT ''"))
        Base.metadata.tables["production_operation_inputs"].create(connection)
        connection.execute(text("ALTER TABLE fulfillment_lines ADD COLUMN off_book BOOLEAN NOT NULL DEFAULT 0"))
        # 0106: «нет на складе» on a kit line.
        connection.execute(text("ALTER TABLE fulfillment_lines ADD COLUMN absent_at DATETIME"))
        connection.execute(text("ALTER TABLE fulfillment_lines ADD COLUMN absent_by VARCHAR(255)"))
        Base.metadata.tables["fulfillment_kit_reserves"].create(connection)
        # 0089: the project / КБ / kind of a request and the year's number counter.
        connection.execute(text("ALTER TABLE fulfillment_requests ADD COLUMN project_key VARCHAR(128)"))
        connection.execute(text("ALTER TABLE fulfillment_requests ADD COLUMN item_index VARCHAR(64)"))
        connection.execute(text("ALTER TABLE fulfillment_requests ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'main'"))
        Base.metadata.tables["fulfillment_request_counters"].create(connection)
        # The picked ГРН is reserved at once (T1): the kit request then looks for the project's «Со склада» covers.
        Base.metadata.tables["procurement_stock_covers"].create(connection)
        # План задания (Цель 2, миграция после 0088): колонки задания, команда и сдачи площадки.
        for column in ("techcard_id CHAR(32)", "techcard_revision INTEGER", "flow_mode VARCHAR(16) NOT NULL DEFAULT 'mixed'",
                       "transfer_batch INTEGER", "early_quantity INTEGER", "launch_seq INTEGER"):  # launch_seq — 0113
            connection.execute(text(f"ALTER TABLE production_tasks ADD COLUMN {column}"))
        Base.metadata.tables["production_task_team"].create(connection)
        Base.metadata.tables["production_task_deliveries"].create(connection)
        # Ключ № проекта (миграция project_keys, U06): трек и «Снабжение» читают проект по нему.
        for table, column in (
            ("production_tasks", "project_key"), ("finished_goods", "project_key"), ("shipment_lines", "project_key"),
            ("techcard_projects", "project_key"), ("procurement_lines", "project_key"), ("requests", "project_key"),
            ("requests", "purpose_project_key"), ("requests", "designation_project_key"),
            ("receiving_lines", "purpose_project_key"), ("receiving_lines", "goal_project_key"),
        ):
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR(255)"))
    sessions =sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.kitset_sheets_reader = FakeSheets()
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit"})
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 29, 10, 0)
    app.dependency_overrides[moscow_today] = lambda: date(2026, 9, 29)
    lot = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000001", package="", name="Корпус Molex 43025-0400",
        item_type="Комплектующие", purpose="Склад", unit="шт", quantity=Decimal("15"),
        storage_location="1F4", source_receiving_ids=("receiving-one",), article="43025-0400",
    )
    with app.state.fulfillment_uow_factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    client = TestClient(app)
    assert _load(client, deadline="2026-10-20").status_code == 200
    lots = client.get("/api/techcards/kitsets/lots", params={"q": "43025"}).json()["lots"]
    client.put("/api/techcards/kitsets/708К.4/lines", json={"edits": [{
        "index": "КБ1", "art": "43025-0400", "per": "2", "scrap": "0", "name": "Корпус Molex",
        "lot": {"lot_id": lots[0]["lot_id"], "warehouse": "ЦАР", "grn": "FRX.26.0000001", "package": ""},
    }]})
    assert client.post("/api/techcards/kitsets/708К.4/issue", json={"deadline": "2026-10-10"}).status_code == 200
    create_project_techcard(CreateProjectTechcardCommand(
        project="708К.4", item_index="КБ1", product_name="Жгут питания", source_calculation="",
        deadline=None, actor=ACTOR, now=NOW,
    ), app.state.techcards_uow_factory())
    launch_production(LaunchProductionCommand(
        idempotency_key="708К.4:КБ1:v1", project="708К.4", item="708К.4КБ1", name="Жгут питания", quantity=10,
        fulfillment_request_id=None, deadline=date(2026, 10, 10),
        operations=(ProductionOperation.create(
            position=1, code="OP-01", stage="Резка", description="Нарезать провод",
            seconds_per_unit=Decimal("60"), setup_seconds=Decimal("0"), quantity=10,
        ),), actor=ACTOR, now=NOW,
    ), app.state.production_uow_factory())
    attach_document("708К.4", "КБ1", "чертёж.pdf", "application/pdf", b"%PDF-1.4", ACTOR, NOW,
                    app.state.techcards_uow_factory)
    yield path, engine, client, lot
    del app.state.kitset_sheets_reader
    for name in (authenticated_principal, moscow_now, moscow_today):
        app.dependency_overrides.pop(name, None)
    for name in STATE:
        delattr(app.state, name)
    engine.dispose()


def test_no_kept_table_points_into_a_wiped_one(tmp_path, monkeypatch):
    # Разовый снос видит схему на своей ревизии: таблицы поздних миграций (входы операций 0081) его не касаются.
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "schema.db"
    command.upgrade(_config(path), WIPE)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    existing = set(inspect(engine).get_table_names())
    engine.dispose()
    wiped = set(wipe.TABLES)
    tables = {table.name for table in Base.metadata.sorted_tables}
    assert wiped <= tables
    assert not {name for name in wiped if name.startswith(("warehouse_", "fulfillment_"))}
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        if table.name not in wiped:
            assert not {fk.column.table.name for fk in table.foreign_keys} & wiped, table.name
    # Дети раньше родителей: RESTRICT на PostgreSQL проверяется сразу.
    order = {name: index for index, name in enumerate(wipe.TABLES)}
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys if table.name in wiped else ():
            parent = fk.column.table.name
            assert parent not in wiped or parent == table.name or order[table.name] < order[parent], (table.name, parent)


def test_wipe_removes_projects_and_leaves_stock_kitting_and_kd(database):
    path, engine, client, lot = database
    with app.state.fulfillment_uow_factory() as uow:
        before = uow.warehouse.get(lot.id)
        requests = len(uow.fulfillment.list(limit=100))
    assert before.reserved == Decimal("15") and requests == 2
    assert (_count(engine, "techcard_kitsets"), _count(engine, "techcard_projects"), _count(engine, "production_tasks")) == (1, 1, 1)
    engine.dispose()

    command.upgrade(_config(path), WIPE)

    assert all(_count(engine, name) == 0 for name in wipe.TABLES)
    with app.state.fulfillment_uow_factory() as uow:
        after = uow.warehouse.get(lot.id)
        assert len(uow.fulfillment.list(limit=100)) == requests
    assert (after.balance, after.reserved) == (before.balance, before.reserved)
    assert _count(engine, "techcard_kitset_documents") == 1
    with engine.connect() as connection:
        mark = json.loads(connection.execute(text(
            "SELECT value FROM operations_job_marks WHERE key = 'projects_wiped'")).scalar())
    assert mark["counts"]["techcard_kitsets"] == 1
    backup = path.with_name("real.db.before-projects-wipe.bak")
    assert mark["backup"] == str(backup)
    copy = create_engine(f"sqlite+pysqlite:///{backup.as_posix()}")
    assert _count(copy, "techcard_kitsets") == 1 and _count(copy, "production_tasks") == 1
    copy.dispose()

    # Владелец заводит проект сам — «Создать проект из расчёта»; КД возвращается в карточку.
    assert _load(client).status_code == 200
    card = client.get("/api/techcards/kitsets/708К.4").json()
    assert [doc["name"] for doc in card["assemblies"][0]["kd"]] == ["708К.4КБ1 — чертёж.pdf"]


def test_an_empty_database_gets_the_mark_and_no_copy(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "fresh.db"
    command.upgrade(_config(path), WIPE)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    with engine.connect() as connection:
        mark = json.loads(connection.execute(text(
            "SELECT value FROM operations_job_marks WHERE key = 'projects_wiped'")).scalar())
    engine.dispose()
    assert mark["backup"] == "" and not any(mark["counts"].values())
    assert not path.with_name("fresh.db.before-projects-wipe.bak").exists()


def test_the_summary_line_does_not_fail_the_wipe_on_a_non_utf8_pipe(tmp_path, monkeypatch):
    """Автообновление из планировщика: вывод Python в трубу идёт в кодировке ANSI Windows. Строка итога с
    кириллицей не должна валить миграцию (раньше UnicodeEncodeError откатывал снос, ERP оставалась прежней)."""
    import io
    import sys

    pipe = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "stdout", pipe)
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "pipe.db"
    command.upgrade(_config(path), WIPE)
    pipe.flush()
    assert "30.09" in pipe.buffer.getvalue().decode("ascii")
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT COUNT(*) FROM operations_job_marks WHERE key = 'projects_wiped'")).scalar() == 1
    engine.dispose()
