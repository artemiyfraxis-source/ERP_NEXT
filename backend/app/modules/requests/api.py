"""Request HTTP contract with server-owned identity and transactional writes."""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request as WebRequest
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared.http_auth import (
    Principal,
    authenticated_principal,
    procurement_edit_principal,
    requests_edit_principal,
    requests_view_principal,
)
from app.shared.clock import moscow_now, moscow_today

from .application import (
    CreateRequestsCommand,
    HandoffRequestsCommand,
    UnitOfWork,
    create_requests as create_requests_use_case,
    handoff_requests as handoff_requests_use_case,
    recent_requests as recent_requests_use_case,
)
from .domain import (
    Actor,
    CATEGORIES,
    DELIVERY_POINTS,
    REQUIRED_FIELDS,
    STATIC_PURPOSES,
    TASK_TYPE,
    UNITS,
    RequestDraft,
    ValidationError,
    calculate_line_sum,
    derive_priority,
    validate_draft,
)


router = APIRouter(prefix="/api/requests", tags=["requests"])
Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=6)]
Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]


class RequestDraftInput(BaseModel):
    purpose: str = Field(max_length=128)
    delivery_point: str = Field(max_length=512)
    name: str = Field(max_length=1024)
    item_type: str = Field(max_length=255)
    quantity: Quantity
    unit: str = Field(max_length=32)
    quality_document: str = Field(max_length=8)
    procurement_deadline: date
    planned_date: date
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


class HandoffRequestsInput(BaseModel):
    request_ids: list[UUID] = Field(min_length=1, max_length=100)


def unit_of_work_factory(request: WebRequest) -> Callable[[], UnitOfWork]:
    factory = getattr(request.app.state, "uow_factory", None)
    if not callable(factory):
        raise HTTPException(status_code=503, detail="База ERP не настроена")
    return factory


@router.get("/schema")
def request_schema(
    _principal: Annotated[Principal, Depends(requests_view_principal)],
) -> dict:
    return {
        "required": list(REQUIRED_FIELDS),
        "purposes": list(STATIC_PURPOSES),
        "delivery_points": list(DELIVERY_POINTS),
        "units": list(UNITS),
        "categories": list(CATEGORIES),
        "item_type": {"mode": "combobox", "required": True},
        "project_reference": {
            "required": False,
            "source": "projects-module",
        },
    }


@router.post("/validate")
def validate_request(
    payload: ValidateRequestInput,
    _principal: Annotated[Principal, Depends(requests_view_principal)],
    today: Annotated[date, Depends(moscow_today)],
) -> dict:
    try:
        normalized = validate_draft(payload.request.to_domain())
        line_sum = calculate_line_sum(normalized.price_with_vat, normalized.quantity)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    normalized_payload = RequestDraftInput(
        **{
            **payload.request.model_dump(),
            "purpose": normalized.purpose,
            "project_reference": normalized.project_reference,
            "delivery_point": normalized.delivery_point,
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


def _request_result(request) -> dict:
    return {
        "id": str(request.id),
        "legacy_id": request.legacy_id,
        "designation": request.designation,
        "status": request.status.value,
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
) -> dict:
    return {
        "requests": [
            _request_result(request)
            for request in recent_requests_use_case(200, uow_factory())
        ]
    }


@router.post("", status_code=201)
def create_requests(
    payload: CreateRequestsInput,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
    principal: Annotated[Principal, Depends(requests_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(unit_of_work_factory)],
) -> dict:
    try:
        command = CreateRequestsCommand(
            creation_key=idempotency_key,
            drafts=tuple(item.to_domain() for item in payload.requests),
            actor=principal.actor,
            now=now,
        )
        requests = _retry_transaction(
            lambda: create_requests_use_case(command, uow_factory()),
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"requests": [_request_result(request) for request in requests]}


@router.post("/handoff", status_code=202)
def handoff_requests(
    payload: HandoffRequestsInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(unit_of_work_factory)],
) -> dict:
    try:
        command = HandoffRequestsCommand(
            request_ids=tuple(payload.request_ids),
            actor=principal.actor,
            now=now,
        )
        messages = _retry_transaction(
            lambda: handoff_requests_use_case(command, uow_factory()),
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
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
