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
from app.shared import article_match
from app.shared.clock import moscow_now
from app.shared.http_auth import (
    Principal,
    authenticated_principal,
    section_allowed,
    fulfillment_admin_principal,
    fulfillment_edit_principal,
    fulfillment_handout_principal,
    fulfillment_view_principal,
)
from app.modules.requests.application import CalcSheetError
from app.modules.requests.domain import ValidationError as CalcValidationError
from app.shared.print_sheet import PrintSheet
from app.workflows import issue_calc
from app.shared.warehouse_location import SHELF_LETTERS, configured_rack_layout, place_code

from .application import (
    AbsentCommand,
    BindLotCommand,
    BindProjectLotsCommand,
    CompleteReturnCommand,
    ConcurrentFulfillmentUpdate,
    CreateFulfillmentCommand,
    DeleteFulfillmentCommand,
    IssueBatchCommand,
    MarkReadyCommand,
    MarkReturnedCommand,
    OffBookCommand,
    ReturnDisposition,
    UnitOfWork,
    UnbindLotCommand,
    VersionedFulfillmentCommand,
    bind_lot,
    bind_project_lots,
    set_absent,
    set_off_book,
    build_issue_print_sheet,
    build_issue_return_sheet,
    cancel_fulfillment,
    close_issued,
    free_for_project,
    project_reserve_by_lot,
    complete_kit,
    complete_return,
    CreateExtraIssueCommand,
    ExtraIssueLine,
    create_extra_issue,
    create_fulfillment,
    delete_fulfillment,
    handout_place,
    issue_batch,
    issue_fulfillment,
    kit_label,
    mark_ready,
    mark_returned,
    return_to_kitting,
    unbind_lot,
)
from .domain import (
    DuplicateKitRequest, FulfillmentLine, FulfillmentRequest, FulfillmentStatus, FulfillmentValidationError, project_of,
)


router = APIRouter(prefix="/api/issues", tags=["fulfillment"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
NonnegativeQuantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]
OptionalQuantity = Annotated[Decimal | None, Field(max_digits=18, decimal_places=6)]


class CreateLinePayload(BaseModel):
    article: str = Field(default="", max_length=255)
    name: str = Field(min_length=1, max_length=1024)
    unit: str = Field(min_length=1, max_length=32)
    requirement: Quantity
    lot_id: UUID | None = None


class CreatePayload(BaseModel):
    purpose: str = Field(min_length=1, max_length=512)
    deadline: date | None = None
    lines: list[CreateLinePayload] = Field(min_length=1, max_length=500)
    # queueIssueRequest shortNote: shortfalls already known on picking (IssueCalc: «нет на складе»).
    short_note: list[Annotated[str, Field(max_length=1024)]] = Field(default_factory=list, max_length=500)


class CalcPreviewPayload(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    warehouse: str = Field(min_length=1, max_length=8)


class VersionPayload(BaseModel):
    version: int = Field(ge=1)


class ExtraLinePayload(BaseModel):
    article: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=1024)
    unit: str = Field(min_length=1, max_length=32)
    quantity: Quantity


class ExtraIssuePayload(BaseModel):
    """«Довыдать» on an issued kit: chosen positions with quantities and why."""
    reason: str = Field(min_length=1, max_length=200)
    deadline: date | None = None
    lines: list[ExtraLinePayload] = Field(min_length=1, max_length=500)


class BindPayload(VersionPayload):
    lot_id: UUID


class ProjectBindPayload(BaseModel):
    """«Запросы на комплектацию»: lots (or «есть, но не заведено») for one item across every КБ of the project."""
    article: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=1024)
    lot_ids: list[UUID] = Field(default_factory=list, max_length=50)
    off_book: bool = False
    # «Сколько есть?» (owner 14.10.2026): only this much is «есть, но не заведено»; None — the whole waiting need.
    # No sign rule here: ≤ 0 and more than waits are refused by the application with a Russian 422.
    off_book_quantity: OptionalQuantity = None
    # The version of every request the screen showed: {request_id: version}.
    versions: dict[UUID, Annotated[int, Field(ge=1)]] = Field(default_factory=dict, max_length=500)


class OffBookPayload(VersionPayload):
    """«есть, но не заведено» on one line; `quantity` below its need splits it (the rest keeps waiting)."""
    quantity: OptionalQuantity = None


class PlacePayload(BaseModel):
    """«Отправить на выдачу» address as the warehouse keeps it (ih-rack / ih-shelf / ih-num / ih-slot)."""
    rack: str = Field(default="", max_length=8)
    shelf: str = Field(default="", max_length=8)
    num: str = Field(default="", max_length=8)
    slot: str = Field(default="", max_length=8)


class ReadyPayload(VersionPayload):
    # Either a ready place string (fallback, old addresses) or `place` — the code is glued here (placeCode_), so
    # every screen writes the same format (portalIssueStatus, owner 03.09.2026).
    handout_location: str | None = Field(default=None, max_length=255)
    place: PlacePayload | None = None


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


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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
        "kind": request.kind.value,
        "handout_location": request.handout_location,
        "return_location": request.return_location,
        "fully_reserved": request.fully_reserved,
        "shortfall": request.shortfall,
        "project": project_of(request.purpose),
        "at_work": _stamp(request.at_work),
        "at_picked": _stamp(request.at_picked),
        "at_issued": _stamp(request.at_issued),
        "at_returned": _stamp(request.at_returned),
        "at_closed": _stamp(request.at_closed),
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
                "off_book": line.off_book,
                "absent_at": _stamp(line.absent_at),
                "absent_by": line.absent_by or None,
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


def _conflict_detail(error: Exception) -> str:
    """409 text of a write the database refused: the live-kit UNIQUE (two presses at once) gets its own words."""
    if "uq_fulfillment_requests_live_kit" in str(error) or "fulfillment_requests.project_key" in str(error):
        return "Заявка на эту КБ проекта уже есть (создана только что) — обнови экран"
    return "Конкурентное изменение: обновите данные"


def _run(operation) -> dict:
    try:
        request = operation()
    except DuplicateKitRequest as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (FulfillmentValidationError, WarehouseValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentFulfillmentUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail=_conflict_detail(error)) from error
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
        # «Отправить на выдачу» in one click: a complete kit carries the place the screen offers (G1-T3).
        places = {
            request.id: handout_place(request, uow) for request in page if request.status is FulfillmentStatus.COMPLETE
        }
    layout = configured_rack_layout()
    return {
        "requests": [
            {**_result(request), "handout_place": places[request.id]} if request.id in places else _result(request)
            for request in page
        ],
        "next_cursor": str(page[-1].id) if len(rows) > limit else None,
        # issueCardResp_ `places.config`: the rack layout for the handout address fields.
        "places": {
            "racks": layout.racks,
            "shelves": layout.shelves,
            "per_rack": {str(rack): count for rack, count in layout.per_rack.items()},
            "slots": SHELF_LETTERS,
        },
    }


_CALC_SHEET_STATUS = {"config": 503, "access": 422, "unsupported": 422, "upstream": 502}


@router.post("/calc-preview")
def calc_preview(
    payload: CalcPreviewPayload,
    request: Request,
    _principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """«Заявка по расчёту» (Issue.gs issueCalcPreview): the ЗКП positions of the linked calculation, matched against the
    warehouse and picked FIFO. Creating the request goes through POST /api/issues with the picked lots + shortNote."""
    try:
        return issue_calc.issue_calc_preview(
            payload.url, payload.warehouse, issue_calc.calc_reader(request.app.state), uow_factory,
        )
    except (issue_calc.IssueCalcError, CalcValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except CalcSheetError as error:
        raise HTTPException(status_code=_CALC_SHEET_STATUS.get(error.kind, 502), detail=str(error)) from error


@router.get("/lots")
def available_lots(
    _principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
    q: Annotated[str, Query(max_length=255)] = "",
    project: Annotated[str, Query(max_length=255)] = "",
) -> dict:
    """`project` (optional): the kit's project — its own reserves on a lot («Со склада» covers and the techcard
    reserves of any of its КБ) count as available: `available` = ДОСТУПНО + `reserved` (the project's part), the same
    shape as /api/kitsets/lots."""
    with uow_factory() as uow:
        lots = uow.warehouse.filtered(query=q, include_zero=False)
        held = project_reserve_by_lot(uow, lots, project.strip())
    candidates = [
        lot for lot in lots
        if free_for_project(lot, held) > 0  # portalIssueStock: every lot with a balance, whatever its unit
    ]  # portalIssueStock: the whole warehouse at once, the browser searches
    return {"lots": [{
        "id": str(lot.id),
        "lot_id": str(lot.id),
        "warehouse": lot.warehouse,
        "grn": lot.grn,
        "package": lot.package,
        "article": lot.article,
        "name": lot.name,
        "unit": lot.unit,
        "storage_location": lot.storage_location,
        "available": _number(free_for_project(lot, held)),
        **({"reserved": _number(held[lot.id])} if held.get(lot.id) else {}),
    } for lot in candidates]}


_CANDIDATES_SHOWN = 10


@router.get("/projects/{project}/candidates")
def project_candidates(
    project: str,
    _principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """«Возможно есть» (owner 12.10.2026): for every position of the project's requests, the warehouse lots that may be
    it — by the position's name first, the КД article only when the name finds nothing; exact before fuzzy
    (`app.shared.article_match`), `by` says which — in ONE pass over the stock. `available` is what
    the project's kit can take (ДОСТУПНО + its own reserve), a lot with nothing to take is not offered. Read-only."""
    project = project.strip()
    with uow_factory() as uow:
        positions: dict[tuple[str, str], str] = {}
        after = None
        while True:
            page = list(uow.fulfillment.list(limit=200, after_id=after))
            for request in page:
                if project_of(request.purpose) != project:
                    continue
                for line in request.lines:
                    positions.setdefault((line.article, line.name), line.unit)
            if len(page) < 200:
                break
            after = page[-1].id
        return _candidates(uow, project, positions)


class CandidatePosition(BaseModel):
    article: str = Field(default="", max_length=255)
    name: str = Field(default="", max_length=512)
    unit: str = Field(default="", max_length=32)


class CandidatesPayload(BaseModel):
    positions: list[CandidatePosition] = Field(default_factory=list, max_length=1000)


def stock_candidates_principal(
    principal: Annotated[Principal, Depends(authenticated_principal)],
) -> Principal:
    """Who may see what the warehouse holds: «Склад» (view) or the kitting desk («Комплектация», edit)."""
    if principal.service or not (
        section_allowed(principal, ("warehouse",), "view") or section_allowed(principal, ("fulfillment",), "edit")
    ):
        raise HTTPException(status_code=403, detail="Остатки склада вам не открыты")
    return principal


@router.post("/projects/{project}/candidates")
def positions_candidates(
    project: str,
    payload: CandidatesPayload,
    _principal: Annotated[Principal, Depends(stock_candidates_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """«Возможно есть» for the positions the caller names — the project's «Снабжение» lists its spec positions before
    any kit request exists. The same lots, matcher and answer as the GET; the project's own reserves count as free."""
    positions: dict[tuple[str, str], str] = {}
    for item in payload.positions:
        positions.setdefault((item.article.strip(), item.name.strip()), item.unit.strip())
    with uow_factory() as uow:
        return _candidates(uow, project.strip(), positions)


def _candidates(uow, project: str, positions: dict[tuple[str, str], str]) -> dict:
    """One pass over the stock for every (article, name) position: by the name, else by the КД article."""
    lots = uow.warehouse.filtered(include_zero=False) if positions else []
    held = project_reserve_by_lot(uow, lots, project)
    free = [lot for lot in lots if free_for_project(lot, held) > 0]
    result = []
    for (article, name), unit in positions.items():
        found = article_match.candidates(article, name, free)
        result.append({
            "article": article,
            "name": name,
            "unit": unit,
            "candidates": [{
                "id": str(lot.id),
                "lot_id": str(lot.id),
                "warehouse": lot.warehouse,
                "grn": lot.grn,
                "package": lot.package,
                "article": lot.article,
                "name": lot.name,
                "unit": lot.unit,
                "storage_location": lot.storage_location,
                "available": _number(free_for_project(lot, held)),
                "kind": match.kind,
                "match": match.label,
                "by": match.basis,
            } for match, lot in found[:_CANDIDATES_SHOWN]],
            "more": max(len(found) - _CANDIDATES_SHOWN, 0),
        })
    return {"positions": result}


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
            lot_ids=tuple(line.lot_id for line in payload.lines) if any(line.lot_id for line in payload.lines) else (),
            short_note=tuple(payload.short_note),
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


def _off_book(request_id: UUID, line_id: UUID, payload: VersionPayload, principal: Principal, now: datetime,
              uow_factory: Callable[[], UnitOfWork], off_book: bool) -> dict:
    return _run(lambda: set_off_book(OffBookCommand(
        request_id=request_id,
        expected_version=payload.version,
        line_id=line_id,
        off_book=off_book,
        quantity=payload.quantity if off_book and isinstance(payload, OffBookPayload) else None,
        actor=principal.actor,
        now=now,
    ), uow_factory()))


@router.post("/{request_id}/lines/{line_id}/off-book")
def mark_off_book(
    request_id: UUID,
    line_id: UUID,
    payload: OffBookPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """«есть, но не заведено»: the waiting position passes without a lot and without a stock movement; `quantity`
    below the need splits the line and the rest keeps waiting."""
    return _off_book(request_id, line_id, payload, principal, now, uow_factory, True)


@router.delete("/{request_id}/lines/{line_id}/off-book")
def clear_off_book(
    request_id: UUID,
    line_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _off_book(request_id, line_id, payload, principal, now, uow_factory, False)


def _absent(request_id: UUID, line_id: UUID, payload: VersionPayload, principal: Principal, now: datetime,
            uow_factory: Callable[[], UnitOfWork], absent: bool) -> dict:
    return _run(lambda: set_absent(AbsentCommand(
        request_id=request_id,
        expected_version=payload.version,
        line_id=line_id,
        absent=absent,
        actor=principal.actor,
        now=now,
    ), uow_factory()))


@router.post("/{request_id}/lines/{line_id}/absent")
def mark_absent(
    request_id: UUID,
    line_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """«нет на складе»: the storekeeper confirms the part is absent — the line stays open as a shortage, supply is told
    once, «есть, но не заведено» on it is dropped. A repeat on a marked line changes nothing."""
    return _absent(request_id, line_id, payload, principal, now, uow_factory, True)


@router.delete("/{request_id}/lines/{line_id}/absent")
def clear_absent(
    request_id: UUID,
    line_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _absent(request_id, line_id, payload, principal, now, uow_factory, False)


@router.post("/projects/{project}/bind-lots")
def bind_lots_for_project(
    project: str,
    payload: ProjectBindPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """One item gets one or several ГРН for every КБ of the project at once (owner 05.10.2026): КБ1, КБ2 … КБ10, lots
    FIFO by ГРН, a short lot splits the line, the rest stays waiting. `off_book` marks the item «есть, но не заведено»
    on every waiting line instead. One transaction; a request changed since the screen loaded → 409."""
    try:
        result = bind_project_lots(BindProjectLotsCommand(
            project=project.strip(),
            article=payload.article,
            name=payload.name,
            lot_ids=tuple(payload.lot_ids),
            off_book=payload.off_book,
            versions=dict(payload.versions),
            actor=principal.actor,
            now=now,
            off_book_quantity=payload.off_book_quantity,
        ), uow_factory())
    except DuplicateKitRequest as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (FulfillmentValidationError, WarehouseValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentFulfillmentUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Партия не найдена на складе") from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error
    kbs = len({item.request_id for item in result.bound})
    label = payload.name or payload.article
    if payload.off_book:
        marked = sum((item.quantity for item in result.bound), Decimal("0"))
        message = f"«{label}»: есть, но не заведено — {_number(marked)} {result.unit}".rstrip() + f" в {kbs} КБ"
        if result.short > 0:
            message += f"; не хватает {_number(result.short)} {result.unit}".rstrip() + " — эти позиции ждут поставки"
        message += "."
    else:
        message = f"«{label}»: партии привязаны — строк {len(result.bound)} в {kbs} КБ."
        if result.short > 0:
            message += f" Не хватает {_number(result.short)} {result.unit}".rstrip() + " — эти позиции ждут поставки."
    return {
        "bound": [{
            "request_id": str(item.request_id),
            "number": item.number,
            "kb": item.kb,
            "line_id": str(item.line_id),
            "lot_id": str(item.lot_id) if item.lot_id else None,
            "grn": item.grn,
            "quantity": _number(item.quantity),
            "off_book": item.off_book,
        } for item in result.bound],
        "short": _number(result.short),
        "message": message,
        "requests": [_result(request) for request in result.requests],
    }


def _handout_location(payload: ReadyPayload) -> str:
    if payload.place is not None:
        code = place_code(payload.place.rack, payload.place.shelf, payload.place.num, payload.place.slot)
        if not code:
            raise HTTPException(status_code=422, detail="Адрес неполный: нужны стеллаж, полка и № на полке.")
        return code
    location = str(payload.handout_location or "").strip()
    if not location:
        raise HTTPException(status_code=422, detail="Укажи место выдачи — без него комплект на полке не найти.")
    return location


# FF-2: «Готов к выдаче» is the picker's step (Issue.gs portalIssueStatus: only КБ Выдана / Готово / КБ Возвращена
# take the `handout` section).
@router.post("/{request_id}/ready")
def ready(
    request_id: UUID,
    payload: ReadyPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    location = _handout_location(payload)
    return _run(lambda: mark_ready(MarkReadyCommand(
        request_id=request_id,
        expected_version=payload.version,
        handout_location=location,
        actor=principal.actor,
        now=now,
    ), uow_factory()))


@router.post("/{request_id}/complete")
def complete(
    request_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: complete_kit(
        VersionedFulfillmentCommand(request_id, payload.version, principal.actor, now), uow_factory()
    ))


@router.post("/{request_id}/close")
def close(
    request_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_handout_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    return _run(lambda: close_issued(
        VersionedFulfillmentCommand(request_id, payload.version, principal.actor, now), uow_factory()
    ))


@router.delete("/{request_id}")
def delete(
    request_id: UUID,
    principal: Annotated[Principal, Depends(fulfillment_admin_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    try:
        result = delete_fulfillment(DeleteFulfillmentCommand(request_id, principal.actor, now), uow_factory())
    except FulfillmentValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Заявка на выдачу не найдена") from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обновите данные") from error
    return {"message": f"Заявка удалена: позиций {result.lines}"
            + (f", снят резерв с {result.freed} партий" if result.freed else "") + "."}


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


class IssueBatchItem(BaseModel):
    id: UUID
    version: int = Field(ge=1)


class IssueBatchPayload(BaseModel):
    """«Выдать выбранные»: the kits ticked on the order (or one КБ), with the versions the screen showed."""
    requests: list[IssueBatchItem] = Field(min_length=1, max_length=200)


@router.post("/issue-batch")
def issue_selected(
    payload: IssueBatchPayload,
    principal: Annotated[Principal, Depends(fulfillment_handout_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """Hand out the whole order or any of its КБ in one transaction (owner 06.10.2026): «Укомплектован» kits are sent
    to the handout point on the way. Any kit refused → 422/409 naming its КБ, and nothing is issued."""
    try:
        issued = issue_batch(IssueBatchCommand(
            versions=tuple((item.id, item.version) for item in payload.requests),
            actor=principal.actor,
            now=now,
        ), uow_factory())
    except (FulfillmentValidationError, WarehouseValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConcurrentFulfillmentUpdate as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error.args[0] if error.args else error)) from error
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Конкурентное изменение: обнови экран, ничего не выдано") from error
    lines = sum(1 for request in issued for line in request.lines if not line.off_book and line.issued > 0)
    return {
        "requests": [_result(request) for request in issued],
        "message": f"Выдано: {', '.join(kit_label(request) for request in issued)}. "
                   f"Списано со склада позиций: {lines}.",
    }


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


@router.post("/{request_id}/extra")
def extra_issue(
    request_id: UUID,
    payload: ExtraIssuePayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """«Довыдать»: an extra request («Довыдача») for positions of an issued kit, waiting for lots like a new one."""
    return _run(lambda: create_extra_issue(CreateExtraIssueCommand(
        request_id=request_id,
        idempotency_key=idempotency_key,
        reason=payload.reason,
        deadline=payload.deadline,
        lines=tuple(ExtraIssueLine(
            article=line.article, name=line.name, unit=line.unit, quantity=line.quantity,
        ) for line in payload.lines),
        actor=principal.actor,
        now=now,
    ), uow_factory()))


@router.post("/{request_id}/return-to-kitting")
def back_to_kitting(
    request_id: UUID,
    payload: VersionPayload,
    principal: Annotated[Principal, Depends(fulfillment_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(fulfillment_uow_factory)],
) -> dict:
    """«Вернуть в комплектацию»: «Готов к выдаче» → «Укомплектован», reserves stay on their lots."""
    return _run(lambda: return_to_kitting(
        _version_command(request_id, payload, principal, now), uow_factory()
    ))


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
