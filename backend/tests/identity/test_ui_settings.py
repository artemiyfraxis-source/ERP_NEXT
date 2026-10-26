"""Personal screen layouts (owner 26.10.2026: «настройка показываемых столбцов у каждого своя»): each person stores
their own layout per view on the server, reads only their own, and «По умолчанию» drops it; migration 0154."""

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity import ui_settings as store
from app.modules.identity.domain import IdentityUser, hash_password
from app.modules.identity.infrastructure import (
    IdentityUiSettingRow,
    SqlAlchemyIdentityRepository,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
    seed_level_matrix,
)
from app.shared.clock import MOSCOW, moscow_now
from app.shared.persistence import create_schema

ROOT = Path(__file__).parents[2]
REVISION = "0154_user_ui_settings"
NOW = datetime(2026, 10, 26, 10, 0, tzinfo=MOSCOW)
ALICE_ID = UUID("54000000-0000-0000-0000-000000000001")
BOB_ID = UUID("54000000-0000-0000-0000-000000000002")
ADMIN_ID = UUID("54000000-0000-0000-0000-000000000003")
STATE = ("identity_uow_factory", "cookie_secure", "identity_clock")
LAYOUT = {"order": ["type", "grn", "name", "maker"], "hidden": ["balance", "upd_name"]}


@pytest.fixture()
def layouts_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        for user_id, login, level in ((ALICE_ID, "alice", "operator"), (BOB_ID, "bob", "operator"), (ADMIN_ID, "admin", "admin")):
            provision_user(
                session,
                IdentityUser(
                    id=user_id, login=login, email=f"{login}@example.test", name=login.title(), department="Склад",
                    role="admin" if level == "admin" else "installer", site="ЦАР", active=True, access_level=level,
                ),
                hash_password(f"{login}-password"),
            )
        seed_level_matrix(session)
        session.commit()
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.state.cookie_secure = False
    app.state.identity_clock = lambda: NOW
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield sessions
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


def _put(client: TestClient, view: str, value) -> object:
    return client.put(f"/api/auth/ui-settings/{view}", json={"value": value}, headers=_csrf(client))


def test_a_person_saves_reads_and_replaces_their_own_layout(layouts_api):
    alice = _login("alice")
    assert alice.get("/api/auth/ui-settings").json() == {"views": {}}

    saved = _put(alice, "stock.grouped", LAYOUT)
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"view": "stock.grouped", "value": LAYOUT}
    assert _put(alice, "stock.flat", {"order": ["grn"], "hidden": []}).status_code == 200
    replaced = {**LAYOUT, "hidden": []}
    assert _put(alice, "stock.grouped", replaced).status_code == 200

    # Another browser of the same person reads the same layouts.
    assert _login("alice").get("/api/auth/ui-settings").json() == {
        "views": {"stock.flat": {"order": ["grn"], "hidden": []}, "stock.grouped": replaced},
    }
    with layouts_api() as session:
        row = session.get(IdentityUiSettingRow, (ALICE_ID, "stock.grouped"))
        assert row is not None and row.updated_at is not None


def test_layouts_are_per_person_and_nobody_reaches_another(layouts_api):
    alice, bob, admin = _login("alice"), _login("bob"), _login("admin")
    assert _put(alice, "stock.grouped", LAYOUT).status_code == 200
    assert _put(bob, "stock.grouped", {"order": ["name"], "hidden": []}).status_code == 200

    assert bob.get("/api/auth/ui-settings").json()["views"]["stock.grouped"] == {"order": ["name"], "hidden": []}
    assert alice.get("/api/auth/ui-settings").json()["views"]["stock.grouped"] == LAYOUT
    # Not even an administrator sees or changes someone else's: the person is the session's, never a parameter.
    assert admin.get("/api/auth/ui-settings").json() == {"views": {}}
    assert admin.get(f"/api/auth/ui-settings?user_id={ALICE_ID}").json() == {"views": {}}
    assert admin.delete("/api/auth/ui-settings/stock.grouped", headers=_csrf(admin)).status_code == 204
    assert alice.get("/api/auth/ui-settings").json()["views"]["stock.grouped"] == LAYOUT


def test_reset_drops_only_that_view_of_that_person(layouts_api):
    alice, bob = _login("alice"), _login("bob")
    for client in (alice, bob):
        assert _put(client, "stock.grouped", LAYOUT).status_code == 200
    assert _put(alice, "stock.goods", {"hidden": ["project"]}).status_code == 200

    assert alice.delete("/api/auth/ui-settings/stock.grouped", headers=_csrf(alice)).status_code == 204
    assert alice.delete("/api/auth/ui-settings/stock.grouped", headers=_csrf(alice)).status_code == 204  # again: no-op

    assert alice.get("/api/auth/ui-settings").json() == {"views": {"stock.goods": {"hidden": ["project"]}}}
    assert bob.get("/api/auth/ui-settings").json() == {"views": {"stock.grouped": LAYOUT}}


@pytest.mark.parametrize("view", ["Stock", "1stock", "stock grouped", "stock/x", "a" * 65, "склад"])
def test_view_names_follow_the_pattern(layouts_api, view):
    alice = _login("alice")
    response = _put(alice, view, LAYOUT)
    assert response.status_code in {404, 405, 422}, (view, response.status_code)
    assert alice.get("/api/auth/ui-settings").json() == {"views": {}}


def test_value_must_be_an_object_of_bounded_size(layouts_api):
    alice = _login("alice")
    assert _put(alice, "stock.grouped", ["type", "grn"]).status_code == 422
    assert _put(alice, "stock.grouped", "текст").status_code == 422
    big = {"order": ["x" * 200] * 100}
    too_big = _put(alice, "stock.grouped", big)
    assert too_big.status_code == 422
    assert too_big.json()["detail"] == "Настройка вида слишком большая"
    assert alice.get("/api/auth/ui-settings").json() == {"views": {}}


def test_a_person_keeps_a_bounded_number_of_views(layouts_api, monkeypatch):
    monkeypatch.setattr(store, "MAX_VIEWS", 2)
    alice = _login("alice")
    assert _put(alice, "a.one", {}).status_code == 200
    assert _put(alice, "a.two", {}).status_code == 200
    third = _put(alice, "a.three", {})
    assert third.status_code == 422 and third.json()["detail"] == "Слишком много сохранённых видов"
    assert _put(alice, "a.two", {"hidden": []}).status_code == 200  # replacing one is not a new one


def test_signed_out_and_api_tokens_have_no_layouts(layouts_api):
    anonymous = TestClient(app, raise_server_exceptions=False)
    assert anonymous.get("/api/auth/ui-settings").status_code == 401
    admin = _login("admin")
    issued = admin.post("/api/auth/api-tokens", json={"name": "Агент", "scopes": ["tcgen"], "days": 30}, headers=_csrf(admin))
    assert issued.status_code == 201, issued.text
    bearer = {"Authorization": f"Bearer {issued.json()['token']}"}
    service = TestClient(app, raise_server_exceptions=False)
    assert service.get("/api/auth/ui-settings", headers=bearer).status_code in {401, 403}
    assert service.put("/api/auth/ui-settings/stock.grouped", json={"value": LAYOUT}, headers=bearer).status_code in {401, 403}


def test_a_stored_row_that_is_not_an_object_is_left_out(layouts_api):
    with layouts_api() as session:
        session.add(IdentityUiSettingRow(user_id=ALICE_ID, view="stock.flat", value="[1, 2]", updated_at=NOW))
        session.add(IdentityUiSettingRow(user_id=ALICE_ID, view="stock.goods", value="{broken", updated_at=NOW))
        session.commit()
    assert _login("alice").get("/api/auth/ui-settings").json() == {"views": {}}


def test_layouts_go_with_the_person_and_are_not_history(layouts_api):
    assert _put(_login("bob"), "stock.grouped", LAYOUT).status_code == 200
    with layouts_api() as session:
        repository = SqlAlchemyIdentityRepository(session)
        before = repository.user_history(BOB_ID)
        repository.set_ui_setting(BOB_ID, "stock.flat", "{}", NOW)
        session.flush()
        assert repository.user_history(BOB_ID) == before
        repository.delete_user(BOB_ID)
        session.commit()
        assert session.scalars(select(IdentityUiSettingRow).where(IdentityUiSettingRow.user_id == BOB_ID)).all() == []


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_migration_follows_0153_and_creates_a_bounded_table(tmp_path):
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_revision(REVISION).down_revision == "0153_upd_scan_origin"
    assert REVISION in {rev.revision for rev in script.iterate_revisions(script.get_current_head(), "base")}

    path = tmp_path / "erp.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    db = sqlite3.connect(path)
    user = "54000000000000000000000000000009"
    db.execute(
        "INSERT INTO identity_users (id, login, email, name, department, role, active, access_level, shop_worker, site_master)"
        " VALUES (?, 'x', 'x@example.test', 'X', '', 'installer', 1, 'operator', 0, 0)", (user,),
    )
    db.execute("INSERT INTO identity_ui_settings (user_id, view, value, updated_at) VALUES (?, 'stock.flat', '{}', '2026-10-26')", (user,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO identity_ui_settings (user_id, view, value, updated_at) VALUES (?, 'stock.big', ?, '2026-10-26')",
                   (user, "x" * 16385))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO identity_ui_settings (user_id, view, value, updated_at) VALUES (?, '', '{}', '2026-10-26')", (user,))
    with pytest.raises(sqlite3.IntegrityError):  # one row per person and view
        db.execute("INSERT INTO identity_ui_settings (user_id, view, value, updated_at) VALUES (?, 'stock.flat', '{}', '2026-10-26')", (user,))
    db.commit()
    db.close()

    command.downgrade(config, "0153_upd_scan_origin")
    db = sqlite3.connect(path)
    assert db.execute("SELECT name FROM sqlite_master WHERE name = 'identity_ui_settings'").fetchall() == []
    db.close()
    command.upgrade(config, REVISION)
    db = sqlite3.connect(path)
    assert db.execute("SELECT count(*) FROM identity_ui_settings").fetchone() == (0,)
    db.close()
    create_engine(f"sqlite+pysqlite:///{path.as_posix()}").dispose()
