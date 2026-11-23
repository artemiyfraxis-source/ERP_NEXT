"""Reference-scoped access to the canonical instrument aggregate."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select

from app.modules.production.api import UpdateInstrumentPayload, _execution, instrument_result
from app.modules.production.quality import UpdateInstrumentCommand
from app.modules.production.infrastructure import SqlAlchemyInstrumentFileRepository
from app.modules.production.instrument_serials import InstrumentSerialContext
from app.modules.references.api import references_uow_factory
from app.modules.references.application import custom_columns
from app.modules.references.domain import TechOperationTab
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, references_view_principal, require_section
from app.shared.stored_files import file_response
from app.shared.persistence import AuditEventRow

router = APIRouter(prefix="/api/references/instruments", tags=["references"])
# A person reaches the card with «Справочник» view and is then sorted by ОТК / «Справочник» edit below; a token edits
# only with `refs:write` — a read-only `refs:read` token is refused at the gate, not inside the endpoint.
instrument_edit_principal = require_section(
    "erpref", "view", detail="Раздел «Справочник» вам не открыт.", scopes=("refs:write",),
)


def _result(catalog, item):
    legacy = catalog.legacy_id(item.id)
    return {**instrument_result(item, catalog.files(item.id), now=moscow_now(),
                               serial_context=catalog.serial_contexts().get(item.id, InstrumentSerialContext())),
            "legacy_unit_id": str(legacy) if legacy else None}


@router.get("")
def instruments(
    principal: Annotated[Principal, Depends(references_view_principal)],
    factory: Annotated[Callable, Depends(references_uow_factory)],
):
    with factory() as uow:
        catalog = uow.instrument_catalog
        rows = catalog.catalog.list()
        return {"instruments": [_result(catalog, item) for item in rows],
                "count": len(rows), "quantity": sum(item.quantity for item in rows),
                "custom_columns": custom_columns(TechOperationTab.MEASURING, uow),
                "can_edit_identity": not principal.service and (principal.full_access or principal.permissions.get("quality") == "edit"),
                "can_edit_custom": principal.full_access or principal.permissions.get("erpref") == "edit" or "refs:write" in principal.scopes}


@router.get("/{record_id}")
def instrument(
    record_id: UUID,
    _principal: Annotated[Principal, Depends(references_view_principal)],
    factory: Annotated[Callable, Depends(references_uow_factory)],
):
    with factory() as uow:
        catalog = uow.instrument_catalog
        item = catalog.resolve(record_id)
        if item is None:
            raise HTTPException(404, "СИ не найдено")
        return {"instrument": _result(catalog, item)}


@router.patch("/{record_id}")
def edit_instrument(
    record_id: UUID,
    payload: UpdateInstrumentPayload,
    principal: Annotated[Principal, Depends(instrument_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    factory: Annotated[Callable, Depends(references_uow_factory)],
):
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    quality = not principal.service and (principal.full_access or principal.permissions.get("quality") == "edit")
    custom = ("refs:write" in principal.scopes) if principal.service else (
        principal.full_access or principal.permissions.get("erpref") == "edit")
    if not quality and not custom:
        raise HTTPException(403, "Реестр СИ открыт только для просмотра")
    if changes.keys() - {"custom_fields"} and not quality:
        raise HTTPException(403, "Паспорт СИ изменяет сотрудник с правом правки ОТК")
    with factory() as uow:
        catalog = uow.instrument_catalog
        item = catalog.resolve(record_id)
        if item is None:
            raise HTTPException(404, "СИ не найдено")
        if "custom_fields" in changes and not quality:
            values = changes["custom_fields"]
            own = set(custom_columns(TechOperationTab.MEASURING, uow))
            if values is None or any(item.custom_fields.get(key) != (values or {}).get(key)
                    for key in set(item.custom_fields) | set(values or {}) if key not in own):
                raise HTTPException(403, "Разрешены только значения собственных столбцов СИ")
        saved = _execution(lambda: catalog.catalog.update(UpdateInstrumentCommand(
            item.id, payload.version, changes, principal.actor, now, "references", catalog.legacy_id(item.id))))
        uow.commit()
        return {"instrument": _result(catalog, saved)}


@router.get("/{record_id}/history")
def history(
    record_id: UUID,
    _principal: Annotated[Principal, Depends(references_view_principal)],
    factory: Annotated[Callable, Depends(references_uow_factory)],
):
    with factory() as uow:
        catalog = uow.instrument_catalog
        item = catalog.resolve(record_id)
        if item is None:
            raise HTTPException(404, "СИ не найдено")
        legacy = catalog.legacy_id(item.id)
        conditions = [AuditEventRow.aggregate_id.in_([item.id, *([legacy] if legacy else [])])]
        # Old reference events use the shared reference aggregate; preserve their UUIDs and payloads.
        if legacy:
            conditions.append(AuditEventRow.details["record_id"].as_string() == str(legacy))
        events = uow.session.scalars(select(AuditEventRow).where(or_(*conditions)).order_by(
            AuditEventRow.occurred_at, AuditEventRow.id))
        return {"instrument_id": str(item.id), "events": [{"id": str(event.id),
            "aggregate_id": str(event.aggregate_id), "event_type": event.event_type,
            "actor_name": event.actor_name, "occurred_at": event.occurred_at.isoformat(),
            "details": event.details} for event in events]}


@router.get("/{record_id}/files/{file_id}")
def instrument_file(
    record_id: UUID,
    file_id: UUID,
    _principal: Annotated[Principal, Depends(references_view_principal)],
    factory: Annotated[Callable, Depends(references_uow_factory)],
):
    with factory() as uow:
        item = uow.instrument_catalog.resolve(record_id)
        if item is None:
            raise HTTPException(404, "СИ не найдено")
        file = SqlAlchemyInstrumentFileRepository(uow.session).get(item.id, file_id)
        if file is None:
            raise HTTPException(404, "Документ СИ не найден")
        return file_response(file.content, file.file_name)
