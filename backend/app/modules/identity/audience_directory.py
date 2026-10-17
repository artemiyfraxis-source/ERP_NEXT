"""Identity's read port for notification delivery: e-mails of active users, by id or by audience.

Operations resolves a `notification.requested` message through this module only — it never reads the level matrix or
the personal exceptions itself. A deactivated user is never an addressee.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audiences import AUDIENCE_RULES, in_audience
from .infrastructure import IdentityLevelAccessRow, IdentityUserLevelAccessRow, IdentityUserRow


def user_emails(session: Session, user_ids: Iterable[UUID]) -> list[str]:
    ids = tuple(user_ids)
    if not ids:
        return []
    return sorted(session.scalars(
        select(IdentityUserRow.email).where(IdentityUserRow.id.in_(ids), IdentityUserRow.active.is_(True))
    ))


def audience_emails(session: Session, audiences: Iterable[str]) -> list[str]:
    """Active people in any of the audiences: the level matrix + the personal exceptions + the access level.

    An audience the code does not know (a removed one in an old message) names nobody."""
    rules = [AUDIENCE_RULES[audience] for audience in dict.fromkeys(audiences) if audience in AUDIENCE_RULES]
    if not rules:
        return []
    sections = {rule[0] for rule in rules if rule is not None}
    level_rows: dict[str, dict[str, str]] = {}
    exceptions: dict[UUID, dict[str, str]] = {}
    if sections:
        for level, section, mode in session.execute(
            select(IdentityLevelAccessRow.level, IdentityLevelAccessRow.section, IdentityLevelAccessRow.mode)
            .where(IdentityLevelAccessRow.section.in_(sections))
        ):
            level_rows.setdefault(level, {})[section] = mode
        for user_id, section, mode in session.execute(
            select(
                IdentityUserLevelAccessRow.user_id, IdentityUserLevelAccessRow.section, IdentityUserLevelAccessRow.mode,
            ).where(IdentityUserLevelAccessRow.section.in_(sections))
        ):
            exceptions.setdefault(user_id, {})[section] = mode
    emails: set[str] = set()
    for user_id, email, level in session.execute(
        select(IdentityUserRow.id, IdentityUserRow.email, IdentityUserRow.access_level)
        .where(IdentityUserRow.active.is_(True))
    ):
        if not str(email or "").strip():
            continue
        if any(in_audience(rule, level, level_rows.get(level, {}), exceptions.get(user_id, {})) for rule in rules):
            emails.add(email)
    return sorted(emails)
