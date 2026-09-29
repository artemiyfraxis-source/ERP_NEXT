"""«Доступы» (NAV-ACCESS): role × section matrix and per-person exceptions, administrator only, audited.

SUPPLYMAP Portal.gs portalAccessGet / portalAccessSave / portalAccessClean_ (ScriptProperties `portal_access`) and the
per-person «Доступ» column of Staff.gs (staffAccessClean_, edited in sdAccessModal_). Differences, «решено агентом —
проверить»: an empty cell means «как задумано ролью» (ROLE_DEFAULTS), not «закрыто» as SUPPLYMAP has since 01.09.2026,
so the matrix offers an explicit `none`; unknown roles/sections are refused (422), not silently dropped; the
«nobody can return rights» guard covers the whole admin set (matrix row + exceptions), not only `admin.staffdir`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from app.shared.clock import as_utc
from app.shared.identity import Actor

from .application import UnitOfWork
from .domain import (
    ROLE_LABELS,
    SECTION_LABELS,
    AccessMode,
    IdentityUser,
    IdentityValidationError,
)
from .staff import _audit, ensure_access_keeper


ACCESS_AGGREGATE = uuid5(NAMESPACE_URL, "fraxis-erp:identity:access-matrix")


@dataclass(frozen=True)
class AccessOverview:
    matrix: dict[str, dict[str, AccessMode]]  # only what the administrator changed on top of ROLE_DEFAULTS
    exceptions: dict[UUID, dict[str, AccessMode]]
    users: list[IdentityUser]


def _clean(row: dict[str, str], where: str) -> dict[str, AccessMode]:
    result: dict[str, AccessMode] = {}
    for section, mode in row.items():
        if section not in SECTION_LABELS:
            raise IdentityValidationError(f"{where}: неизвестный раздел «{section}».")
        if mode in ("", None):
            continue  # an empty cell = «как задумано ролью» / «как у роли»
        try:
            result[section] = AccessMode(mode)
        except ValueError as error:
            raise IdentityValidationError(f"{where}: неизвестный режим «{mode}».") from error
    return result


def clean_matrix(matrix: dict[str, dict[str, str]]) -> dict[str, dict[str, AccessMode]]:
    """portalAccessClean_: known roles, sections and modes only. A cell equal to the role default is kept — it pins the
    value if ROLE_DEFAULTS later change (the screen shows it as set)."""
    result: dict[str, dict[str, AccessMode]] = {}
    for role, row in matrix.items():
        if role not in ROLE_LABELS:
            raise IdentityValidationError(f"Неизвестная роль «{role}».")
        cleaned = _clean(row or {}, ROLE_LABELS[role])
        if cleaned:
            result[role] = cleaned
    return result


def _shown(row: dict[str, AccessMode]) -> dict[str, str]:
    return {section: mode.value for section, mode in sorted(row.items())}


def _changes(before: dict[str, AccessMode], after: dict[str, AccessMode]) -> dict[str, list[str]]:
    return {
        section: [before[section].value if section in before else "", after[section].value if section in after else ""]
        for section in sorted(set(before) | set(after))
        if before.get(section) is not after.get(section)
    }


def access_overview(uow: UnitOfWork) -> AccessOverview:
    """portalAccessGet: the stored matrix (the defaults travel as ROLE_DEFAULTS) + every person's exceptions."""
    with uow:
        users = sorted(uow.identity.all_users(), key=lambda user: (not user.active, user.name.casefold(), user.login))
        return AccessOverview(
            matrix=uow.identity.all_role_access(),
            exceptions=uow.identity.all_user_access(),
            users=users,
        )


def save_role_matrix(
    matrix: dict[str, dict[str, str]], actor: Actor, now: datetime, uow: UnitOfWork,
) -> dict[str, dict[str, AccessMode]]:
    """portalAccessSave: the whole matrix at once; a role missing from it follows ROLE_DEFAULTS again. One audit event
    per role whose row changed, with «было → стало» per section ("" = как задумано ролью)."""
    cleaned = clean_matrix(matrix)
    now = as_utc(now)
    with uow:
        ensure_access_keeper(uow, admin_matrix=cleaned.get("admin", {}))
        stored = uow.identity.all_role_access()
        for role in sorted(set(stored) | set(cleaned)):
            before, after = stored.get(role, {}), cleaned.get(role, {})
            changes = _changes(before, after)
            if not changes:
                continue
            uow.identity.replace_role_access(role, after)
            _audit(uow, ACCESS_AGGREGATE, "identity.role_access_saved", actor, now, {
                "role": role, "changes": json.dumps(changes, ensure_ascii=False),
            })
        uow.commit()
        return cleaned


def save_user_access(
    user_id: UUID, access: dict[str, str], actor: Actor, now: datetime, uow: UnitOfWork,
) -> dict[str, AccessMode]:
    """sdAccessModal_ + portalStaffSave `access`: one person's exceptions, stronger than the role (`none` closes)."""
    now = as_utc(now)
    with uow:
        user = uow.identity.user_for_update(user_id)
        if user is None:
            raise KeyError("Сотрудник не найден.")
        cleaned = _clean(access, user.name)
        ensure_access_keeper(uow, exceptions=(user_id, cleaned))
        before = uow.identity.user_access(user_id)
        changes = _changes(before, cleaned)
        if changes:
            uow.identity.replace_user_access(user_id, cleaned)
            _audit(uow, user_id, "identity.user_access_saved", actor, now, {
                "login": user.login, "changes": json.dumps(changes, ensure_ascii=False),
            })
            uow.commit()
        return cleaned

