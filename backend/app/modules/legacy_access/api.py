"""Issue narrow, short-lived grants for the preserved legacy UI."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.events import AuditEvent
from app.modules.identity.api import identity_uow_factory
from app.modules.identity.application import UnitOfWork

from .grant import issue_legacy_grant


router = APIRouter(prefix="/api/legacy", tags=["legacy-access"])


class LegacyGrantInput(BaseModel):
    scope: Literal["projects", "calculation"]


@router.post("/grant")
def create_grant(
    payload: LegacyGrantInput,
    request: Request,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(identity_uow_factory)],
) -> dict:
    mode = principal.permissions.get(payload.scope)
    if mode not in {"view", "edit"}:
        raise HTTPException(status_code=403, detail="Недостаточно прав для legacy-раздела")
    secret = getattr(request.app.state, "legacy_bridge_secret", "")
    try:
        grant = issue_legacy_grant(secret, principal, payload.scope, mode, now)
    except ValueError as error:
        raise HTTPException(status_code=503, detail="Legacy bridge не настроен") from error
    with uow_factory() as uow:
        uow.audit.add(
            AuditEvent(
                id=uuid4(),
                aggregate_id=principal.actor.id,
                event_type="legacy.grant_issued",
                actor=principal.actor,
                occurred_at=now,
                details={"scope": payload.scope, "mode": mode},
            )
        )
        uow.commit()
    return {
        "grant": grant.token,
        "expires_at": grant.expires_at.isoformat(),
        "scope": payload.scope,
        "mode": mode,
    }
