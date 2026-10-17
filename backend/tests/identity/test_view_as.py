"""«Смотреть как сотрудник» (owner 12.10.2026): an administrator's session reads as another employee — their level,
personal exceptions and identity — while every write is refused centrally, except the exit and the logout."""

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.application import VIEW_AS_TTL
from app.modules.identity.domain import AccessMode, IdentityUser, hash_password
from app.modules.identity.infrastructure import (
    IdentitySessionRow,
    IdentityUserRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_level_matrix,
)
from app.modules.identity.middleware import VIEW_AS_WRITES, VIEW_ONLY_DETAIL
from app.shared.clock import MOSCOW, moscow_now
from app.shared.persistence import AuditEventRow, create_schema
from tests.identity.test_level_gates import _api_routes


NOW = datetime(2026, 10, 12, 10, 0, tzinfo=MOSCOW)
ADMIN_ID = UUID("50000000-0000-0000-0000-000000000001")
OPERATOR_ID = UUID("50000000-0000-0000-0000-000000000002")
ADMIN2_ID = UUID("50000000-0000-0000-0000-000000000003")
GONE_ID = UUID("50000000-0000-0000-0000-000000000004")
STATE = ("identity_uow_factory", "cookie_secure", "identity_clock")
PATH_PARAM = re.compile(r"{([^}:]+)(:[^}]+)?}")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def view_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    people = (
        (ADMIN_ID, "admin", "admin", True, "Админ Главный"),
        (OPERATOR_ID, "operator", "operator", True, "Иванов Пётр"),
        (ADMIN2_ID, "admin2", "admin", True, "Админ Второй"),
        (GONE_ID, "gone", "operator", False, "Уволенный Сидор"),
    )
    with sessions() as session:
        for user_id, login, level, active, name in people:
            provision_user(
                session,
                IdentityUser(
                    id=user_id, login=login, email=f"{login}@example.test", name=name, department="Цех",
                    role="admin" if level == "admin" else "installer", site="ЦАР", active=active, access_level=level,
                ),
                hash_password(f"{login}-password"),
                # The operator's personal exception: the warehouse on edit over the level's view.
                level_access={"warehouse": AccessMode.EDIT} if login == "operator" else None,
            )
        seed_level_matrix(session)
        session.commit()
    clock = Clock()
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
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
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/api/auth/login", json={"login": login, "password": f"{login}-password"}).status_code == 200
    return client


def _csrf(client: TestClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("erp_csrf")}


def _view(client: TestClient, user_id: UUID):
    return client.post("/api/auth/view-as", json={"user_id": str(user_id)}, headers=_csrf(client))


def _events(sessions, event_type: str) -> list[AuditEventRow]:
    with sessions() as session:
        return list(session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == event_type).order_by(AuditEventRow.occurred_at)
        ))


def test_viewing_reads_with_the_employees_level_exceptions_and_identity(view_api):
    sessions, _ = view_api
    own = _login("operator").get("/api/auth/me").json()
    admin = _login("admin")

    started = _view(admin, OPERATOR_ID)

    assert started.status_code == 200, started.text
    body = started.json()
    assert body["user"] == own["user"]
    assert body["permissions"] == own["permissions"]
    assert body["permissions"]["warehouse"] == "edit"  # the personal exception, not just the level row
    assert (body["access_level"], body["can_admin_mode"], body["admin_mode"]) == ("operator", False, False)
    assert body["viewing"]["viewer"] == {"id": str(ADMIN_ID), "name": "Админ Главный"}
    assert datetime.fromisoformat(body["viewing"]["until"]) == NOW + VIEW_AS_TTL
    me = admin.get("/api/auth/me").json()
    assert (me["user"]["id"], me["viewing"]["viewer"]["id"]) == (str(OPERATOR_ID), str(ADMIN_ID))
    # Reads are the employee's: their own profile, and the administrator's tools closed.
    assert admin.get("/api/auth/profile").json()["login"] == "operator"
    assert admin.get("/api/auth/staff").status_code == 403
    assert admin.get("/api/auth/access").status_code == 403
    # The audit: who, whom, when.
    [row] = _events(sessions, "identity.view_as_on")
    assert (row.actor_id, row.aggregate_id) == (ADMIN_ID, OPERATOR_ID)
    assert row.details == {"viewed_id": str(OPERATOR_ID), "viewed": "Иванов Пётр"}


def test_every_write_route_is_refused_while_viewing_except_the_exit_and_logout(view_api):
    admin = _login("admin")
    assert _view(admin, OPERATOR_ID).status_code == 200
    headers = _csrf(admin)
    checked = 0
    for route, _ in _api_routes(app.routes):
        for method in sorted(route.methods - {"GET", "HEAD", "OPTIONS"}):
            if (method, route.path) in VIEW_AS_WRITES:
                continue
            url = PATH_PARAM.sub("00000000-0000-0000-0000-000000000001", route.path)
            response = admin.request(method, url, headers=headers, json={})
            assert response.status_code == 403, (method, route.path, response.status_code)
            assert response.json() == {"detail": VIEW_ONLY_DETAIL}, (method, route.path)
            checked += 1
    assert checked > 200
    assert VIEW_AS_WRITES == {("DELETE", "/api/auth/view-as"), ("POST", "/api/auth/logout")}
    # Still viewing: nothing above slipped through and ended it.
    assert admin.get("/api/auth/me").json()["user"]["id"] == str(OPERATOR_ID)


def test_exit_returns_the_own_principal_and_writes_work_again(view_api):
    sessions, clock = view_api
    admin = _login("admin")
    assert _view(admin, OPERATOR_ID).status_code == 200
    clock.now = NOW + timedelta(minutes=5)

    back = admin.delete("/api/auth/view-as", headers=_csrf(admin))

    assert back.status_code == 200, back.text
    assert (back.json()["user"]["id"], back.json()["viewing"]) == (str(ADMIN_ID), None)
    assert admin.get("/api/auth/me").json()["viewing"] is None
    assert admin.get("/api/auth/staff").status_code == 200
    assert _view(admin, ADMIN2_ID).status_code == 200  # a write again (a new view)
    ended = _events(sessions, "identity.view_as_off")
    assert [(row.actor_id, row.aggregate_id, row.details["reason"]) for row in ended] == [
        (ADMIN_ID, OPERATOR_ID, "вернулся к себе"),
    ]
    # Exit without a view is harmless and writes no journal row.
    admin.delete("/api/auth/view-as", headers=_csrf(admin))
    assert admin.delete("/api/auth/view-as", headers=_csrf(admin)).json()["viewing"] is None
    assert len(_events(sessions, "identity.view_as_off")) == 2


def test_only_an_administrators_session_starts_a_view(view_api):
    operator = _login("operator")
    assert _view(operator, ADMIN_ID).status_code == 403
    assert operator.get("/api/auth/me").json()["viewing"] is None

    admin = _login("admin")
    # An API token never starts it (no session, no full access), even one the administrator issued.
    issued = admin.post(
        "/api/auth/api-tokens", json={"name": "Скрипт", "scopes": ["refs:write"], "days": 30}, headers=_csrf(admin),
    ).json()
    service = TestClient(app, raise_server_exceptions=False)
    bearer = {"Authorization": f"Bearer {issued['token']}"}
    assert service.post("/api/auth/view-as", json={"user_id": str(OPERATOR_ID)}, headers=bearer).status_code == 403
    assert service.delete("/api/auth/view-as", headers=bearer).status_code == 403


def test_a_view_needs_another_working_employee(view_api):
    admin = _login("admin")
    assert _view(admin, ADMIN_ID).status_code == 422  # oneself
    assert _view(admin, GONE_ID).status_code == 422  # not working any more
    assert _view(admin, UUID(int=7)).status_code == 404
    # Another administrator may be viewed (read-only all the same).
    assert _view(admin, ADMIN2_ID).json()["user"]["id"] == str(ADMIN2_ID)
    # A second view starts only after the exit: the start is a write.
    second = _view(admin, OPERATOR_ID)
    assert (second.status_code, second.json()["detail"]) == (403, VIEW_ONLY_DETAIL)


def test_the_view_ends_by_itself_after_two_hours(view_api):
    sessions, clock = view_api
    admin = _login("admin")
    assert _view(admin, OPERATOR_ID).status_code == 200
    clock.now = NOW + VIEW_AS_TTL - timedelta(seconds=1)
    assert admin.get("/api/auth/me").json()["viewing"] is not None

    clock.now = NOW + VIEW_AS_TTL
    me = admin.get("/api/auth/me").json()

    assert (me["user"]["id"], me["viewing"]) == (str(ADMIN_ID), None)
    [row] = _events(sessions, "identity.view_as_off")
    assert (row.actor_id, row.details["reason"]) == (ADMIN_ID, "время просмотра истекло")
    with sessions() as session:
        stored = session.scalar(select(IdentitySessionRow).where(IdentitySessionRow.user_id == ADMIN_ID))
        assert (stored.view_as_user_id, stored.view_as_until) == (None, None)


def test_the_view_ends_when_the_administrator_loses_full_access(view_api):
    sessions, _ = view_api
    admin = _login("admin")
    assert _view(admin, OPERATOR_ID).status_code == 200
    with sessions() as session:
        session.execute(update(IdentityUserRow).where(IdentityUserRow.id == ADMIN_ID).values(access_level="operator"))
        session.commit()

    me = admin.get("/api/auth/me").json()

    assert (me["user"]["id"], me["viewing"], me["access_level"]) == (str(ADMIN_ID), None, "operator")
    [row] = _events(sessions, "identity.view_as_off")
    assert row.details["reason"] == "нет прав администратора"


def test_logout_while_viewing_ends_the_view_and_journals_the_real_person(view_api):
    sessions, _ = view_api
    admin = _login("admin")
    assert _view(admin, OPERATOR_ID).status_code == 200

    assert admin.post("/api/auth/logout", headers=_csrf(admin)).status_code == 204

    assert admin.get("/api/auth/me").status_code == 401
    [ended] = _events(sessions, "identity.view_as_off")
    [logout] = _events(sessions, "identity.logout")
    assert (ended.actor_id, ended.aggregate_id, ended.details["reason"]) == (ADMIN_ID, OPERATOR_ID, "выход из ERP")
    assert logout.actor_id == ADMIN_ID


def test_deleting_the_viewed_employee_ends_the_view(view_api):
    sessions, _ = view_api
    admin = _login("admin")
    assert _view(admin, OPERATOR_ID).status_code == 200
    with sessions() as session:
        session.execute(
            update(IdentitySessionRow)
            .where(IdentitySessionRow.user_id == ADMIN_ID)
            .values(view_as_user_id=None)  # what ON DELETE SET NULL leaves on PostgreSQL
        )
        session.commit()

    assert admin.get("/api/auth/me").json()["viewing"] is None
    [row] = _events(sessions, "identity.view_as_off")
    assert row.details["reason"] == "сотрудник удалён"


# ── Migration 0109 ───────────────────────────────────────────────────────────────────────────────────────

AFTER = "0109_session_view_as"


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


BEFORE = ScriptDirectory.from_config(_config(Path("unused.db"))).get_revision(AFTER).down_revision


def _columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(identity_sessions)")}


def test_migration_adds_the_view_columns_with_their_checks_and_downgrade_drops_them(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    command.upgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        for user_id, login in (("u1", "one"), ("u2", "two")):
            connection.execute(
                "INSERT INTO identity_users (id, login, email, name, department, role, active, access_level)"
                " VALUES (?, ?, ?, ?, 'Цех', 'installer', 1, 'operator')",
                (user_id, login, f"{login}@example.test", login),
            )
        connection.execute(
            "INSERT INTO identity_sessions (id, user_id, token_hash, csrf_hash, created_at, last_seen_at, expires_at,"
            " absolute_expires_at) VALUES ('s1', 'u1', 't1', 'c1', '2026-10-12 09:00:00', '2026-10-12 09:00:00',"
            " '2026-10-26 09:00:00', '2026-11-11 09:00:00')"
        )

    command.upgrade(_config(database), AFTER)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT view_as_user_id, view_as_until FROM identity_sessions").fetchall() == [
            (None, None),
        ]
        connection.execute(
            "UPDATE identity_sessions SET view_as_user_id = 'u2', view_as_until = '2026-10-12 11:00:00'"
        )
        with pytest.raises(sqlite3.IntegrityError):  # a view always has an end
            connection.execute("UPDATE identity_sessions SET view_as_until = NULL")
        with pytest.raises(sqlite3.IntegrityError):  # never one's own user
            connection.execute("UPDATE identity_sessions SET view_as_user_id = 'u1'")

    command.downgrade(_config(database), BEFORE)
    with sqlite3.connect(database) as connection:
        assert not {"view_as_user_id", "view_as_until"} & _columns(connection)
        assert connection.execute("SELECT COUNT(*) FROM identity_sessions").fetchone() == (1,)
