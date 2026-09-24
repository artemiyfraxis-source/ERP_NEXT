import base64
import hashlib
import hmac
import json
from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.modules.legacy_access.api import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor


SECRET = "test-legacy-bridge-secret-with-32-bytes"
NOW = datetime(2026, 9, 21, 10, 0)


class FakeAudit:
    def __init__(self):
        self.items = []

    def add(self, event):
        self.items.append(event)


class FakeUnitOfWork:
    def __init__(self):
        self.audit = FakeAudit()
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def commit(self):
        self.commits += 1


def decode_payload(token: str) -> tuple[dict, str, str]:
    payload, signature = token.split(".")
    padding = "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload + padding)), payload, signature


def test_grant_is_signed_short_lived_and_scoped_to_native_permission():
    uow = FakeUnitOfWork()
    app.state.legacy_bridge_secret = SECRET
    app.state.identity_uow_factory = lambda: uow
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"staff"}),
        permissions={"projects": "view"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    client = TestClient(app)
    try:
        response = client.post("/api/legacy/grant", json={"scope": "projects"})
        denied = client.post("/api/legacy/grant", json={"scope": "calculation"})
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        app.dependency_overrides.pop(moscow_now, None)
        app.state.legacy_bridge_secret = ""
        del app.state.identity_uow_factory

    assert response.status_code == 200
    assert denied.status_code == 403
    claims, payload, signature = decode_payload(response.json()["grant"])
    expected = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    assert hmac.compare_digest(signature, expected)
    assert claims["scope"] == "projects"
    assert claims["mode"] == "view"
    assert claims["exp"] - claims["iat"] == 120
    assert [event.event_type for event in uow.audit.items] == ["legacy.grant_issued"]
    assert uow.audit.items[0].details == {"scope": "projects", "mode": "view"}
    assert uow.commits == 1


def test_grant_fails_closed_without_shared_secret():
    app.state.legacy_bridge_secret = ""
    app.state.identity_uow_factory = lambda: FakeUnitOfWork()
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"staff"}),
        permissions={"projects": "view"},
    )
    client = TestClient(app)
    try:
        response = client.post("/api/legacy/grant", json={"scope": "projects"})
    finally:
        app.dependency_overrides.pop(authenticated_principal, None)
        del app.state.identity_uow_factory

    assert response.status_code == 503
