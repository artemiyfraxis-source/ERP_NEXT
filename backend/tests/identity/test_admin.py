from argparse import Namespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.identity_admin import create_user
from app.modules.identity.domain import LEVEL_SEED, AccessMode
from app.modules.identity.infrastructure import (
    IdentityLevelAccessRow,
    IdentityUserLevelAccessRow,
    IdentityUserRow,
)
from app.shared.persistence import create_schema


@pytest.fixture()
def admin_database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions
    engine.dispose()


def args(login: str, email: str, permissions: list[str], *, level: str | None = "operator", role: str | None = None,
         site: str | None = None) -> Namespace:
    return Namespace(
        login=login,
        email=email,
        name=login,
        department="Производство",
        level=level,
        role=role,
        site=site,
        permission=permissions,
    )


def _row(sessions, login: str) -> IdentityUserRow:
    with sessions() as session:
        return session.scalar(select(IdentityUserRow).where(IdentityUserRow.login == login))


def test_cli_permission_is_a_personal_exception_on_top_of_the_level(monkeypatch, admin_database):
    monkeypatch.setattr("app.identity_admin.getpass.getpass", lambda _prompt: "safe-password")

    create_user(args("first", "first@example.test", []), admin_database)
    create_user(args("second", "second@example.test", ["requests=view", "warehouse=none"]), admin_database)

    with admin_database() as session:
        first, second = list(session.scalars(select(IdentityUserRow).order_by(IdentityUserRow.login)))
        assert (first.login, first.access_level, second.access_level) == ("first", "operator", "operator")
        # The level row is the matrix's: creating a person writes nothing into it.
        assert session.scalars(select(IdentityLevelAccessRow)).first() is None
        exceptions = {
            row.section: row.mode
            for row in session.scalars(select(IdentityUserLevelAccessRow).where(
                IdentityUserLevelAccessRow.user_id == second.id,
            ))
        }
        assert exceptions == {"requests": AccessMode.VIEW.value, "warehouse": AccessMode.NONE.value}


def test_cli_takes_the_level_and_the_legacy_role_still_maps_to_one(monkeypatch, admin_database):
    monkeypatch.setattr("app.identity_admin.getpass.getpass", lambda _prompt: "safe-password")

    create_user(args("boss", "boss@example.test", [], level="superadmin"), admin_database)
    create_user(args("preview", "preview@example.test", [], level=None, role="admin"), admin_database)
    create_user(args("master", "master@example.test", [], level=None, role="master", site="ЦАР"), admin_database)

    boss, preview, master = (_row(admin_database, login) for login in ("boss", "preview", "master"))
    assert (boss.access_level, boss.role) == ("superadmin", "")
    assert (preview.access_level, preview.role) == ("admin", "admin")
    assert (master.access_level, master.site_master, master.shop_worker) == ("foreman", True, False)
    with pytest.raises(SystemExit, match="--level"):
        create_user(args("nobody", "nobody@example.test", [], level=None), admin_database)


def test_cli_refuses_a_section_outside_the_level_matrix(monkeypatch, admin_database):
    monkeypatch.setattr("app.identity_admin.getpass.getpass", lambda _prompt: "safe-password")

    for permission in ("staffdir=edit", "nowhere=view", "requests=full"):
        with pytest.raises(SystemExit, match="Invalid permission"):
            create_user(args("x", "x@example.test", [permission]), admin_database)
    assert _row(admin_database, "x") is None


def test_cli_rejects_cross_field_identifier_collision(monkeypatch, admin_database):
    monkeypatch.setattr("app.identity_admin.getpass.getpass", lambda _prompt: "safe-password")
    create_user(args("shared", "first@example.test", []), admin_database)

    with pytest.raises(SystemExit, match="already exists"):
        create_user(args("second", "shared", []), admin_database)

    assert _row(admin_database, "second") is None


def test_the_plans_default_matrix():
    """docs/migration/access-levels-plan.md «Матрица по умолчанию» (a level nobody holds starts from it)."""
    rows = {level: {section: mode.value for section, mode in row.items()} for level, row in LEVEL_SEED.items()}
    filing = {"newrequest": "edit", "myrequests": "edit"}
    assert rows["commercial"] == {
        **filing, "projects": "view", "shipping": "view", "procurement": "view", "warehouse": "view",
        "production": "view", "requests": "edit",
    }
    assert rows["tech_lead"] == {
        **filing, "projects": "edit", "techcards": "edit", "erptctpl": "edit", "tcgen": "edit", "erpref": "edit",
        "production": "edit", "procurement": "edit", "requests": "edit", "fulfillment": "edit", "receiving": "view",
        "warehouse": "view", "handout": "view", "quality": "view", "shipping": "view",
    }
    assert rows["foreman"] == {
        **filing, "production": "edit", "myops": "edit", "receiving": "edit", "warehouse": "edit",
        "fulfillment": "edit", "handout": "edit", "shipping_goods": "edit", "procurement": "view", "quality": "view",
    }
    assert rows["operator"] == {**filing, "myops": "edit", "receiving": "view", "warehouse": "view", "erpref": "view"}
    assert "superadmin" not in rows and "admin" not in rows  # always full, no row
