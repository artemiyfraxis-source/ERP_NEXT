"""Внутренние проекты «ВП-001»."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.shared.events import AuditEvent
from app.shared.http_auth import (
    Principal,
    authenticated_principal,
    procurement_edit_principal,
    procurement_view_principal,
    requests_form_principal,
    section_allowed,
)
from app.workflows import purchasing_internal_projects as port

from . import internal_projects as service
from .deps import purchasing_uow_factory


def internal_view(principal: Annotated[Principal, Depends(authenticated_principal)]) -> Principal:
    """Procurement or the requests sections may read the list (the request form groups by it)."""
    try:
        return procurement_view_principal(principal)
    except HTTPException:
        return requests_form_principal(principal)


def internal_option(request: Request, principal: Annotated[Principal, Depends(internal_view)]) -> None:
    """After the gate: a token or a stranger is refused (401/403), and so is a write by
    someone who holds «Лист закупки» on view only (view-mode contract, ID-6)."""
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not section_allowed(principal, ("procurement",), "edit"):
        raise HTTPException(status_code=403, detail="Раздел «Лист закупки» открыт вам только на просмотр.")


router = APIRouter(
    prefix="/api/purchasing/internal-projects", tags=["purchasing"], dependencies=[Depends(internal_option)],
)

Editor = Annotated[Principal, Depends(procurement_edit_principal)]
Factory = Annotated[Any, Depends(purchasing_uow_factory)]


class ProjectBody(BaseModel):
    name: str = Field(max_length=255)
    purpose: str = Field(max_length=64)
    responsible_id: UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


class ProjectPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, max_length=255)
    purpose: str | None = Field(default=None, max_length=64)
    responsible_id: UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(uow, principal: Principal, project_id: UUID, event: str, now: datetime, **details: str) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=project_id, event_type=f"purchasing.internal_project_{event}", actor=principal.actor,
        occurred_at=now, details=details,
    ))


def _run(operation):
    """Domain refusals → HTTP; a lost numbering race (unique number) is retried once."""
    for attempt in range(2):
        try:
            return operation()
        except service.InternalProjectError as error:
            raise HTTPException(status_code=error.status, detail=str(error)) from error
        except IntegrityError as error:
            if attempt == 1:
                raise HTTPException(status_code=409, detail="Конкурентное изменение: повторите") from error
    raise AssertionError("unreachable")


@router.get("")
def list_projects(factory: Factory) -> dict[str, Any]:
    with factory() as uow:
        return {"projects": port.listing(uow.session)}


@router.post("", status_code=201)
def create_project(body: ProjectBody, principal: Editor, factory: Factory) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        now = _now()
        with factory() as uow:
            row = service.create(uow.session, body.model_dump(), principal.actor.id, now)
            _audit(uow, principal, row.id, "created", now, number=row.number, name=row.name)
            uow.commit()
            return service.serialize(row)

    return _run(operation)


@router.patch("/{project_id}")
def update_project(project_id: UUID, body: ProjectPatch, principal: Editor, factory: Factory) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True, exclude={"version"})

    def operation() -> dict[str, Any]:
        now = _now()
        with factory() as uow:
            row = service.update(uow.session, project_id, body.version, data, principal.actor.id, now)
            _audit(uow, principal, row.id, "updated", now, number=row.number, fields=", ".join(sorted(data)))
            uow.commit()
            return service.serialize(row)

    return _run(operation)


def _close(project_id: UUID, principal: Principal, factory: Any, closed: bool) -> dict[str, Any]:
    def operation() -> dict[str, Any]:
        now = _now()
        with factory() as uow:
            row = service.set_closed(uow.session, project_id, closed, principal.actor.id, now)
            _audit(uow, principal, row.id, "closed" if closed else "reopened", now, number=row.number)
            uow.commit()
            return service.serialize(row)

    return _run(operation)


@router.post("/{project_id}/close")
def close_project(project_id: UUID, principal: Editor, factory: Factory) -> dict[str, Any]:
    return _close(project_id, principal, factory, True)


@router.post("/{project_id}/reopen")
def reopen_project(project_id: UUID, principal: Editor, factory: Factory) -> dict[str, Any]:
    return _close(project_id, principal, factory, False)
