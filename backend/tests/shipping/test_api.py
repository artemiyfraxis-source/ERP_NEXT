from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor


@pytest.fixture()
def shipping_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    actor = Actor(id=UUID("10000000-0000-0000-0000-000000000001"), name="Администратор", department="ERP")
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=actor, email="admin@example.test", roles=frozenset({"admin"}), permissions={"shipping": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 9, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.shipping_uow_factory
    engine.dispose()


def test_finished_good_partial_shipment_and_close_gates(shipping_api):
    client = shipping_api
    created = client.post("/api/shipping/goods", json={
        "project": "591К.3", "item_index": "КБ1", "customer": "Заказчик",
        "name": "Жгут", "ordered": "40", "unit": "шт",
    })
    good = created.json()["good"]
    accepted = client.post(f"/api/shipping/goods/{good['id']}/accept", json={"version": good["version"], "quantity": "6"})
    good = accepted.json()["good"]
    made = client.post("/api/shipping/shipments", json={
        "receiver": "Получатель", "shipping_point": "Москва", "terms": "ДОСТАВКА ТК",
        "upd_type": "ЭДО", "lines": [{"good_id": good["id"], "quantity": "10"}],
    })
    shipment = made.json()["shipment"]
    picked = client.post(f"/api/shipping/shipments/{shipment['id']}/pick", json={"version": shipment["version"]})
    shipment = picked.json()["shipment"]
    packed = client.post(f"/api/shipping/shipments/{shipment['id']}/pack", json={"version": shipment["version"]})
    shipment = packed.json()["shipment"]
    shipped = client.post(f"/api/shipping/shipments/{shipment['id']}/ship", json={"version": shipment["version"]})
    shipment = shipped.json()["shipment"]
    rejected = client.post(f"/api/shipping/shipments/{shipment['id']}/close", json={"version": shipment["version"]})
    documented = client.put(f"/api/shipping/shipments/{shipment['id']}/documents", json={
        "version": shipment["version"], "upd_type": "ЭДО", "upd_number": "123",
        "upd_url": "https://files.example/upd.pdf", "track_number": "TRACK-1",
    })
    shipment = documented.json()["shipment"]
    closed = client.post(f"/api/shipping/shipments/{shipment['id']}/close", json={"version": shipment["version"]})

    assert created.status_code == 200
    assert good["grn"] == "ГП.26.0000001"
    assert shipment["lines"][0]["requested"] == "10"
    assert shipment["lines"][0]["picked"] == "6"
    assert shipment["lines"][0]["shortage"] == "4"
    assert rejected.status_code == 422
    assert closed.status_code == 200
    assert closed.json()["shipment"]["status"] == "Закрыта"


def test_manager_cannot_execute_warehouse_step(shipping_api):
    client = shipping_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=UUID("10000000-0000-0000-0000-000000000002"), name="Менеджер", department="Продажи"),
        email="pm@example.test", roles=frozenset({"pm"}), permissions={"shipping": "edit"},
    )
    response = client.post("/api/shipping/shipments/10000000-0000-0000-0000-000000000099/pick", json={"version": 1})
    assert response.status_code == 403
