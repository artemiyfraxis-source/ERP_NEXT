"""«Настройки закупки» — gate, VAT, waits (stage hybrid)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, procurement_view_principal
from app.workflows.stage_options_api import admin_principal

from . import settings
from .deps import purchasing_uow_factory, require_option

router = APIRouter(prefix="/api/purchasing/settings", tags=["purchasing"])

OPTIONS = ("procurement.rfq", "references.suppliers", "references.items")
# One aggregate for every settings change in the audit log.
SETTINGS_AGGREGATE_ID = UUID("00000000-0000-0000-0000-0000000000a1")


class SettingsChange(BaseModel):
    changes: dict[str, Any]


def _json(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value


def _rows(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "key": s.key, "label": s.label, "hint": s.hint, "kind": s.kind, "value": _json(values[s.key]),
            "default": _json(s.default), "minimum": _json(s.minimum), "maximum": _json(s.maximum),
        }
        for s in settings.SETTINGS
    ]


@router.get("")
def list_settings(
    _principal: Annotated[Principal, Depends(procurement_view_principal)],
    factory: Annotated[Any, Depends(purchasing_uow_factory)],
) -> list[dict[str, Any]]:
    require_option(*OPTIONS)
    with factory() as uow:
        return _rows(settings.load(uow.session))


@router.put("")
def save_settings(
    payload: SettingsChange,
    principal: Annotated[Principal, Depends(admin_principal)],
    factory: Annotated[Any, Depends(purchasing_uow_factory)],
) -> list[dict[str, Any]]:
    require_option(*OPTIONS)
    now = datetime.now(timezone.utc)
    with factory() as uow:
        before = settings.load(uow.session)
        try:
            after = settings.save(uow.session, payload.changes, principal.actor.id, now)
        except settings.InvalidSetting as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        changed = {key: f"{_json(before[key])} → {_json(after[key])}" for key in payload.changes if before[key] != after[key]}
        if changed:
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=SETTINGS_AGGREGATE_ID, event_type="purchasing.settings_changed",
                actor=principal.actor, occurred_at=now, details=changed,
            ))
        uow.commit()
        return _rows(after)
