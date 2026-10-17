"""Audit F01: a stock command that read a line, cover or lot loses to a commit that lands before its write.

Each test runs command B inside command A — after A read and validated its rows, before A writes — through the real
application, unit of work and repositories over separate SQLite connections. B commits; A must be refused with
StockVersionConflict (409 over HTTP) and write nothing, so the database is exactly what B left.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.modules.fulfillment import application as fulfillment_application
from app.modules.fulfillment.application import BindLotCommand, CreateFulfillmentCommand, bind_lot, create_fulfillment
from app.modules.fulfillment.domain import FulfillmentLine
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.procurement.application import (
    BindStockCoverCommand,
    CoverFromStockCommand,
    OfflineAction,
    ProcurementOfflineCommand,
    ReleaseStockCoverCommand,
    bind_stock_cover,
    cover_from_stock,
    release_stock_cover,
    set_procurement_offline_status,
)
from app.modules.procurement.domain import ProcurementLine, StockCover
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.procurement.stock_port import StockVersionConflict
from app.modules.requests.api import moscow_now
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import Base, create_schema
from app.workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from tests.procurement.test_stock_cover import (
    BUYER, NOW, add_lot, buyer_principal, cover_without_lot, get_lot, line_in_work, stock,
)


@pytest.fixture()
def concurrent_env(tmp_path, monkeypatch):
    # Separate connections: B's commit is a real commit A's next read can see.
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'stock-versions.db'}", connect_args={"check_same_thread": False})
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(app.state, "procurement_uow_factory", lambda: SqlAlchemyProcurementUnitOfWork(sessions), raising=False)
    monkeypatch.setattr(
        app.state, "procurement_stock_uow_factory", lambda: SqlAlchemyProcurementStockUnitOfWork(sessions), raising=False,
    )
    monkeypatch.setitem(app.dependency_overrides, authenticated_principal, buyer_principal)
    monkeypatch.setitem(app.dependency_overrides, moscow_now, lambda: NOW)
    client = TestClient(app)
    yield client, sessions
    client.close()
    engine.dispose()


def everything(sessions) -> dict[str, list[dict]]:
    """Every row of every table, as the database holds it."""
    with sessions() as session:
        return {
            table.name: [
                dict(row) for row in session.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for table in Base.metadata.sorted_tables
        }


def run_inside(monkeypatch, owner, method, nested):
    """After the first call of `owner.method` returns, run `nested` (command B) once and keep what it left."""
    original = getattr(owner, method)
    state: dict = {"ran": False, "after": None}

    def patched(*args, **kwargs):
        result = original(*args, **kwargs)
        if not state["ran"]:
            state["ran"] = True
            nested()
            state["after"] = everything(nested.sessions)
        return result

    monkeypatch.setattr(owner, method, patched)
    return state


def stock_uow(sessions):
    return SqlAlchemyProcurementStockUnitOfWork(sessions)


def cover_command(line: dict, lot: StockLot | None, quantity: str = "80") -> CoverFromStockCommand:
    return CoverFromStockCommand(
        UUID(line["id"]), line["version"], lot.id if lot else None, Decimal(quantity), BUYER, NOW,
    )


def bind_command(line: dict, cover_id: str, lot: StockLot) -> BindStockCoverCommand:
    return BindStockCoverCommand(UUID(line["id"]), line["version"], UUID(cover_id), lot.id, BUYER, NOW)


def lotless_line(client, sessions, quantity: str = "80") -> tuple[dict, str]:
    line = line_in_work(client, sessions)
    response = cover_without_lot(client, line, quantity)
    assert response.status_code == 200, response.text
    return response.json()["line"], response.json()["cover"]["id"]


def nested(sessions, command):
    def run():
        command()
    run.sessions = sessions
    return run


def line_row(state, line_id) -> dict:
    return next(row for row in state["procurement_lines"] if row["id"] == UUID(str(line_id)))


def lot_row(state, lot_id) -> dict:
    return next(row for row in state["warehouse_lots"] if row["id"] == lot_id)


# --- the two reproductions of the audit (docs/audit/evidence/2026-10-10-stock-cover-lost-update.py) -------------


def test_two_covers_of_one_line_from_two_lots_leave_one(concurrent_env, monkeypatch):
    client, sessions = concurrent_env
    line = line_in_work(client, sessions)  # КОЛ-ВО 124: one cover of 80 fits, two do not
    lot_a = add_lot(sessions, stock("100", grn="FRX.26.0000201"))
    lot_b = add_lot(sessions, stock("100", grn="FRX.26.0000202"))
    state = run_inside(monkeypatch, ProcurementLine, "cover_from_stock", nested(
        sessions, lambda: cover_from_stock(cover_command(line, lot_b), stock_uow(sessions)),
    ))

    with pytest.raises(StockVersionConflict):
        cover_from_stock(cover_command(line, lot_a), stock_uow(sessions))

    after = everything(sessions)
    assert after == state["after"]
    stored = line_row(after, line["id"])
    assert (stored["version"], stored["stock_covered"]) == (line["version"] + 1, Decimal("80"))
    (only,) = after["procurement_stock_covers"]
    assert (only["lot_id"], only["quantity"]) == (lot_b.id, Decimal("80"))
    assert (lot_row(after, lot_a.id)["reserved"], lot_row(after, lot_b.id)["reserved"]) == (0, Decimal("80"))


def test_one_lotless_cover_bound_to_two_lots_stays_on_one(concurrent_env, monkeypatch):
    client, sessions = concurrent_env
    line, cover_id = lotless_line(client, sessions)
    lot_a = add_lot(sessions, stock("100", grn="FRX.26.0000201"))
    lot_b = add_lot(sessions, stock("100", grn="FRX.26.0000202"))
    state = run_inside(monkeypatch, ProcurementLine, "bind_stock_cover", nested(
        sessions, lambda: bind_stock_cover(bind_command(line, cover_id, lot_b), stock_uow(sessions)),
    ))

    with pytest.raises(StockVersionConflict):
        bind_stock_cover(bind_command(line, cover_id, lot_a), stock_uow(sessions))

    after = everything(sessions)
    assert after == state["after"]
    (bound,) = after["procurement_stock_covers"]
    assert (bound["lot_id"], bound["quantity"], bound["version"]) == (lot_b.id, Decimal("80"), 2)
    assert (lot_row(after, lot_a.id)["reserved"], lot_row(after, lot_b.id)["reserved"]) == (0, Decimal("80"))
    assert line_row(after, line["id"])["version"] == line["version"] + 1


@pytest.mark.parametrize("action", ["cover", "bind"])
def test_http_loser_gets_409_after_its_retry_and_writes_nothing(concurrent_env, monkeypatch, action):
    client, sessions = concurrent_env
    if action == "cover":
        line, cover_id = line_in_work(client, sessions), None
    else:
        line, cover_id = lotless_line(client, sessions)
    lot_a = add_lot(sessions, stock("100", grn="FRX.26.0000201"))
    lot_b = add_lot(sessions, stock("100", grn="FRX.26.0000202"))
    if action == "cover":
        method, command = "cover_from_stock", lambda: cover_from_stock(cover_command(line, lot_b), stock_uow(sessions))
    else:
        method = "bind_stock_cover"
        command = lambda: bind_stock_cover(bind_command(line, cover_id, lot_b), stock_uow(sessions))  # noqa: E731
    state = run_inside(monkeypatch, ProcurementLine, method, nested(sessions, command))

    if action == "cover":
        response = client.post(f"/api/procurement/{line['id']}/stock-cover", json={
            "version": line["version"], "lot_id": str(lot_a.id), "quantity": "80",
        })
    else:
        response = client.post(f"/api/procurement/{line['id']}/stock-cover/{cover_id}/bind", json={
            "version": line["version"], "lot_id": str(lot_a.id),
        })

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Строка изменилась после загрузки формы"
    assert everything(sessions) == state["after"]


# --- bind against release / cancel of the same cover -------------------------------------------------------


@pytest.mark.parametrize("other", ["release", "cancel"])
def test_bind_loses_to_a_release_or_cancel_committed_meanwhile(concurrent_env, monkeypatch, other):
    client, sessions = concurrent_env
    line, cover_id = lotless_line(client, sessions)
    lot = add_lot(sessions, stock("100"))

    def take_back():
        if other == "release":
            release_stock_cover(ReleaseStockCoverCommand(UUID(line["id"]), line["version"], BUYER, NOW), stock_uow(sessions))
        else:
            set_procurement_offline_status(ProcurementOfflineCommand(
                UUID(line["id"]), line["version"], OfflineAction.CANCEL, BUYER, NOW,
            ), stock_uow(sessions))

    state = run_inside(monkeypatch, ProcurementLine, "bind_stock_cover", nested(sessions, take_back))

    with pytest.raises(StockVersionConflict):
        bind_stock_cover(bind_command(line, cover_id, lot), stock_uow(sessions))

    after = everything(sessions)
    assert after == state["after"]
    (cover,) = after["procurement_stock_covers"]
    assert (cover["lot_id"], cover["quantity"]) == (None, 0)
    assert lot_row(after, lot.id)["reserved"] == 0


@pytest.mark.parametrize("other", ["release", "cancel"])
def test_release_or_cancel_loses_to_a_bind_committed_meanwhile(concurrent_env, monkeypatch, other):
    client, sessions = concurrent_env
    line, cover_id = lotless_line(client, sessions)
    lot = add_lot(sessions, stock("100"))
    state = run_inside(
        monkeypatch, ProcurementLine, "release_stock_cover" if other == "release" else "cancel",
        nested(sessions, lambda: bind_stock_cover(bind_command(line, cover_id, lot), stock_uow(sessions))),
    )

    with pytest.raises(StockVersionConflict):
        if other == "release":
            release_stock_cover(ReleaseStockCoverCommand(UUID(line["id"]), line["version"], BUYER, NOW), stock_uow(sessions))
        else:
            set_procurement_offline_status(ProcurementOfflineCommand(
                UUID(line["id"]), line["version"], OfflineAction.CANCEL, BUYER, NOW,
            ), stock_uow(sessions))

    after = everything(sessions)
    assert after == state["after"]
    (cover,) = after["procurement_stock_covers"]
    # The lot keeps its reserve and the cover still holds it: no reserve without a holder.
    assert (cover["lot_id"], cover["quantity"]) == (lot.id, Decimal("80"))
    assert lot_row(after, lot.id)["reserved"] == Decimal("80")


# --- a kit reserve against a procurement cover on the same lot ---------------------------------------------


def kit(sessions, purpose: str, need: str):
    line = FulfillmentLine.create(article="43025-0400", name="Разъём", unit="шт", requirement=Decimal(need))
    return create_fulfillment(CreateFulfillmentCommand(
        idempotency_key=f"kit:{purpose}:{need}", purpose=purpose, deadline=date(2026, 10, 10), lines=(line,),
        actor=BUYER, now=NOW,
    ), SqlAlchemyFulfillmentUnitOfWork(sessions))


def bind_kit(sessions, request, lot):
    return bind_lot(BindLotCommand(
        request_id=request.id, expected_version=request.version, line_id=request.lines[0].id, lot_id=lot.id,
        actor=BUYER, now=NOW,
    ), SqlAlchemyFulfillmentUnitOfWork(sessions))


def test_procurement_cover_loses_to_a_kit_reserve_on_its_lot(concurrent_env, monkeypatch):
    client, sessions = concurrent_env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    request = kit(sessions, "700К.1 · КБ1 · Жгут", "50")
    state = run_inside(monkeypatch, ProcurementLine, "cover_from_stock", nested(
        sessions, lambda: bind_kit(sessions, request, lot),
    ))

    with pytest.raises(StockVersionConflict):
        cover_from_stock(cover_command(line, lot), stock_uow(sessions))

    after = everything(sessions)
    assert after == state["after"]
    assert (lot_row(after, lot.id)["reserved"], lot_row(after, lot.id)["version"]) == (Decimal("50"), lot.version + 1)
    assert after["procurement_stock_covers"] == []


def test_kit_reserve_loses_to_a_procurement_cover_on_its_lot(concurrent_env, monkeypatch):
    client, sessions = concurrent_env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    request = kit(sessions, "700К.1 · КБ1 · Жгут", "50")
    state = run_inside(monkeypatch, fulfillment_application, "_kit_reserves", nested(
        sessions, lambda: cover_from_stock(cover_command(line, lot), stock_uow(sessions)),
    ))

    with pytest.raises(StockVersionConflict):
        bind_kit(sessions, request, lot)

    after = everything(sessions)
    assert after == state["after"]
    # Without the compare-and-swap the kit wrote its РЕЗЕРВ 50 over the cover's 80: a cover with no reserve under it.
    assert (lot_row(after, lot.id)["reserved"], lot_row(after, lot.id)["version"]) == (Decimal("80"), lot.version + 1)
    (fulfillment_line,) = after["fulfillment_lines"]
    assert fulfillment_line["reserved"] == 0


def test_kit_takeover_loses_to_a_release_of_the_cover(concurrent_env, monkeypatch):
    client, sessions = concurrent_env
    line = line_in_work(client, sessions)
    lot = add_lot(sessions, stock("100"))
    covered = client.post(f"/api/procurement/{line['id']}/stock-cover", json={
        "version": line["version"], "lot_id": str(lot.id), "quantity": "80",
    })
    assert covered.status_code == 200, covered.text
    line = covered.json()["line"]
    request = kit(sessions, "591К.3 · КБ1 · Жгут", "90")
    state = run_inside(monkeypatch, StockCover, "consume", nested(
        sessions,
        lambda: release_stock_cover(ReleaseStockCoverCommand(UUID(line["id"]), line["version"], BUYER, NOW), stock_uow(sessions)),
    ))

    with pytest.raises(StockVersionConflict):
        bind_kit(sessions, request, lot)

    after = everything(sessions)
    assert after == state["after"]
    (cover,) = after["procurement_stock_covers"]
    assert (cover["quantity"], cover["consumed"]) == (0, 0)
    assert lot_row(after, lot.id)["reserved"] == 0


# --- the repository contract ---------------------------------------------------------------------------------


def test_lot_written_twice_in_one_transaction_and_read_again_after_commit(concurrent_env):
    _, sessions = concurrent_env
    lot = add_lot(sessions, stock("100"))
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        first = uow.warehouse.get(lot.id).reserve(Decimal("10"))
        uow.warehouse.add(first)
        uow.session.flush()
        uow.warehouse.add(uow.warehouse.get(lot.id).reserve(Decimal("5")))
        uow.commit()
        # The same session after its commit: someone else moves the lot on, a fresh read writes over it fine.
        with SqlAlchemyWarehouseUnitOfWork(sessions) as other:
            other.warehouse.add(other.warehouse.get(lot.id).reserve(Decimal("1")))
            other.commit()
        uow.session.expunge_all()
        uow.warehouse.add(uow.warehouse.get(lot.id).reserve(Decimal("2")))
        uow.commit()
    stored = get_lot(sessions, lot.id)
    assert (stored.reserved, stored.version) == (Decimal("18"), lot.version + 4)


def test_a_snapshot_older_than_the_row_is_refused(concurrent_env):
    _, sessions = concurrent_env
    lot = add_lot(sessions, stock("100"))
    with pytest.raises(StockVersionConflict):
        with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
            read = uow.warehouse.get(lot.id)
            uow.warehouse.add(read.reserve(Decimal("10")))
            uow.warehouse.add(uow.warehouse.get(lot.id).reserve(Decimal("5")))
            uow.warehouse.add(read.reserve(Decimal("20")))  # built on the first read, older than the row now
            uow.commit()
    assert get_lot(sessions, lot.id) == lot


def test_line_and_cover_refuse_a_write_over_a_commit_made_after_the_read(concurrent_env):
    client, sessions = concurrent_env
    line, cover_id = lotless_line(client, sessions)
    with SqlAlchemyProcurementUnitOfWork(sessions) as uow:
        read_line = uow.procurement.get(UUID(line["id"]))
        read_cover = uow.stock_covers.get(UUID(cover_id))
        with SqlAlchemyProcurementUnitOfWork(sessions) as other:
            other.stock_covers.add(other.stock_covers.get(UUID(cover_id)).release(NOW))
            other.procurement.add(other.procurement.get(UUID(line["id"])).bind_stock_cover("шт", BUYER))
            other.commit()
        with pytest.raises(StockVersionConflict):
            uow.stock_covers.add(read_cover.release(NOW))
        with pytest.raises(StockVersionConflict):
            uow.procurement.add(read_line.bind_stock_cover("шт", BUYER))
