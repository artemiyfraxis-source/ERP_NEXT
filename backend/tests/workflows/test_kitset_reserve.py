"""Резерв при выборе ГРН в спецификации (T1, 06.10.2026): a lot picked for a spec row is reserved for the project's
КБ at once; a re-pick moves the reserve, a cleared ГРН / a dropped row / a deleted project releases it, the kit request
takes it over without counting the goods twice."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db_integrity import take_snapshot
from app.main import app
from app.modules.fulfillment.application import HoldKitReserveCommand, hold_kit_reserve
from app.modules.fulfillment.infrastructure import (
    FulfillmentKitReserveRow,
    FulfillmentLineRow,
    SqlAlchemyFulfillmentUnitOfWork,
)
from app.modules.procurement.domain import StockCover
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared.clock import moscow_now, moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows import kitset_reserve
from app.workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from tests.factories import Actor
from tests.techcards.calc_sheets import FakeSheets, calc_sheets, grid


STATE = (
    "production_uow_factory", "techcards_uow_factory", "fulfillment_uow_factory",
    "shipping_uow_factory", "uow_factory", "procurement_uow_factory", "procurement_stock_uow_factory",
)
CALC = "https://docs.google.com/spreadsheets/d/1AbC-calc_id/edit#gid=0"
PROJECT = "708К.4"
ART = "43025-0400"  # КБ1: 2 шт на изделие × тираж 10, штучное без брака → на партию 20


def lot(grn: str, quantity: str, unit: str = "шт") -> StockLot:
    return StockLot.create(
        warehouse="ЦАР", grn=grn, package="", name="Корпус Molex 43025-0400", item_type="Комплектующие",
        purpose="Склад", unit=unit, quantity=Decimal(quantity), storage_location="1F4",
        source_receiving_ids=(f"receiving-{grn}",), article=ART,
    )


@pytest.fixture()
def env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.procurement_stock_uow_factory = lambda: SqlAlchemyProcurementStockUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}), permissions={"projects": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 10, 6, 10, 0)
    app.dependency_overrides[moscow_today] = lambda: datetime(2026, 10, 6).date()
    app.state.kitset_sheets_reader = FakeSheets()
    client = TestClient(app)
    assert client.post("/api/techcards/kitsets/load", json={"url": CALC}).status_code == 200
    yield client, engine
    del app.state.kitset_sheets_reader
    for name in (authenticated_principal, moscow_now, moscow_today):
        app.dependency_overrides.pop(name, None)
    for name in STATE:
        delattr(app.state, name)
    engine.dispose()


def put(*lots: StockLot) -> None:
    with app.state.fulfillment_uow_factory() as uow:
        for item in lots:
            uow.warehouse.add(item)
        uow.commit()


def stored(item: StockLot) -> StockLot:
    with app.state.fulfillment_uow_factory() as uow:
        return uow.warehouse.get(item.id)


def reserves(engine, active_only: bool = True) -> list[FulfillmentKitReserveRow]:
    with Session(engine) as session:
        query = select(FulfillmentKitReserveRow).order_by(FulfillmentKitReserveRow.created_at)
        if active_only:
            query = query.where(FulfillmentKitReserveRow.quantity > 0)
        return list(session.scalars(query))


def binding(item: StockLot | None, **extra) -> dict | None:
    if item is None:
        return None
    return {"lot_id": str(item.id), "warehouse": item.warehouse, "grn": item.grn, "package": item.package, **extra}


def save(client, item: StockLot | None, *, per: str = "2", index: str = "КБ1", art: str = ART, **lot_extra):
    return client.put(f"/api/techcards/kitsets/{PROJECT}/lines", json={"edits": [{
        "index": index, "art": art, "per": per, "scrap": "0", "lot": binding(item, **lot_extra),
    }]})


def component(client, art: str = ART, index: str = "КБ1") -> dict:
    card = client.get(f"/api/techcards/kitsets/{PROJECT}").json()
    assembly = next(item for item in card["assemblies"] if item["index"] == index)
    return next(item for item in assembly["components"] if item["art"] == art)


def assert_clean(engine) -> None:
    """db_integrity: no lot holds less РЕЗЕРВ than its holders, nothing negative, no reserve without its lot."""
    counts = take_snapshot(engine)["counts"]
    bad = {key: value for key, value in counts.items() if value and key != "lot_key_constraint_missing"}
    assert bad == {}


def test_pick_reserves_min_of_batch_and_free_and_other_projects_see_less(env):
    client, engine = env
    small, big = lot("FRX.26.0000001", "15"), lot("FRX.26.0000002", "30")
    put(small, big)

    assert save(client, small).status_code == 200
    [held] = reserves(engine)
    assert (held.project, held.item_index, held.article, held.lot_id, held.quantity, held.unit) == (
        PROJECT, "КБ1", ART, small.id, Decimal("15"), "шт",
    )  # the lot holds 15 of the 20 needed: partial
    assert (stored(small).reserved, stored(small).available) == (Decimal("15"), Decimal("0"))
    row = component(client)
    assert row["reserve"] == {
        "quantity": "15", "unit": "шт", "lot_id": str(small.id), "warehouse": "ЦАР", "grn": small.grn, "package": "",
    }
    own = client.get("/api/techcards/kitsets/lots", params={"q": "43025", "project": PROJECT}).json()["lots"]
    other = client.get("/api/techcards/kitsets/lots", params={"q": "43025", "project": "709К.1"}).json()["lots"]
    assert {item["grn"]: item["available"] for item in own} == {small.grn: "15", big.grn: "30"}
    assert {item["grn"]: item["available"] for item in other} == {big.grn: "30"}  # 0 free for another project

    assert save(client, big).status_code == 200  # whole batch fits: 20 of 30
    [held] = reserves(engine)
    assert (held.lot_id, held.quantity) == (big.id, Decimal("20"))
    assert (stored(big).reserved, stored(big).available) == (Decimal("20"), Decimal("10"))
    assert_clean(engine)


def test_repick_moves_the_reserve_and_clear_releases_it(env):
    client, engine = env
    first, second = lot("FRX.26.0000001", "40"), lot("FRX.26.0000002", "40")
    put(first, second)

    save(client, first)
    save(client, second)
    [held] = reserves(engine)
    assert (held.lot_id, held.quantity) == (second.id, Decimal("20"))
    assert (stored(first).reserved, stored(second).reserved) == (Decimal("0"), Decimal("20"))
    released = [row for row in reserves(engine, active_only=False) if row.lot_id == first.id]
    assert [(row.quantity, row.released_at is not None) for row in released] == [(Decimal("0"), True)]

    save(client, second, per="3")  # «на партию» 30: the reserve follows the row's need
    assert [row.quantity for row in reserves(engine)] == [Decimal("30")]
    assert stored(second).reserved == Decimal("30")

    save(client, second, per="3")  # an unchanged row leaves the reserve as it is
    assert len(reserves(engine, active_only=False)) == 3

    assert save(client, None).status_code == 200
    assert reserves(engine) == []
    assert stored(second).reserved == Decimal("0")
    assert component(client)["lot"] is None and component(client)["reserve"] is None
    with Session(engine) as session:
        events = list(session.scalars(select(AuditEventRow.event_type).where(AuditEventRow.aggregate_id == second.id)))
    assert events.count("warehouse.kit_reserve_held") == 2 and events.count("warehouse.kit_reserve_released") == 2
    assert_clean(engine)


def test_units_mismatch_binds_the_grn_without_a_reserve(env):
    client, engine = env
    metres = lot("FRX.26.0000003", "100", unit="м")
    put(metres)

    assert save(client, metres).status_code == 200
    assert component(client)["lot"]["grn"] == metres.grn
    assert component(client)["reserve"] is None
    assert reserves(engine) == [] and stored(metres).reserved == Decimal("0")


def test_stale_lot_version_is_refused_with_409_and_nothing_moves(env):
    client, engine = env
    item = lot("FRX.26.0000001", "40")
    put(item)
    shown = client.get("/api/techcards/kitsets/lots", params={"q": "43025", "project": PROJECT}).json()["lots"]
    assert shown[0]["version"] == 1
    with app.state.fulfillment_uow_factory() as uow:  # the storekeeper moves the lot after the list was loaded
        uow.warehouse.add(uow.warehouse.get(item.id).reserve(Decimal("1")))
        uow.commit()

    refused = save(client, item, version=shown[0]["version"])

    assert refused.status_code == 409, refused.text
    assert component(client)["lot"] is None
    assert reserves(engine) == [] and stored(item).reserved == Decimal("1")
    fresh = client.get("/api/techcards/kitsets/lots", params={"q": "43025", "project": PROJECT}).json()["lots"]
    assert save(client, item, version=fresh[0]["version"]).status_code == 200
    assert [row.quantity for row in reserves(engine)] == [Decimal("20")]
    # the binding is kept: a later edit of the same row with the old version shown does not recheck the lot
    assert save(client, item, per="2", version=1).status_code == 200


def test_failed_kitset_save_gives_the_reserve_back(env, monkeypatch):
    client, engine = env
    item = lot("FRX.26.0000001", "40")
    put(item)
    original = kitset_reserve.sync_rows

    def sync_then_someone_saves(*args, **kwargs):
        result = original(*args, **kwargs)
        with app.state.techcards_uow_factory() as uow:  # another tab saves the card meanwhile
            current = uow.kitsets.get(PROJECT)
            uow.kitsets.save(current.with_edits([], who="Другой", now=datetime(2026, 10, 6, 10, 1))[0])
            uow.commit()
        return result

    monkeypatch.setattr(kitset_reserve, "sync_rows", sync_then_someone_saves)
    refused = save(client, item)
    monkeypatch.setattr(kitset_reserve, "sync_rows", original)

    assert refused.status_code == 409
    assert component(client)["lot"] is None
    assert reserves(engine) == [] and stored(item).reserved == Decimal("0")  # no orphan reserve
    assert_clean(engine)


def test_refresh_releases_a_dropped_row_and_recounts_a_changed_run(env):
    client, engine = env
    housing = lot("FRX.26.0000001", "100")
    contact = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000002", package="", name="Контакт 43030", item_type="Комплектующие",
        purpose="Склад", unit="шт", quantity=Decimal("100"), storage_location="1F5",
        source_receiving_ids=("receiving-contact",), article="43030-0007",
    )
    put(housing, contact)
    save(client, housing)  # КБ1: 20
    assert client.put(f"/api/techcards/kitsets/{PROJECT}/lines", json={"edits": [{
        "index": "КБ2", "art": "43030-0007", "per": "8", "scrap": "0", "lot": binding(contact),
    }]}).status_code == 200  # КБ2: 8 × 4 = 32
    assert sorted(row.quantity for row in reserves(engine)) == [Decimal("20"), Decimal("32")]

    sheets = calc_sheets()
    sheets["СПЯ"] = grid([row for row in sheets["СПЯ"] if row[1] != ART])  # the housing left the calculation
    sheets["ЗКЗ"] = grid([row if row[1] != "КБ2" else [2, "КБ2", "Жгут сигнальный", 5, 1] for row in sheets["ЗКЗ"]])
    app.state.kitset_sheets_reader = FakeSheets(sheets)
    refreshed = client.post("/api/techcards/kitsets/load", json={"url": CALC, "number": PROJECT})

    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["res"]["reserve_error"] == ""
    [held] = reserves(engine)
    assert (held.item_index, held.article, held.quantity) == ("КБ2", "43030-0007", Decimal("40"))  # 8 × 5
    assert (stored(housing).reserved, stored(contact).reserved) == (Decimal("0"), Decimal("40"))
    assert_clean(engine)


def test_refresh_does_not_backfill_bindings_without_a_reserve(env):
    client, engine = env
    item = lot("FRX.26.0000001", "100")
    put(item)
    save(client, item)
    with app.state.fulfillment_uow_factory() as uow:  # a binding from before T1: the ГРН without a reserve
        for reserve in kitset_reserve.reserves_by_row(PROJECT, app.state.fulfillment_uow_factory)[("КБ1", ART)]:
            uow.kit_reserves.add(reserve.release(datetime(2026, 10, 6, 9, 0)))
        uow.warehouse.add(uow.warehouse.get(item.id).release_reserve(Decimal("20")))
        uow.commit()

    assert client.post("/api/techcards/kitsets/load", json={"url": CALC, "number": PROJECT}).status_code == 200

    assert reserves(engine) == [] and stored(item).reserved == Decimal("0")
    assert component(client)["lot"]["grn"] == item.grn


def test_deleted_project_releases_its_reserves(env):
    client, engine = env
    item = lot("FRX.26.0000001", "100")
    put(item)
    save(client, item)
    assert stored(item).reserved == Decimal("20")

    deleted = client.delete(f"/api/techcards/kitsets/{PROJECT}")

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["res"]["reserve_error"] == ""
    assert reserves(engine) == [] and stored(item).reserved == Decimal("0")
    assert_clean(engine)


def test_issue_takes_the_reserve_over_without_counting_twice(env):
    client, engine = env
    item = lot("FRX.26.0000001", "15")
    put(item)
    save(client, item)
    assert stored(item).reserved == Decimal("15")

    issued = client.post(f"/api/techcards/kitsets/{PROJECT}/issue", json={"deadline": "2026-10-20"})

    assert issued.status_code == 200, issued.text
    assert issued.json()["reserve_error"] == ""
    assert reserves(engine) == []
    [taken] = reserves(engine, active_only=False)
    assert (taken.consumed, taken.consumed_by.startswith("REQ.")) == (Decimal("15"), True)
    with Session(engine) as session:
        kit_held = session.scalar(
            select(func.coalesce(func.sum(FulfillmentLineRow.reserved), 0)).where(FulfillmentLineRow.lot_id == item.id)
        )
    assert Decimal(str(kit_held)) == Decimal("15")
    assert (stored(item).reserved, stored(item).available) == (Decimal("15"), Decimal("0"))  # РЕЗЕРВ stays the same

    # the КБ is in kitting now: a later edit of its row does not hold the goods a second time
    assert save(client, item, per="3").status_code == 200
    assert reserves(engine) == [] and stored(item).reserved == Decimal("15")
    assert_clean(engine)


class _Store:
    def __init__(self, items=()):
        self.items = {item.id: item for item in items}

    def get(self, item_id):
        return self.items.get(item_id)

    def add(self, item):
        self.items[item.id] = item

    def active_on_lots(self, lot_ids):
        return [item for item in self.items.values() if item.lot_id in lot_ids and item.quantity > 0]

    def for_project(self, project):
        return [item for item in self.items.values() if item.project == project]


class _Audit:
    def add(self, item):
        pass


class _Uow:
    def __init__(self, lots, covers=()):
        self.warehouse, self.kit_reserves, self.stock_covers = _Store(lots), _Store(), _Store(covers)
        self.audit = _Audit()


def test_hold_leaves_out_what_the_projects_stock_cover_already_holds():
    """The kit takes the project's «Со склада» cover first: the techcard reserve holds only the rest of the need."""
    item = lot("FRX.26.0000001", "40").reserve(Decimal("12"))
    cover = StockCover(
        id=UUID("60000000-0000-0000-0000-000000000001"), line_id=UUID("60000000-0000-0000-0000-000000000002"),
        legacy_id="SM-1", project_reference=PROJECT, lot_id=item.id, warehouse=item.warehouse, grn=item.grn,
        package="", storage_location="1F4", unit="шт", quantity=Decimal("12"),
        created_at=datetime(2026, 10, 6, 9, 0), created_by="Снабженец",
    )
    uow = _Uow([item], [cover])
    command = HoldKitReserveCommand(
        project=PROJECT, item_index="КБ1", article=ART, lot_id=item.id, quantity=Decimal("20"), unit="шт",
        actor=Actor(name="Технолог", department="Производство"), now=datetime(2026, 10, 6, 10, 0),
    )

    held = hold_kit_reserve(command, uow)

    assert held.quantity == Decimal("8")  # 20 needed − 12 under the cover
    assert uow.warehouse.get(item.id).reserved == Decimal("20")
    other = hold_kit_reserve(replace(command, project="709К.1"), uow)
    assert other.quantity == Decimal("20")  # another project's need: the cover is not its own
    assert uow.warehouse.get(item.id).reserved == Decimal("40")
