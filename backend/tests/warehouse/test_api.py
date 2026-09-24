from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor


@pytest.fixture()
def warehouse_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.state.warehouse_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кладовщик", department="Склад"),
        email="warehouse@example.test",
        roles=frozenset({"receiver"}),
        permissions={"warehouse": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 21, 12, 0)
    lot = StockLot.create(
        warehouse="ЦАР",
        grn="FRX.26.0000001",
        package="",
        name="Разъём",
        item_type="Комплектующие",
        purpose="FRX.001",
        unit="шт",
        quantity=Decimal("10"),
        storage_location="1F4",
        source_receiving_ids=("20000000-0000-0000-0000-000000000001",),
    )
    with factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    yield TestClient(app), lot
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.warehouse_uow_factory
    engine.dispose()


def test_warehouse_requires_receiver_role(warehouse_api):
    client, _ = warehouse_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Закупщик", department="Закупки"),
        email="buyer@example.test",
        roles=frozenset({"buyer"}),
        permissions={"warehouse": "edit"},
    )

    assert client.get("/api/stock").status_code == 403


def test_warehouse_has_stable_cursor_pages(warehouse_api):
    client, first_lot = warehouse_api
    factory = app.state.warehouse_uow_factory
    second_lot = StockLot.create(
        warehouse="ЖУК",
        grn="PPM.26.0000002",
        package="",
        name="Корпус",
        item_type="Материалы",
        purpose="Производство",
        unit="шт",
        quantity=Decimal("5"),
        storage_location="2A1",
        source_receiving_ids=("20000000-0000-0000-0000-000000000002",),
    )
    with factory() as uow:
        uow.warehouse.add(second_lot)
        uow.commit()

    first_page = client.get("/api/stock?limit=1")
    cursor = first_page.json()["next_cursor"]
    second_page = client.get(f"/api/stock?limit=1&after={cursor}")

    assert first_page.status_code == 200
    assert cursor is not None
    assert second_page.status_code == 200
    assert second_page.json()["next_cursor"] is None
    assert {
        first_page.json()["lots"][0]["id"],
        second_page.json()["lots"][0]["id"],
    } == {str(first_lot.id), str(second_lot.id)}


def test_lot_can_move_and_writeoff_but_stale_update_is_rejected(warehouse_api):
    client, lot = warehouse_api
    moved = client.post(
        f"/api/stock/batches/{lot.id}/move",
        json={"version": lot.version, "storage_location": "1F5"},
    )
    written = client.post(
        f"/api/stock/batches/{lot.id}/writeoff",
        json={
            "version": moved.json()["lot"]["version"],
            "quantity": "2",
            "reason": "Выдача в производство",
        },
    )
    stale = client.post(
        f"/api/stock/batches/{lot.id}/writeoff",
        json={
            "version": moved.json()["lot"]["version"],
            "quantity": "1",
            "reason": "Устаревшая форма",
        },
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["lot"]["storage_location"] == "1F5"
    assert written.status_code == 200, written.text
    assert written.json()["lot"]["balance"] == "8"
    assert stale.status_code == 409


def test_receiver_can_edit_article_description_and_series(warehouse_api):
    client, lot = warehouse_api

    response = client.put(
        f"/api/stock/batches/{lot.id}/identity",
        json={
            "version": lot.version,
            "article": "CON-001",
            "name": "Разъём питания",
            "series": "LOT-2409",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["lot"] | {
        "article": "CON-001",
        "name": "Разъём питания",
        "series": "LOT-2409",
    } == response.json()["lot"]


def test_stock_filters_match_the_live_erp_contract(warehouse_api):
    client, first_lot = warehouse_api
    factory = app.state.warehouse_uow_factory
    empty_lot = StockLot.create(
        warehouse="ЖУК",
        grn="PPM.26.0000002",
        package="",
        name="Корпус",
        item_type="Материалы",
        purpose="Производство",
        unit="шт",
        quantity=Decimal("5"),
        storage_location="2A1",
        source_receiving_ids=("20000000-0000-0000-0000-000000000002",),
    ).write_off(Decimal("5"))
    with factory() as uow:
        uow.warehouse.add(empty_lot)
        uow.commit()

    visible = client.get("/api/stock?q=%D1%80%D0%B0%D0%B7%D1%8A%D1%91%D0%BC&warehouse=%D0%A6%D0%90%D0%A0")
    with_zero = client.get("/api/stock?zero=true&warehouse=%D0%96%D0%A3%D0%9A")

    assert visible.status_code == 200
    assert visible.json()["total"] == 1
    assert visible.json()["lots"][0]["id"] == str(first_lot.id)
    assert with_zero.status_code == 200
    assert with_zero.json()["lots"][0]["balance"] == "0"


def test_stock_searches_article_and_series(warehouse_api):
    client, _ = warehouse_api

    response = client.post(
        "/api/stock/batches",
        json={"items": [{
            "warehouse": "ЦАР",
            "article": "GHR-04V-S",
            "series": "LOT-2409",
            "name": "Разъём питания",
            "quantity": "2",
            "storage_location": "2A1",
        }]},
    )

    assert response.status_code == 200, response.text
    by_article = client.get("/api/stock?q=GHR-04V-S")
    by_series = client.get("/api/stock?q=LOT-2409")
    assert by_article.json()["lots"][0]["article"] == "GHR-04V-S"
    assert by_series.json()["lots"][0]["series"] == "LOT-2409"


def test_stock_article_info_infers_current_supplymap_coax_codes(warehouse_api):
    client, _ = warehouse_api

    response = client.get("/api/stock/article-info?q=N-C58P")

    assert response.status_code == 200, response.text
    assert response.json()["info"] == {
        "series": "N",
        "name": "N-male RG-58",
        "item_type": "Коаксиальный соединитель",
        "source": "разбор кода",
    }


def test_receiver_can_add_manually_counted_stock(warehouse_api):
    client, _ = warehouse_api

    response = client.post(
        "/api/stock/batches",
        json={
            "items": [{
                "warehouse": "ЖУК",
                "article": "CASE-01",
                "series": "BATCH-7",
                "name": "Корпус",
                "item_type": "Материалы",
                "purpose": "Склад",
                "unit": "шт",
                "quantity": "4",
                "storage_location": "2A1",
            }],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["lots"][0]["grn"] == "OLD.26.0000001"
    assert response.json()["lots"][0]["balance"] == "4"
    assert response.json()["lots"][0]["article"] == "CASE-01"
    assert response.json()["lots"][0]["series"] == "BATCH-7"


def test_manual_stock_rejects_whitespace_article_even_with_a_name(warehouse_api):
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{
        "warehouse": "ЦАР", "article": "   ", "name": "Разъём",
        "quantity": "1", "storage_location": "2A1",
    }]})

    assert response.status_code == 422


def test_receiver_moves_a_whole_location_and_preserves_each_slot(warehouse_api):
    client, first = warehouse_api
    factory = app.state.warehouse_uow_factory
    first = first.move("18C3A")
    second = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000002", package="", name="Контакт",
        item_type="Комплектующие", purpose="FRX.001", unit="шт",
        quantity=Decimal("5"), storage_location="18C3B",
        source_receiving_ids=("20000000-0000-0000-0000-000000000002",),
    )
    with factory() as uow:
        uow.warehouse.add(first)
        uow.warehouse.add(second)
        uow.commit()

    response = client.post("/api/stock/locations/move", json={
        "warehouse": "ЦАР", "source_location": "18C3", "target_location": "20B5",
    })

    assert response.status_code == 200, response.text
    assert [item["storage_location"] for item in response.json()["lots"]] == ["20B5A", "20B5B"]
