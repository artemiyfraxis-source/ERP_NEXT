"""«Со склада» (owner 2026-10-04): a purchase-sheet line covered from our own lot instead of being bought."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.application import (
    BindLotCommand,
    CreateFulfillmentCommand,
    bind_lot,
    create_fulfillment,
)
from app.modules.fulfillment.domain import FulfillmentLine
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.domain import (
    ProcurementLine,
    ProcurementStatus,
    ProcurementValidationError,
    StockCover,
    project_key,
)
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.requests.api import moscow_now
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

BUYER = Actor(name="Петров", department="Закупки")
NOW = datetime(2026, 10, 4, 11, 30)


def message(suffix: int = 1, quantity: str = "124", project: str | None = "591К.3", unit: str = "шт") -> OutboxMessage:
    return OutboxMessage(
        id=UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
        topic="procurement.requested",
        idempotency_key=f"request:{suffix}:procurement:v2",
        created_at=datetime(2026, 10, 2, 11, 0),
        payload={
            "source_request_id": f"10000000-0000-0000-0000-{suffix:012d}",
            "legacy_id": f"SM-20261002-{suffix:02d}",
            "request_date": "2026-10-02",
            "initiator": "Иванов",
            "department": "РНД",
            "task_type": "Закупка",
            "designation": f"RND.{suffix:03d}",
            "project_reference": project,
            "priority": "Очень срочно",
            "delivery_point": "Москва",
            "planned_date": "2026-10-20",
            "link": None,
            "item_type": "Комплектующие",
            "design_article": None,
            "category": "Общепром",
            "calculated_name": "Разъём MOLEX 43025-0400",
            "calculated_quantity": quantity,
            "calculated_unit": unit,
            "calculated_price": None,
            "calculated_price_with_vat": None,
            "calculated_sum": None,
        },
    )


def stock(quantity: str = "80", *, grn: str = "FRX.26.0000001", name: str = "Разъем Molex 43025-0400",
          unit: str = "шт", warehouse: str = "ЦАР") -> StockLot:
    return StockLot.create(
        warehouse=warehouse, grn=grn, package="", name=name, item_type="Комплектующие", purpose="Склад",
        unit=unit, quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"receiving-{grn}",),
        article="43025-0400",
    )


def buyer_principal(name: str = "Петров", roles=frozenset({"buyer"}), permissions=None) -> Principal:
    return Principal(
        actor=Actor(name=name, department="Закупки"),
        email=f"{name}@example.test",
        roles=roles,
        permissions=permissions if permissions is not None else {"procurement": "edit", "warehouse": "view"},
    )


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    procurement = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.procurement_uow_factory = procurement
    app.state.warehouse_uow_factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = buyer_principal
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    for name in ("procurement_uow_factory", "warehouse_uow_factory", "fulfillment_uow_factory"):
        delattr(app.state, name)
    engine.dispose()


def add_lot(sessions, lot: StockLot) -> StockLot:
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        uow.warehouse.add(lot)
        uow.commit()
    return lot


def get_lot(sessions, lot_id) -> StockLot:
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        return uow.warehouse.get(lot_id)


def line_in_work(client, sessions, *, facts: bool = True, **kwargs) -> dict:
    line = consume_procurement_requested(message(**kwargs), SqlAlchemyProcurementUnitOfWork(sessions))
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).json()["lines"][0]
    if not facts:
        return taken
    saved = client.patch(f"/api/procurement/{line.id}/facts", json={
        "version": taken["version"], "supplier": "ООО Поставка",
        "actual_quantity": kwargs.get("quantity", "124"), "actual_unit": "шт",
    })
    assert saved.status_code == 200, saved.text
    return saved.json()["line"]


def cover(client, line: dict, lot: StockLot, quantity: str):
    return client.post(f"/api/procurement/{line['id']}/stock-cover", json={
        "version": line["version"], "lot_id": str(lot.id), "quantity": quantity,
    })


def events(sessions, aggregate_id) -> list[AuditEventRow]:
    with sessions() as session:
        return list(session.scalars(
            select(AuditEventRow).where(AuditEventRow.aggregate_id == UUID(str(aggregate_id)))
            .order_by(AuditEventRow.occurred_at, AuditEventRow.id)
        ))


# --- domain ----------------------------------------------------------------------------------------

def domain_line(status=ProcurementStatus.IN_WORK, actual: str | None = "124", quantity: str = "124") -> ProcurementLine:
    from app.modules.procurement.domain import create_line_from_requested

    line = create_line_from_requested(message(quantity=quantity))
    return replace(
        line, status=status, buyer=BUYER, supplier="ООО Поставка", actual_unit="шт",
        actual_quantity=Decimal(actual) if actual is not None else None,
    )


def test_full_cover_closes_line_as_done_without_money():
    updated, trimmed = domain_line().cover_from_stock(Decimal("124"), "шт", BUYER)

    assert updated.status is ProcurementStatus.DONE
    assert (updated.stock_covered, updated.to_buy, trimmed) == (Decimal("124"), Decimal("0"), Decimal("0"))
    assert updated.money.sum is None and updated.invoice_url is None and updated.paid_at is None
    assert updated.order_number is None


def test_partial_cover_keeps_stage_and_cuts_actual_quantity_to_what_is_left():
    updated, trimmed = domain_line(status=ProcurementStatus.READY).cover_from_stock(Decimal("80"), "шт.", BUYER)

    assert updated.status is ProcurementStatus.READY
    assert (updated.stock_covered, updated.to_buy) == (Decimal("80"), Decimal("44"))
    assert (updated.actual_quantity, trimmed) == (Decimal("44"), Decimal("80"))


def test_partial_cover_never_cuts_actual_quantity_below_what_is_left_or_by_more_than_covered():
    packed, trimmed = domain_line(actual="200").cover_from_stock(Decimal("80"), "шт", BUYER)
    assert (packed.actual_quantity, trimmed) == (Decimal("120"), Decimal("80"))
    small, trimmed = domain_line(actual="30").cover_from_stock(Decimal("80"), "шт", BUYER)
    assert (small.actual_quantity, trimmed) == (Decimal("30"), Decimal("0"))
    empty, trimmed = domain_line(actual=None).cover_from_stock(Decimal("80"), "шт", BUYER)
    assert (empty.actual_quantity, empty.status, trimmed) == (None, ProcurementStatus.IN_WORK, Decimal("0"))


@pytest.mark.parametrize("status", [
    ProcurementStatus.NEW, ProcurementStatus.AWAITING_INVOICE, ProcurementStatus.TO_PAY,
    ProcurementStatus.IN_TRANSIT, ProcurementStatus.APPROVAL, ProcurementStatus.CANCELLED,
])
def test_cover_only_before_the_supplier_request(status):
    with pytest.raises(ProcurementValidationError, match="до запроса поставщику"):
        domain_line(status=status).cover_from_stock(Decimal("1"), "шт", BUYER)


def test_cover_refuses_more_than_left_to_buy_other_unit_zero_and_foreign_buyer():
    line = domain_line()
    with pytest.raises(ProcurementValidationError, match="осталось 124"):
        line.cover_from_stock(Decimal("125"), "шт", BUYER)
    with pytest.raises(ProcurementValidationError, match="Единицы не совпадают"):
        line.cover_from_stock(Decimal("10"), "м", BUYER)
    with pytest.raises(ProcurementValidationError, match="больше нуля"):
        line.cover_from_stock(Decimal("0"), "шт", BUYER)
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        line.cover_from_stock(Decimal("1"), "шт", Actor(name="Сидоров", department="Закупки"))


def test_release_reopens_done_line_on_its_data_stage_and_gives_actual_quantity_back():
    partial, trimmed = domain_line().cover_from_stock(Decimal("80"), "шт", BUYER)
    full, _ = partial.cover_from_stock(Decimal("44"), "шт", BUYER)
    assert full.status is ProcurementStatus.DONE

    reopened = full.release_stock_cover(Decimal("124"), trimmed, BUYER)

    assert reopened.status is ProcurementStatus.READY
    assert (reopened.stock_covered, reopened.to_buy, reopened.actual_quantity) == (0, Decimal("124"), Decimal("124"))


def test_release_is_refused_once_the_order_is_out_and_a_cover_line_cannot_be_cancelled():
    partial, trimmed = domain_line().cover_from_stock(Decimal("80"), "шт", BUYER)
    ordered = replace(partial, status=ProcurementStatus.AWAITING_INVOICE)
    with pytest.raises(ProcurementValidationError, match="Заказ поставщику уже сформирован"):
        ordered.release_stock_cover(Decimal("80"), trimmed, BUYER)
    full, _ = domain_line().cover_from_stock(Decimal("124"), "шт", BUYER)
    with pytest.raises(ProcurementValidationError, match="сначала снимите «Со склада»"):
        full.cancel(BUYER)


def test_cover_consume_and_release_and_project_match():
    held = StockCover(
        id=UUID(int=1), line_id=UUID(int=2), legacy_id="SM-20261002-01", project_reference="591К.3",
        lot_id=UUID(int=3), warehouse="ЦАР", grn="FRX.26.0000001", package="", storage_location="1F4", unit="шт",
        quantity=Decimal("80"), created_at=NOW, created_by="Петров",
    )
    assert held.label == "SM-20261002-01 / 591К.3"
    assert held.serves_project("591K.3") and not held.serves_project("591К.31") and not held.serves_project("")
    assert project_key("728К") == project_key("728к.1") == "728К.1"
    taken = held.consume(Decimal("30"), "REQ.26.0000001").consume(Decimal("10"), "REQ.26.0000002")
    assert (taken.quantity, taken.consumed, taken.consumed_by) == (40, 40, "REQ.26.0000001, REQ.26.0000002")
    with pytest.raises(ProcurementValidationError):
        taken.consume(Decimal("41"), "REQ.26.0000003")
    released = taken.release(NOW)
    assert (released.active, released.released_at) == (False, NOW)
    with pytest.raises(ProcurementValidationError, match="уже снят"):
        released.release(NOW)


# --- API, warehouse reserve, audit -------------------------------------------------------------------

def test_candidates_find_lots_like_the_warehouse_search_with_available_and_holders(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    good = add_lot(sessions, stock("80"))
    add_lot(sessions, stock("5", grn="FRX.26.0000002", unit="м"))
    add_lot(sessions, stock("9", grn="FRX.26.0000003", name="Корпус другой", warehouse="ЖУК"))

    found = client.get(f"/api/procurement/{line['id']}/stock-candidates")

    assert found.status_code == 200, found.text
    body = found.json()
    assert body["query"] == "Разъём MOLEX 43025-0400"
    assert [lot["grn"] for lot in body["lots"]] == ["FRX.26.0000001", "FRX.26.0000002"]
    first = body["lots"][0]
    assert (first["available"], first["warehouse"], first["storage_location"], first["unit_matches"]) == (
        "80", "ЦАР", "1F4", True,
    )
    assert body["lots"][1]["unit_matches"] is False
    assert body["covers"] == [] and body["line"]["to_buy"] == "124"
    typed = client.get(f"/api/procurement/{line['id']}/stock-candidates", params={"q": "корпус"}).json()
    assert [lot["grn"] for lot in typed["lots"]] == ["FRX.26.0000003"]

    cover(client, line, good, "80")
    again = client.get(f"/api/procurement/{line['id']}/stock-candidates").json()
    assert again["lots"][0]["available"] == "0"
    assert again["lots"][0]["reserve_note"] == "резерв: SM-20261002-01 / 591К.3 — 80 шт"
    assert [item["quantity"] for item in again["covers"]] == ["80"]


def test_partial_cover_reserves_lot_and_leaves_rest_to_buy_then_full_cover_closes(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("80"))
    rest = add_lot(sessions, stock("50", grn="FRX.26.0000009"))

    partial = cover(client, line, lot, "80")

    assert partial.status_code == 200, partial.text
    body = partial.json()
    assert body["line"]["status"] == "Готов к запросу"
    assert (body["line"]["stock_covered"], body["line"]["to_buy"], body["line"]["actual_quantity"]) == ("80", "44", "44")
    assert (body["cover"]["grn"], body["cover"]["quantity"], body["cover"]["label"]) == (
        "FRX.26.0000001", "80", "SM-20261002-01 / 591К.3",
    )
    stored = get_lot(sessions, lot.id)
    assert (stored.reserved, stored.available) == (Decimal("80"), Decimal("0"))

    full = cover(client, body["line"], rest, "44")

    assert full.status_code == 200, full.text
    closed = full.json()["line"]
    assert (closed["status"], closed["to_buy"], closed["actual_sum"], closed["invoice_url"]) == ("Готово", "0", None, None)
    assert get_lot(sessions, rest.id).available == Decimal("6")
    # A line closed «Со склада» never reaches the supplier request (nothing ready → no order).
    requested = client.post("/api/procurement/supplier-requests")
    assert requested.status_code == 422 or requested.json()["orders"] == []
    after = client.get(f"/api/procurement/{line['id']}/stock-candidates").json()["line"]
    assert (after["status"], after["order_number"]) == ("Готово", None)
    kinds = [event.event_type for event in events(sessions, line["id"])]
    assert kinds.count("procurement.stock_covered") == 2
    (lot_event,) = [event for event in events(sessions, lot.id) if event.event_type == "warehouse.reserved_for_procurement"]
    assert lot_event.details["holder"] == "SM-20261002-01 / 591К.3" and lot_event.details["quantity"] == "80"


def test_cover_over_available_and_over_to_buy_is_refused_and_writes_nothing(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("80"))
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        uow.warehouse.add(uow.warehouse.get(lot.id).reserve(Decimal("30")))  # a kit already holds 30
        uow.commit()

    over_available = cover(client, line, lot, "60")
    over_need = cover(client, line, add_lot(sessions, stock("500", grn="FRX.26.0000005")), "125")

    assert over_available.status_code == 422
    assert "доступно 50 шт" in over_available.json()["detail"]
    assert over_need.status_code == 422 and "осталось 124" in over_need.json()["detail"]
    assert get_lot(sessions, lot.id).reserved == Decimal("30")
    current = client.get(f"/api/procurement/{line['id']}/stock-candidates").json()["line"]
    assert (current["version"], current["stock_covered"]) == (line["version"], "0")


def test_release_returns_reserve_and_reopens_the_line(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("200"))
    closed = cover(client, line, lot, "124").json()["line"]
    assert closed["status"] == "Готово"

    stale = client.post(f"/api/procurement/{line['id']}/stock-cover/release", json={"version": line["version"]})
    released = client.post(f"/api/procurement/{line['id']}/stock-cover/release", json={"version": closed["version"]})
    nothing = client.post(
        f"/api/procurement/{line['id']}/stock-cover/release", json={"version": released.json()["line"]["version"]},
    )

    assert stale.status_code == 409
    assert released.status_code == 200, released.text
    body = released.json()
    assert (body["line"]["status"], body["line"]["stock_covered"], body["line"]["to_buy"]) == ("Готов к запросу", "0", "124")
    assert [(item["quantity"], item["active"]) for item in body["released"]] == [("0", False)]
    assert get_lot(sessions, lot.id).reserved == 0
    assert nothing.status_code == 422 and "нет резерва" in nothing.json()["detail"]
    assert "procurement.stock_cover_released" in [event.event_type for event in events(sessions, line["id"])]
    assert "warehouse.procurement_reserve_released" in [event.event_type for event in events(sessions, lot.id)]
    # «Журнал склада» is an administrator's tool (superadmin / admin).
    app.dependency_overrides[authenticated_principal] = lambda: buyer_principal("Админ", roles=frozenset({"admin"}))
    journal = client.get("/api/stock/journal").json()
    texts = [row["event"] for row in journal["rows"]]
    assert "Снят резерв «Со склада»: 124 шт · SM-20261002-01 / 591К.3" in texts
    assert "Резерв «Со склада»: 124 шт · SM-20261002-01 / 591К.3" in texts


def test_cancelling_a_partly_covered_line_gives_the_reserve_back(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("80"))
    covered = cover(client, line, lot, "80").json()["line"]

    cancelled = client.post(f"/api/procurement/{line['id']}/cancel", json={"version": covered["version"]})

    assert cancelled.status_code == 200, cancelled.text
    assert (cancelled.json()["line"]["status"], cancelled.json()["line"]["stock_covered"]) == ("Отменено", "0")
    assert get_lot(sessions, lot.id).reserved == 0


def test_guards_buyer_roles_only_and_own_lines_only(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("80"))

    # Access levels: the matrix decides — without «Лист закупки» nothing opens, on view nothing is covered.
    app.dependency_overrides[authenticated_principal] = lambda: buyer_principal(
        "Иванов", roles=frozenset({"operator"}), permissions={"requests": "edit"},
    )
    assert client.get(f"/api/procurement/{line['id']}/stock-candidates").status_code == 403
    assert cover(client, line, lot, "1").status_code == 403
    app.dependency_overrides[authenticated_principal] = lambda: buyer_principal(
        "Смотрящий", roles=frozenset({"commercial"}), permissions={"procurement": "view"},
    )
    assert cover(client, line, lot, "1").status_code == 403
    app.dependency_overrides[authenticated_principal] = lambda: buyer_principal("Сидоров")
    foreign = cover(client, line, lot, "1")
    assert foreign.status_code == 422 and "только свои строки" in foreign.json()["detail"]
    app.dependency_overrides[authenticated_principal] = buyer_principal
    assert cover(client, line, lot, "1").status_code == 200
    assert client.post(f"/api/procurement/{line['id']}/stock-cover", json={
        "version": 1, "lot_id": str(lot.id), "quantity": "-1",
    }).status_code == 422


def test_warehouse_list_shows_who_holds_the_reserve_and_row_edit_cannot_go_under_it(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("80"))
    cover(client, line, lot, "80")

    lots = client.get("/api/stock").json()["lots"]
    (shown,) = [item for item in lots if item["id"] == str(lot.id)]
    assert shown["reserve_note"] == "резерв: SM-20261002-01 / 591К.3 — 80 шт"
    assert shown["reserve_holders"][0]["procurement_line_id"] == line["id"]

    app.dependency_overrides[authenticated_principal] = lambda: buyer_principal(
        "Кладовщик", roles=frozenset({"receiver"}), permissions={"warehouse": "edit"},
    )
    stored = get_lot(sessions, lot.id)
    refused = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": stored.version, "reserved": "10"})
    assert refused.status_code == 422
    assert "Со склада" in refused.json()["detail"]


# --- consumption by a kit request of the same project --------------------------------------------------

def kit(sessions, purpose: str, need: str, lot: StockLot | None = None):
    line = FulfillmentLine.create(article="43025-0400", name="Разъём", unit="шт", requirement=Decimal(need))
    return create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"kit:{purpose}:{need}", purpose=purpose, deadline=date(2026, 10, 10), lines=(line,),
        actor=BUYER, now=NOW, lot_ids=(lot.id,) if lot else (),
    ), SqlAlchemyFulfillmentUnitOfWork(sessions))


def covers_of(sessions, line_id) -> list[StockCover]:
    with SqlAlchemyProcurementUnitOfWork(sessions) as uow:
        return list(uow.stock_covers.for_line(UUID(str(line_id))))


def test_kit_of_the_same_project_takes_over_the_cover_before_free_stock(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    cover(client, line, lot, "80")  # reserved 80, available 20

    request = kit(sessions, "591К.3 · КБ1 · Жгут", "90", lot)

    assert request.lines[0].reserved == Decimal("90") and request.shortfall_note == ""
    stored = get_lot(sessions, lot.id)
    assert (stored.reserved, stored.available) == (Decimal("90"), Decimal("10"))
    (held,) = covers_of(sessions, line["id"])
    assert (held.quantity, held.consumed, held.consumed_by) == (0, Decimal("80"), request.number)
    (taken,) = [event for event in events(sessions, line["id"]) if event.event_type == "procurement.stock_cover_taken_by_kit"]
    assert (taken.details["request"], taken.details["quantity"]) == (request.number, "80")
    assert "warehouse.procurement_reserve_taken_by_kit" in [event.event_type for event in events(sessions, lot.id)]
    # The line stays covered: the kit is what the stock was kept for.
    current = client.get(f"/api/procurement/{line['id']}/stock-candidates").json()["line"]
    assert (current["stock_covered"], current["status"]) == ("80", "Готов к запросу")
    released = client.post(f"/api/procurement/{line['id']}/stock-cover/release", json={"version": current["version"]})
    assert released.status_code == 422 and "нет резерва" in released.json()["detail"]


def test_kit_of_another_project_cannot_use_the_cover_and_bind_takes_over_partially(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    cover(client, line, lot, "80")

    other = kit(sessions, "700К.1 · КБ1 · Жгут", "50", lot)
    assert other.lines[0].reserved == Decimal("20")
    assert get_lot(sessions, lot.id).available == 0

    own = kit(sessions, "591К.3 · КБ2 · Жгут", "30")
    bound = bind_lot(BindLotCommand(
        request_id=own.id, expected_version=own.version, line_id=own.lines[0].id, lot_id=lot.id, actor=BUYER, now=NOW,
    ), SqlAlchemyFulfillmentUnitOfWork(sessions))

    assert bound.lines[0].reserved == Decimal("30")
    assert get_lot(sessions, lot.id).reserved == Decimal("100")
    (held,) = covers_of(sessions, line["id"])
    assert (held.quantity, held.consumed) == (Decimal("50"), Decimal("30"))
    current = client.get(f"/api/procurement/{line['id']}/stock-candidates").json()["line"]
    released = client.post(f"/api/procurement/{line['id']}/stock-cover/release", json={"version": current["version"]})
    assert released.status_code == 200, released.text
    assert released.json()["line"]["stock_covered"] == "30"
    assert get_lot(sessions, lot.id).reserved == Decimal("50")


# --- «Есть на складе, без ГРН» (owner, 06.10) ------------------------------------------------------------

def cover_without_lot(client, line: dict, quantity: str, warehouse: str | None = None):
    body = {"version": line["version"], "quantity": quantity}
    if warehouse is not None:
        body["warehouse"] = warehouse
    return client.post(f"/api/procurement/{line['id']}/stock-cover", json=body)


def bind(client, line: dict, cover_id: str, lot: StockLot):
    return client.post(f"/api/procurement/{line['id']}/stock-cover/{cover_id}/bind", json={
        "version": line["version"], "lot_id": str(lot.id),
    })


def assert_integrity_clean(sessions) -> None:
    """db_integrity: no lot holds less РЕЗЕРВ than its holders, nothing negative, no reserve without its lot."""
    from app.db_integrity import take_snapshot

    counts = take_snapshot(sessions.kw["bind"])["counts"]
    bad = {key: value for key, value in counts.items() if value and key != "lot_key_constraint_missing"}
    assert bad == {}


def test_lotless_cover_binds_only_a_lot_and_a_bound_cover_rebinding_is_refused():
    lotless = StockCover(
        id=UUID(int=1), line_id=UUID(int=2), legacy_id="SM-20261002-01", project_reference="591К.3",
        lot_id=None, warehouse="", grn="", package="", storage_location="", unit="шт",
        quantity=Decimal("10"), created_at=NOW, created_by="Петров",
    )
    bound = lotless.bind_lot(
        lot_id=UUID(int=3), warehouse="ЦАР", grn="FRX.26.0000001", package="", storage_location="1F4", unit="шт.",
    )
    assert (bound.lot_id, bound.grn, bound.unit, bound.quantity, bound.version) == (
        UUID(int=3), "FRX.26.0000001", "шт.", Decimal("10"), 2,
    )
    with pytest.raises(ProcurementValidationError, match="уже есть ГРН"):
        bound.bind_lot(lot_id=UUID(int=4), warehouse="ЦАР", grn="X", package="", storage_location="", unit="шт")
    with pytest.raises(ProcurementValidationError, match="уже снято"):
        lotless.release(NOW).bind_lot(
            lot_id=UUID(int=4), warehouse="ЦАР", grn="X", package="", storage_location="", unit="шт",
        )


def test_line_bind_keeps_quantities_checks_unit_buyer_and_stage():
    full, _ = domain_line().cover_from_stock(Decimal("124"), "шт", BUYER)
    bound = full.bind_stock_cover("шт.", BUYER)
    assert (bound.status, bound.stock_covered, bound.version) == (ProcurementStatus.DONE, Decimal("124"), full.version + 1)
    with pytest.raises(ProcurementValidationError, match="Единицы не совпадают"):
        full.bind_stock_cover("м", BUYER)
    with pytest.raises(ProcurementValidationError, match="только свои строки"):
        full.bind_stock_cover("шт", Actor(name="Сидоров", department="Закупки"))
    partial, _ = domain_line().cover_from_stock(Decimal("80"), "шт", BUYER)
    with pytest.raises(ProcurementValidationError, match="Заказ поставщику уже сформирован"):
        replace(partial, status=ProcurementStatus.AWAITING_INVOICE).bind_stock_cover("шт", BUYER)


def test_full_lotless_cover_closes_line_without_money_and_reserves_nothing(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("200"))

    covered = cover_without_lot(client, line, "124")

    assert covered.status_code == 200, covered.text
    body = covered.json()
    assert (body["line"]["status"], body["line"]["stock_covered"], body["line"]["to_buy"]) == ("Готово", "124", "0")
    assert (body["line"]["actual_sum"], body["line"]["invoice_url"], body["line"]["paid_at"]) == (None, None, None)
    assert (body["cover"]["lot_id"], body["cover"]["grn"], body["cover"]["warehouse"]) == (None, "", "")
    assert (body["cover"]["unit"], body["cover"]["quantity"], body["cover"]["active"]) == ("шт", "124", True)
    assert get_lot(sessions, lot.id).reserved == 0
    assert [event.event_type for event in events(sessions, lot.id)] == []
    (stored,) = covers_of(sessions, line["id"])
    assert stored.lot_id is None
    (event,) = [item for item in events(sessions, line["id"]) if item.event_type == "procurement.stock_covered"]
    assert (event.details["lot_id"], event.details["quantity"]) == ("", "124")
    assert_integrity_clean(sessions)


def test_partial_lotless_cover_leaves_the_rest_to_buy_in_the_line_unit_with_optional_warehouse(env):
    client, sessions = env
    line = line_in_work(client, sessions, unit="м", quantity="50")

    partial = cover_without_lot(client, line, "20", warehouse="ЖУК")

    assert partial.status_code == 200, partial.text
    body = partial.json()
    assert (body["line"]["status"], body["line"]["stock_covered"], body["line"]["to_buy"]) == ("Готов к запросу", "20", "30")
    assert body["line"]["actual_quantity"] == "30"
    assert (body["cover"]["unit"], body["cover"]["warehouse"], body["cover"]["lot_id"]) == ("м", "ЖУК", None)
    over = cover_without_lot(client, body["line"], "31")
    assert over.status_code == 422 and "осталось 30" in over.json()["detail"]
    unknown = cover_without_lot(client, body["line"], "1", warehouse="СКЛАД")
    assert unknown.status_code == 422
    assert_integrity_clean(sessions)


def test_lotless_cover_has_the_same_stage_and_buyer_rules(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    app.dependency_overrides[authenticated_principal] = lambda: buyer_principal("Сидоров")
    foreign = cover_without_lot(client, line, "1")
    assert foreign.status_code == 422 and "только свои строки" in foreign.json()["detail"]
    app.dependency_overrides[authenticated_principal] = buyer_principal
    fresh = consume_procurement_requested(message(suffix=2), SqlAlchemyProcurementUnitOfWork(sessions))
    unclaimed = cover_without_lot(client, {"id": str(fresh.id), "version": fresh.version}, "1")
    assert unclaimed.status_code == 422
    closed = cover_without_lot(client, line, "124").json()["line"]
    again = cover_without_lot(client, closed, "1")
    assert again.status_code == 422 and "до запроса поставщику" in again.json()["detail"]


def test_releasing_a_lotless_cover_only_reopens_the_line(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("200"))
    closed = cover_without_lot(client, line, "124", warehouse="ЦАР").json()["line"]

    released = client.post(f"/api/procurement/{line['id']}/stock-cover/release", json={"version": closed["version"]})

    assert released.status_code == 200, released.text
    body = released.json()
    assert (body["line"]["status"], body["line"]["stock_covered"], body["line"]["to_buy"]) == ("Готов к запросу", "0", "124")
    assert [(item["quantity"], item["active"], item["lot_id"]) for item in body["released"]] == [("0", False, None)]
    assert get_lot(sessions, lot.id).reserved == 0
    assert [event.event_type for event in events(sessions, lot.id)] == []
    (event,) = [item for item in events(sessions, line["id"]) if item.event_type == "procurement.stock_cover_released"]
    assert event.details["lots"] == "ЦАР без ГРН"
    assert_integrity_clean(sessions)


def test_cancelling_a_line_with_a_lotless_cover_touches_no_lot(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    covered = cover_without_lot(client, line, "80").json()["line"]

    cancelled = client.post(f"/api/procurement/{line['id']}/cancel", json={"version": covered["version"]})

    assert cancelled.status_code == 200, cancelled.text
    assert (cancelled.json()["line"]["status"], cancelled.json()["line"]["stock_covered"]) == ("Отменено", "0")
    assert_integrity_clean(sessions)


def test_bind_reserves_the_whole_covered_quantity_on_the_chosen_lot(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("200"))
    covered = cover_without_lot(client, line, "124").json()
    cover_id = covered["cover"]["id"]

    stale = bind(client, line, cover_id, lot)
    bound = bind(client, covered["line"], cover_id, lot)

    assert stale.status_code == 409
    assert bound.status_code == 200, bound.text
    body = bound.json()
    assert (body["line"]["status"], body["line"]["stock_covered"], body["line"]["version"]) == (
        "Готово", "124", covered["line"]["version"] + 1,
    )
    assert (body["cover"]["lot_id"], body["cover"]["grn"], body["cover"]["warehouse"], body["cover"]["quantity"]) == (
        str(lot.id), "FRX.26.0000001", "ЦАР", "124",
    )
    stored = get_lot(sessions, lot.id)
    assert (stored.reserved, stored.available) == (Decimal("124"), Decimal("76"))
    (lot_event,) = [event for event in events(sessions, lot.id) if event.event_type == "warehouse.reserved_for_procurement"]
    assert lot_event.details["quantity"] == "124"
    assert "procurement.stock_cover_bound" in [event.event_type for event in events(sessions, line["id"])]
    again = bind(client, body["line"], cover_id, add_lot(sessions, stock("500", grn="FRX.26.0000002")))
    assert again.status_code == 422 and "уже есть ГРН" in again.json()["detail"]
    assert_integrity_clean(sessions)
    # Bound, the cover is an ordinary «Со склада» reserve: release gives it back to the lot.
    released = client.post(f"/api/procurement/{line['id']}/stock-cover/release", json={"version": body["line"]["version"]})
    assert released.status_code == 200, released.text
    assert get_lot(sessions, lot.id).reserved == 0
    assert_integrity_clean(sessions)


def test_bind_refuses_a_lot_with_too_little_or_another_unit_and_writes_nothing(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    covered = cover_without_lot(client, line, "80").json()
    cover_id = covered["cover"]["id"]
    small = add_lot(sessions, stock("79"))
    metres = add_lot(sessions, stock("500", grn="FRX.26.0000003", unit="м"))

    short = bind(client, covered["line"], cover_id, small)
    other_unit = bind(client, covered["line"], cover_id, metres)
    missing = client.post(f"/api/procurement/{line['id']}/stock-cover/{UUID(int=9)}/bind", json={
        "version": covered["line"]["version"], "lot_id": str(small.id),
    })

    assert short.status_code == 422 and "доступно 79 шт" in short.json()["detail"]
    assert other_unit.status_code == 422 and "Единицы не совпадают" in other_unit.json()["detail"]
    assert missing.status_code == 404
    assert (get_lot(sessions, small.id).reserved, get_lot(sessions, metres.id).reserved) == (0, 0)
    (stored,) = covers_of(sessions, line["id"])
    assert (stored.lot_id, stored.quantity) == (None, Decimal("80"))
    current = client.get(f"/api/procurement/{line['id']}/stock-candidates").json()["line"]
    assert current["version"] == covered["line"]["version"]
    assert_integrity_clean(sessions)


def test_kit_of_the_same_project_never_takes_over_a_lotless_cover(env):
    client, sessions = env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    cover_without_lot(client, line, "80")

    request = kit(sessions, "591К.3 · КБ1 · Жгут", "30", lot)

    assert request.lines[0].reserved == Decimal("30")
    (held,) = covers_of(sessions, line["id"])
    assert (held.quantity, held.consumed, held.lot_id) == (Decimal("80"), 0, None)
    lots = client.get("/api/stock").json()["lots"]
    (shown,) = [item for item in lots if item["id"] == str(lot.id)]
    assert all(holder["kind"] != "procurement" for holder in shown["reserve_holders"])
    assert_integrity_clean(sessions)
