"""Опции стенда по HTTP: список виден любому вошедшему (экраны решают, что показать), переключает администратор."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.shared import stage_options
from app.shared.http_auth import Principal, authenticated_principal, require_full_access

router = APIRouter(prefix="/api/stage-options", tags=["stage-options"])
admin_principal = require_full_access("Опции стенда переключает администратор.")


class Toggle(BaseModel):
    enabled: bool


def _payload() -> dict:
    states = stage_options.states()
    return {
        "available": stage_options.available(),
        "options": [
            {"key": o.key, "block": o.block, "title": o.title, "before": o.before, "after": o.after, "enabled": states[o.key]}
            for o in stage_options.OPTIONS
        ],
    }


@router.get("")
def list_options(_principal: Annotated[Principal, Depends(authenticated_principal)]) -> dict:
    return _payload()


@router.put("/{key}")
def toggle(key: str, payload: Toggle, _principal: Annotated[Principal, Depends(admin_principal)]) -> dict:
    try:
        stage_options.set_enabled(key, payload.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="Нет такой опции") from None
    except stage_options.StageOptionsUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return _payload()
