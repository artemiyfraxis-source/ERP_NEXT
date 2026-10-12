"""Черновик генератора ТК на сервере (владелец 11.10.2026): the unfinished generator form of a person survives another
computer or browser. One draft per person and position, saved over a version (409 on a stale one), own only, capped
in size; migration 0102 adds the table and nothing else."""

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.domain import IdentityUser, hash_password
from app.modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork, provision_user, seed_level_matrix
from app.modules.techcards import generator_drafts
from app.modules.techcards.generator_drafts import (
    GeneratorDraftConflict,
    SaveGeneratorDraftCommand,
    save_generator_draft,
)
from app.modules.techcards.generator_drafts_infrastructure import GeneratorDraftRow
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.shared.clock import MOSCOW, moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, create_schema


NOW = datetime(2026, 10, 11, 12, 5, tzinfo=MOSCOW)
ADMIN_ID = UUID("51000000-0000-0000-0000-000000000001")
MASTER_ID = UUID("51000000-0000-0000-0000-000000000002")
OTHER_ID = UUID("51000000-0000-0000-0000-000000000003")
PROJECT, INDEX = "708К.4", "КБ1"
URL = "/api/techcards/generator-draft"
POSITION = {"project": PROJECT, "item_index": INDEX}
FORM = {"S": {"name": "Жгут питания", "wires": [{"name": "МГТФ 0,12", "len": "150"}]}, "manual": {}, "terEdits": {}}
STATE = ("identity_uow_factory", "techcards_uow_factory", "cookie_secure", "identity_clock")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _principal(user_id: UUID, permissions: dict | None = None) -> Principal:
    return Principal(
        actor=Actor(id=user_id, name="Мастер", department="Производство"), email=f"{user_id}@example.test",
        roles=frozenset({"operator"}), permissions={"tcgen": "view"} if permissions is None else permissions,
    )


@pytest.fixture()
def drafts():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        provision_user(session, IdentityUser(
            id=ADMIN_ID, login="admin", email="admin@example.test", name="Admin", department="ИТ", role="admin",
            site=None, active=True, access_level="admin",
        ), hash_password("admin-password"))
        for user_id, login in ((MASTER_ID, "master"), (OTHER_ID, "other")):
            provision_user(session, IdentityUser(
                id=user_id, login=login, email=f"{login}@example.test", name=login, department="Производство",
                role="staff", site=None, active=True, access_level="operator",
            ), hash_password("password-123"))
        seed_level_matrix(session)
        session.commit()
    clock = Clock()
    app.state.identity_uow_factory = lambda: SqlAlchemyIdentityUnitOfWork(sessions)
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.cookie_secure = False
    app.state.identity_clock = clock
    app.dependency_overrides[moscow_now] = clock
    app.dependency_overrides[authenticated_principal] = lambda: _principal(MASTER_ID)
    yield sessions, clock
    app.dependency_overrides.pop(moscow_now, None)
    app.dependency_overrides.pop(authenticated_principal, None)
    for name in STATE:
        if hasattr(app.state, name):
            delattr(app.state, name)
    engine.dispose()


def _as(user_id: UUID, permissions: dict | None = None) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: _principal(user_id, permissions)


def _put(client: TestClient, base_version: int, payload: dict | None = None, fingerprint: str = "12:abc", **position):
    return client.put(URL, params={**POSITION, **position}, json={
        "payload": FORM if payload is None else payload, "build_fingerprint": fingerprint, "base_version": base_version,
    })


def _rows(sessions) -> list[GeneratorDraftRow]:
    with sessions() as session:
        return list(session.scalars(select(GeneratorDraftRow).order_by(GeneratorDraftRow.created_at)))


def test_first_save_creates_the_draft_and_the_next_one_goes_over_its_version(drafts):
    sessions, clock = drafts
    client = TestClient(app)
    assert client.get(URL, params=POSITION).json() == {"draft": None}

    created = _put(client, 0)
    assert created.status_code == 200, created.text
    assert created.json() == {
        "project": PROJECT, "item_index": INDEX, "payload": FORM, "build_fingerprint": "12:abc", "version": 1,
        "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
    }
    assert client.get(URL, params=POSITION).json()["draft"] == created.json()

    clock.now = NOW + timedelta(minutes=3)
    changed = {**FORM, "S": {**FORM["S"], "name": "Жгут питания 2"}}
    saved = _put(client, 1, changed, "14:def")
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and saved.json()["payload"] == changed
    assert saved.json()["created_at"] == NOW.isoformat()
    assert saved.json()["updated_at"] == clock.now.isoformat()
    read = client.get(URL, params=POSITION).json()["draft"]
    assert read == saved.json()
    [row] = _rows(sessions)
    assert (row.user_id, row.version, row.build_fingerprint) == (MASTER_ID, 2, "14:def")
    # A scratch pad: no audit event per save.
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0


def test_a_save_over_a_stale_version_is_refused_and_the_newer_draft_stays(drafts):
    sessions, clock = drafts
    here, there = TestClient(app), TestClient(app)
    assert _put(here, 0).status_code == 200
    clock.now = NOW + timedelta(minutes=7)
    assert _put(there, 1, {"S": {"name": "с другого компьютера"}}).status_code == 200

    stale = _put(here, 1, {"S": {"name": "отсюда"}})
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "message": "Черновик изменён в другом месте в 12:12", "code": "generator_draft_conflict", "version": 2,
        "updated_at": clock.now.isoformat(),
    }
    # A base the server never had (ahead of it) is stale too.
    assert _put(here, 5).status_code == 409
    assert here.get(URL, params=POSITION).json()["draft"]["payload"] == {"S": {"name": "с другого компьютера"}}
    assert [row.version for row in _rows(sessions)] == [2]


def test_a_person_reads_writes_and_deletes_only_their_own_draft(drafts):
    sessions, _clock = drafts
    client = TestClient(app)
    assert _put(client, 0).status_code == 200

    _as(OTHER_ID)
    assert client.get(URL, params=POSITION).json() == {"draft": None}
    # Their first save is their own draft (version 1), not a conflict with the master's.
    own = _put(client, 0, {"S": {"name": "чужое"}})
    assert own.status_code == 200 and own.json()["version"] == 1
    assert client.delete(URL, params=POSITION).json() == {"deleted": True}
    assert client.delete(URL, params=POSITION).json() == {"deleted": False}

    _as(MASTER_ID)
    assert client.get(URL, params=POSITION).json()["draft"]["payload"] == FORM
    assert [row.user_id for row in _rows(sessions)] == [MASTER_ID]


def test_drafts_are_kept_apart_per_position(drafts):
    client = TestClient(app)
    assert _put(client, 0).status_code == 200
    second = _put(client, 0, {"S": {"name": "КБ2"}}, item_index="КБ2")
    assert second.status_code == 200 and second.json()["version"] == 1
    assert client.get(URL, params=POSITION).json()["draft"]["payload"] == FORM
    # Spaces around the position are not a new position.
    assert client.get(URL, params={"project": f" {PROJECT} ", "item_index": "КБ2 "}).json()["draft"]["version"] == 1


def test_delete_removes_the_draft_and_the_next_save_creates_it_again(drafts):
    sessions, _clock = drafts
    client = TestClient(app)
    assert _put(client, 0).status_code == 200
    assert _put(client, 1).status_code == 200
    assert client.delete(URL, params=POSITION).json() == {"deleted": True}
    assert client.get(URL, params=POSITION).json() == {"draft": None}
    assert _rows(sessions) == []
    # Another device still typing over version 2: its save is the newest form, it creates the draft anew.
    again = _put(client, 2)
    assert again.status_code == 200 and again.json()["version"] == 1


def test_a_draft_over_the_size_cap_is_refused_in_russian(drafts):
    sessions, _clock = drafts
    client = TestClient(app)
    big = _put(client, 0, {"S": {"name": "x" * (513 * 1024)}})
    assert big.status_code == 413
    assert big.json()["detail"].startswith("Черновик слишком большой: 513 КБ при пределе 512 КБ.")
    # Cyrillic is counted as the database stores it (escaped): 100 000 letters are ~586 КБ of JSON text.
    cyrillic = _put(client, 0, {"S": {"name": "ж" * 100_000}})
    assert cyrillic.status_code == 413
    assert _rows(sessions) == []
    assert _put(client, 0, {"S": {"name": "ж" * 10_000}}).status_code == 200


def test_the_position_is_required_and_the_body_checked(drafts):
    client = TestClient(app)
    missing = client.get(URL, params={"project": " ", "item_index": INDEX})
    assert missing.status_code == 422 and missing.json()["detail"] == "Укажите проект и КБ черновика."
    assert client.delete(URL, params={"project": PROJECT}).status_code == 422
    assert client.put(URL, params=POSITION, json={"payload": [], "base_version": 0}).status_code == 422
    assert client.put(URL, params=POSITION, json={"payload": {}, "base_version": -1}).status_code == 422


def test_without_the_generator_section_there_is_no_draft(drafts):
    client = TestClient(app)
    _as(MASTER_ID, {"techcards": "edit"})
    assert client.get(URL, params=POSITION).status_code == 403
    assert _put(client, 0).status_code == 403
    assert client.delete(URL, params=POSITION).status_code == 403


def test_an_api_token_has_no_draft(drafts):
    app.dependency_overrides.pop(authenticated_principal, None)  # the real session and token middleware decide
    admin = TestClient(app)
    assert admin.post("/api/auth/login", json={"login": "admin", "password": "admin-password"}).status_code == 200
    csrf = {"X-CSRF-Token": admin.cookies.get("erp_csrf")}
    assert admin.put(URL, params=POSITION, headers=csrf, json={"payload": FORM, "base_version": 0}).status_code == 200
    issued = admin.post(
        "/api/auth/api-tokens", headers=csrf, json={"name": "Claude Code", "scopes": ["tcgen"], "days": 30},
    )
    assert issued.status_code == 201, issued.text
    agent = TestClient(app)
    bearer = {"Authorization": f"Bearer {issued.json()['token']}"}
    assert agent.get(URL, params=POSITION, headers=bearer).status_code == 403
    assert agent.put(URL, params=POSITION, headers=bearer, json={"payload": {}, "base_version": 1}).status_code == 403
    assert agent.delete(URL, params=POSITION, headers=bearer).status_code == 403
    assert admin.get(URL, params=POSITION).json()["draft"]["version"] == 1


def test_a_concurrent_first_save_loses_to_the_one_that_created_the_draft(drafts, monkeypatch):
    sessions, _clock = drafts
    factory = app.state.techcards_uow_factory
    first = SaveGeneratorDraftCommand(
        user_id=MASTER_ID, project=PROJECT, item_index=INDEX, payload={"S": {"name": "первый"}},
        build_fingerprint="", base_version=0, now=NOW,
    )
    save_generator_draft(first, factory)
    # The second save read «no draft yet» before the first one committed: the UNIQUE constraint refuses its insert.
    real_find, calls = generator_drafts._find, []

    def stale_first_read(*args):
        calls.append(args)
        return None if len(calls) == 1 else real_find(*args)

    monkeypatch.setattr(generator_drafts, "_find", stale_first_read)
    with pytest.raises(GeneratorDraftConflict) as conflict:
        save_generator_draft(first, factory)
    assert conflict.value.current.version == 1
    monkeypatch.undo()
    assert [row.payload for row in _rows(sessions)] == [{"S": {"name": "первый"}}]


# ── Миграция 0102 ────────────────────────────────────────────────────────

BEFORE = "0101_production_stage_history"
AFTER = "0102_techcard_generator_drafts"
USER = uuid4()


def _config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "generator-drafts.db"
    command.upgrade(_config(path), BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO identity_users (id, login, email, name, department, role, active, job_title, qualification, "
            "access_level, shop_worker, site_master) VALUES (?, 'master', 'master@example.test', 'Мастер', "
            "'Производство', 'staff', 1, '', '', 'operator', 0, 0)",
            (USER.hex,),
        )
    command.upgrade(_config(path), AFTER)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    yield path, engine
    engine.dispose()


def _draft_row(**overrides) -> GeneratorDraftRow:
    values = dict(id=uuid4(), user_id=USER, project=PROJECT, item_index=INDEX, payload={"S": {}},
                  build_fingerprint="", version=1, created_at=NOW, updated_at=NOW)
    values.update(overrides)
    return GeneratorDraftRow(**values)


def test_migration_creates_the_table_as_the_orm_row_describes_it(migrated):
    path, engine = migrated
    schema = inspect(engine)
    table = GeneratorDraftRow.__table__
    columns = {column["name"]: column for column in schema.get_columns(table.name)}
    assert set(columns) == {column.name for column in table.columns}
    for column in table.columns:
        assert columns[column.name]["nullable"] == column.nullable, column.name
    stored = {check["name"] for check in schema.get_check_constraints(table.name)}
    assert stored == {c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
    [unique] = schema.get_unique_constraints(table.name)
    assert unique["column_names"] == ["user_id", "project", "item_index"]
    [key] = schema.get_foreign_keys(table.name)
    assert (key["referred_table"], key["constrained_columns"], key["options"].get("ondelete")) == (
        "identity_users", ["user_id"], "RESTRICT",
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM identity_users").fetchone() == (1,)
        assert connection.execute(f"SELECT count(*) FROM {table.name}").fetchone() == (0,)


@pytest.mark.parametrize("bad", [
    {"version": 0},
    {"project": ""},
    {"item_index": ""},
    {"payload": {"S": "x" * (1_048_576 + 1)}},
])
def test_constraints_refuse_bad_states(migrated, bad):
    _path, engine = migrated
    with Session(engine) as session:
        session.add(_draft_row(**bad))
        with pytest.raises(IntegrityError):
            session.commit()


def test_one_draft_per_person_and_position_and_a_person_with_a_draft_stays(migrated):
    path, engine = migrated
    with Session(engine) as session:
        session.add(_draft_row())
        session.commit()
        session.add(_draft_row())
        with pytest.raises(IntegrityError):
            session.commit()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM identity_users WHERE id = ?", (USER.hex,))


def test_downgrade_drops_the_table_and_keeps_the_rest(migrated):
    path, engine = migrated
    with Session(engine) as session:
        session.add(_draft_row())
        session.commit()
    command.downgrade(_config(path), BEFORE)
    assert "techcard_generator_drafts" not in set(inspect(engine).get_table_names())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM identity_users").fetchone() == (1,)
