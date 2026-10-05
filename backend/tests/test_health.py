import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.main import app


@pytest.fixture
def swap_engine():
    missing = object()
    original = getattr(app.state, "database_engine", missing)

    def install(engine):
        app.state.database_engine = engine

    yield install
    if original is missing:
        del app.state.database_engine
    else:
        app.state.database_engine = original


def _sqlite_engine():
    return create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


def test_plain_health_is_unchanged():
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "erp-next"}


def test_health_db_returns_revision(swap_engine):
    engine = _sqlite_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('abc123')"))
    swap_engine(engine)

    response = TestClient(app).get("/api/health?db=1")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "revision": "abc123"}


def test_health_db_without_alembic_table_is_503(swap_engine):
    swap_engine(_sqlite_engine())

    response = TestClient(app).get("/api/health?db=1")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert "alembic" not in body["error"].lower()


def test_health_db_unreachable_is_503(swap_engine):
    swap_engine(create_engine("sqlite:////nonexistent-dir/x/erp.db"))

    response = TestClient(app).get("/api/health?db=1")

    assert response.status_code == 503
    assert response.json()["ok"] is False


def test_health_db_without_configured_engine_is_503(swap_engine):
    swap_engine(None)

    response = TestClient(app).get("/api/health?db=1")

    assert response.status_code == 503
