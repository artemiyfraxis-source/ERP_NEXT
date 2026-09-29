"""HTTP contract for human-bound production-kit fulfillment."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.modules.warehouse.domain import WarehouseValidationError
from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal,
    fulfillment_edit_principal,
    fulfillment_handout_principal,
    fulfillment_view_principal,
)
from app.shared.print_sheet import PrintSheet

from .application import (
    BindLotCommand,
    CompleteReturnCommand,
    ConcurrentFulfillmentUpdate,
    CreateFulfillmentCommand,
    MarkReadyCommand,
    MarkReturnedCommand,
    ReturnDisposition,
    UnitOfWork,
    UnbindLotCommand,
    VersionedFulfillmentCommand,
    bind_lot,
    build_issue_print_sheet,
    build_issue_return_sheet,
    cancel_fulfillment,
    complete_return,
    create_fulfillment,
    issue_fulfillment,
    mark_ready,
    mark_returned,
    unbind_lot,
)
from .domain import FulfillmentLine, FulfillmentRequest, FulfillmentValidationError


router = APIRouter(prefix="/api/issues", tags=["fulfillment"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
NonnegativeQuantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]


class CreateLinePayload(BaseModel):
    article: str = Field(default="", max_length=255)
    name: str = Field(min_length=1, max_length=1024)
    unit: str = Field(min_length=1, max_length=32)
    requirement: Quantity


class CreatePayload(BaseModel):
    purpose: str = Field(min_length=1, max_length=512)
    deadline: date | None = None
    lines: list[CreateLinePayload] = Field(min_length=1, max_length=500)


class VersionPayload(BaseModel):
    version: int = Field(ge=1)


class BindPayload(VersionPayload):
    lot_id: UUID


class ReadyPayload(VersionPayload):
    handout_location: str = Field(min_length=1, max_length=255)


class ReturnedPayload(VersionPayload):
    return_location: str = Field(min_length=1, max_length=255)


class ReturnLinePayload(BaseModel):
    line_id: UUID
    returned_good: NonnegativeQuantity = Decimal("0")
    defective_returned: NonnegativeQuantity = Decimal("0")
    defective_missing: NonnegativeQuantity = Decimal("0")
    storage_location: str | None = Field(default=None, max_length=16)


class CompleteReturnPayload(VersionPayload):
    lines: list[ReturnLinePayload] = Field(min_length=1, max_length=500)


def fulfillment_uow_factory(request: Request) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "fulfillment_uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _result(request: FulfillmentRequest) -> dict:
    return {
        "id": str(request.id),
        "number": request.number,
        "version": request.version,
        "created_at": request.created_at.isoformat(),
        "requester": request.requester,
        "purpose": request.purpose,
        "deadline": request.deadline.isoformat() if request.deadline else None,
        "status": request.status.value,
        "handout_location": request.handout_location,
        "return_location": request.return_location,
        "fully_reserved": request.fully_reserved,
        "lines": [
            {
                "id": str(line.id),
                "requirement_id": str(line.requirement_id),
                "article": line.article,
                "name": line.name,
                "unit": line.unit,
                "requirement": _number(line.requirement),
                "reserved": _number(line.reserved),
                "issued": _number(line.issued),
                "returned_good": _number(line.returned_good),
                "defective_returned": _number(line.defective_returned),
                "defective_missing": _number(line.defective_missing),
                "consumed": _number(line.consumed),
                "lot": None if line.lot is None else {
                    "id": str(line.lot.lot_id),
                    "warehouse": line.lot.warehouse,
                    "grn": line.lot.grn,
                    "package": line.lot.package,
                    "storage_location": line.lot.storage_location,
                },
            }
            for line in request.lines
        ],
    }


def _run(operation) -> dict:
    try:
        request = operation()
    except (FulfillmentValidationError, WarehouseValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentFulfillmentUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error
    return {"request": _result(request)}


@router.get("")
def requests(
    _principal: Annotated[Principal, Depends(fulfillment_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after: UUID | None = None,
) -> dict:
    with uow_factory() as uow:
        if after is not None and uow.fulfillment.get(after) is None:
            raise HTTPException(status_code=404, detail="Курсор выдачи не найден")
        rows = list(uow.fulfillment.list(limit + 1, after))
    page = rows[:limit]
    return {
        "requests": [_result(request) for request in page],
        "next_cursor": str(page[-1].id) if len(rows) > limit else None,
    }


@router.get("/lots")
def available_lots(
    _principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
    q: Annotated[str, Query(max_length=255)] = "",
    unit: Annotated[str, Query(max_length=32)] = "",
) -> dict:
    with uow_factory() as uow:
        lots = uow.warehouse.list(limit=1000, query=q, include_zero=False)
    normalized_unit = unit.strip().casefold()
    candidates = [
        lot for lot in lots
        if lot.available > 0
        and (not normalized_unit or lot.unit.strip().casefold() == normalized_unit)
    ][:200]
    return {"lots": [{
        "id": str(lot.id),
        "warehouse": lot.warehouse,
        "grn": lot.grn,
        "package": lot.package,
        "article": lot.article,
        "name": lot.name,
        "unit": lot.unit,
        "storage_location": lot.storage_location,
        "available": _number(lot.available),
    } for lot in candidates]}


def _print_response(
    request_id: UUID,
    uow_factory: Callable[[], UnitOfWork],
    now: datetime,
    build: Callable[[FulfillmentRequest, datetime], PrintSheet],
) -> dict:
    with uow_factory() as uow:
        request = uow.fulfillment.get(request_id)
        if request is None:
            raise HTTPException(status_code=404, detail="Заявка на выдачу не найдена")
        try:
            sheet = build(request, now)
        except FulfillmentValidationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    return {"sheet": sheet.to_dict()}


@router.get("/{request_id}/print")
def print_issue(
    request_id: UUID,
    _principal: Annotated[Principal, Depends(fulfillment_view_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _print_response(request_id, uow_factory, now, build_issue_print_sheet)


@router.get("/{request_id}/return-sheet")
def print_return_sheet(
    request_id: UUID,
    _principal: Annotated[Principal, Depends(fulfillment_view_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _print_response(request_id, uow_factory, now, build_issue_return_sheet)


@router.post("")
def create(
    payload: CreatePayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: create_fulfillment(
        CreateFulfillmentCommand(
            idempotency_key=idempotency_key,
            purpose=payload.purpose.strip(),
            deadline=payload.deadline,
            lines=tuple(FulfillmentLine.create(
                article=line.article,
                name=line.name,
                unit=line.unit,
                requirement=line.requirement,
            ) for line in payload.lines),
            actor=principal.actor,
            now=now,
        ),
        uow_factory(),
    ))


@router.post("/{request_id}/lines/{line_id}/bind")
def bind(
    request_id: UUID,
    line_id: UUID,
    payload: BindPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: bind_lot(BindLotCommand(
        request_id=request_id,
        expected_version=payload.version,
        line_id=line_id,
        lot_id=payload.lot_id,
        actor=principal.actor,
        now=now,
    ), uow_factory()))


@router.delete("/{request_id}/lines/{line_id}/bind")
def unbind(
    request_id: UUID,
    line_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: unbind_lot(UnbindLotCommand(
        request_id=request_id,
        expected_version=payload.version,
        line_id=line_id,
        actor=principal.actor,
        now=now,
    ), uow_factory()))


@router.post("/{request_id}/ready")
def ready(
    request_id: UUID,
    payload: ReadyPayload,
    principal: Annotated[Principal, Depends(fulfillment_handout_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: mark_ready(MarkReadyCommand(
        request_id=request_id,
        expected_version=payload.version,
        handout_location=payload.handout_location,
        actor=principal.actor,
        now=now,
    ), uow_factory()))


def _version_command(request_id: UUID, payload: VersionPayload, principal: Principal, now: datetime):
    return VersionedFulfillmentCommand(request_id, payload.version, principal.actor, now)


@router.post("/{request_id}/issue")
def issue(
    request_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_handout_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: issue_fulfillment(
        _version_command(request_id, payload, principal, now), uow_factory()
    ))


@router.post("/{request_id}/returned")
def returned(
    request_id: UUID,
    payload: ReturnedPayload,
    principal: Annotated[Principal, Depends(fulfillment_handout_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: mark_returned(MarkReturnedCommand(
        request_id=request_id,
        expected_version=payload.version,
        return_location=payload.return_location,
        actor=principal.actor,
        now=now,
    ), uow_factory()))


@router.post("/{request_id}/complete-return")
def complete_return_route(
    request_id: UUID,
    payload: CompleteReturnPayload,
    principal: Annotated[Principal, Depends(fulfillment_handout_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: complete_return(CompleteReturnCommand(
        request_id=request_id,
        expected_version=payload.version,
        actor=principal.actor,
        now=now,
        lines=tuple(ReturnDisposition(
            line_id=line.line_id,
            returned_good=line.returned_good,
            defective_returned=line.defective_returned,
            defective_missing=line.defective_missing,
            storage_location=line.storage_location,
        ) for line in payload.lines),
    ), uow_factory()))


@router.post("/{request_id}/cancel")
def cancel(
    request_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: cancel_fulfillment(
        _version_command(request_id, payload, principal, now), uow_factory()
    ))
