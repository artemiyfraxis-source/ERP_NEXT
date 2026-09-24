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
from app.shared.http_auth import identity_edit_principal

from .application import (
    AuthenticationError,
    AuthenticationRateLimited,
    UnitOfWork,
    login,
    logout,
    deactivate_user,
)
from .domain import IdentityValidationError, secret_hash
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
    }


@router.post("/login")
def login_route(
    payload: LoginInput,
    request: Request,
    response: Response,
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    client_address = request.client.host if request.client else "unknown"
    client_key = f"client:{secret_hash(client_address)}"
    try:
        result = login(
            payload.login,
            payload.password,
            now,
            uow_factory(),
            client_key=client_key,
        )
    except AuthenticationRateLimited as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except (AuthenticationError, IdentityValidationError) as error:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль") from error
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="Повторите попытку входа") from error

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


@router.get("/me")
def me(principal: Annotated[Principal, Depends(authenticated_principal)]) -> dict:
    return _principal_result(principal)


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
    principal: Annotated[Principal, Depends(identity_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    try:
        user = deactivate_user(user_id, principal.actor, now, uow_factory())
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"user_id": str(user.id), "active": user.active}
