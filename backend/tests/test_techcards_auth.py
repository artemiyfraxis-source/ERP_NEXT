from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor


def payload() -> dict:
    return {"wires": [{"name": "провод"}]}


def test_techcard_routes_require_authentication():
    with TestClient(app) as client:
        response = client.post("/api/route/harness", json=payload())

    assert response.status_code == 401


def test_techcard_routes_require_section_permission():
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(
            id=UUID("30000000-0000-0000-0000-000000000002"),
            name="Сотрудник",
            department="Производство",
        ),
        email="staff@example.test",
        roles=frozenset({"staff"}),
        permissions={"requests": "view"},
    )
    try:
        with TestClient(app) as client:
            response = client.post("/api/route/harness", json=payload())
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)

    assert response.status_code == 403
