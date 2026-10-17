"""«Личная карточка» сотрудника: чтение по HTTP (`app.workflows.employee_card`). Своя карточка открыта любому вошедшему,
чужая — тому, кому открыт раздел «Сотрудники» (`staffdir`, просмотр); роли с полным доступом проходят всегда."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal, require_section

from .employee_card import employee_card
from .worktime import parse_month, worktime

router = APIRouter(prefix="/api/employee-card", tags=["employee-card"])

staffdir_view_principal = require_section("staffdir", "view", detail="Чужая карточка открыта только с доступом к разделу «Сотрудники».")


def _factory(request: Request, name: str):
    factory = getattr(request.app.state, name, None)
    return factory if callable(factory) else None


def _card(user_id: UUID, request: Request, now: datetime) -> dict:
    identity = _factory(request, "identity_uow_factory")
    if identity is None:
        raise HTTPException(status_code=503, detail="Identity ERP не настроен")
    card = employee_card(
        user_id, now, identity=identity, production=_factory(request, "production_uow_factory"),
        techcards=_factory(request, "techcards_uow_factory"), requests=_factory(request, "uow_factory"), operations=_factory(request, "operations_uow_factory"),
    )
    if card is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден.")
    return card


@router.get("/me")
def my_card(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    return _card(principal.actor.id, request, now)


def _worktime(user_id: UUID, month: str | None, request: Request, now: datetime) -> dict:
    identity = _factory(request, "identity_uow_factory")
    if identity is None:
        raise HTTPException(status_code=503, detail="Identity ERP не настроен")
    try:
        first = parse_month(month, now)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    body = worktime(user_id, first, now, identity=identity, production=_factory(request, "production_uow_factory"))
    if body is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден.")
    return body


@router.get("/me/worktime")
def my_worktime(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
    month: Annotated[str | None, Query(description="Месяц ГГГГ-ММ; не задан — текущий")] = None,
) -> dict:
    """Рабочее время: месяц по дням и баланс года (`app.workflows.worktime`)."""
    return _worktime(principal.actor.id, month, request, now)


@router.get("/{user_id}/worktime")
def worktime_of(
    user_id: UUID,
    _principal: Annotated[Principal, Depends(staffdir_view_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
    month: Annotated[str | None, Query(description="Месяц ГГГГ-ММ; не задан — текущий")] = None,
) -> dict:
    return _worktime(user_id, month, request, now)


@router.get("/{user_id}")
def card_of(
    user_id: UUID,
    _principal: Annotated[Principal, Depends(staffdir_view_principal)],
    request: Request,
    now: Annotated[datetime, Depends(moscow_now)],
) -> dict:
    return _card(user_id, request, now)
