"""«Склад» → «Распознавание УПД»: the model API that reads paper УПД scans (owner 26.10.2026). Administrator only
(`access_admin_principal`, superadmin / admin) — the key is a paid account's, and the server's .env is root-only, so
it is set here instead.

- `GET /api/stock/receipts/llm-settings` → `{base_url, model, fallback_model, effort, key_set, key_hint, env,
  defaults, effective, updated_at, updated_by}`: the saved values ('' — the default), `key_hint` «sk-ib…AgnD» (the key
  itself is never answered), `env` — the fields the server env sets (they win over the saved ones).
- `PUT` the same four fields + `api_key` (absent / null — kept, a text — replaced) + `clear_key` → the view.
- `POST /api/stock/receipts/llm-settings/check` → `{ok, models: [{model, ok, seconds, error}]}` — a one-token call to
  each model with what is in force now.
Included before the receipts router: `/receipts/{receipt_id}` would take «llm-settings» for an id."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, access_admin_principal

from . import receipt_llm
from .api import warehouse_uow_factory
from .application import UnitOfWork
from .receipts_application import save_upd_llm_settings, upd_llm_settings

router = APIRouter(prefix="/api/stock", tags=["warehouse"])
UowFactory = Annotated[Callable[[], UnitOfWork], Depends(warehouse_uow_factory)]
EDITABLE = ("base_url", "model", "fallback_model", "effort")


class LlmSettingsInput(BaseModel):
    base_url: str = Field(default="", max_length=512)
    model: str = Field(default="", max_length=128)
    fallback_model: str = Field(default="", max_length=128)
    effort: str = Field(default="", max_length=16)
    api_key: str | None = Field(default=None, max_length=512)
    clear_key: bool = False


def _view(saved: dict) -> dict:
    config = receipt_llm.llm_config(saved)
    defaults = receipt_llm.LlmConfig()
    updated_at = saved.get("updated_at")
    return {
        **{name: str(saved.get(name) or "") for name in EDITABLE},
        "key_set": bool(config.api_key), "key_hint": receipt_llm.key_hint(config.api_key),
        "env": receipt_llm.env_fields(),
        "defaults": {name: getattr(defaults, name) for name in EDITABLE},
        "effective": {name: getattr(config, name) for name in EDITABLE},
        "efforts": list(receipt_llm.EFFORTS),
        "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
        "updated_by": str(saved.get("updated_by") or ""),
    }


def _values(payload: LlmSettingsInput) -> dict:
    values = {name: " ".join(getattr(payload, name).split()) for name in EDITABLE}
    if values["base_url"] and not values["base_url"].startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="Адрес API начинается с https:// (или http://)")
    if values["effort"] and values["effort"] not in receipt_llm.EFFORTS:
        raise HTTPException(status_code=422, detail="Усилие — minimal, low, medium или high")
    if payload.clear_key:
        values["api_key"] = ""
    elif payload.api_key is not None and payload.api_key.strip():
        if any(char.isspace() for char in payload.api_key.strip()):
            raise HTTPException(status_code=422, detail="В ключе API не бывает пробелов")
        values["api_key"] = payload.api_key.strip()
    return values


@router.get("/receipts/llm-settings")
def llm_settings(
    _principal: Annotated[Principal, Depends(access_admin_principal)], uow_factory: UowFactory,
) -> dict:
    return _view(upd_llm_settings(uow_factory()))


@router.put("/receipts/llm-settings")
def save_llm_settings(
    payload: LlmSettingsInput,
    principal: Annotated[Principal, Depends(access_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: UowFactory,
) -> dict:
    values = _values(payload)
    try:
        saved = save_upd_llm_settings(uow_factory(), values, principal.actor, now)
    except IntegrityError as error:
        raise HTTPException(status_code=422, detail="Настройки распознавания вне допустимых значений") from error
    return _view(saved)


@router.post("/receipts/llm-settings/check")
async def check_llm_settings(
    _principal: Annotated[Principal, Depends(access_admin_principal)], uow_factory: UowFactory,
) -> dict:
    config = receipt_llm.llm_config(await run_in_threadpool(lambda: upd_llm_settings(uow_factory())))
    if not config.ready:
        return {"ok": False, "models": [], "error": "Ключ API не задан"}
    models = await run_in_threadpool(receipt_llm.check, config)
    return {"ok": all(model["ok"] for model in models), "models": models, "error": ""}
