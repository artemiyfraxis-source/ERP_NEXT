"""Native Identity HTTP contract using secure same-origin cookies."""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.http_auth import access_admin_principal, staffdir_principal

from .application import (
    API_TOKEN_MAX_DAYS,
    API_TOKEN_NAME_MAX,
    API_TOKEN_SCOPES,
    ApiToken,
    AuthenticationError,
    AuthenticationRateLimited,
    LoginResult,
    PinRefused,
    PinReveal,
    UnitOfWork,
    change_own_password,
    issue_api_token,
    list_api_tokens,
    login,
    login_staff,
    login_with_pin,
    logout,
    deactivate_user,
    release_login_locks,
    revoke_api_token,
    role_label,
    toggle_admin_mode,
)
from .access import access_overview, save_level_matrix, save_user_access
from .domain import (
    FULL_ACCESS_LEVELS,
    FULL_LEVEL_SECTIONS,
    LEVEL_LABELS,
    LEVEL_SECTION_LABELS,
    LEVEL_SEED,
    ROLE_LABELS,
    WORK_SITES,
    AccessMode,
    IdentityUser,
    IdentityValidationError,
    secret_hash,
)
from .staff import (
    PERSONNEL_FIELDS,
    WORK_FLAGS,
    EmployeeChange,
    NewEmployee,
    StaffConflict,
    StaffForbidden,
    create_employee,
    personnel_profile,
    pin_assigned,
    remove_employee,
    rename_department,
    save_departments,
    set_department_head,
    staff_directory,
    update_employee,
)
from .level_migration import level_for_role
from .middleware import cookie_names, security_cookie


router = APIRouter(prefix="/api/auth", tags=["identity"])


class LoginInput(BaseModel):
    login: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


def identity_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "identity_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="Identity ERP не настроен")
    return factory


def _principal_result(principal: Principal) -> dict:
    return {
        "user": {
            "id": str(principal.actor.id),
            "email": principal.email,
            "name": principal.actor.name,
            "department": principal.actor.department,
        },
        # `roles` holds the access level key (legacy name, one release); `access_level` is the same key.
        "roles": sorted(principal.roles),
        "access_level": principal.level,
        "permissions": principal.permissions,
        # portalState_: roleLabel («· режим» in the admin mode) — the level's name now, canAdminMode, adminMode.
        "role_label": role_label(principal),
        "can_admin_mode": principal.can_admin_mode,
        "admin_mode": principal.admin_mode,
    }


def _client_key(request: Request) -> str:
    client_address = request.client.host if request.client else "unknown"
    return f"client:{secret_hash(client_address)}"


def _start_session(request: Request, response: Response, result: LoginResult) -> dict:
    secure = bool(getattr(request.app.state, "cookie_secure", True))
    session_cookie, csrf_cookie = cookie_names(secure)
    response.set_cookie(
        session_cookie,
        result.session_token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        csrf_cookie,
        result.csrf_token,
        max_age=30 * 24 * 60 * 60,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return _principal_result(result.principal)


@router.post("/login")
def login_route(
    payload: LoginInput,
    request: Request,
    response: Response,
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        result = login(
            payload.login,
            payload.password,
            now,
            uow_factory(),
            client_key=_client_key(request),
        )
    except AuthenticationRateLimited as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except (AuthenticationError, IdentityValidationError) as error:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль") from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Повторите попытку входа") from error
    return _start_session(request, response, result)


# ── Вход по PIN (SUPPLYMAP Portal.gs portalStaffLite_ / portalLogin / portalRevealPin_) ────────────────


@router.get("/login-staff")
def login_staff_route(
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    """The login list before sign-in: active employees — id, name, department. No e-mails (portalStaffLite_)."""
    return {
        "staff": [
            {"id": str(user.id), "name": user.name, "department": user.department}
            for user in login_staff(uow_factory())
        ]
    }


class PinLoginInput(BaseModel):
    who: str = Field(min_length=1, max_length=255)  # the employee id from the list, or an e-mail / login
    pin: str = Field(default="", max_length=128)  # empty → «узнать PIN» on the first sign-in


@router.post("/pin-login")
def pin_login_route(
    payload: PinLoginInput,
    request: Request,
    response: Response,
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        result = login_with_pin(payload.who, payload.pin, now, uow_factory(), client_key=_client_key(request))
    except AuthenticationRateLimited as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except PinRefused as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AuthenticationError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Повторите попытку входа") from error
    if isinstance(result, PinReveal):
        # Shown once: the screen asks to write it down and fills the PIN field.
        return {"reveal": True, "name": result.name, "pin": result.pin}
    return _start_session(request, response, result)


# ── «Режим админа» (Portal.gs portalToggleAdmin) ─────────────────────────────────────────────────────


class AdminModeInput(BaseModel):
    on: bool


@router.post("/admin-mode")
def admin_mode_route(
    payload: AdminModeInput,
    request: Request,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    session = getattr(request.state, "identity_session", None)
    if session is None:
        raise HTTPException(status_code=401, detail="Требуется вход в ERP")
    try:
        updated = toggle_admin_mode(session, principal, payload.on, now, uow_factory())
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except AuthenticationError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    return _principal_result(updated)


@router.get("/me")
def me(principal: Annotated[Principal, Depends(authenticated_principal)]) -> dict:
    return _principal_result(principal)


class PasswordChangeInput(BaseModel):
    current: str = Field(min_length=1, max_length=128)
    new: str = Field(min_length=1, max_length=128)


@router.get("/profile")
def profile_route(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    """Шапка / профиль: ФИО, e-mail, уровень доступа, отдел, место работы (portalState_) + кадровые поля «Личной
    карточки». `role` / `role_label` — legacy: the old role key, and the level's name."""
    try:
        profile = personnel_profile(principal.actor.id, uow_factory())
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Сотрудник не найден.") from error
    user = profile.user
    return {
        "name": user.name, "login": user.login, "email": user.email, "department": user.department,
        "role": user.role, "role_label": _level_label(user),
        "access_level": _level(user), "access_level_label": _level_label(user),
        "site": user.site or "", "site_label": WORK_SITES.get(user.site or "", "все площадки"),
        **_personnel(user),
        "manager": _person_ref(profile.manager),
        "department_head": _person_ref(profile.department_head),
    }


@router.post("/password", status_code=204)
def change_password_route(
    payload: PasswordChangeInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> None:
    try:
        change_own_password(principal.actor.id, payload.current, payload.new, principal.actor, now, uow_factory())
    except AuthenticationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except IdentityValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/logout", status_code=204)
def logout_route(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> None:
    secure = bool(getattr(request.app.state, "cookie_secure", True))
    session_cookie, csrf_cookie = cookie_names(secure)
    logout(
        security_cookie(request, session_cookie),
        principal.actor,
        now,
        uow_factory(),
    )
    response.delete_cookie(session_cookie, path="/", secure=secure, samesite="strict")
    response.delete_cookie(csrf_cookie, path="/", secure=secure, samesite="strict")


@router.post("/users/{user_id}/deactivate")
def deactivate_user_route(
    user_id: UUID,
    principal: Annotated[Principal, Depends(staffdir_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        user = deactivate_user(user_id, principal.actor, now, uow_factory(), actor_level=principal.level)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except StaffForbidden as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except StaffConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"user_id": str(user.id), "active": user.active}


# ── «Сотрудники» (ID-8): administrator only, every change audited ─────────────────────────────────


class EmployeeCreateInput(BaseModel):
    login: str = Field(default="", max_length=255)
    email: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    department: str = Field(default="", max_length=255)
    # Уровень доступа; the legacy `role` (one release) sets it when the level is not sent.
    access_level: str = Field(default="", max_length=16)
    role: str = Field(default="", max_length=64)
    site: str = Field(default="", max_length=8)
    password: str = Field(default="", max_length=128)  # empty → PIN only
    pin: str = Field(default="", max_length=16)  # 6–8 digits; empty → none yet
    # Кадровые поля «Личной карточки», all optional.
    personnel_number: str = Field(default="", max_length=64)
    job_title: str = Field(default="", max_length=255)
    qualification: str = Field(default="", max_length=255)
    job_since: date | None = None
    manager_id: UUID | None = None
    # «Монтажник (работает в цеху)», «Мастер площадки»; left out / null → from the role (site, department).
    shop_worker: bool | None = None
    site_master: bool | None = None


class EmployeeUpdateInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    department: str = Field(default="", max_length=255)
    # "" — stays as stored; a changed legacy role without a level moves the level with it.
    access_level: str = Field(default="", max_length=16)
    role: str = Field(default="", max_length=64)
    site: str = Field(default="", max_length=8)
    active: bool
    password: str | None = Field(default=None, max_length=128)  # empty → the password stays
    pin: str | None = Field(default=None, max_length=16)  # empty → the PIN stays
    # Кадровые поля: a field left out of the request stays as stored; "" / null clears it.
    personnel_number: str = Field(default="", max_length=64)
    job_title: str = Field(default="", max_length=255)
    qualification: str = Field(default="", max_length=255)
    job_since: date | None = None
    manager_id: UUID | None = None
    # Флаги: left out / null → as stored (one agreeing with the role rule follows a new role); true / false → set.
    shop_worker: bool | None = None
    site_master: bool | None = None


class DepartmentsInput(BaseModel):
    departments: list[str] = Field(max_length=500)


class DepartmentRenameInput(BaseModel):
    source: str = Field(min_length=1, max_length=255)
    target: str = Field(min_length=1, max_length=255)


class DepartmentHeadInput(BaseModel):
    department: str = Field(min_length=1, max_length=255)
    head_id: UUID | None = None  # null → the department has no head


def _personnel(user: IdentityUser) -> dict:
    return {
        "personnel_number": user.personnel_number,
        "job_title": user.job_title,
        "qualification": user.qualification,
        "job_since": user.job_since.isoformat() if user.job_since is not None else None,
        "manager_id": str(user.manager_id) if user.manager_id is not None else None,
    }


def _person_ref(user: IdentityUser | None) -> dict | None:
    return None if user is None else {"id": str(user.id), "name": user.name}


def _level(user: IdentityUser) -> str:
    return user.access_level or level_for_role(user.role)


def _level_label(user: IdentityUser) -> str:
    level = _level(user)
    return LEVEL_LABELS.get(level, level)


def _employee(user: IdentityUser, pin_set: bool = False) -> dict:
    # No password, PIN or hash ever leaves the server; the screen only sets a new one and sees `pin_set`.
    return {
        "id": str(user.id),
        "login": user.login,
        "email": user.email,
        "name": user.name,
        "department": user.department,
        "access_level": _level(user),
        "access_level_label": _level_label(user),
        "role": user.role,  # legacy, one release
        "site": user.site or "",
        "active": user.active,
        "pin_set": pin_set,
        **_personnel(user),
        "shop_worker": user.shop_worker,
        "site_master": user.site_master,
    }


def _heads(heads: dict[str, UUID]) -> dict[str, str]:
    return {department: str(head) for department, head in heads.items()}


def _staff_errors(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error.args[0] if error.args else error))
    if isinstance(error, StaffForbidden):
        return HTTPException(status_code=403, detail=str(error))
    if isinstance(error, StaffConflict):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, IntegrityError):
        if "personnel_number" in str(error.orig):
            return HTTPException(status_code=409, detail="Этот табельный номер уже занят.")
        return HTTPException(status_code=409, detail="Сотрудник с таким логином или e-mail уже есть.")
    return HTTPException(status_code=422, detail=str(error))


_STAFF_ERRORS = (KeyError, StaffForbidden, StaffConflict, IdentityValidationError, IntegrityError)


@router.get("/staff")
def staff_route(
    principal: Annotated[Principal, Depends(staffdir_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    del principal
    directory = staff_directory(uow_factory())
    return {
        "users": [_employee(user, user.id in directory.pin_set) for user in directory.users],
        "departments": directory.departments,
        "department_heads": _heads(directory.department_heads),
        "levels": _levels(),
        "roles": [{"key": key, "label": label} for key, label in ROLE_LABELS.items()],  # legacy, one release
        "sites": [{"code": code, "city": city} for code, city in WORK_SITES.items()],
    }


@router.post("/staff", status_code=201)
def create_employee_route(
    payload: EmployeeCreateInput,
    principal: Annotated[Principal, Depends(staffdir_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        created = create_employee(
            NewEmployee(**payload.model_dump()), principal.actor, now, uow_factory(), actor_level=principal.level,
        )
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    # The PIN is shown once so the administrator can hand it over (owner: «все пины мне … заводить новых сам»).
    return {"user": _employee(created.user, pin_set=True), "pin": created.pin}


@router.put("/staff/{user_id}")
def update_employee_route(
    user_id: UUID,
    payload: EmployeeUpdateInput,
    principal: Annotated[Principal, Depends(staffdir_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    # A personnel field the request did not carry, and a flag it did not set (absent or null), are left out, so
    # EmployeeChange keeps the stored value.
    fields = {
        key: value for key, value in payload.model_dump().items()
        if (key not in PERSONNEL_FIELDS or key in payload.model_fields_set)
        and not (key in WORK_FLAGS and value is None)
    }
    try:
        user = update_employee(
            user_id, EmployeeChange(**fields), principal.actor, now, uow_factory(), actor_level=principal.level,
        )
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"user": _employee(user, pin_assigned(user.id, uow_factory()))}


@router.delete("/staff/{user_id}")
def remove_employee_route(
    user_id: UUID,
    principal: Annotated[Principal, Depends(staffdir_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        removed = remove_employee(user_id, principal.actor, now, uow_factory(), actor_level=principal.level)
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"user": _employee(removed.user), "deleted": removed.deleted}


@router.put("/departments")
def save_departments_route(
    payload: DepartmentsInput,
    principal: Annotated[Principal, Depends(staffdir_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        departments = save_departments(payload.departments, principal.actor, now, uow_factory())
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"departments": departments}


@router.post("/departments/rename")
def rename_department_route(
    payload: DepartmentRenameInput,
    principal: Annotated[Principal, Depends(staffdir_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        departments, moved = rename_department(payload.source, payload.target, principal.actor, now, uow_factory())
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"departments": departments, "users": moved}


@router.put("/departments/head")
def set_department_head_route(
    payload: DepartmentHeadInput,
    principal: Annotated[Principal, Depends(staffdir_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    """Руководитель отдела («Личная карточка»): one employee or none."""
    try:
        result = set_department_head(payload.department, payload.head_id, principal.actor, now, uow_factory())
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"departments": result.departments, "department_heads": _heads(result.heads)}


# ── «Доступы» (NAV-ACCESS): level × section matrix + per-person exceptions, superadmin/admin only, audited ──


def _modes(row: dict[str, AccessMode]) -> dict[str, str]:
    return {section: mode.value for section, mode in row.items()}


def _levels() -> list[dict]:
    return [{"key": key, "label": label, "full": key in FULL_ACCESS_LEVELS} for key, label in LEVEL_LABELS.items()]


class LevelMatrixInput(BaseModel):
    # {level: {section: "none" | "view" | "edit" | ""}}; a level or a cell left out = closed.
    matrix: dict[str, dict[str, str]] = Field(max_length=64)


class UserAccessInput(BaseModel):
    access: dict[str, str] = Field(max_length=64)  # {section: mode}; left out / "" = как у уровня


@router.get("/access")
def access_route(
    principal: Annotated[Principal, Depends(access_admin_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    del principal
    overview = access_overview(uow_factory())
    levels = _levels()
    return {
        "sections": [{"key": key, "label": label} for key, label in LEVEL_SECTION_LABELS.items()],
        "levels": levels,
        "roles": levels,  # legacy name of the rows, one release
        # What an empty row means: superadmin/admin — everything on edit, whatever is stored; any other level — closed.
        "defaults": {
            level: ({section: "edit" for section in FULL_LEVEL_SECTIONS} if level in FULL_ACCESS_LEVELS else {})
            for level in LEVEL_LABELS
        },
        # The plan's default rows (a level nobody holds starts from them).
        "seed": {level: _modes(row) for level, row in LEVEL_SEED.items()},
        "matrix": {level: _modes(row) for level, row in overview.matrix.items()},
        "users": [
            {
                "id": str(user.id), "login": user.login, "name": user.name, "role": user.role,
                "access_level": _level(user), "active": user.active,
                "access": _modes(overview.exceptions.get(user.id, {})),
            }
            for user in overview.users
        ],
    }


def _save_matrix(payload: LevelMatrixInput, principal: Principal, now: datetime, uow_factory) -> dict:
    try:
        matrix = save_level_matrix(payload.matrix, principal.actor, now, uow_factory())
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"matrix": {level: _modes(row) for level, row in matrix.items()}}


@router.put("/access/levels")
def save_level_matrix_route(
    payload: LevelMatrixInput,
    principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    return _save_matrix(payload, principal, now, uow_factory)


@router.put("/access/roles")
def save_level_matrix_legacy_route(
    payload: LevelMatrixInput,
    principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    """The old path of the same save (the screen calls it until it moves to /access/levels); the rows are levels."""
    return _save_matrix(payload, principal, now, uow_factory)


@router.put("/access/users/{user_id}")
def save_user_access_route(
    user_id: UUID,
    payload: UserAccessInput,
    principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        access = save_user_access(user_id, payload.access, principal.actor, now, uow_factory())
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"user_id": str(user_id), "access": _modes(access)}


# ── Блокировки входа: the administrator's emergency path (audit U09) ─────────────────────────────────


class LoginLockReleaseInput(BaseModel):
    login: str | None = Field(default=None, min_length=1, max_length=255)  # none — every lock


@router.post("/login-locks/release")
def release_login_locks_route(
    payload: LoginLockReleaseInput,
    principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    """Lift the login lock of one account, or every login lock, before its 10 minutes end. Journalled."""
    released = release_login_locks(principal.actor, now, uow_factory(), login=payload.login)
    return {"released": released}


# ── Токены API: administrator only; the secret is in the create answer once and nowhere else ──────────


class ApiTokenInput(BaseModel):
    name: str = Field(min_length=1, max_length=API_TOKEN_NAME_MAX)
    scopes: list[str] = Field(min_length=1, max_length=len(API_TOKEN_SCOPES))
    days: int = Field(default=90, ge=1, le=API_TOKEN_MAX_DAYS)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _api_token_result(token: ApiToken, now: datetime) -> dict:
    return {
        "id": str(token.id),
        "name": token.name,
        "scopes": sorted(token.scopes),
        "scope_labels": [API_TOKEN_SCOPES.get(scope, scope) for scope in sorted(token.scopes)],
        "status": token.status(now),
        "created_at": _iso(token.created_at),
        "created_by": token.created_by_name,
        "expires_at": _iso(token.expires_at),
        "last_used_at": _iso(token.last_used_at),
        "revoked_at": _iso(token.revoked_at),
        "revoked_by": token.revoked_by_name,
    }


@router.get("/api-tokens")
def api_tokens_route(
    _principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    return {
        "tokens": [_api_token_result(token, now) for token in list_api_tokens(uow_factory())],
        "scopes": API_TOKEN_SCOPES,
    }


@router.post("/api-tokens", status_code=201)
def issue_api_token_route(
    payload: ApiTokenInput,
    principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        issued = issue_api_token(payload.name, payload.scopes, payload.days, principal.actor, now, uow_factory())
    except IdentityValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # `token` is shown once: only its hash is stored.
    return {**_api_token_result(issued.token, now), "token": issued.secret}


@router.post("/api-tokens/{token_id}/revoke")
def revoke_api_token_route(
    token_id: UUID,
    principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        token = revoke_api_token(token_id, principal.actor, now, uow_factory())
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Токен не найден.") from error
    return _api_token_result(token, now)
