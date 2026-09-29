"""Portal inbox plus restricted audit and outbox diagnostics."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal,
    authenticated_principal,
    operations_view_principal,
)

from .application import MarkReadCommand, UnitOfWork, mark_notifications_read


router = APIRouter(tags=["operations"])


class ReadPayload(BaseModel):
    ids: list[UUID] = Field(default_factory=list, max_length=100)


def operations_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "operations_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _notification(item) -> dict:
    return {
        "id": str(item.id),
        "created_at": item.created_at.isoformat(),
        "type": item.kind,
        "reference": item.reference,
        "title": item.title,
        "body": item.body,
        "read": item.read_at is not None,
    }


@router.get("/api/notifications")
def notifications(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
) -> dict:
    with uow_factory() as uow:
        rows = uow.notifications.list_for(principal.email, limit)
    return {
        "notifications": [_notification(item) for item in rows],
        "unread": sum(item.read_at is None for item in rows),
    }


@router.get("/api/notifications/unread")
def unread(
    principal: Annotated[Principal, Depends(authenticated_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
) -> dict:
    """The bell on every screen (PortalApp.html paintBell_): only the count, polled once a minute."""
    with uow_factory() as uow:
        return {"unread": uow.notifications.unread_count(principal.email)}


@router.post("/api/notifications/read")
def mark_read(
    payload: ReadPayload,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
) -> dict:
    unread = mark_notifications_read(
        MarkReadCommand(principal.email, tuple(payload.ids), now),
        uow_factory(),
    )
    return {"unread": unread}


@router.get("/api/operations/audit")
def audit(
    _principal: Annotated[Principal, Depends(operations_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
    event_type: Annotated[str, Query(max_length=128)] = "",
    aggregate_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    with uow_factory() as uow:
        rows = uow.operations.audit(
            event_type=event_type.strip(),
            aggregate_id=aggregate_id,
            limit=limit,
        )
    return {"events": [{
        "id": str(row["id"]),
        "aggregate_id": str(row["aggregate_id"]),
        "event_type": row["event_type"],
        "actor": row["actor"],
        "department": row["department"],
        "occurred_at": row["occurred_at"].isoformat(),
        "details": row["details"],
    } for row in rows]}


@router.get("/api/operations/outbox")
def outbox(
    _principal: Annotated[Principal, Depends(operations_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(operations_uow_factory)],
) -> dict:
    with uow_factory() as uow:
        health = uow.operations.outbox_health()
    return {
        **{key: health[key] for key in ("total", "published", "pending", "dead")},
        "failures": [{
            "id": str(row["id"]),
            "topic": row["topic"],
            "attempts": row["attempts"],
            "error": row["error"],
            "dead": row["dead"],
        } for row in health["failures"]],
    }
