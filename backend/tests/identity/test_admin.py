from argparse import Namespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.identity_admin import ROLE_DEFAULTS, create_user
from app.modules.identity.domain import AccessMode
from app.modules.identity.infrastructure import (
    IdentityRoleAccessRow,
    IdentityUserAccessRow,
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


def args(login: str, email: str, permissions: list[str]) -> Namespace:
    return Namespace(
        login=login,
        email=email,
        name=login,
        department="Производство",
        role="staff",
        site=None,
        permission=permissions,
    )


def test_cli_permission_is_per_user_override(monkeypatch, admin_database):
    monkeypatch.setattr("app.identity_admin.getpass.getpass", lambda _prompt: "safe-password")

    create_user(args("first", "first@example.test", []), admin_database)
    create_user(
        args("second", "second@example.test", ["requests=view"]),
        admin_database,
    )

    with admin_database() as session:
        first, second = list(
            session.scalars(select(IdentityUserRow).order_by(IdentityUserRow.login))
        )
        role_access = session.get(IdentityRoleAccessRow, ("staff", "requests"))
        override = session.get(IdentityUserAccessRow, (second.id, "requests"))
        assert first.login == "first"
        assert role_access.mode == AccessMode.EDIT.value
        assert override.mode == AccessMode.VIEW.value


def test_cli_rejects_cross_field_identifier_collision(monkeypatch, admin_database):
    monkeypatch.setattr("app.identity_admin.getpass.getpass", lambda _prompt: "safe-password")
    create_user(args("shared", "first@example.test", []), admin_database)

    with pytest.raises(SystemExit, match="already exists"):
        create_user(args("second", "shared", []), admin_database)

    with admin_database() as session:
        assert session.scalar(select(IdentityUserRow).where(IdentityUserRow.login == "second")) is None


def test_operational_roles_include_receiving_and_warehouse_permissions():
    assert ROLE_DEFAULTS["receiver"] == {
        "receiving": AccessMode.EDIT,
        "warehouse": AccessMode.EDIT,
        "fulfillment": AccessMode.EDIT,
        "shipping": AccessMode.EDIT,
        "references": AccessMode.VIEW,
    }
    assert ROLE_DEFAULTS["admin"]["receiving"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["admin"]["warehouse"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["admin"]["fulfillment"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["engineer"]["fulfillment"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["prodhead"]["fulfillment"] is AccessMode.VIEW
    assert ROLE_DEFAULTS["admin"]["production"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["master"]["production"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["prodhead"]["production"] is AccessMode.VIEW
    assert ROLE_DEFAULTS["pm"]["shipping"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["master"]["shipping"] is AccessMode.VIEW
    assert ROLE_DEFAULTS["admin"]["references"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["staff"]["references"] is AccessMode.VIEW
    assert ROLE_DEFAULTS["admin"]["operations"] is AccessMode.EDIT
    assert ROLE_DEFAULTS["management"]["operations"] is AccessMode.VIEW
    assert ROLE_DEFAULTS["qc"]["production"] is AccessMode.EDIT
