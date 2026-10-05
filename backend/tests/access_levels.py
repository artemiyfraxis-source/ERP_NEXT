"""Test principals on the access levels (docs/migration/access-levels-plan.md, T2).

Tests written for the role model name an old role and its sections. `principal_as` turns that into the principal the
level model gives the same person: the level the role moved to, and the sections the person could really use — the
role's matrix row (or the sections the test names) cut by the old route role lists, in the new sections (myops,
quality, shipping_goods), exactly as the transfer (`level_migration.usable`) computes the compensating exceptions.
A level key passes through as it is: `principal_as("foreman", {"production": "edit"})`."""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.identity.domain import ACCESS_LEVELS, ROLE_SEED
from app.modules.identity.level_migration import ROLE_TO_LEVEL, old_permissions, usable
from app.shared.http_auth import Principal
from tests.factories import Actor


def level_and_sections(who: str, sections: Mapping[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """(level, permissions) for an old role (its effective access in the new sections) or a level key."""
    if who in ACCESS_LEVELS and who not in ROLE_TO_LEVEL:
        return who, dict(sections or {})
    seed = {section: mode.value for section, mode in ROLE_SEED.get(who, {}).items()}
    old = old_permissions(who, seed if sections is None else {}, {} if sections is None else sections)
    return ROLE_TO_LEVEL.get(who, "operator"), usable(who, old)


def principal_as(
    who: str,
    sections: Mapping[str, str] | None = None,
    *,
    name: str | None = None,
    department: str = "Тест",
    email: str | None = None,
    actor=None,
) -> Principal:
    level, permissions = level_and_sections(who, sections)
    return Principal(
        actor=actor or Actor(name=name or f"Тест {who}", department=department),
        email=email or f"{who}@example.test",
        roles=frozenset({level}),
        permissions=permissions,
    )
