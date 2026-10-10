"""«Отпуска и заявления» в «Моей карточке» по HTTP (identity/leave.py + календарь цеха, workflows/leave_calendar.py).

Every signed-in employee files and follows their own requests; who may decide is the route's rule (the manager, the
substitute, «Кадры»), checked by the use case — not a section gate, so the inbox works for any manager whatever their
level. An API token never reaches these routes (`authenticated_principal` refuses it)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.identity.application import AuthenticationRateLimited
from app.modules.identity.leave import (
    LeaveConflict,
    LeaveError,
    LeaveForbidden,
    LeaveNotFound,
    LeaveRequest,
    create_leave,
    decide_leave,
    leave_overview,
    leave_view,
    sign_leave,
    staff_of,
    update_leave,
    waiting_count,
    withdraw_leave,
    year_balance,
)
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.modules.identity.api import _client_key as client_key  # the login's counters: a signature is a login check

from .leave_calendar import sync_after_decision

router = APIRouter(prefix="/api/leave-requests", tags=["leave-requests"])


class LeaveInput(BaseModel):
    kind: str = Field(min_length=1, max_length=16)
    from_date: date
    to_date: date
    comment: str = Field(default="", max_length=1000)


class LeaveEdit(LeaveInput):
    version: int = Field(ge=1)


class SignInput(BaseModel):
    version: int = Field(ge=1)
    secret: str = Field(min_length=1, max_length=128)


class DecisionInput(SignInput):
    approve: bool
    comment: str = Field(default="", max_length=1000)


class WithdrawInput(BaseModel):
    version: int = Field(ge=1)
    secret: str = Field(default="", max_length=128)
    comment: str = Field(default="", max_length=1000)


def _factory(request: Request, name: str) -> Callable[[], object] | None:
    factory = getattr(request.app.state, name, None)
    return factory if callable(factory) else None


def _identity(request: Request) -> Callable[[], object]:
    factory = _factory(request, "identity_uow_factory")
    if factory is None:
        raise HTTPException(status_code=503, detail="Identity ERP не настроен")
    return factory


def _run(action: Callable[[], LeaveRequest]) -> LeaveRequest:
    try:
        return action()
    except LeaveNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LeaveForbidden as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LeaveConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (StaleDataError, IntegrityError) as error:
        raise HTTPException(status_code=409, detail="Заявление уже изменилось — обновите страницу.") from error
    except AuthenticationRateLimited as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except LeaveError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _view(request: Request, item: LeaveRequest, principal: Principal, now: datetime) -> dict:
    factory = _identity(request)
    with factory() as uow:
        staff = staff_of(uow)
    return leave_view(item, staff, principal.actor.id, now.date())


@router.get("")
def overview(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    """Own requests with the year's vacation balance, the requests waiting for this person, and — with «Кадры» edit —
    the journal of every request but drafts."""
    user_id = principal.actor.id
    result = leave_overview(user_id, _identity(request)())
    today = now.date()
    return {
        "today": today.isoformat(),
        "balance": year_balance(result.own, today.year),
        "own": [leave_view(item, result.staff, user_id, today) for item in result.own],
        "inbox": [leave_view(item, result.staff, user_id, today) for item in result.inbox],
        "journal": None if result.all is None else [
            leave_view(item, result.staff, user_id, today) for item in result.all
        ],
        "hr": user_id in result.staff.hr_ids,
    }


@router.get("/waiting")
def waiting(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
) -> dict:
    """The badge: how many requests wait for this person's decision."""
    return {"count": waiting_count(principal.actor.id, _identity(request)())}


@router.post("", status_code=201)
def create(
    payload: LeaveInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    item = _run(lambda: create_leave(
        principal.actor, payload.kind, payload.from_date, payload.to_date, payload.comment, now, _identity(request)(),
    ))
    return _view(request, item, principal, now)


@router.put("/{request_id}")
def edit(
    request_id: UUID,
    payload: LeaveEdit,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    item = _run(lambda: update_leave(
        request_id, payload.version, principal.actor, payload.kind, payload.from_date, payload.to_date,
        payload.comment, now, _identity(request)(),
    ))
    return _view(request, item, principal, now)


@router.post("/{request_id}/sign")
def sign(
    request_id: UUID,
    payload: SignInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    item = _run(lambda: sign_leave(
        request_id, payload.version, principal.actor, payload.secret, now, _identity(request)(),
        client_key=client_key(request),
    ))
    return _view(request, item, principal, now)


@router.post("/{request_id}/decision")
def decide(
    request_id: UUID,
    payload: DecisionInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    item = _run(lambda: decide_leave(
        request_id, payload.version, principal.actor, payload.approve, payload.comment, payload.secret, now,
        _identity(request)(), client_key=client_key(request),
    ))
    sync_after_decision(_identity(request), _factory(request, "production_uow_factory"), now, principal.actor)
    return _view(request, item, principal, now)


@router.post("/{request_id}/withdraw")
def withdraw(
    request_id: UUID,
    payload: WithdrawInput,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    item = _run(lambda: withdraw_leave(
        request_id, payload.version, principal.actor, payload.secret, payload.comment, now, _identity(request)(),
        client_key=client_key(request),
    ))
    sync_after_decision(_identity(request), _factory(request, "production_uow_factory"), now, principal.actor)
    return _view(request, item, principal, now)
