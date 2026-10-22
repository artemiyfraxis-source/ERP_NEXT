"""Shared HTTP plumbing of the purchasing hybrid: unit-of-work factories and the admin gate."""

from __future__ import annotations

from collections.abc import Callable

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.shared.http_auth import Principal, authenticated_principal


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


def admin_principal(principal: Annotated[Principal, Depends(authenticated_principal)]) -> Principal:
    """Purchasing settings change: superadmin / admin only, no tokens."""
    if principal.service or not principal.full_access:
        raise HTTPException(status_code=403, detail="Настройки закупки меняет администратор.")
    return principal
