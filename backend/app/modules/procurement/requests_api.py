"""HTTP of «Заявки» закупщика (stage option procurement.requests_rb): the view and its two new commands.

Reads open for a viewer (ID-6), writes take `procurement_edit_principal` — so a viewer gets 403 before the option's 404."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.shared import stage_options
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, procurement_edit_principal, procurement_view_principal
from app.workflows.procurement_requests_view import PurchasingRfqSplitter, free_stock, last_purchases, rfq_states

from .api import (
    _command_errors,
    _line_result,
    _retry_transaction,
    procurement_purposes,
    procurement_stock_uow_factory,
    procurement_supplier_directory,
    procurement_uow_factory,
)
from .application import StockCoverUnitOfWork, SupplierDirectory, UnitOfWork
from .domain import CANCEL_REASON_MAX_LENGTH, ProcurementLine, ProcurementValidationError
from .requests_application import (
    CANCEL_MANY_LIMIT,
    CancelManyCommand,
    CancelManyItem,
    SplitByQuantityCommand,
    cancel_procurement_lines,
    split_procurement_line_by_quantity,
)
from .requests_infrastructure import cancel_reasons, last_paid_purchases, view_lines
from .requests_view import batch_lines, build_summary, line_results

OPTION = "procurement.requests_rb"
BATCH_LIMIT = 300  # requests listed without a project filter
router = APIRouter(prefix="/api/procurement", tags=["procurement"])
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]


def _require_option() -> None:
    if not stage_options.enabled(OPTION):
        raise HTTPException(status_code=404, detail="Опция выключена")


@router.get("/requests-view")
def requests_view(
    principal: Annotated[Principal, Depends(procurement_view_principal)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
    purposes_of: Annotated[Callable[[Sequence[ProcurementLine]], dict[UUID, str]], Depends(procurement_purposes)],
    suppliers: Annotated[SupplierDirectory | None, Depends(procurement_supplier_directory)],
    scope: Literal["mine", "all"] = "mine",
    project: Annotated[str | None, Query(max_length=255)] = None,
    batch: Annotated[str | None, Query(max_length=800)] = None,
    line: UUID | None = None,
) -> dict:
    """Projects → request batches of the scope with RB's «Ход закупки» (without a project — the newest `BATCH_LIMIT`,
    `batches_total` says how many); with `batch` only that batch's lines instead (plan with VAT and link, last purchase,
    free stock, request for quotes, what the viewer may do). «Мои» — the buyer's lines and the unclaimed «Новый» ones;
    «Все» — every line, read only. `line` (a `&line=` link) names its batch and project."""
    _require_option()
    actor_id = principal.actor.id if scope == "mine" else None
    uow = uow_factory()
    with uow:
        session = uow.session
        lines = view_lines(session, actor_id)
        purposes = purposes_of(lines)
        if batch:
            # One batch opened: only its lines, nothing of the summary is recomputed.
            chosen = batch_lines(lines, batch, purposes)
            states = rfq_states(session, [item.id for item in chosen])
            return {"scope": scope, "batch": batch, "lines": line_results(
                chosen, states, actor_id,
                suppliers=suppliers.supplier_map() if suppliers is not None else {},
                free_stock=free_stock(session, chosen),
                last=last_purchases(session, chosen, last_paid_purchases(session, chosen)),
                reasons=cancel_reasons(session, [item.id for item in chosen if item.status == "Отменено"]),
            )}
        states = rfq_states(session, [item.id for item in lines])
        chosen_project = (project or "").strip() or None
        summary = build_summary(lines, purposes, states, actor_id, project=chosen_project, focus=line)
    total = len(summary["batches"])
    if chosen_project is None and total > BATCH_LIMIT:
        # Without a project only the newest requests (the focused one kept): a project shows all of its own.
        kept = summary["batches"][:BATCH_LIMIT]
        focus = summary["focus"]
        if focus and all(item["key"] != focus["batch"] for item in kept):
            kept.append(next(item for item in summary["batches"] if item["key"] == focus["batch"]))
        summary["batches"] = kept
    return {"scope": scope, **summary, "batches_total": total}


class SplitQuantityInput(BaseModel):
    version: int = Field(ge=1)
    quantity: Quantity


@router.post("/{line_id}/split-qty")
def split_quantity(
    line_id: UUID,
    payload: SplitQuantityInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], UnitOfWork], Depends(procurement_uow_factory)],
) -> dict:
    """«Разделить» by quantity: `quantity` goes to a new line of the same request; in a request for quotes the new line
    joins it with a copy of every quote (decisions are not copied)."""
    _require_option()
    result = _command_errors(lambda: split_procurement_line_by_quantity(
        SplitByQuantityCommand(
            line_id=line_id, expected_version=payload.version, quantity=payload.quantity, actor=principal.actor,
            now=now,
        ),
        uow_factory(),
        PurchasingRfqSplitter(),
    ))
    return {"original": _line_result(result.original), "created": _line_result(result.created), "rfq": result.rfq}


class CancelLineRef(BaseModel):
    id: UUID
    version: int = Field(ge=1)


class CancelManyInput(BaseModel):
    lines: list[CancelLineRef] = Field(min_length=1, max_length=CANCEL_MANY_LIMIT)
    reason: str = Field(max_length=CANCEL_REASON_MAX_LENGTH * 2)


@router.post("/cancel-many")
def cancel_many(
    payload: CancelManyInput,
    principal: Annotated[Principal, Depends(procurement_edit_principal)],
    now: Annotated[datetime, Depends(moscow_now)],
    uow_factory: Annotated[Callable[[], StockCoverUnitOfWork], Depends(procurement_stock_uow_factory)],
) -> dict:
    """«Отменить выбранные» with a reason: lines that cannot be cancelled (paid, another buyer's, changed since the list
    was read, already cancelled) are skipped with the reason — never a partial failure."""
    _require_option()
    try:
        result = _retry_transaction(lambda: cancel_procurement_lines(
            CancelManyCommand(
                items=tuple(CancelManyItem(item.id, item.version) for item in payload.lines),
                reason=payload.reason, actor=principal.actor, now=now,
            ),
            uow_factory,
        ))
    except ProcurementValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "cancelled": [str(line.id) for line in result.cancelled],
        "skipped": [{"id": str(line_id), "reason": reason} for line_id, reason in result.skipped],
    }
