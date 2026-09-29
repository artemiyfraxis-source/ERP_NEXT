"""«Сотрудники» (ID-8): the administrator keeps the staff directory and the department list.

SUPPLYMAP Staff.gs:1-179 (portalStaffGet / portalStaffSave / staffDepts_) saved the whole directory at once
and assigned a PIN. Here every employee is a row with its own audited change, and the credential is an
Argon2id password the administrator types — the same path as the CLI `create-user`; it is never echoed
back. Removing a row in SUPPLYMAP took away the login; here the employee is deactivated (sessions revoked),
so the id that owns their history stays."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.shared.clock import as_utc
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .application import UnitOfWork
from .domain import (
    AccessMode,
    IdentityUser,
    IdentityValidationError,
    department_is_role_like,
    department_list,
    hash_password,
    normalize_department,
    normalize_email,
    normalize_login,
    normalize_name,
    normalize_role,
    normalize_site,
    effective_permissions,
    role_permissions,
)


DEPARTMENTS_AGGREGATE = uuid5(NAMESPACE_URL, "fraxis-erp:identity:departments")
DEPARTMENTS_LIMIT = 500
LAST_ADMIN = (
    "Нужен хотя бы один администратор с доступом к «Сотрудникам» и «Доступам» — иначе некому будет вернуть права."
)


class StaffConflict(ValueError):
    """The change is valid on its own but collides with the directory (duplicate login, last admin)."""


@dataclass(frozen=True)
class StaffDirectory:
    users: list[IdentityUser]
    departments: list[str]


@dataclass(frozen=True)
class NewEmployee:
    email: str
    name: str
    role: str
    password: str
    login: str = ""  # empty → the e-mail, as SUPPLYMAP logged in by e-mail
    department: str = ""
    site: str | None = None


@dataclass(frozen=True)
class EmployeeChange:
    name: str
    department: str
    role: str
    active: bool
    site: str | None = None
    password: str | None = None  # None / empty → the password stays


def _audit(uow: UnitOfWork, aggregate_id: UUID, event_type: str, actor: Actor, now: datetime, details: dict) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=aggregate_id, event_type=event_type, actor=actor, occurred_at=now,
        details=details,
    ))


def _shown(value: object) -> object:
    return "" if value is None else value


def _name_key(user: IdentityUser) -> tuple[str, str]:
    lowered = user.name.casefold()
    return lowered.replace("ё", "е"), user.login


def staff_directory(uow: UnitOfWork) -> StaffDirectory:
    """portalStaffGet: every employee by name + the department list (saved ∪ in use, staffDepts_)."""
    with uow:
        users = uow.identity.all_users()
        departments = department_list(uow.identity.saved_departments(), [user.department for user in users])
        return StaffDirectory(users=sorted(users, key=_name_key), departments=departments)


def create_employee(data: NewEmployee, actor: Actor, now: datetime, uow: UnitOfWork) -> IdentityUser:
    email = normalize_email(data.email)
    login = normalize_login(data.login or email)
    name = normalize_name(data.name)
    department = normalize_department(data.department)
    role = normalize_role(data.role)
    site = normalize_site(data.site)
    password_hash = hash_password(data.password)
    now = as_utc(now)
    with uow:
        if uow.identity.identifiers_taken({login, email}):
            raise StaffConflict("Сотрудник с таким логином или e-mail уже есть.")
        user = IdentityUser(
            id=uuid4(), login=login, email=email, name=name, department=department, role=role, site=site,
            active=True,
        )
        # The role's sections come from ROLE_DEFAULTS + the «Доступы» matrix; nothing is seeded (NAV-ACCESS).
        uow.identity.add_user(user, password_hash, {})
        _audit(uow, user.id, "identity.user_created", actor, now, {
            "login": login, "email": email, "name": name, "department": department, "role": role,
            "site": site or "",
        })
        uow.commit()
        return user


def access_keepers(
    users: list[IdentityUser],
    admin_matrix: dict[str, AccessMode],
    exceptions: dict[UUID, dict[str, AccessMode]],
) -> set[UUID]:
    """Active administrators who can still open «Сотрудники» (`staffdir` edit + the admin role; «Доступы» is the admin
    role alone) — after the matrix row of the admin role and their own exceptions. SUPPLYMAP portalAccessSave drops
    `admin.staffdir = none` for the same reason."""
    base = role_permissions("admin", admin_matrix)
    return {
        user.id
        for user in users
        if user.active and user.role == "admin"
        and effective_permissions(base, exceptions.get(user.id, {})).get("staffdir") is AccessMode.EDIT
    }


def ensure_access_keeper(
    uow: UnitOfWork,
    *,
    user: IdentityUser | None = None,
    admin_matrix: dict[str, AccessMode] | None = None,
    exceptions: tuple[UUID, dict[str, AccessMode]] | None = None,
) -> None:
    """portalStaffSave «хотя бы один администратор» + portalAccessSave «себе вход в настройки не закрываем»: after the
    change (a user row, the admin matrix row or one person's exceptions) someone must still be able to return rights.
    Call inside an open unit of work; the admin rows are locked, so two admins cannot shut each other out at once."""
    uow.identity.active_admin_ids()
    users = [user if user is not None and row.id == user.id else row for row in uow.identity.all_users()]
    matrix = admin_matrix if admin_matrix is not None else uow.identity.all_role_access().get("admin", {})
    personal = uow.identity.all_user_access()
    if exceptions is not None:
        personal[exceptions[0]] = exceptions[1]
    if not access_keepers(users, matrix, personal):
        raise StaffConflict(LAST_ADMIN)


def update_employee(
    user_id: UUID, change: EmployeeChange, actor: Actor, now: datetime, uow: UnitOfWork,
) -> IdentityUser:
    name = normalize_name(change.name)
    department = normalize_department(change.department)
    role = normalize_role(change.role)
    site = normalize_site(change.site)
    password_hash = hash_password(change.password) if change.password else None
    now = as_utc(now)
    with uow:
        current = uow.identity.user_for_update(user_id)
        if current is None:
            raise KeyError("Сотрудник не найден.")
        updated = replace(current, name=name, department=department, role=role, site=site, active=bool(change.active))
        if current.role == "admin" and current.active and (updated.role != "admin" or not updated.active):
            ensure_access_keeper(uow, user=updated)
        changes = {
            field: [_shown(getattr(current, field)), _shown(getattr(updated, field))]
            for field in ("name", "department", "role", "site", "active")
            if getattr(current, field) != getattr(updated, field)
        }
        if not changes and password_hash is None:
            return current
        uow.identity.update_user(updated)
        if password_hash is not None:
            uow.identity.set_password(user_id, password_hash)
        # A new password or a lost login ends every open session of the employee.
        if password_hash is not None or (current.active and not updated.active):
            uow.identity.revoke_user_sessions(user_id, now)
        details = {"login": current.login, "changes": json.dumps(changes, ensure_ascii=False)}
        if password_hash is not None:
            details["password"] = "сменён"
        _audit(uow, user_id, "identity.user_updated", actor, now, details)
        uow.commit()
        return updated


def ensure_admin_remains(user_id: UUID, uow: UnitOfWork) -> None:
    """The same last-admin rule for the standalone deactivation route; call inside an open unit of work."""
    current = uow.identity.user_for_update(user_id)
    if current is not None and current.role == "admin" and current.active:
        ensure_access_keeper(uow, user=replace(current, active=False))


def save_departments(values: list[str], actor: Actor, now: datetime, uow: UnitOfWork) -> list[str]:
    """portalStaffSave `depts`: what the admin typed ∪ the departments people have; role-like values and
    duplicates are dropped. A department someone still has cannot leave the list — rename it instead."""
    if len(values) > DEPARTMENTS_LIMIT:
        raise IdentityValidationError(f"Отделов больше {DEPARTMENTS_LIMIT}.")
    typed = [normalize_department(value) for value in values]
    now = as_utc(now)
    with uow:
        departments = department_list(typed, [user.department for user in uow.identity.all_users()])
        uow.identity.replace_departments(departments)
        _audit(uow, DEPARTMENTS_AGGREGATE, "identity.departments_saved", actor, now, {
            "departments": json.dumps(departments, ensure_ascii=False), "count": str(len(departments)),
        })
        uow.commit()
        return departments


def rename_department(source: str, target: str, actor: Actor, now: datetime, uow: UnitOfWork) -> tuple[list[str], int]:
    """«переименование подхватывается в строках сотрудников»: the list entry and every employee with this
    department (case-insensitive) move to the new name. Renaming into an existing department merges them."""
    old = normalize_department(source)
    new = normalize_department(target)
    if not old or not new:
        raise IdentityValidationError("Укажите старое и новое название отдела.")
    if old == new:
        raise IdentityValidationError("Новое название совпадает со старым.")
    if department_is_role_like(new):
        raise IdentityValidationError(f"«{new}» — это роль, а не отдел.")
    now = as_utc(now)
    with uow:
        saved = uow.identity.saved_departments()
        users = uow.identity.all_users()
        moved = [user for user in users if user.department.casefold() == old.casefold()]
        if not moved and all(name.casefold() != old.casefold() for name in saved):
            raise KeyError(f"Отдела «{old}» нет.")
        for user in moved:
            uow.identity.update_user(replace(user, department=new))
        renamed = [new if name.casefold() == old.casefold() else name for name in saved]
        departments = department_list(renamed, [new] if moved else [], [
            user.department for user in users if user.department.casefold() != old.casefold()
        ])
        uow.identity.replace_departments(departments)
        _audit(uow, DEPARTMENTS_AGGREGATE, "identity.department_renamed", actor, now, {
            "source": old, "target": new, "users": str(len(moved)),
        })
        uow.commit()
        return departments, len(moved)
