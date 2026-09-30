"""app.workflows.projects_wipe: разовый снос всех проектов (владелец 30.09.2026), склад и комплектация целы."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

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
from app.shared.persistence import Base, create_schema
from app.workflows import projects_wipe
from app.workflows.project_kitset import attach_document
from tests.factories import Actor
from tests.techcards.calc_sheets import FakeSheets
from tests.techcards.test_kitset_api import STATE, _load, _principal

NOW = datetime(2026, 9, 30, 12, 0)
ACTOR = Actor(id=UUID("40000000-0000-0000-0000-000000000002"), name="снос", department="")


@pytest.fixture()
def loaded(tmp_path):
    """Файловая SQLite-база: проект из расчёта, выданная комплектация с резервом партии, ТК, задание цеха, КД."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'real.db'}", connect_args={"check_same_thread": False})
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
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
    yield engine, lot
    del app.state.kitset_sheets_reader
    for name in (authenticated_principal, moscow_now, moscow_today):
        app.dependency_overrides.pop(name, None)
    for name in STATE:
        delattr(app.state, name)
    engine.dispose()


def _rows(engine, name: str) -> int:
    table = Base.metadata.tables[name]
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(table))


def test_no_kept_table_points_into_a_wiped_one():
    tables = projects_wipe.wiped_tables()
    wiped = {table.name for table in tables}
    for table in Base.metadata.sorted_tables:
        if table.name in wiped:
            continue
        targets = {fk.column.table.name for fk in table.foreign_keys} & wiped
        assert not targets, f"{table.name} ссылается на сносимые {targets}"
    assert wiped == projects_wipe.WIPED_TABLES


def test_wipe_removes_projects_once_and_leaves_stock_and_kitting(loaded):
    engine, lot = loaded
    with app.state.fulfillment_uow_factory() as uow:
        before = uow.warehouse.get(lot.id)
        requests = len(uow.fulfillment.list(limit=100))
    assert before.reserved == Decimal("15") and requests == 2
    counts = projects_wipe.count_rows(engine)
    assert (counts["techcard_kitsets"], counts["techcard_projects"], counts["production_tasks"]) == (1, 1, 1)
    assert counts["production_plan_projects"] == 1 and counts["finished_goods"] == 2

    done = projects_wipe.wipe_projects(engine, actor=ACTOR, now=NOW)

    assert done == counts
    assert all(_rows(engine, name) == 0 for name in projects_wipe.WIPED_TABLES)
    with app.state.fulfillment_uow_factory() as uow:
        after = uow.warehouse.get(lot.id)
        assert len(uow.fulfillment.list(limit=100)) == requests
    assert (after.balance, after.reserved) == (before.balance, before.reserved)
    assert _rows(engine, "techcard_kitset_documents") == 1  # КД вернётся с проектом
    assert projects_wipe.wiped_at(engine) == NOW.isoformat(timespec="seconds")
    assert projects_wipe.wipe_projects(engine, actor=ACTOR, now=NOW) is None


def test_loaded_marks_survive_until_the_wipe(loaded):
    engine, _lot = loaded
    projects_wipe.mark_loaded(engine, ["708К.4", "708 к.4", "591К.3"], NOW)
    assert projects_wipe.loaded_projects(engine) == {"708К.4", "591К.3"}
    projects_wipe.wipe_projects(engine, actor=ACTOR, now=NOW)
    assert projects_wipe.loaded_projects(engine) == set()
    projects_wipe.mark_loaded(engine, ["708К.4"], NOW)
    assert projects_wipe.loaded_projects(engine) == {"708К.4"}


def test_cli_copies_the_database_first_and_runs_once(loaded, monkeypatch, capsys):
    engine, _lot = loaded
    path = engine.url.database
    monkeypatch.setenv("ERP_DATABASE_URL", f"sqlite+pysqlite:///{path}")

    assert projects_wipe.main([]) == 0
    first = capsys.readouterr().out
    backup = engine.url.set(database=f"{path}.before-projects-wipe.bak")
    copy = create_engine(backup)
    assert "Копия базы до сноса" in first and "карточек проектов 1" in first
    assert _rows(copy, "techcard_kitsets") == 1 and _rows(engine, "techcard_kitsets") == 0
    copy.dispose()

    assert projects_wipe.main([]) == 0
    assert "уже снесены" in capsys.readouterr().out
