"""Native Identity HTTP contract using secure same-origin cookies."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.http_auth import access_admin_principal, staffdir_principal

from .application import (
    AuthenticationError,
    AuthenticationRateLimited,
    LoginResult,
    PinRefused,
    PinReveal,
    UnitOfWork,
    change_own_password,
    own_profile,
    login,
    login_staff,
    login_with_pin,
    logout,
    deactivate_user,
    role_label,
    toggle_admin_mode,
)
from .access import access_overview, save_role_matrix, save_user_access
from .domain import (
    ROLE_DEFAULTS,
    ROLE_LABELS,
    SECTION_LABELS,
    WORK_SITES,
    AccessMode,
    IdentityUser,
    IdentityValidationError,
    secret_hash,
)
from .staff import (
    EmployeeChange,
    NewEmployee,
    StaffConflict,
    create_employee,
    pin_assigned,
    rename_department,
    save_departments,
    staff_directory,
    update_employee,
)
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
        "roles": sorted(principal.roles),
        "permissions": principal.permissions,
        # portalState_: roleLabel («· режим» in the admin mode), canAdminMode, adminMode.
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
    """Шапка / профиль: ФИО, e-mail, роль, отдел, место работы (portalState_)."""
    try:
        user = own_profile(principal.actor.id, uow_factory())
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Сотрудник не найден.") from error
    return {
        "name": user.name, "login": user.login, "email": user.email, "department": user.department,
        "role": user.role, "role_label": ROLE_LABELS.get(user.role, user.role),
        "site": user.site or "", "site_label": WORK_SITES.get(user.site or "", "все площадки"),
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
        user = deactivate_user(user_id, principal.actor, now, uow_factory())
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except StaffConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"user_id": str(user.id), "active": user.active}


# ── «Сотрудники» (ID-8): administrator only, every change audited ─────────────────────────────────


class EmployeeCreateInput(BaseModel):
    login: str = Field(default="", max_length=255)
    email: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    department: str = Field(default="", max_length=255)
    role: str = Field(min_length=1, max_length=64)
    site: str = Field(default="", max_length=8)
    password: str = Field(default="", max_length=128)  # empty → PIN only
    pin: str = Field(default="", max_length=16)  # 6–8 digits; empty → none yet


class EmployeeUpdateInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    department: str = Field(default="", max_length=255)
    role: str = Field(min_length=1, max_length=64)
    site: str = Field(default="", max_length=8)
    active: bool
    password: str | None = Field(default=None, max_length=128)  # empty → the password stays
    pin: str | None = Field(default=None, max_length=16)  # empty → the PIN stays


class DepartmentsInput(BaseModel):
    departments: list[str] = Field(max_length=500)


class DepartmentRenameInput(BaseModel):
    source: str = Field(min_length=1, max_length=255)
    target: str = Field(min_length=1, max_length=255)


def _employee(user: IdentityUser, pin_set: bool = False) -> dict:
    # No password, PIN or hash ever leaves the server; the screen only sets a new one and sees `pin_set`.
    return {
        "id": str(user.id),
        "login": user.login,
        "email": user.email,
        "name": user.name,
        "department": user.department,
        "role": user.role,
        "site": user.site or "",
        "active": user.active,
        "pin_set": pin_set,
    }


def _staff_errors(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error.args[0] if error.args else error))
    if isinstance(error, StaffConflict):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, IntegrityError):
        return HTTPException(status_code=409, detail="Сотрудник с таким логином или e-mail уже есть.")
    return HTTPException(status_code=422, detail=str(error))


_STAFF_ERRORS = (KeyError, StaffConflict, IdentityValidationError, IntegrityError)


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
        "roles": [{"key": key, "label": label} for key, label in ROLE_LABELS.items()],
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
        created = create_employee(NewEmployee(**payload.model_dump()), principal.actor, now, uow_factory())
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
    try:
        user = update_employee(user_id, EmployeeChange(**payload.model_dump()), principal.actor, now, uow_factory())
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"user": _employee(user, pin_assigned(user.id, uow_factory()))}


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


# ── «Доступы» (NAV-ACCESS): role × section matrix + per-person exceptions, administrator only, audited ──────


def _modes(row: dict[str, AccessMode]) -> dict[str, str]:
    return {section: mode.value for section, mode in row.items()}


class RoleMatrixInput(BaseModel):
    # {role: {section: "none" | "view" | "edit" | ""}}; a role or a cell left out = «как задумано ролью».
    matrix: dict[str, dict[str, str]] = Field(max_length=64)


class UserAccessInput(BaseModel):
    access: dict[str, str] = Field(max_length=64)  # {section: mode}; left out / "" = как у роли


@router.get("/access")
def access_route(
    principal: Annotated[Principal, Depends(access_admin_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    del principal
    overview = access_overview(uow_factory())
    return {
        "sections": [{"key": key, "label": label} for key, label in SECTION_LABELS.items()],
        "roles": [{"key": key, "label": label} for key, label in ROLE_LABELS.items()],
        "defaults": {role: _modes(ROLE_DEFAULTS.get(role, {})) for role in ROLE_LABELS},
        "matrix": {role: _modes(row) for role, row in overview.matrix.items()},
        "users": [
            {
                "id": str(user.id), "login": user.login, "name": user.name, "role": user.role, "active": user.active,
                "access": _modes(overview.exceptions.get(user.id, {})),
            }
            for user in overview.users
        ],
    }


@router.put("/access/roles")
def save_role_matrix_route(
    payload: RoleMatrixInput,
    principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        matrix = save_role_matrix(payload.matrix, principal.actor, now, uow_factory())
    except _STAFF_ERRORS as error:
        raise _staff_errors(error) from error
    return {"matrix": {role: _modes(row) for role, row in matrix.items()}}


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
