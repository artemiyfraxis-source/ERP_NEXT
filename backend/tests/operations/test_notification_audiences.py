"""Notification audiences (access-levels plan, «Уведомления — аудитории по разделам»): a producer addresses an audience,
Operations resolves it through identity's read port from the level matrix, the personal exceptions and the access level,
among active people only. A message already in the outbox with an old role key resolves to the same audience."""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.identity.audience_directory import audience_emails, user_emails
from app.modules.identity.audiences import AUDIENCE_RULES, EDIT, in_audience
from app.modules.identity.infrastructure import IdentityLevelAccessRow, IdentityUserLevelAccessRow, IdentityUserRow
from app.modules.operations.application import dispatch_next_notification
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.shared.events import OutboxMessage
from app.shared.notifications import (
    AUDIENCES,
    LEGACY_ROLE_AUDIENCES,
    PROJECT_MANAGER_AUDIENCE,
    STOREKEEPER_AUDIENCE,
    SUPPLY_AUDIENCE,
    TECHNOLOGIST_AUDIENCE,
    notification_requested,
)
from app.shared.persistence import OutboxMessageRow, create_schema

NOW = datetime(2026, 10, 4, 10, 0)

# login → (access level, active). A person's e-mail is `<login>@example.test`.
PEOPLE = {
    "gd": ("superadmin", True),
    "tech": ("admin", True),
    "tech-gone": ("admin", False),
    "buyer": ("tech_lead", True),  # procurement edit by the level row
    "buyer-gone": ("tech_lead", False),  # the same, deactivated
    "reader": ("operator", True),  # procurement view only
    "viewer-plus": ("operator", True),  # procurement view by the row, edit by a personal exception
    "pm": ("commercial", True),  # shipping edit by the level row
    "pm-closed": ("commercial", True),  # shipping edit by the row, closed by a personal `none`
    "store": ("foreman", True),  # shipping_goods edit by the level row
    "fitter": ("operator", True),  # nothing
    "fitter-plus": ("operator", True),  # shipping_goods edit by a personal exception only
    "admin-explicit": ("admin", True),  # a full level that holds procurement explicitly
    "admin-viewing": ("admin", True),  # a full level whose personal exception is only a view
}
LEVEL_ROWS = {
    ("tech_lead", "procurement"): "edit",
    ("commercial", "procurement"): "view",
    ("operator", "procurement"): "view",
    ("commercial", "shipping"): "edit",
    ("foreman", "shipping_goods"): "edit",
    ("foreman", "shipping"): "view",
    # A full level's stored row is edit everywhere: it must not count as «explicit».
    ("admin", "procurement"): "edit",
    ("admin", "shipping"): "edit",
    ("admin", "shipping_goods"): "edit",
}
EXCEPTIONS = {
    ("viewer-plus", "procurement"): "edit",
    ("pm-closed", "shipping"): "none",
    ("fitter-plus", "shipping_goods"): "edit",
    ("admin-explicit", "procurement"): "edit",
    ("admin-viewing", "procurement"): "view",
}


def _emails(*logins: str) -> list[str]:
    return sorted(f"{login}@example.test" for login in logins)


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    create_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory() as session:
        ids = {}
        for login, (level, active) in PEOPLE.items():
            ids[login] = uuid4()
            session.add(IdentityUserRow(
                id=ids[login], login=login, email=f"{login}@example.test", name=login, department="Офис",
                role="staff", site=None, active=active, access_level=level,
            ))
        session.flush()
        for (level, section), mode in LEVEL_ROWS.items():
            session.add(IdentityLevelAccessRow(level=level, section=section, mode=mode))
        for (login, section), mode in EXCEPTIONS.items():
            session.add(IdentityUserLevelAccessRow(user_id=ids[login], section=section, mode=mode))
        session.commit()
    factory.ids = ids
    yield factory
    engine.dispose()


# ── Who is in an audience ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("audience", "members"),
    [
        # Technologist, owner and security alerts: the superadministrators and administrators, active ones.
        (TECHNOLOGIST_AUDIENCE, ("gd", "tech", "admin-explicit", "admin-viewing")),
        # Supply: «Лист закупки» edit — the level row, a personal exception, or an explicit grant to a full level.
        (SUPPLY_AUDIENCE, ("buyer", "viewer-plus", "admin-explicit")),
        # The project manager: «Отгрузка» edit; a personal `none` closes it; a full level without a grant is out.
        (PROJECT_MANAGER_AUDIENCE, ("pm",)),
        # The storekeeper: «Отгрузка — склад» edit, the foreman's row or a personal exception.
        (STOREKEEPER_AUDIENCE, ("store", "fitter-plus")),
    ],
)
def test_each_audience_resolves_by_its_section_edit(sessions, audience, members):
    with sessions() as session:
        assert audience_emails(session, (audience,)) == _emails(*members)


def test_fixed_full_levels_join_a_section_audience_only_by_an_explicit_exception(sessions):
    with sessions() as session:
        supply = set(audience_emails(session, (SUPPLY_AUDIENCE,)))
        manager = set(audience_emails(session, (PROJECT_MANAGER_AUDIENCE,)))
        storekeeper = set(audience_emails(session, (STOREKEEPER_AUDIENCE,)))
    # gd and tech are full everywhere, and the admin row stores edit on every section — neither makes them supply,
    # a project manager or a storekeeper.
    for login in ("gd", "tech", "admin-viewing"):
        assert f"{login}@example.test" not in supply | manager | storekeeper
    assert "admin-explicit@example.test" in supply
    assert "admin-explicit@example.test" not in manager | storekeeper


def test_a_view_is_not_an_edit_and_a_personal_none_closes_the_level_row(sessions):
    with sessions() as session:
        supply = audience_emails(session, (SUPPLY_AUDIENCE,))
        manager = audience_emails(session, (PROJECT_MANAGER_AUDIENCE,))
    assert "reader@example.test" not in supply  # a view of the section
    assert "pm-closed@example.test" not in manager  # the row says edit, the person's own `none` wins
    assert "viewer-plus@example.test" in supply  # a personal edit over a view row


def test_inactive_people_are_never_addressed(sessions):
    with sessions() as session:
        everyone = audience_emails(session, tuple(AUDIENCES))
        by_id = user_emails(session, tuple(sessions.ids.values()))
    assert not {"tech-gone@example.test", "buyer-gone@example.test"} & set(everyone)
    assert not {"tech-gone@example.test", "buyer-gone@example.test"} & set(by_id)


def test_several_audiences_give_each_person_once_and_an_unknown_one_nobody(sessions):
    with sessions() as session:
        assert audience_emails(session, (SUPPLY_AUDIENCE, SUPPLY_AUDIENCE, PROJECT_MANAGER_AUDIENCE)) == _emails(
            "buyer", "viewer-plus", "admin-explicit", "pm",
        )
        assert audience_emails(session, ("nobody",)) == []
        assert audience_emails(session, ()) == []


def test_a_section_nobody_edits_is_an_empty_audience():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    create_schema(engine)
    with Session(engine) as session:
        session.add(IdentityUserRow(
            id=uuid4(), login="x", email="x@example.test", name="x", department="Офис", role="staff", site=None,
            active=True, access_level="tech_lead",
        ))
        session.commit()
        assert audience_emails(session, (SUPPLY_AUDIENCE,)) == []  # an absent matrix cell is closed
    engine.dispose()


def test_the_pure_rule_matches_the_directory_on_the_edge_cases():
    supply = AUDIENCE_RULES[SUPPLY_AUDIENCE]
    assert supply == ("procurement", EDIT)
    assert in_audience(supply, "tech_lead", {"procurement": "edit"}, {})
    assert not in_audience(supply, "tech_lead", {"procurement": "view"}, {})
    assert not in_audience(supply, "tech_lead", {"procurement": "edit"}, {"procurement": "none"})
    assert in_audience(supply, "operator", {}, {"procurement": "edit"})
    assert not in_audience(supply, "admin", {"procurement": "edit"}, {})
    assert in_audience(supply, "superadmin", {}, {"procurement": "edit"})
    assert in_audience(AUDIENCE_RULES[TECHNOLOGIST_AUDIENCE], "superadmin", {}, {})
    assert not in_audience(AUDIENCE_RULES[TECHNOLOGIST_AUDIENCE], "tech_lead", {"procurement": "edit"}, {})


# ── Delivery ─────────────────────────────────────────────────────────────────────────────────────


def _inbox(sessions) -> dict[str, list[str]]:
    while dispatch_next_notification(NOW, SqlAlchemyOperationsUnitOfWork(sessions)) is not None:
        pass
    inbox: dict[str, list[str]] = {}
    with SqlAlchemyOperationsUnitOfWork(sessions) as uow:
        for login in PEOPLE:
            titles = [item.title for item in uow.notifications.list_for(f"{login}@example.test", 40)]
            if titles:
                inbox[login] = titles
    return inbox


def _publish(sessions, message) -> None:
    with SqlAlchemyOperationsUnitOfWork(sessions) as uow:
        uow.outbox.add(message)
        uow.commit()


def test_a_published_audience_reaches_exactly_its_people(sessions):
    _publish(sessions, notification_requested(
        key="a:supply", now=NOW, title="Закупка", body="", kind="info", audiences=(SUPPLY_AUDIENCE,),
    ))
    assert _inbox(sessions) == {login: ["Закупка"] for login in ("buyer", "viewer-plus", "admin-explicit")}


def test_the_payload_names_audiences_and_an_unknown_one_is_refused():
    message = notification_requested(
        key="a:payload", now=NOW, title="x", body="", kind="info",
        audiences=(TECHNOLOGIST_AUDIENCE, SUPPLY_AUDIENCE, TECHNOLOGIST_AUDIENCE),
    )
    assert message.payload["recipient_audiences"] == "technologist,supply"
    assert "recipient_roles" not in message.payload
    with pytest.raises(ValueError):
        notification_requested(key="a:bad", now=NOW, title="x", body="", kind="info", audiences=("buyer",))


def _old_message(roles: str, key: str) -> OutboxMessage:
    """What the outbox already holds from before audiences: role keys in `recipient_roles`."""
    return OutboxMessage(
        id=uuid4(), topic="notification.requested", idempotency_key=f"notify:{key}", created_at=NOW,
        payload={
            "recipient_user_ids": "", "recipient_roles": roles, "type": "info", "reference": "",
            "title": f"Старое {key}", "body": "",
        },
    )


@pytest.mark.parametrize("role", sorted(LEGACY_ROLE_AUDIENCES))
def test_an_old_outbox_role_key_resolves_to_the_same_audience_as_the_new_one(sessions, role):
    _publish(sessions, _old_message(role, "old"))
    old = _inbox(sessions)
    with sessions() as session:
        expected = audience_emails(session, (LEGACY_ROLE_AUDIENCES[role],))
    assert sorted(f"{login}@example.test" for login in old) == expected
    assert expected  # the fixture has people in every audience


def test_old_role_keys_combine_and_a_key_nobody_used_names_nobody(sessions):
    _publish(sessions, _old_message("admin,buyer", "pair"))
    assert set(_inbox(sessions)) == {"gd", "tech", "admin-explicit", "admin-viewing", "buyer", "viewer-plus"}

    _publish(sessions, _old_message("engineer", "stray"))
    assert dispatch_next_notification(NOW, SqlAlchemyOperationsUnitOfWork(sessions)) == 0  # dropped, not poisoned
    with sessions() as session:
        row = session.query(OutboxMessageRow).filter_by(idempotency_key="notify:stray").one()
        assert row.published_at is not None and row.dead_lettered_at is None
