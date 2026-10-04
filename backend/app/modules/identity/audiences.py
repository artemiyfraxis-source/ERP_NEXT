"""Notification audiences: who an addressed audience is (access-levels plan, «Уведомления — аудитории по разделам»).

A producer addresses an audience (`app/shared/notifications.py`), never a role key. The audience is a rule over the
level matrix — pure, no I/O, so the report «было / стало» (`level_migration.verify`) and the delivery
(`audience_directory.audience_emails`) cannot drift apart:

- technologist / owner / security alerts → the superadministrators and administrators;
- supply → people who edit «Лист закупки» (`procurement`), the project manager → «Отгрузка» (`shipping`), the
  storekeeper → «Отгрузка — склад» (`shipping_goods`) — in each case among the people below the fixed full levels.
  A superadministrator or administrator is full everywhere by definition, so they would otherwise get every
  section's alerts; they join a section's audience only when they hold that section explicitly, i.e. a personal
  exception on it (`identity_user_level_access`). The matrix row of a full level is not such an explicit grant.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.shared.notifications import (
    PROJECT_MANAGER_AUDIENCE,
    STOREKEEPER_AUDIENCE,
    SUPPLY_AUDIENCE,
    TECHNOLOGIST_AUDIENCE,
)

from .domain import FULL_ACCESS_LEVELS

VIEW, EDIT, NONE = "view", "edit", "none"
_RANK = {NONE: 0, VIEW: 1, EDIT: 2}

# audience → what its members hold: (section, minimum mode), or None — the full levels themselves.
AudienceRule = tuple[str, str] | None
AUDIENCE_RULES: dict[str, AudienceRule] = {
    TECHNOLOGIST_AUDIENCE: None,
    SUPPLY_AUDIENCE: ("procurement", EDIT),
    PROJECT_MANAGER_AUDIENCE: ("shipping", EDIT),
    STOREKEEPER_AUDIENCE: ("shipping_goods", EDIT),
}
AUDIENCE_LABELS: dict[str, str] = {
    TECHNOLOGIST_AUDIENCE: "Технолог, владелец, безопасность",
    SUPPLY_AUDIENCE: "Снабжение",
    PROJECT_MANAGER_AUDIENCE: "Менеджер проекта",
    STOREKEEPER_AUDIENCE: "Кладовщик",
}


def holds(mode: str | None, minimum: str) -> bool:
    return _RANK.get(str(mode or NONE), 0) >= _RANK[minimum]


def in_audience(
    rule: AudienceRule,
    level: str,
    level_row: Mapping[str, str],
    exceptions: Mapping[str, str],
) -> bool:
    """Whether a person at `level` — with the level's matrix row and their personal exceptions — is in the audience.

    Below the full levels a personal exception overrides the level row (`none` closes the section)."""
    if rule is None:
        return level in FULL_ACCESS_LEVELS
    section, minimum = rule
    if level in FULL_ACCESS_LEVELS:
        return holds(exceptions.get(section), minimum)
    return holds(exceptions.get(section, level_row.get(section)), minimum)
