"""Request HTTP contract with server-owned identity and transactional writes."""

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request as WebRequest
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.http_auth import (
    Principal,
    authenticated_principal,
    handoff_principal,
    myrequests_principal,
    newrequest_principal,
    requests_form_principal,
    requests_view_principal,
    section_allowed,
)
from app.shared.clock import moscow_now, moscow_today

from .application import (
    CalcSheetError,
    CalcSpreadsheetReader,
    CreateRequestsCommand,
    HandoffRequestsCommand,
    ProcurementDetails,
    ProcurementStageLookup,
    ProjectRegistry,
    directory_purposes,
    UnitOfWork,
    create_requests as create_requests_use_case,
    extract_from_calc,
    handoff_requests as handoff_requests_use_case,
    my_requests as my_requests_use_case,
    recent_requests as recent_requests_use_case,
)
from .domain import (
    Actor,
    CATEGORIES,
    DELIVERY_POINTS,
    ITEM_TYPES,
    DEFAULT_ITEM_TYPE,
    REQUIRED_FIELDS,
    STATIC_PURPOSES,
    TASK_TYPE,
    UNITS,
    CalcImport,
    CalcImportItem,
    RequestDraft,
    ValidationError,
    build_import_drafts,
    calculate_line_sum,
    derive_priority,
    import_project_warning,
    normalize_project_reference,
    parse_calc_number,
    CalcItem,
    calc_project,
    parse_zpk,
    validate_draft,
)


router = APIRouter(prefix="/api/requests", tags=["requests"])
Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=6)]
Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
CALC_IMPORT_LIMIT = 500  # SUPPLYMAP has no cap on an import; решено агентом — проверить


class RequestDraftInput(BaseModel):
    purpose: str = Field(max_length=128)
    delivery_point: str | None = Field(default=None, max_length=512)
    name: str = Field(max_length=1024)
    item_type: str | None = Field(default=None, max_length=255)
    quantity: Quantity
    unit: str = Field(max_length=32)
    quality_document: str | None = Field(default=None, max_length=8)
    procurement_deadline: date | None = None
    planned_date: date | None = None
    project_reference: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=32)
    design_article: str | None = Field(default=None, max_length=512)
    link: str | None = Field(default=None, max_length=4096)
    price: Money | None = None
    price_with_vat: Money | None = None
    comment: str | None = Field(default=None, max_length=10000)

    def to_domain(self) -> RequestDraft:
        return RequestDraft(**self.model_dump())


class ValidateRequestInput(BaseModel):
    request: RequestDraftInput


class CreateRequestsInput(BaseModel):
    requests: list[RequestDraftInput] = Field(min_length=1, max_length=100)


class CalcGridInput(BaseModel):
    """The ЗКП sheet as display values (≤ 20 columns, as `extractFromCalc` reads) plus ЗКЗ!B2."""

    grid: list[list[str | None]] = Field(max_length=5000)
    project: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=512)


class CalcLinkInput(BaseModel):
    """«Из расчёта»: the link to the calculation Google Sheet, as pasted in SUPPLYMAP `openCalc`."""

    url: str = Field(min_length=1, max_length=2048)


class CalcImportItemInput(BaseModel):
    item_type: str = Field(default="", max_length=255)
    name: str = Field(max_length=1024)
    quantity: Quantity
    unit: str | None = Field(default=None, max_length=32)
    quality_document: str | None = Field(default=None, max_length=8)
    planned_date: date | None = None
    category: str | None = Field(default=None, max_length=32)
    link: str | None = Field(default=None, max_length=4096)
    price: Money | None = None
    price_with_vat: Money | None = None


class CalcImportInput(BaseModel):
    project: str = Field(default="", max_length=128)
    purpose: str = Field(max_length=128)
    delivery_point: str | None = Field(default=None, max_length=512)
    procurement_deadline: date | None = None
    items: list[CalcImportItemInput] = Field(max_length=CALC_IMPORT_LIMIT)

    def to_domain(self) -> CalcImport:
        return CalcImport(
            project=self.project,
            purpose=self.purpose,
            delivery_point=self.delivery_point,
            procurement_deadline=self.procurement_deadline,
            items=tuple(CalcImportItem(**item.model_dump()) for item in self.items),
        )


class HandoffRequestsInput(BaseModel):
    request_ids: list[UUID] = Field(min_length=1, max_length=100)
    retransfer: bool = False


def unit_of_work_factory(request: WebRequest) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


def project_registry(request: WebRequest) -> ProjectRegistry | None:
    return getattr(request.app.state, "project_registry", None)


def calc_spreadsheet_reader(request: WebRequest) -> CalcSpreadsheetReader:
    """Tests put a fake on ``app.state.calc_spreadsheet_reader``; otherwise the Google adapter from env."""
    reader = getattr(request.app.state, "calc_spreadsheet_reader", None)
    if reader is not None:
        return reader
    from .google_sheets import reader_from_env

    try:
        return reader_from_env()
    except CalcSheetError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def procurement_stage_lookup(request: WebRequest) -> ProcurementStageLookup | None:
    return getattr(request.app.state, "procurement_stage_lookup", None)


_INTERNAL = re.compile(r"ВП-\d{3,}")


def ensure_internal_projects_open(drafts: Iterable[RequestDraft], registry: object) -> None:
    """A «ВП-…» reference must be an OPEN internal project and the purpose the project's own."""
    internal = {item["number"]: item["purpose"] for item in internal_projects(registry)}
    for index, draft in enumerate(drafts, start=1):
        number = normalize_project_reference(draft.project_reference)
        if not _INTERNAL.fullmatch(number):
            continue
        if number not in internal:
            raise ValidationError(f"Позиция {index}: внутренний проект «{number}» закрыт или не заведён")
        if " ".join(str(draft.purpose or "").split()).casefold() != internal[number].casefold():
            raise ValidationError(f"Позиция {index}: назначение берётся из проекта «{number}» — {internal[number]}")


def internal_projects(registry: object) -> list[dict[str, str]]:
    """Open internal projects (number, name, purpose) for the form's own group."""
    getter = getattr(registry, "internal_projects", None)
    return list(getter()) if callable(getter) else []


@router.get("/schema")
def request_schema(
    _principal: Annotated[Principal, Depends(requests_form_principal)],
    projects: Annotated[ProjectRegistry | None, Depends(project_registry)],
) -> dict:
    # `purposeProjects_`: open registry projects feed the ЦЕЛЬ dropdown; registry down → empty list.
    open_projects = projects.open_projects() if projects is not None else None
    return {
        "projects": list(open_projects or []),
        "internal_projects": internal_projects(projects),
        "required": list(REQUIRED_FIELDS),
        "purposes": directory_purposes(projects) or list(STATIC_PURPOSES),
        "delivery_points": list(DELIVERY_POINTS),
        "units": list(UNITS),
        "categories": list(CATEGORIES),
        "item_types": list(ITEM_TYPES),
        "item_type": {"mode": "combobox", "required": False, "default": DEFAULT_ITEM_TYPE},
        "project_reference": {
            "required": False,
            "source": "projects-module",
        },
    }


@router.post("/validate")
def validate_request(
    payload: ValidateRequestInput,
    _principal: Annotated[Principal, Depends(requests_form_principal)],
    today: Annotated[date, Depends(moscow_today)],
    projects: Annotated[ProjectRegistry | None, Depends(project_registry)],
) -> dict:
    try:
        normalized = validate_draft(payload.request.to_domain(), today, directory_purposes(projects))
        line_sum = calculate_line_sum(normalized.price_with_vat, normalized.quantity)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    normalized_payload = RequestDraftInput(
        **{
            **payload.request.model_dump(),
            "purpose": normalized.purpose,
            "project_reference": normalized.project_reference,
            "delivery_point": normalized.delivery_point,
            "procurement_deadline": normalized.procurement_deadline,
            "planned_date": normalized.planned_date,
            "name": normalized.name,
            "item_type": normalized.item_type,
            "unit": normalized.unit,
            "quality_document": normalized.quality_document,
            "category": normalized.category,
            "design_article": normalized.design_article,
            "link": normalized.link,
            "comment": normalized.comment,
        }
    ).model_dump(mode="json", exclude_none=True)
    return {
        "valid": True,
        "normalized": normalized_payload,
        "derived": {
            "priority": derive_priority(normalized.procurement_deadline, today),
            "line_sum": str(line_sum) if line_sum is not None else None,
            "task_type": TASK_TYPE,
        },
    }


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _procurement_result(details: ProcurementDetails | None) -> dict:
    return {
        "buyer": details.buyer if details else None,
        "supplier": details.supplier if details else None,
        "order_number": details.order_number if details else None,
        "expected_arrival": (
            details.expected_arrival.isoformat() if details and details.expected_arrival else None
        ),
        "procurement_sum": _money(details.sum) if details else None,
    }


def _request_result(request, stage: str | None = None) -> dict:
    return {
        "id": str(request.id),
        "legacy_id": request.legacy_id,
        "designation": request.designation,
        "status": request.status.value,
        "stage": stage if stage is not None else request.status.value,
        "created_at": request.created_at.isoformat(),
        "initiator": request.actor.name,
        "department": request.actor.department,
        "purpose": request.purpose,
        "project_reference": request.project_reference,
        "delivery_point": request.delivery_point,
        "name": request.name,
        "item_type": request.item_type,
        "category": request.category,
        "quantity": format(request.quantity.normalize(), "f"),
        "unit": request.unit,
        "priority": request.priority,
        "planned_date": request.planned_date.isoformat(),
        "procurement_deadline": request.procurement_deadline.isoformat(),
        "quality_document": request.quality_document,
        "design_article": request.design_article,
        "link": request.link,
        "price": _money(request.price),
        "price_with_vat": _money(request.price_with_vat),
        "line_sum": _money(request.line_sum),
        "comment": request.comment,
        "task_type": request.task_type,
    }


def _retry_transaction(operation):
    for attempt in range(2):
        try:
            return operation()
        except (IntegrityError, StaleDataError) as error:
            if attempt == 1:
                raise HTTPException(
                    status_code=409,
                    detail="Конкурентное изменение: повторите команду",
                ) from error
    raise AssertionError("unreachable")


@router.get("")
def list_requests(
    _principal: Annotated[Principal, Depends(requests_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(unit_of_work_factory)],
    q: Annotated[str, Query(max_length=200)] = "",
) -> dict:
    return {
        "requests": [
            _request_result(request)
            for request in recent_requests_use_case(200, uow_factory(), q)
        ]
    }


@router.get("/mine")
def my_requests(
    principal: Annotated[Principal, Depends(myrequests_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(unit_of_work_factory)],
    stages: Annotated[ProcurementStageLookup | None, Depends(procurement_stage_lookup)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
) -> dict:
    """RQ-MINE: server-side filter by initiator, paginated, enriched with the procurement stage.

    Replaces the client-side name match over the latest 200 rows (NAV-1): filtered by the caller's
    own actor id, not a name match, and not capped to a fixed window.
    """
    items, total = my_requests_use_case(principal.actor.id, limit, offset, uow_factory(), q)
    details_by_id = stages.details_for([item.id for item in items]) if stages is not None else {}
    results = []
    for item in items:
        details = details_by_id.get(item.id)
        results.append(
            {
                **_request_result(item, details.stage if details else None),
                **_procurement_result(details),
            }
        )
    return {
        "requests": results,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("", status_code=201)
def create_requests(
    payload: CreateRequestsInput,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
    principal: Annotated[Principal, Depends(newrequest_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(unit_of_work_factory)],
    projects: Annotated[ProjectRegistry | None, Depends(project_registry)],
) -> dict:
    try:
        drafts = tuple(item.to_domain() for item in payload.requests)
        ensure_internal_projects_open(drafts, projects)
        command = CreateRequestsCommand(
            creation_key=idempotency_key,
            drafts=drafts,
            actor=principal.actor,
            now=now,
        )
        requests = _retry_transaction(
            lambda: create_requests_use_case(command, uow_factory(), projects),
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"requests": [_request_result(request) for request in requests]}


def _calc_number(value: str) -> str | None:
    number = parse_calc_number(value)
    return format(number.normalize(), "f") if number is not None else None


def _calc_preview(project: str, items: list[CalcItem], warnings: list[str] | None = None) -> dict:
    preview = {
        "project": project,
        "items": [
            {
                "item_type": item.item_type,
                "name": item.name,
                "quantity": _calc_number(item.quantity),
                "price": _calc_number(item.price),
                "price_with_vat": _calc_number(item.price_with_vat),
                "link": item.link,
                "country": item.country,
                "unit": item.unit,
                "quality_document": "",
                "planned_date": None,
            }
            for item in items
        ],
    }
    if warnings is not None:
        preview["warnings"] = warnings
    return preview


_CALC_SHEET_STATUS = {"config": 503, "access": 422, "unsupported": 422, "upstream": 502}


@router.post("/import/calc")
def extract_calculation(
    payload: CalcLinkInput,
    _principal: Annotated[Principal, Depends(newrequest_principal)],
    reader: Annotated[CalcSpreadsheetReader, Depends(calc_spreadsheet_reader)],
    projects: Annotated[ProjectRegistry | None, Depends(project_registry)],
) -> dict:
    """`portalExtractFromCalc(token, url)`: read the ЗКП sheet of the linked calculation.

    The unknown-project warning of `importRequests_` is shown already here, before anything is created.
    """
    try:
        extract = extract_from_calc(payload.url, reader)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except CalcSheetError as error:
        raise HTTPException(status_code=_CALC_SHEET_STATUS.get(error.kind, 502), detail=str(error)) from error
    warning = import_project_warning(
        extract.project, projects.open_projects() if projects is not None else None
    )
    return _calc_preview(extract.project, extract.items, [warning] if warning else [])


@router.post("/import/parse")
def parse_calculation(
    payload: CalcGridInput,
    _principal: Annotated[Principal, Depends(newrequest_principal)],
) -> dict:
    """Fallback without Google: the ЗКП range pasted as a grid (plus ЗКЗ!B2 / file title if known)."""
    grid = [[cell or "" for cell in row[:20]] for row in payload.grid]
    return _calc_preview(calc_project(payload.project, payload.title), parse_zpk(grid))


@router.post("/import", status_code=201)
def import_requests(
    payload: CalcImportInput,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
    principal: Annotated[Principal, Depends(newrequest_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(unit_of_work_factory)],
    projects: Annotated[ProjectRegistry | None, Depends(project_registry)],
) -> dict:
    """`importRequests_`: one batch, one `project.NNN`, never blocked by an unknown project."""
    try:
        data = payload.to_domain()
        command = CreateRequestsCommand(
            creation_key=idempotency_key,
            drafts=tuple(build_import_drafts(data)),
            actor=principal.actor,
            now=now,
            designation_base=data.project.strip(),
            source="import",
        )
        requests = _retry_transaction(
            lambda: create_requests_use_case(command, uow_factory()),
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    warning = import_project_warning(data.project, projects.open_projects() if projects is not None else None)
    return {
        "requests": [_request_result(request) for request in requests],
        "warnings": [warning] if warning else [],
    }


@router.post("/handoff", status_code=202)
def handoff_requests(
    payload: HandoffRequestsInput,
    principal: Annotated[Principal, Depends(handoff_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(unit_of_work_factory)],
) -> dict:
    try:
        buyer = section_allowed(principal, ("procurement",), "edit")
        command = HandoffRequestsCommand(
            request_ids=tuple(payload.request_ids),
            actor=principal.actor,
            now=now,
            retransfer=payload.retransfer,
            as_buyer=buyer,
            own_only=not buyer and not section_allowed(principal, ("requests",), "edit"),
        )
        messages = _retry_transaction(
            lambda: handoff_requests_use_case(command, uow_factory()),
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return {
        "accepted": [
            {
                "request_id": message.payload["source_request_id"],
                "outbox_id": str(message.id),
                "idempotency_key": message.idempotency_key,
            }
            for message in messages
        ]
    }
