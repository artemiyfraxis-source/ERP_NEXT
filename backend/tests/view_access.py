"""«Режим просмотра» (ID-6): helpers that sweep every write route of a section."""

import importlib
import re
from uuid import UUID

from fastapi.routing import APIRoute

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor

READ_METHODS = {"GET", "HEAD", "OPTIONS"}
ANY_ID = UUID("99999999-9999-9999-9999-999999999999")


def as_viewer(role: str, section: str, mode: str = "view") -> Principal:
    principal = Principal(
        actor=Actor(name=f"Просмотр {role}", department="Просмотр"),
        email=f"{role}-viewer@example.test",
        roles=frozenset({role}),
        permissions={section: mode},
    )
    app.dependency_overrides[authenticated_principal] = lambda: principal
    return principal


def write_routes(module: str) -> list[tuple[str, str]]:
    """Every non-read route of `app.modules.<module>.api` (its router carries the full /api/... path)."""
    router = importlib.import_module(f"app.modules.{module}.api").router
    routes = []
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - READ_METHODS):
            routes.append((method, re.sub(r"\{[^}]+\}", str(ANY_ID), route.path)))
    return routes


def assert_every_write_refused(client, module: str) -> None:
    routes = write_routes(module)
    assert routes, f"no write routes in {module}"
    for method, path in routes:
        response = client.request(method, path, json={})
        assert response.status_code == 403, (method, path, response.status_code, response.text)
