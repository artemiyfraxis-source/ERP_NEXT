"""«Сотрудники» (ID-8): the administrator keeps the staff directory and the department list.

SUPPLYMAP Staff.gs:1-179 (portalStaffGet / portalStaffSave / staffDepts_) saved the whole directory at once
and assigned a PIN. Here every employee is a row with its own audited change. The administrator sets the PIN
(6–8 digits, PORTAL_PIN_RE_) and sees whether one is assigned (`pinSet`); a password for the login+password path is
optional. Both are Argon2id hashes and are never echoed back. Removing a row in SUPPLYMAP took away the login; here the employee is deactivated (sessions revoked),
so the id that owns their history stays.

Кадровые поля «Личной карточки» (native, owner 04.10.2026): табельный номер (unique), должность, категория /
квалификация, «в должности с», непосредственный руководитель (not oneself, no closed loop); a department may have a
head. Every change is in the audit."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.shared.clock import as_utc
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .application import UnitOfWork
from .domain import (
    AccessMode,
    IdentityUser,
    IdentityValidationError,
    check_manager_chain,
    department_is_role_like,
    department_list,
    hash_password,
    hash_pin,
    new_pin,
    validate_pin,
    normalize_department,
    normalize_email,
    normalize_job_text,
    normalize_login,
    normalize_name,
    normalize_personnel_number,
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
    pin_set: frozenset[UUID] = frozenset()  # portalStaffGet `pinSet`
    department_heads: dict[str, UUID] = field(default_factory=dict)  # listed department → its head


class Keep(Enum):
    """A personnel field the request did not carry stays as stored, so an older screen cannot wipe it."""

    KEEP = "keep"


KEEP = Keep.KEEP


@dataclass(frozen=True)
class NewEmployee:
    email: str
    name: str
    role: str
    password: str = ""  # empty → no password: the employee signs in by PIN (SUPPLYMAP had PINs only)
    login: str = ""  # empty → the e-mail, as SUPPLYMAP logged in by e-mail
    department: str = ""
    site: str | None = None
    pin: str = ""  # empty → no PIN yet (the employee may learn one on the first sign-in, portalRevealPin_)
    personnel_number: str = ""
    job_title: str = ""
    qualification: str = ""
    job_since: date | None = None
    manager_id: UUID | None = None


@dataclass(frozen=True)
class EmployeeChange:
    name: str
    department: str
    role: str
    active: bool
    site: str | None = None
    password: str | None = None  # None / empty → the password stays
    pin: str | None = None  # None / empty → the PIN stays (portalStaffSave: pin is optional)
    # Кадровые поля: KEEP → as stored; "" / None → cleared.
    personnel_number: str | Keep = KEEP
    job_title: str | Keep = KEEP
    qualification: str | Keep = KEEP
    job_since: date | None | Keep = KEEP
    manager_id: UUID | None | Keep = KEEP


PERSONNEL_FIELDS = ("personnel_number", "job_title", "qualification", "job_since", "manager_id")
JOB_TITLE = "Должность"
QUALIFICATION = "Категория / квалификация"


def _audit(uow: UnitOfWork, aggregate_id: UUID, event_type: str, actor: Actor, now: datetime, details: dict) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=aggregate_id, event_type=event_type, actor=actor, occurred_at=now,
        details=details,
    ))


def _shown(value: object) -> object:
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return "" if value is None else value


def _name_key(user: IdentityUser) -> tuple[str, str]:
    lowered = user.name.casefold()
    return lowered.replace("ё", "е"), user.login


def _heads_for(departments: list[str], heads: dict[str, UUID]) -> dict[str, UUID]:
    """Saved heads keyed by the department names as listed (the list may spell a name in another case)."""
    by_name = {name.casefold(): head for name, head in heads.items()}
    return {name: by_name[name.casefold()] for name in departments if name.casefold() in by_name}


def staff_directory(uow: UnitOfWork) -> StaffDirectory:
    """portalStaffGet: every employee by name + the department list (saved ∪ in use, staffDepts_) + department heads."""
    with uow:
        users = uow.identity.all_users()
        departments = department_list(uow.identity.saved_departments(), [user.department for user in users])
        return StaffDirectory(
            users=sorted(users, key=_name_key), departments=departments,
            pin_set=frozenset(uow.identity.pin_set_ids()),
            department_heads=_heads_for(departments, uow.identity.department_heads()),
        )


def _check_personnel(uow: UnitOfWork, user: IdentityUser, current: IdentityUser | None, users: list[IdentityUser]) -> None:
    """Табельный номер is unique; a newly chosen руководитель exists, is switched on, is not the employee and does not
    close a loop. Unchanged values pass as they are (a manager switched off later stays on their people)."""
    by_id = {row.id: row for row in users}
    number = user.personnel_number
    if number and (current is None or number != current.personnel_number):
        owner = uow.identity.personnel_number_owner(number)
        if owner is not None and owner != user.id:
            raise StaffConflict(f"Табельный номер {number} уже занят: {by_id[owner].name if owner in by_id else 'другой сотрудник'}.")
    if user.manager_id is None or (current is not None and user.manager_id == current.manager_id):
        return
    check_manager_chain(
        user.id, user.manager_id, {row.id: row.manager_id for row in users}, {row.id: row.name for row in users},
    )
    manager = by_id.get(user.manager_id)
    if manager is None:
        raise IdentityValidationError("Руководитель не найден в справочнике сотрудников.")
    if not manager.active:
        raise IdentityValidationError(f"Руководитель «{manager.name}» отключён — выберите другого.")


def _person(user_id: UUID | None, users: list[IdentityUser]) -> str:
    return next((row.name for row in users if row.id == user_id), "") if user_id is not None else ""


def pin_assigned(user_id: UUID, uow: UnitOfWork) -> bool:
    with uow:
        return user_id in uow.identity.pin_set_ids()


@dataclass(frozen=True)
class CreatedEmployee:
    """The new employee plus the PIN to hand them: the administrator issues staff themselves and sees the PIN once
    (owner ask 2026-09-29 «все пины мне … чтобы я мог заводить новых сам»). A blank PIN is generated here rather
    than left for portalRevealPin_, so the administrator always leaves the screen with a PIN to give out."""

    user: IdentityUser
    pin: str  # the PIN the administrator entered, or the one generated for a blank field — shown once


def create_employee(data: NewEmployee, actor: Actor, now: datetime, uow: UnitOfWork) -> CreatedEmployee:
    email = normalize_email(data.email)
    login = normalize_login(data.login or email)
    name = normalize_name(data.name)
    department = normalize_department(data.department)
    role = normalize_role(data.role)
    site = normalize_site(data.site)
    # Without a password the credential is a hash of a random secret nobody knows: only the PIN opens the door.
    password_hash = hash_password(data.password or secrets.token_urlsafe(32))
    typed_pin = str(data.pin or "").strip()
    pin = validate_pin(typed_pin) if typed_pin else new_pin()  # blank → issue one so the admin can hand it over
    pin_hash = hash_pin(pin)
    personnel_number = normalize_personnel_number(data.personnel_number)
    job_title = normalize_job_text(data.job_title, JOB_TITLE)
    qualification = normalize_job_text(data.qualification, QUALIFICATION)
    now = as_utc(now)
    with uow:
        if uow.identity.identifiers_taken({login, email}):
            raise StaffConflict("Сотрудник с таким логином или e-mail уже есть.")
        user = IdentityUser(
            id=uuid4(), login=login, email=email, name=name, department=department, role=role, site=site,
            active=True, personnel_number=personnel_number, job_title=job_title, qualification=qualification,
            job_since=data.job_since, manager_id=data.manager_id,
        )
        users = uow.identity.all_users()
        _check_personnel(uow, user, None, users)
        # The role's sections come from the «Доступы» matrix row of the role; nothing is seeded.
        uow.identity.add_user(user, password_hash, {}, pin_hash)
        details = {
            "login": login, "email": email, "name": name, "department": department, "role": role,
            "site": site or "", "pin": "назначен",
        }
        personnel = {
            "personnel_number": personnel_number, "job_title": job_title, "qualification": qualification,
            "job_since": _shown(data.job_since), "manager": _person(data.manager_id, users),
        }
        details.update({key: value for key, value in personnel.items() if value})
        _audit(uow, user.id, "identity.user_created", actor, now, details)
        uow.commit()
        return CreatedEmployee(user=user, pin=pin)


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
    pin_hash = hash_pin(change.pin) if str(change.pin or "").strip() else None
    personnel: dict[str, object] = {}
    if change.personnel_number is not KEEP:
        personnel["personnel_number"] = normalize_personnel_number(change.personnel_number)
    if change.job_title is not KEEP:
        personnel["job_title"] = normalize_job_text(change.job_title, JOB_TITLE)
    if change.qualification is not KEEP:
        personnel["qualification"] = normalize_job_text(change.qualification, QUALIFICATION)
    if change.job_since is not KEEP:
        personnel["job_since"] = change.job_since
    if change.manager_id is not KEEP:
        personnel["manager_id"] = change.manager_id
    now = as_utc(now)
    with uow:
        current = uow.identity.user_for_update(user_id)
        if current is None:
            raise KeyError("Сотрудник не найден.")
        updated = replace(
            current, name=name, department=department, role=role, site=site, active=bool(change.active), **personnel,
        )
        if current.role == "admin" and current.active and (updated.role != "admin" or not updated.active):
            ensure_access_keeper(uow, user=updated)
        users = uow.identity.all_users()
        _check_personnel(uow, updated, current, users)
        changes = {
            key: [_shown(getattr(current, key)), _shown(getattr(updated, key))]
            for key in ("name", "department", "role", "site", "active", "personnel_number", "job_title",
                        "qualification", "job_since")
            if getattr(current, key) != getattr(updated, key)
        }
        if current.manager_id != updated.manager_id:
            changes["manager"] = [_person(current.manager_id, users), _person(updated.manager_id, users)]
        if not changes and password_hash is None and pin_hash is None:
            return current
        uow.identity.update_user(updated)
        if password_hash is not None:
            uow.identity.set_password(user_id, password_hash)
        # portalStaffSave: a new PIN replaces the hash; open sessions stay, as in SUPPLYMAP.
        if pin_hash is not None:
            uow.identity.set_pin(user_id, pin_hash)
        # A new password or a lost login ends every open session of the employee.
        if password_hash is not None or (current.active and not updated.active):
            uow.identity.revoke_user_sessions(user_id, now)
        details = {"login": current.login, "changes": json.dumps(changes, ensure_ascii=False)}
        if password_hash is not None:
            details["password"] = "сменён"
        if pin_hash is not None:
            details["pin"] = "назначен"
        _audit(uow, user_id, "identity.user_updated", actor, now, details)
        uow.commit()
        return updated


@dataclass(frozen=True)
class RemovedEmployee:
    user: IdentityUser
    deleted: bool  # True — erased; False — has history, so switched off and hidden (the id keeps owning it)


def remove_employee(user_id: UUID, actor: Actor, now: datetime, uow: UnitOfWork) -> RemovedEmployee:
    """«Удалить» on «Сотрудники» (owner 2026-10-01 «функцию по удалению сотрудника»). An employee nobody's work points
    at (never assigned, never moved stock, only signed in at most) is erased with their login. One with history is
    switched off instead — login closed, sessions revoked, gone from every pick list — because operations, ЖУК, stock
    moves and the audit still name them. Deleting a switched-off employee with history changes nothing (409)."""
    if user_id == actor.id:
        raise StaffConflict("Себя удалить нельзя — это сделает другой администратор.")
    now = as_utc(now)
    with uow:
        current = uow.identity.user_for_update(user_id)
        if current is None:
            raise KeyError("Сотрудник не найден.")
        if current.role == "admin" and current.active:
            ensure_access_keeper(uow, user=replace(current, active=False))
        details = {"login": current.login, "name": current.name}
        if uow.identity.user_history(user_id) == 0:
            uow.identity.delete_user(user_id)
            _audit(uow, user_id, "identity.user_deleted", actor, now, details)
            uow.commit()
            return RemovedEmployee(user=replace(current, active=False), deleted=True)
        if not current.active:
            raise StaffConflict(
                f"У сотрудника {current.name} есть история работы, поэтому стереть его нельзя; он уже отключён и скрыт."
            )
        updated = replace(current, active=False)
        uow.identity.update_user(updated)
        uow.identity.revoke_user_sessions(user_id, now)
        _audit(uow, user_id, "identity.user_updated", actor, now, {
            **details, "changes": json.dumps({"active": [True, False]}, ensure_ascii=False), "reason": "удаление",
        })
        uow.commit()
        return RemovedEmployee(user=updated, deleted=False)


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
        # The head moves with the name; renaming into a department that has its own head keeps that head.
        heads = uow.identity.department_heads()
        carried = {new: head for name, head in heads.items() if name.casefold() == old.casefold()}
        carried.update({name: head for name, head in heads.items() if name.casefold() != old.casefold()})
        uow.identity.replace_departments(departments, carried)
        _audit(uow, DEPARTMENTS_AGGREGATE, "identity.department_renamed", actor, now, {
            "source": old, "target": new, "users": str(len(moved)),
        })
        uow.commit()
        return departments, len(moved)


@dataclass(frozen=True)
class DepartmentHeads:
    departments: list[str]
    heads: dict[str, UUID]  # listed department → its head


def set_department_head(
    department: str, head_id: UUID | None, actor: Actor, now: datetime, uow: UnitOfWork,
) -> DepartmentHeads:
    """Руководитель отдела: one employee per department, or none. A department only people have (never saved) is
    written into the saved list together with the head, as «Сохранить отделы» would."""
    wanted = normalize_department(department)
    if not wanted:
        raise IdentityValidationError("Укажите отдел.")
    now = as_utc(now)
    with uow:
        users = uow.identity.all_users()
        departments = department_list(uow.identity.saved_departments(), [user.department for user in users])
        listed = next((name for name in departments if name.casefold() == wanted.casefold()), None)
        if listed is None:
            raise KeyError(f"Отдела «{wanted}» нет.")
        stored = uow.identity.department_heads()
        current = _heads_for(departments, stored)
        previous = current.get(listed)
        if head_id == previous:
            return DepartmentHeads(departments=departments, heads=current)
        if head_id is not None:
            head = next((user for user in users if user.id == head_id), None)
            if head is None:
                raise IdentityValidationError("Руководитель не найден в справочнике сотрудников.")
            if not head.active:
                raise IdentityValidationError(f"Сотрудник «{head.name}» отключён — выберите другого руководителя.")
        heads = {name: head for name, head in stored.items() if name.casefold() != listed.casefold()}
        if head_id is not None:
            heads[listed] = head_id
        uow.identity.replace_departments(departments, heads)
        _audit(uow, DEPARTMENTS_AGGREGATE, "identity.department_head_set", actor, now, {
            "department": listed, "head": _person(head_id, users), "previous": _person(previous, users),
        })
        uow.commit()
        return DepartmentHeads(departments=departments, heads=_heads_for(departments, heads))


@dataclass(frozen=True)
class PersonnelProfile:
    """The signed-in employee with their руководитель and the head of their department (for the profile and the card)."""

    user: IdentityUser
    manager: IdentityUser | None
    department_head: IdentityUser | None


def personnel_profile(user_id: UUID, uow: UnitOfWork) -> PersonnelProfile:
    with uow:
        users = {user.id: user for user in uow.identity.all_users()}
        user = users.get(user_id)
        if user is None or not user.active:
            raise KeyError("Сотрудник не найден.")
        department = user.department.casefold()
        head_id = next(
            (head for name, head in uow.identity.department_heads().items() if department and name.casefold() == department),
            None,
        )
        return PersonnelProfile(
            user=user,
            manager=users.get(user.manager_id) if user.manager_id is not None else None,
            department_head=users.get(head_id) if head_id is not None else None,
        )
