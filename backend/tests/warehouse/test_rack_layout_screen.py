"""«Раскладка» стеллажей с экрана склада (SUPPLYMAP portalStock05PlacesSave)."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.warehouse.infrastructure import SqlAlchemyRackLayoutStore, SqlAlchemyWarehouseUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from app.shared.warehouse_location import existing_storage_location, set_rack_layout_loader
from tests.factories import Actor

NOW = datetime(2026, 9, 29, 12, 0)


@pytest.fixture()
def client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    store = SqlAlchemyRackLayoutStore(sessions)
    app.state.rack_layout_store = store
    app.state.warehouse_uow_factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    set_rack_layout_loader(store.load)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кладовщик", department="Склад"), email="keeper@example.test",
        roles=frozenset({"receiver"}), permissions={"warehouse": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    set_rack_layout_loader(None)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.rack_layout_store
    del app.state.warehouse_uow_factory
    engine.dispose()


def test_default_layout_until_saved_then_the_saved_one_checks_places(client):
    http, _ = client
    assert http.get("/api/stock/rack-layout").json()["layout"] == {"racks": 32, "shelves": 26, "perRack": {}}

    saved = http.put("/api/stock/rack-layout", json={"racks": 5, "shelves": 7, "perRack": {"2": 3, "4": 7}})

    assert saved.status_code == 200, saved.text
    assert saved.json()["message"] == "Стеллажей: 5, полок по умолчанию: 7, особых стеллажей: 1."
    assert saved.json()["layout"] == {"racks": 5, "shelves": 7, "perRack": {"2": 3}}
    assert http.get("/api/stock/rack-layout").json()["layout"]["racks"] == 5
    assert existing_storage_location("5G1") == "5G1"
    with pytest.raises(ValueError, match="Места 6A1 нет"):
        existing_storage_location("6A1")
    with pytest.raises(ValueError, match="Места 2D1 нет"):
        existing_storage_location("2D1")


def test_invalid_layout_is_refused_with_the_supplymap_text(client):
    http, _ = client
    assert http.put("/api/stock/rack-layout", json={"racks": 100, "shelves": 7}).json()["detail"] == "Стеллажей должно быть от 1 до 99."
    assert http.put("/api/stock/rack-layout", json={"racks": 5, "shelves": 7, "perRack": {"9": 3}}).json()["detail"] == "Стеллажа №9 нет (всего 5)."


def test_save_is_journaled(client):
    http, sessions = client
    http.put("/api/stock/rack-layout", json={"racks": 5, "shelves": 7})
    from app.modules.warehouse.journal import warehouse_journal

    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        page = warehouse_journal(uow, "раскладка")
    assert [row.event for row in page.rows] == ["Склад 0.5: раскладка стеллажей — Стеллажей: 5, полок по умолчанию: 7."]
