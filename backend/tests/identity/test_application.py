from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.application import (
    AuthenticationError,
    AuthenticationRateLimited,
    deactivate_user,
    login,
    logout,
    resolve_session,
)
from app.modules.identity.domain import (
    AccessMode,
    IdentityUser,
    hash_password,
    secret_hash,
)
from app.modules.identity.infrastructure import (
    IdentityIdentifierRow,
    IdentityLoginThrottleRow,
    IdentitySessionRow,
    IdentityUserAccessRow,
    IdentityUserRow,
    SqlAlchemyIdentityUnitOfWork,
    provision_user,
)
from app.shared.clock import as_utc
from app.shared.persistence import create_schema
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow


NOW = datetime(2026, 9, 15, 10, 0)
USER_ID = UUID("10000000-0000-0000-0000-000000000001")


@pytest.fixture()
def identity_database():
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
                id=USER_ID,
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
    yield sessions
    engine.dispose()


def factory(sessions):
    return lambda: SqlAlchemyIdentityUnitOfWork(sessions)


def test_login_resolve_logout_and_server_owned_principal(identity_database):
    uow_factory = factory(identity_database)
    result = login(
        " PETROV ",
        "correct-password",
        NOW,
        uow_factory(),
        client_key="client:test",
    )
    resolved = resolve_session(result.session_token, NOW, uow_factory())

    assert resolved is not None
    assert resolved.principal.actor.id == USER_ID
    # Stored rows sit on top of ROLE_DEFAULTS (NAV-ACCESS): the buyer's default view grants come along.
    assert resolved.principal.permissions == {
        "newrequest": "edit",
        "myrequests": "edit",
        "requests": "edit",
        "procurement": "edit",
        "warehouse": "view",
        "erpref": "view",
    }
    assert result.session_token not in resolved.session.token_hash

    logout(
        result.session_token,
        result.principal.actor,
        NOW + timedelta(minutes=1),
        uow_factory(),
    )
    assert resolve_session(result.session_token, NOW, uow_factory()) is None
    with identity_database() as session:
        succeeded = session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "identity.login_succeeded"
            )
        )
        assert succeeded.aggregate_id == USER_ID
        assert succeeded.actor_id == USER_ID
        logged_out = session.scalar(
            select(AuditEventRow).where(AuditEventRow.event_type == "identity.logout")
        )
        assert logged_out.aggregate_id == USER_ID
        assert logged_out.actor_id == USER_ID


def test_wrong_password_is_generic_and_rate_limited_by_account(identity_database):
    uow_factory = factory(identity_database)
    with pytest.raises(AuthenticationError, match="Неверный"):
        login(
            "petrov",
            "wrong-password",
            NOW,
            uow_factory(),
            client_key="client:test",
        )
    with identity_database() as session:
        throttle = session.get(IdentityLoginThrottleRow, "login:petrov")
        assert throttle.failures == 1
        failed = session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "identity.login_failed"
            )
        )
        assert failed.aggregate_id == USER_ID
        assert "identifier_hash" in failed.details
        assert "petrov" not in failed.details.values()

    with identity_database() as session:
        throttle = session.get(IdentityLoginThrottleRow, "login:petrov")
        throttle.failures = 10
        session.commit()
    with pytest.raises(AuthenticationRateLimited):
        login(
            "petrov",
            "correct-password",
            NOW,
            uow_factory(),
            client_key="client:test",
        )
    with pytest.raises(AuthenticationRateLimited):
        login(
            "petrov",
            "correct-password",
            NOW,
            uow_factory(),
            client_key="client:test",
        )
    with identity_database() as session:
        rate_limit_events = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "identity.login_rate_limited"
                )
            )
        )
        assert len(rate_limit_events) == 1


def test_user_override_removes_permission_and_deactivation_revokes_session(identity_database):
    with identity_database() as session:
        session.add(
            IdentityUserAccessRow(
                user_id=USER_ID,
                section="procurement",
                mode=AccessMode.NONE.value,
            )
        )
        session.commit()
    uow_factory = factory(identity_database)
    result = login(
        "petrov@example.test",
        "correct-password",
        NOW,
        uow_factory(),
        client_key="client:test",
    )
    assert result.principal.permissions == {
        "newrequest": "edit", "myrequests": "edit", "requests": "edit", "warehouse": "view", "erpref": "view",
    }

    with identity_database() as session:
        session.get(IdentityUserRow, USER_ID).active = False
        session.commit()
    assert resolve_session(result.session_token, NOW, uow_factory()) is None
    with identity_database() as session:
        stored = session.scalar(select(IdentitySessionRow))
        assert stored.revoked_at == NOW


def test_sliding_expiry_never_exceeds_absolute_lifetime(identity_database):
    uow_factory = factory(identity_database)
    result = login(
        "petrov",
        "correct-password",
        NOW,
        uow_factory(),
        client_key="client:test",
    )
    day_10 = resolve_session(result.session_token, NOW + timedelta(days=10), uow_factory())
    day_20 = resolve_session(result.session_token, NOW + timedelta(days=20), uow_factory())
    day_29 = resolve_session(result.session_token, NOW + timedelta(days=29), uow_factory())
    expired = resolve_session(
        result.session_token,
        NOW + timedelta(days=30, seconds=1),
        uow_factory(),
    )

    assert day_10 is not None
    assert day_20 is not None
    assert day_29 is not None
    assert day_29.session.expires_at == as_utc(NOW + timedelta(days=30))
    assert expired is None


def test_deactivation_revokes_all_user_sessions_atomically(identity_database):
    uow_factory = factory(identity_database)
    first = login(
        "petrov",
        "correct-password",
        NOW,
        uow_factory(),
        client_key="client:first",
    )
    second = login(
        "petrov",
        "correct-password",
        NOW,
        uow_factory(),
        client_key="client:second",
    )

    admin = Actor(
        id=UUID("20000000-0000-0000-0000-000000000001"),
        name="Администратор",
        department="ИТ",
    )
    user = deactivate_user(
        USER_ID,
        admin,
        NOW + timedelta(minutes=1),
        uow_factory(),
    )

    assert user.active is False
    assert resolve_session(first.session_token, NOW, uow_factory()) is None
    assert resolve_session(second.session_token, NOW, uow_factory()) is None
    with identity_database() as session:
        sessions = list(session.scalars(select(IdentitySessionRow)))
        assert len(sessions) == 2
        assert all(item.revoked_at == NOW + timedelta(minutes=1) for item in sessions)
        audit = session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.event_type == "identity.user_deactivated"
            )
        )
        assert audit.actor_id == admin.id
        assert audit.aggregate_id == USER_ID


def test_sliding_refresh_cannot_resurrect_revoked_session(identity_database):
    uow_factory = factory(identity_database)
    result = login(
        "petrov",
        "correct-password",
        NOW,
        uow_factory(),
        client_key="client:race",
    )
    stale_uow = uow_factory()
    with stale_uow:
        record = stale_uow.identity.session_with_user(secret_hash(result.session_token))
        assert record is not None
        stale_session, _ = record
        logout(
            result.session_token,
            result.principal.actor,
            NOW + timedelta(minutes=16),
            uow_factory(),
        )

        refreshed = replace(
            stale_session,
            last_seen_at=NOW + timedelta(minutes=17),
            expires_at=NOW + timedelta(days=14, minutes=17),
        )
        assert stale_uow.identity.update_session(refreshed) is False

    with identity_database() as session:
        stored = session.get(IdentitySessionRow, stale_session.id)
        assert stored.revoked_at == NOW + timedelta(minutes=16)


def test_login_and_email_share_one_unique_identifier_namespace(identity_database):
    with identity_database() as session:
        provision_user(
            session,
            IdentityUser(
                id=UUID("10000000-0000-0000-0000-000000000002"),
                login="another",
                email="petrov",
                name="Другой",
                department="ИТ",
                role="staff",
                site=None,
                active=True,
            ),
            hash_password("another-password"),
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with identity_database() as session:
        identifiers = list(session.scalars(select(IdentityIdentifierRow)))
        assert {item.identifier for item in identifiers} == {
            "petrov",
            "petrov@example.test",
        }


def test_user_override_does_not_mutate_shared_role_policy(identity_database):
    second_id = UUID("10000000-0000-0000-0000-000000000002")
    third_id = UUID("10000000-0000-0000-0000-000000000003")
    with identity_database() as session:
        for user_id, login_name, access in (
            (second_id, "staff-edit", None),
            (third_id, "staff-view", {"requests": AccessMode.VIEW}),
        ):
            provision_user(
                session,
                IdentityUser(
                    id=user_id,
                    login=login_name,
                    email=f"{login_name}@example.test",
                    name=login_name,
                    department="Производство",
                    role="staff",
                    site=None,
                    active=True,
                ),
                hash_password("another-password"),
                role_access={"requests": AccessMode.EDIT},
                user_access=access,
            )
        session.commit()

    edit = login(
        "staff-edit",
        "another-password",
        NOW,
        factory(identity_database)(),
        client_key="client:edit",
    )
    view = login(
        "staff-view",
        "another-password",
        NOW,
        factory(identity_database)(),
        client_key="client:view",
    )

    assert edit.principal.permissions["requests"] == "edit"
    assert view.principal.permissions["requests"] == "view"
