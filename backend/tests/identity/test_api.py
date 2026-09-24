from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import AccessMode, IdentityUser, hash_password
from app.modules.identity.infrastructure import (
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
)
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.requests.api import moscow_now
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.shared.persistence import create_schema
from app.shared.clock import MOSCOW


NOW = datetime(2026, 9, 15, 10, 0)


@pytest.fixture()
def identity_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        provision_user(
            session,
            IdentityUser(
                id=UUID("10000000-0000-0000-0000-000000000001"),
                login="petrov",
                email="petrov@example.test",
                name="Петров",
                department="Закупки",
                role="buyer",
                site=None,
                active=True,
            ),
            hash_password("correct-password"),
            role_access={
                "requests": AccessMode.EDIT,
                "procurement": AccessMode.EDIT,
            },
        )
        session.commit()
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.cookie_secure = False
    app.state.identity_clock = lambda: NOW
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app)
    app.dependency_overrides.pop(moscow_now, None)
    for name in (
        "identity_uow_factory",
        "uow_factory",
        "procurement_uow_factory",
        "cookie_secure",
        "identity_clock",
    ):
        delattr(app.state, name)
    engine.dispose()


def request_payload() -> dict:
    return {
        "requests": [
            {
                "purpose": "RND",
                "delivery_point": "г. Москва, ул. 6-я Радиальная, д. 17",
                "name": "Разъём",
                "item_type": "Комплектующие",
                "quantity": str(Decimal("2")),
                "unit": "шт",
                "quality_document": "Нет",
                "procurement_deadline": "2026-09-18",
                "planned_date": "2026-09-25",
            }
        ]
    }


def test_login_cookie_resolves_principal_and_logout_revokes_it(identity_api):
    client = identity_api
    login = client.post(
        "/api/auth/login",
        json={"login": "petrov", "password": "correct-password"},
    )

    assert login.status_code == 200, login.text
    assert login.json()["user"]["id"] == "10000000-0000-0000-0000-000000000001"
    assert "session_token" not in login.json()
    assert "HttpOnly" in login.headers.get("set-cookie", "")
    assert client.get("/api/auth/me").status_code == 200

    csrf = client.cookies.get("erp_csrf")
    logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_cookie_authenticated_write_requires_matching_csrf(identity_api):
    client = identity_api
    assert client.post(
        "/api/auth/login",
        json={"login": "petrov", "password": "correct-password"},
    ).status_code == 200

    denied = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "csrf:denied"},
        json=request_payload(),
    )
    csrf = client.cookies.get("erp_csrf")
    allowed = client.post(
        "/api/requests",
        headers={"Idempotency-Key": "csrf:allowed", "X-CSRF-Token": csrf},
        json=request_payload(),
    )

    assert denied.status_code == 403
    assert allowed.status_code == 201, allowed.text


def test_login_error_is_generic(identity_api):
    response = identity_api.post(
        "/api/auth/login",
        json={"login": "unknown", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Неверный логин или пароль"


def test_persisted_sqlite_session_accepts_aware_server_clock(identity_api):
    aware_now = datetime(2026, 9, 15, 10, 0, tzinfo=MOSCOW)
    app.state.identity_clock = lambda: aware_now
    app.dependency_overrides[moscow_now] = lambda: aware_now

    assert identity_api.post(
        "/api/auth/login",
        json={"login": "petrov", "password": "correct-password"},
    ).status_code == 200
    csrf = identity_api.cookies.get("erp_csrf")
    response = identity_api.post(
        "/api/requests",
        headers={"Idempotency-Key": "aware-clock", "X-CSRF-Token": csrf},
        json=request_payload(),
    )

    assert response.status_code == 201, response.text


def test_secure_mode_uses_host_only_cookie_prefixes(identity_api):
    app.state.cookie_secure = True

    response = identity_api.post(
        "/api/auth/login",
        json={"login": "petrov", "password": "correct-password"},
    )

    cookies = response.headers.get_list("set-cookie")
    assert any(cookie.startswith("__Host-erp_session=") for cookie in cookies)
    assert any(cookie.startswith("__Host-erp_csrf=") for cookie in cookies)
    assert all("Secure" in cookie for cookie in cookies)
    assert all("Domain=" not in cookie for cookie in cookies)


def test_duplicate_security_cookie_is_rejected(identity_api):
    client = identity_api
    assert client.post(
        "/api/auth/login",
        json={"login": "petrov", "password": "correct-password"},
    ).status_code == 200
    token = client.cookies.get("erp_session")

    response = client.get(
        "/api/auth/me",
        headers={"Cookie": f"erp_session={token}; erp_session=attacker"},
    )

    assert response.status_code == 401
