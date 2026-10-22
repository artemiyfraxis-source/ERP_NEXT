"""Справочник поставщиков."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.shared.events import AuditEvent
from app.shared.http_auth import Principal, procurement_edit_principal, procurement_view_principal
from app.workflows import purchasing_procurement as port

from . import suppliers
from .deps import procurement_uow_factory, purchasing_uow_factory
from .infrastructure import SupplierRow

router = APIRouter(prefix="/api/purchasing/suppliers", tags=["purchasing"])

Editor = Annotated[Principal, Depends(procurement_edit_principal)]
Viewer = Annotated[Principal, Depends(procurement_view_principal)]
Factory = Annotated[Any, Depends(purchasing_uow_factory)]
ProcurementFactory = Annotated[Any, Depends(procurement_uow_factory)]


class SupplierBody(BaseModel):
    name: str
    inn: str | None = None
    categories: list[str] = []
    is_key: bool = False
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    site: str | None = None
    domains: list[str] = []
    notes: str | None = None


class SupplierPatch(BaseModel):
    version: int
    name: str | None = None
    inn: str | None = None
    categories: list[str] | None = None
    is_key: bool | None = None
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    site: str | None = None
    domains: list[str] | None = None
    notes: str | None = None


class NameBody(BaseModel):
    name: str
    supplier_id: UUID


class ImportBody(BaseModel):
    dry_run: bool = True


def _refuse(error: suppliers.SupplierError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=str(error))


def _audit(uow, principal: Principal, supplier_id: UUID, event: str, now: datetime, **details: str) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=supplier_id, event_type=f"purchasing.supplier_{event}", actor=principal.actor,
        occurred_at=now, details=details,
    ))


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("")
def list_suppliers(
    request: Request,
    _principal: Viewer,
    factory: Factory,
    q: str = "",
    category: str | None = None,
    key: bool = False,
    archived: bool = False,
) -> list[dict[str, Any]]:
    with factory() as uow:
        rows = [
            row for row in uow.session.scalars(select(SupplierRow).order_by(SupplierRow.name_key))
            if (row.archived_at is not None) == archived
            and (not key or row.is_key)
            and (not category or category in (row.categories or []))
            and (not q.strip() or suppliers.matches_search(row, q))
        ]
        totals: dict[UUID, tuple[int, Any]] = {}
        procurement = getattr(request.app.state, "procurement_uow_factory", None)
        if rows and callable(procurement):
            totals = suppliers.list_totals(port.supplier_lines(procurement), suppliers.supplier_by_key(uow.session))
        result = []
        for row in rows:
            count, total = totals.get(row.id, (0, 0))
            result.append({**suppliers.serialize(row), "purchases_count": count, "purchases_sum": str(total)})
        return result


@router.post("/import-from-procurement")
def import_from_procurement(
    request: Request, body: ImportBody, principal: Editor, factory: Factory, procurement: ProcurementFactory,
) -> dict[str, Any]:
    lines = port.supplier_lines(procurement)
    domains = port.domain_map(getattr(request.app.state, "procurement_supplier_directory", None))
    now = _now()
    with factory() as uow:
        plan = suppliers.plan_import(uow.session, port.supplier_texts(lines), domains)
        if body.dry_run:
            return {**plan, "dry_run": True}
        outcome = suppliers.apply_import(uow.session, plan, principal.actor.id, now)
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=uuid4(), event_type="purchasing.suppliers_imported", actor=principal.actor,
            occurred_at=now, details={name: str(value) for name, value in outcome.items()},
        ))
        uow.commit()
        return {**plan, **outcome, "dry_run": False}


@router.post("/names")
def map_name(body: NameBody, principal: Editor, factory: Factory) -> dict[str, str]:
    now = _now()
    with factory() as uow:
        try:
            key = suppliers.map_name(uow.session, body.name, body.supplier_id, now)
        except suppliers.SupplierError as error:
            raise _refuse(error) from None
        _audit(uow, principal, body.supplier_id, "name_mapped", now, name=body.name)
        uow.commit()
        return {"name_key": key, "supplier_id": str(body.supplier_id)}


@router.post("", status_code=201)
def create_supplier(body: SupplierBody, principal: Editor, factory: Factory) -> dict[str, Any]:
    now = _now()
    with factory() as uow:
        try:
            row = suppliers.create(uow.session, body.model_dump(), principal.actor.id, now)
        except suppliers.SupplierError as error:
            raise _refuse(error) from None
        _audit(uow, principal, row.id, "created", now, name=row.name)
        uow.commit()
        return suppliers.serialize(row)


@router.get("/{supplier_id}")
def supplier_card(supplier_id: UUID, request: Request, _principal: Viewer, factory: Factory) -> dict[str, Any]:
    with factory() as uow:
        try:
            row = suppliers.get(uow.session, supplier_id)
        except suppliers.SupplierError as error:
            raise _refuse(error) from None
        lines: list[suppliers.LineFacts] = []
        procurement = getattr(request.app.state, "procurement_uow_factory", None)
        if callable(procurement):
            lines = suppliers.lines_of(port.supplier_lines(procurement), suppliers.keys_by_supplier(uow.session, row.id))
        return {
            **suppliers.serialize(row),
            "procurement": suppliers.procurement_stats(lines),
            "rfq": suppliers.rfq_stats(uow.session, row.id),
        }


@router.patch("/{supplier_id}")
def update_supplier(supplier_id: UUID, body: SupplierPatch, principal: Editor, factory: Factory) -> dict[str, Any]:
    now = _now()
    data = body.model_dump(exclude_unset=True, exclude={"version"})
    with factory() as uow:
        try:
            row = suppliers.update(uow.session, supplier_id, body.version, data, principal.actor.id, now)
        except suppliers.SupplierError as error:
            raise _refuse(error) from None
        _audit(uow, principal, row.id, "updated", now, fields=", ".join(sorted(data)))
        uow.commit()
        return suppliers.serialize(row)


def _archive(supplier_id: UUID, principal: Principal, factory: Any, archived: bool) -> dict[str, Any]:
    now = _now()
    with factory() as uow:
        try:
            row = suppliers.set_archived(uow.session, supplier_id, archived, principal.actor.id, now)
        except suppliers.SupplierError as error:
            raise _refuse(error) from None
        _audit(uow, principal, row.id, "archived" if archived else "unarchived", now, name=row.name)
        uow.commit()
        return suppliers.serialize(row)


@router.post("/{supplier_id}/archive")
def archive_supplier(supplier_id: UUID, principal: Editor, factory: Factory) -> dict[str, Any]:
    return _archive(supplier_id, principal, factory, True)


@router.post("/{supplier_id}/unarchive")
def unarchive_supplier(supplier_id: UUID, principal: Editor, factory: Factory) -> dict[str, Any]:
    return _archive(supplier_id, principal, factory, False)
