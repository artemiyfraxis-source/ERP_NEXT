"""A stock cover must use the same lot version that procurement validated."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.main import app
from app.modules.procurement.domain import ProcurementLine
from app.modules.procurement.infrastructure import ProcurementStockCoverRow, SqlAlchemyProcurementUnitOfWork
from app.modules.requests.api import moscow_now
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from tests.procurement.test_stock_cover import (
    NOW, add_lot, bind, buyer_principal, cover, cover_without_lot, get_lot, line_in_work, stock,
)


@pytest.fixture()
def concurrent_env(tmp_path, monkeypatch):
    # Separate connections; each concurrent edit commits before the cover writes anything.
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'stock-cover.db'}", connect_args={"check_same_thread": False})
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


def edit_lot_metadata(sessions, lot_id):
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        lot = uow.warehouse.get(lot_id)
        updated = lot.edit_row({"unit": "м", "storage_location": "2G5"})
        uow.warehouse.add(updated)
        uow.commit()
        return updated


@pytest.mark.parametrize("action", ["reserve", "release"])
def test_stock_port_rejects_a_view_changed_before_mutation(concurrent_env, action):
    _, sessions = concurrent_env
    lot = add_lot(sessions, stock().reserve(Decimal("20")))

    with SqlAlchemyProcurementStockUnitOfWork(sessions) as uow:
        viewed = uow.stock.get(lot.id)
        edited = edit_lot_metadata(sessions, lot.id)

        with pytest.raises(StaleDataError):
            getattr(uow.stock, action)(lot.id, Decimal("10"), expected_version=viewed.version)

    assert get_lot(sessions, lot.id) == edited


@pytest.mark.parametrize("action", ["cover", "bind"])
def test_command_retries_and_revalidates_changed_unit(concurrent_env, monkeypatch, action):
    client, sessions = concurrent_env
    line = line_in_work(client, sessions, quantity="10")
    lot = add_lot(sessions, stock())
    if action == "bind":
        response = cover_without_lot(client, line, "10")
        assert response.status_code == 200, response.text
        line = response.json()["line"]
        cover_id = response.json()["cover"]["id"]
    with sessions() as session:
        audit_before = set(session.scalars(select(AuditEventRow.id)))
        covers_before = list(session.execute(select(ProcurementStockCoverRow.__table__)).mappings())
    validated_units = []
    method = "cover_from_stock" if action == "cover" else "bind_stock_cover"
    original = getattr(ProcurementLine, method)

    def edit_after_validation(current, *args):
        lot_unit = args[1] if action == "cover" else args[0]
        validated_units.append(lot_unit)
        result = original(current, *args)
        if len(validated_units) == 1:
            edit_lot_metadata(sessions, lot.id)
        return result

    monkeypatch.setattr(ProcurementLine, method, edit_after_validation)

    response = cover(client, line, lot, "10") if action == "cover" else bind(client, line, cover_id, lot)

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Единицы не совпадают: в строке «шт», у партии «м»"
    assert validated_units == ["шт", "м"]
    stored = get_lot(sessions, lot.id)
    assert (stored.version, stored.unit, stored.storage_location, stored.reserved) == (lot.version + 1, "м", "2G5", 0)
    current = client.get(f"/api/procurement/{line['id']}/stock-candidates").json()["line"]
    assert (current["version"], current["stock_covered"], current["status"]) == (
        line["version"], line["stock_covered"], line["status"],
    )
    with sessions() as session:
        assert list(session.execute(select(ProcurementStockCoverRow.__table__)).mappings()) == covers_before
        assert set(session.scalars(select(AuditEventRow.id))) == audit_before


@pytest.mark.parametrize("action,domain_method", [("reserve", "reserve"), ("release", "release_reserve")])
def test_stock_port_flush_rejects_change_after_domain_mutation(concurrent_env, monkeypatch, action, domain_method):
    _, sessions = concurrent_env
    lot = add_lot(sessions, stock().reserve(Decimal("20")))
    original = getattr(StockLot, domain_method)
    edited = []

    def edit_after_mutation(current, quantity):
        updated = original(current, quantity)
        edited.append(edit_lot_metadata(sessions, lot.id))
        return updated

    monkeypatch.setattr(StockLot, domain_method, edit_after_mutation)

    with pytest.raises(StaleDataError):
        with SqlAlchemyProcurementStockUnitOfWork(sessions) as uow:
            viewed = uow.stock.get(lot.id)
            getattr(uow.stock, action)(lot.id, Decimal("10"), expected_version=viewed.version)
            uow.commit()

    assert len(edited) == 1
    assert get_lot(sessions, lot.id) == edited[0]
