from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.requests.api import moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor


client = TestClient(app)


@pytest.fixture(autouse=True)
def fixed_server_date():
    app.dependency_overrides[moscow_today] = lambda: date(2026, 9, 15)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Иванов", department="РНД"),
        email="ivanov@example.test",
        roles=frozenset({"employee"}),
        permissions={"requests": "view"},
    )
    yield
    app.dependency_overrides.pop(moscow_today, None)
    app.dependency_overrides.pop(authenticated_principal, None)


def valid_payload() -> dict:
    return {
        "purpose": "RND",
        "project_reference": "591К.3",
        "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
        "name": "Разъём SMA",
        "item_type": "Комплектующие",
        "quantity": "2",
        "unit": "шт",
        "category": "ОП",
        "quality_document": "Нет",
        "procurement_deadline": "2026-09-18",
        "planned_date": "2026-09-25",
        "price_with_vat": "1000.50",
    }


def test_schema_exposes_supplymap_required_fields_and_fixed_purposes():
    response = client.get("/api/requests/schema")

    assert response.status_code == 200
    body = response.json()
    assert body["required"] == [
        "purpose",
        "delivery_point",
        "name",
        "item_type",
        "quantity",
        "unit",
        "quality_document",
        "procurement_deadline",
        "planned_date",
    ]
    assert "RND" in body["purposes"]
    assert body["project_reference"]["required"] is False


def test_validate_returns_normalized_derived_values_without_creating_data():
    response = client.post(
        "/api/requests/validate",
        json={"request": valid_payload()},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "valid": True,
        "normalized": {
            **valid_payload(),
            "quality_document": "Да",
            "category": "ОП",
        },
        "derived": {"priority": "Очень срочно", "line_sum": "2001.00", "task_type": "Закупка"},
    }


def test_validate_rejects_unknown_purpose():
    payload = valid_payload()
    payload["purpose"] = "591К.3"

    response = client.post("/api/requests/validate", json={"request": payload})

    assert response.status_code == 422
    assert "НАЗНАЧЕНИЕ" in response.json()["detail"]


def test_validate_rejects_delivery_point_outside_published_reference():
    payload = valid_payload()
    payload["delivery_point"] = "ЦАР"

    response = client.post("/api/requests/validate", json={"request": payload})

    assert response.status_code == 422
    assert "ТОЧКА ОТГРУЗКИ" in response.json()["detail"]


def test_validate_rejects_unbounded_decimal_instead_of_returning_500():
    payload = valid_payload()
    payload["price_with_vat"] = "1E+999999999"

    response = client.post("/api/requests/validate", json={"request": payload})

    assert response.status_code == 422
