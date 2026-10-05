"""Токены API (owner 05.10.2026): an administrator issues, lists and revokes named tokens; a `Bearer erp_…` request
acts as a service principal with the token's scopes only, needs no CSRF, and is refused everywhere else."""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import IdentityUser, hash_password
from app.modules.identity.infrastructure import (
    IdentityApiTokenRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_level_matrix,
)
from app.modules.identity.middleware import IdentitySessionMiddleware
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared import http_auth
from app.shared.clock import MOSCOW, moscow_now
from app.shared.http_auth import Principal, require_full_access, require_section
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, create_schema
from tests.identity.test_level_gates import _api_routes, _walk


NOW = datetime(2026, 10, 5, 10, 0, tzinfo=MOSCOW)
ADMIN_ID = UUID("40000000-0000-0000-0000-000000000001")
OPERATOR_ID = UUID("40000000-0000-0000-0000-000000000002")
HARNESS = {"wires": [{"name": "МК 26-13", "length_mm": 150, "qty": 2}], "side_a": {"name": "ZHR-5", "contacts": 3}}
STATE = ("identity_uow_factory", "warehouse_uow_factory", "techcards_uow_factory", "cookie_secure", "identity_clock")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def tokens_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        for user_id, login, level in ((ADMIN_ID, "admin", "admin"), (OPERATOR_ID, "operator", "operator")):
            provision_user(
                session,
                IdentityUser(
                    id=user_id, login=login, email=f"{login}@example.test", name=login.title(), department="ИТ",
                    role="admin" if level == "admin" else "installer", site=None, active=True, access_level=level,
                ),
                hash_password(f"{login}-password"),
            )
        seed_level_matrix(session)
        session.commit()
    clock = Clock()
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.state.warehouse_uow_factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.cookie_secure = False
    app.state.identity_clock = clock
    app.dependency_overrides[moscow_now] = clock
    yield sessions, clock
    app.dependency_overrides.pop(moscow_now, None)
    for name in STATE:
        if hasattr(app.state, name):
            delattr(app.state, name)
    engine.dispose()


def _login(login: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"login": login, "password": f"{login}-password"}).status_code == 200
    return client


def _csrf(client: TestClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("erp_csrf")}


def _issue(admin: TestClient, scopes=("tcgen",), days: int = 30, name: str = "Claude Code") -> dict:
    response = admin.post(
        "/api/auth/api-tokens", json={"name": name, "scopes": list(scopes), "days": days}, headers=_csrf(admin),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def test_admin_issues_lists_and_revokes_a_token_and_only_the_hash_is_stored(tokens_api):
    sessions, _clock = tokens_api
    admin = _login("admin")

    issued = _issue(admin, scopes=("tcgen", "assembly"))
    secret = issued["token"]
    assert secret.startswith("erp_") and len(secret) > 40
    assert issued["scopes"] == ["assembly", "tcgen"] and issued["status"] == "active"
    assert issued["created_by"] == "Admin"
    assert issued["expires_at"].startswith("2026-11-04")

    listed = admin.get("/api/auth/api-tokens").json()
    [row] = listed["tokens"]
    assert row["id"] == issued["id"] and row["name"] == "Claude Code"
    assert "token" not in row and secret not in str(listed)
    assert set(listed["scopes"]) == {"tcgen", "assembly"}

    with sessions() as session:
        stored = session.scalar(select(IdentityApiTokenRow))
        assert stored.token_hash == hashlib.sha256(secret.encode()).hexdigest()
        values = [getattr(stored, column.key) for column in IdentityApiTokenRow.__table__.columns]
        assert all(secret not in str(value) for value in values)

    revoked = admin.post(f"/api/auth/api-tokens/{issued['id']}/revoke", headers=_csrf(admin))
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked" and revoked.json()["revoked_by"] == "Admin"
    again = admin.post(f"/api/auth/api-tokens/{issued['id']}/revoke", headers=_csrf(admin))
    assert again.status_code == 200 and again.json()["revoked_at"] == revoked.json()["revoked_at"]
    missing = admin.post(f"/api/auth/api-tokens/{OPERATOR_ID}/revoke", headers=_csrf(admin))
    assert missing.status_code == 404

    with sessions() as session:
        events = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type.like("identity.api_token_%"))
        ).all()
    assert [event.event_type for event in events] == ["identity.api_token_created", "identity.api_token_revoked"]
    assert events[0].details["scopes"] == "assembly,tcgen" and events[0].actor_id == ADMIN_ID
    assert all(secret not in str(event.details) for event in events)


def test_only_the_administrator_manages_tokens(tokens_api):
    operator = _login("operator")
    assert operator.get("/api/auth/api-tokens").status_code == 403
    response = operator.post(
        "/api/auth/api-tokens", json={"name": "x", "scopes": ["tcgen"]}, headers=_csrf(operator),
    )
    assert response.status_code == 403
    assert TestClient(app).get("/api/auth/api-tokens").status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x", "scopes": ["warehouse"]},
        {"name": "x", "scopes": []},
        {"name": "x", "scopes": ["tcgen"], "days": 366},
        {"name": "x", "scopes": ["tcgen"], "days": 0},
        {"name": "   ", "scopes": ["tcgen"]},
    ],
)
def test_token_input_is_validated(tokens_api, payload):
    admin = _login("admin")
    assert admin.post("/api/auth/api-tokens", json=payload, headers=_csrf(admin)).status_code == 422


def test_bearer_token_calls_its_scope_without_csrf_and_records_last_use(tokens_api):
    sessions, clock = tokens_api
    secret = _issue(_login("admin"))["token"]
    service = TestClient(app)  # no cookies at all

    response = service.post("/api/route/harness", json=HARNESS, headers=_bearer(secret))
    assert response.status_code == 200, response.text
    assert response.json()["steps"]

    with sessions() as session:
        assert session.scalar(select(IdentityApiTokenRow.last_used_at)) is not None
    # The scheme name is case-insensitive.
    assert service.post("/api/route/harness", json=HARNESS, headers={"Authorization": f"bearer {secret}"}).status_code == 200


def test_bearer_token_is_refused_outside_its_scopes(tokens_api):
    secret = _issue(_login("admin"))["token"]
    service = TestClient(app)
    headers = _bearer(secret)
    # Another section, the user's own endpoints, the administrator's tools, other techcards routes.
    assert service.get("/api/stock", headers=headers).status_code == 403
    assert service.get("/api/auth/me", headers=headers).status_code == 403
    assert service.get("/api/auth/api-tokens", headers=headers).status_code == 403
    assert service.post(
        "/api/auth/api-tokens", json={"name": "y", "scopes": ["tcgen"]}, headers=headers,
    ).status_code == 403
    assert service.get("/api/techcards/templates", headers=headers).status_code == 403
    assert service.get("/api/techcards/kitsets", headers=headers).status_code == 403


def test_assembly_token_does_not_open_the_generator(tokens_api):
    secret = _issue(_login("admin"), scopes=("assembly",))["token"]
    assert TestClient(app).post("/api/route/harness", json=HARNESS, headers=_bearer(secret)).status_code == 403


@pytest.mark.parametrize(
    "secret", ["erp_unknown", "not-an-erp-token", "erp_" + "x" * 200, "erp_" + "x" * 42 + "!", "erp_" + "x" * 44, "erp_" + "A" * 43, ""],
)
def test_unknown_or_malformed_token_is_401(tokens_api, secret):
    response = TestClient(app).post("/api/route/harness", json=HARNESS, headers=_bearer(secret))
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    assert secret not in response.text or not secret


def test_expired_token_is_401(tokens_api):
    _sessions, clock = tokens_api
    secret = _issue(_login("admin"), days=1)["token"]
    service = TestClient(app)
    clock.now = NOW + timedelta(hours=23)
    assert service.post("/api/route/harness", json=HARNESS, headers=_bearer(secret)).status_code == 200
    clock.now = NOW + timedelta(days=1)
    assert service.post("/api/route/harness", json=HARNESS, headers=_bearer(secret)).status_code == 401


def test_revoked_token_is_401(tokens_api):
    admin = _login("admin")
    issued = _issue(admin)
    service = TestClient(app)
    assert service.post("/api/route/harness", json=HARNESS, headers=_bearer(issued["token"])).status_code == 200
    assert admin.post(f"/api/auth/api-tokens/{issued['id']}/revoke", headers=_csrf(admin)).status_code == 200
    assert service.post("/api/route/harness", json=HARNESS, headers=_bearer(issued["token"])).status_code == 401


def test_token_stops_when_its_creator_is_deactivated(tokens_api):
    sessions, _clock = tokens_api
    secret = _issue(_login("admin"))["token"]
    from app.modules.identity.infrastructure import IdentityUserRow

    with sessions() as session:
        session.get(IdentityUserRow, ADMIN_ID).active = False
        session.commit()
    assert TestClient(app).post("/api/route/harness", json=HARNESS, headers=_bearer(secret)).status_code == 401


def test_cookie_write_without_csrf_is_still_refused(tokens_api):
    admin = _login("admin")
    response = admin.post("/api/auth/api-tokens", json={"name": "x", "scopes": ["tcgen"]})
    assert response.status_code == 403
    assert response.json()["detail"] == "Проверка CSRF не пройдена"


def test_bearer_wins_over_cookies_and_never_carries_the_session_rights(tokens_api):
    admin = _login("admin")
    secret = _issue(admin)["token"]
    # The admin's cookies ride along, but the request is the token's: no CSRF needed, no admin rights.
    assert admin.post("/api/route/harness", json=HARNESS, headers=_bearer(secret)).status_code == 200
    assert admin.get("/api/auth/api-tokens", headers=_bearer(secret)).status_code == 403


# ── The gate contract with a service principal (no HTTP) ─────────────────────────────────────────────


def _service(*scopes: str) -> Principal:
    return Principal(
        actor=Actor(id=UUID(int=7), name="Claude Code", department="Токен API"), email="", roles=frozenset(),
        permissions={}, service=True, scopes=frozenset(scopes),
    )


def test_gates_admit_a_service_principal_only_with_a_named_scope():
    from fastapi import HTTPException

    def passes(dependency, principal) -> bool:
        try:
            dependency(principal)
        except HTTPException as error:
            assert error.status_code == 403
            return False
        return True

    assert passes(http_auth.tcgen_principal, _service("tcgen"))
    assert not passes(http_auth.tcgen_principal, _service("assembly"))
    assert not passes(http_auth.techcards_view_principal, _service("tcgen"))
    assert not passes(http_auth.warehouse_view_principal, _service("tcgen", "assembly"))
    assert not passes(require_full_access(), _service("tcgen", "assembly"))
    assert passes(require_section("tcgen", "edit", scopes=("assembly",)), _service("assembly"))


def test_a_route_accepting_a_scope_admits_the_token_and_a_plain_route_does_not(tokens_api):
    """A test app with the real middleware: one route names `assembly`, one takes `authenticated_principal` directly."""
    secret = _issue(_login("admin"), scopes=("assembly",))["token"]
    probe = FastAPI()
    probe.add_middleware(IdentitySessionMiddleware)
    probe.state.identity_uow_factory = app.state.identity_uow_factory
    probe.state.identity_clock = app.state.identity_clock
    probe.state.cookie_secure = False
    assembly = require_section("tcgen", "edit", scopes=("assembly",))

    @probe.post("/assembly")
    def accepts(principal: Annotated[Principal, Depends(assembly)]) -> dict:
        return {"name": principal.actor.name, "scopes": sorted(principal.scopes), "service": principal.service}

    @probe.post("/plain")
    def plain(principal: Annotated[Principal, Depends(http_auth.authenticated_principal)]) -> dict:
        return {"name": principal.actor.name}

    @probe.post("/both")
    def both(
        _gate: Annotated[Principal, Depends(assembly)],
        principal: Annotated[Principal, Depends(http_auth.authenticated_principal)],
    ) -> dict:
        return {"name": principal.actor.name}

    client = TestClient(probe)
    accepted = client.post("/assembly", headers=_bearer(secret))
    assert accepted.status_code == 200
    assert accepted.json() == {"name": "API: Claude Code", "scopes": ["assembly"], "service": True}
    assert client.post("/plain", headers=_bearer(secret)).status_code == 403
    # The acceptance belongs to the scoped gate: a direct `authenticated_principal` on the same route still refuses.
    assert client.post("/both", headers=_bearer(secret)).status_code == 403


def test_malformed_bearer_is_refused_before_a_unit_of_work_opens(tokens_api):
    opened = []
    factory = app.state.identity_uow_factory
    app.state.identity_uow_factory = lambda: opened.append(1) or factory()
    for secret in ("erp_short", "erp_" + "x" * 44, "erp_" + "x" * 42 + "=", "Erp_" + "x" * 43):
        assert TestClient(app).post("/api/route/harness", json=HARNESS, headers=_bearer(secret)).status_code == 401
    assert opened == []
    assert TestClient(app).post("/api/route/harness", json=HARNESS, headers=_bearer("erp_" + "x" * 43)).status_code == 401
    assert opened == [1]


def test_a_tcgen_token_never_builds_a_project_techcard(tokens_api):
    """The generator scope reads and routes; an issued card keeps its revision and document."""
    admin = _login("admin")
    secret = _issue(admin)["token"]
    card = admin.post("/api/techcards/projects", headers=_csrf(admin), json={
        "project": "P-700", "item_index": "01", "product_name": "Жгут", "source_calculation": "",
    }).json()
    document = {"operations": [{"position": 1, "code": "10", "connector": "XS1", "name": "Сборка"}]}
    built = admin.put(
        f"/api/techcards/projects/{card['id']}/etk", headers=_csrf(admin),
        json={"version": card["version"], "document": document},
    ).json()
    issued = admin.post(
        f"/api/techcards/projects/{card['id']}/issue", headers=_csrf(admin), json={"version": built["version"]},
    )
    assert issued.status_code == 200, issued.text
    before = admin.get(f"/api/techcards/projects/{card['id']}").json()
    assert before["status"] == issued.json()["status"] != built["status"] and before["revision"] == 1

    response = TestClient(app).post(
        f"/api/techcards/projects/{card['id']}/build", headers=_bearer(secret),
        json={"version": before["version"], "config": {"ops": []}},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Токен API не собирает техкарты проектов."
    after = admin.get(f"/api/techcards/projects/{card['id']}").json()
    assert (after["version"], after["revision"], after["document"]) == (
        before["version"], before["revision"], before["document"],
    )


# ── Contract: over every API route a token passes only the routes whose own gate names its scope ───────

PATH_PARAM = re.compile(r"{([^}:]+)(:[^}]+)?}")
# Routes whose gate names `tcgen` but which refuse a token anyway (`refuse_api_tokens`: a project techcard write).
TOKEN_REFUSED_IN_ENDPOINT = {("POST", "/api/techcards/projects/{card_id}/build")}


def _route_calls(route, inherited) -> list:
    return [*inherited, *_walk(route.dependant)]


def test_a_token_passes_only_routes_whose_own_gate_names_its_scope(tokens_api):
    admin = _login("admin")
    tcgen = _bearer(_issue(admin, scopes=("tcgen",))["token"])
    assembly = _bearer(_issue(admin, scopes=("assembly",))["token"])
    service = TestClient(app, raise_server_exceptions=False)
    accepting, checked = [], 0
    for route, inherited in _api_routes(app.routes):
        calls = _route_calls(route, inherited)
        if http_auth.authenticated_principal not in calls:
            continue  # public: login, PIN login, health
        named = set().union(*(getattr(call, "token_scopes", frozenset()) for call in calls))
        method = sorted(route.methods - {"HEAD"})[0]
        url = PATH_PARAM.sub("00000000-0000-0000-0000-000000000001", route.path)
        for headers, scope in ((tcgen, "tcgen"), (assembly, "assembly")):
            status = service.request(method, url, headers=headers).status_code
            if scope in named and (method, route.path) not in TOKEN_REFUSED_IN_ENDPOINT:
                assert status not in (401, 403), (method, route.path, scope, status)
                accepting.append((method, route.path, scope))
            else:
                assert status in (401, 403), (method, route.path, scope, status)
            checked += 1
    assert checked > 200
    # Only the generator's own routes take a tcgen token; nothing takes `assembly` yet.
    assert sorted(accepting) == [
        ("POST", "/api/route/coax", "tcgen"),
        ("POST", "/api/route/harness", "tcgen"),
        ("POST", "/api/techcards/grapher/import", "tcgen"),
    ]
