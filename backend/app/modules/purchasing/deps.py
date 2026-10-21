"""Shared HTTP plumbing of the purchasing hybrid: unit-of-work factories and the option gate."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request

from app.shared import stage_options


def purchasing_uow_factory(request: Request) -> Callable:
    factory = getattr(request.app.state, "purchasing_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def procurement_uow_factory(request: Request) -> Callable:
    factory = getattr(request.app.state, "procurement_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def optional_factory(request: Request, name: str) -> Callable | None:
    """A cross-module unit-of-work factory that may be absent (stand without the module): None then."""
    factory = getattr(request.app.state, name, None)
    return factory if callable(factory) else None


def warehouse_uow_factory(request: Request) -> Callable | None:
    return optional_factory(request, "warehouse_uow_factory")


def receiving_uow_factory(request: Request) -> Callable | None:
    return optional_factory(request, "receiving_uow_factory")


def require_option(*keys: str) -> None:
    """404 unless at least one of the stage options is on — off means the feature does not exist."""
    if not any(stage_options.enabled(key) for key in keys):
        raise HTTPException(status_code=404, detail="Опция выключена")
