"""«Доступы» (NAV-ACCESS): level × section matrix and per-person exceptions, superadmin/admin only, audited.

SUPPLYMAP Portal.gs portalAccessGet / portalAccessSave / portalAccessClean_ (ScriptProperties `portal_access`) and the
per-person «Доступ» column of Staff.gs (staffAccessClean_, edited in sdAccessModal_). Since the access levels (owner
04.10.2026, docs/migration/access-levels-plan.md) the rows are the levels (`identity_level_access`) and the exceptions
sit on top of the person's level (`identity_user_level_access`); the role tables are legacy and no longer written.
Differences, «решено агентом — проверить»: an empty cell = closed; a personal exception may be an explicit `none`;
unknown levels/sections are refused (422), not silently dropped; superadmin/admin are full whatever is stored, so their
rows are neither shown nor saved (a cell sent for them is ignored). Their personal exceptions open nothing more, but a
section held explicitly that way puts them into that section's notification audience (identity/audiences.py)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from app.shared.clock import as_utc
from app.shared.identity import Actor

from .application import UnitOfWork
from .domain import (
    FULL_ACCESS_LEVELS,
    LEVEL_LABELS,
    LEVEL_SECTION_LABELS,
    AccessMode,
    IdentityUser,
    IdentityValidationError,
)
from .staff import _audit


ACCESS_AGGREGATE = uuid5(NAMESPACE_URL, "fraxis-erp:identity:access-matrix")


@dataclass(frozen=True)
class AccessOverview:
    matrix: dict[str, dict[str, AccessMode]]  # the explicit cells of the matrix levels; an empty one is closed
    exceptions: dict[UUID, dict[str, AccessMode]]
    users: list[IdentityUser]


def _clean(row: dict[str, str], where: str) -> dict[str, AccessMode]:
    result: dict[str, AccessMode] = {}
    for section, mode in row.items():
        if section not in LEVEL_SECTION_LABELS:
            raise IdentityValidationError(f"{where}: неизвестный раздел «{section}».")
        if mode in ("", None):
            continue  # an empty cell = closed for a level, «как у уровня» for a person
        try:
            result[section] = AccessMode(mode)
        except ValueError as error:
            raise IdentityValidationError(f"{where}: неизвестный режим «{mode}».") from error
    return result


def clean_matrix(matrix: dict[str, dict[str, str]]) -> dict[str, dict[str, AccessMode]]:
    """portalAccessClean_: known levels, sections and modes only; the full levels' rows are dropped (always full)."""
    result: dict[str, dict[str, AccessMode]] = {}
    for level, row in matrix.items():
        if level not in LEVEL_LABELS:
            raise IdentityValidationError(f"Неизвестный уровень доступа «{level}».")
        cleaned = _clean(row or {}, LEVEL_LABELS[level])
        if cleaned and level not in FULL_ACCESS_LEVELS:
            result[level] = cleaned
    return result


def _changes(before: dict[str, AccessMode], after: dict[str, AccessMode]) -> dict[str, list[str]]:
    return {
        section: [before[section].value if section in before else "", after[section].value if section in after else ""]
        for section in sorted(set(before) | set(after))
        if before.get(section) is not after.get(section)
    }


def access_overview(uow: UnitOfWork) -> AccessOverview:
    """portalAccessGet: the stored matrix of the levels the matrix drives + every person's exceptions."""
    with uow:
        users = sorted(uow.identity.all_users(), key=lambda user: (not user.active, user.name.casefold(), user.login))
        return AccessOverview(
            matrix={
                level: row for level, row in uow.identity.all_level_access().items() if level not in FULL_ACCESS_LEVELS
            },
            exceptions=uow.identity.all_user_level_access(),
            users=users,
        )


def save_level_matrix(
    matrix: dict[str, dict[str, str]], actor: Actor, now: datetime, uow: UnitOfWork,
) -> dict[str, dict[str, AccessMode]]:
    """portalAccessSave: the whole matrix at once; a level missing from it has nothing open (superadmin/admin: always
    full). One audit event per level whose row changed, with «было → стало» per section ("" = пусто)."""
    cleaned = clean_matrix(matrix)
    now = as_utc(now)
    with uow:
        stored = uow.identity.all_level_access()
        for level in sorted((set(stored) | set(cleaned)) - FULL_ACCESS_LEVELS):
            before, after = stored.get(level, {}), cleaned.get(level, {})
            changes = _changes(before, after)
            if not changes:
                continue
            uow.identity.replace_level_access(level, after)
            _audit(uow, ACCESS_AGGREGATE, "identity.level_access_saved", actor, now, {
                "level": level, "changes": json.dumps(changes, ensure_ascii=False),
            })
        uow.commit()
        return cleaned


def save_user_access(
    user_id: UUID, access: dict[str, str], actor: Actor, now: datetime, uow: UnitOfWork,
) -> dict[str, AccessMode]:
    """sdAccessModal_ + portalStaffSave `access`: one person's exceptions, stronger than the level (`none` closes).
    A superadmin/admin keeps everything whatever is stored here (an explicit section only adds its notifications)."""
    now = as_utc(now)
    with uow:
        user = uow.identity.user_for_update(user_id)
        if user is None:
            raise KeyError("Сотрудник не найден.")
        cleaned = _clean(access, user.name)
        before = uow.identity.user_level_access(user_id)
        changes = _changes(before, cleaned)
        if changes:
            uow.identity.replace_user_level_access(user_id, cleaned)
            _audit(uow, user_id, "identity.user_access_saved", actor, now, {
                "login": user.login, "changes": json.dumps(changes, ensure_ascii=False),
            })
            uow.commit()
        return cleaned
