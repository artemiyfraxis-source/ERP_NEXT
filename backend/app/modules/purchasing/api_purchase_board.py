"""«Закупка»: the board of decided positions by supplier and the intent-to-buy letter.

Both are reads (open on view, like «Счета и поставки»). The stage moves — the request, the invoice, the ПП file, the
payment — are procurement's own commands, called by the screen with the line versions it showed (409 on a stale one).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.shared.http_auth import Principal, procurement_view_principal
from app.workflows.purchasing_procurement_bridge import ProcurementLinesBridge

from . import purchase_board
from . import rfq as rfq_service
from . import rfq_invoice_requests
from .deps import procurement_uow_factory, purchasing_uow_factory

router = APIRouter(prefix="/api/purchasing/buy", tags=["purchasing"])

Viewer = Annotated[Principal, Depends(procurement_view_principal)]
Uow = Annotated[Callable, Depends(purchasing_uow_factory)]


def procurement_lines(
    request: Request, factory: Annotated[Callable, Depends(procurement_uow_factory)],
) -> rfq_service.ProcurementLines:
    return ProcurementLinesBridge(factory, getattr(request.app.state, "procurement_supplier_directory", None))


Lines = Annotated[rfq_service.ProcurementLines, Depends(procurement_lines)]


def _run(operation):
    try:
        return operation()
    except rfq_service.RfqError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.get("")
def buy_board(principal: Viewer, uow: Uow, lines: Lines, scope: Literal["mine", "all"] = "mine") -> dict:
    """The cards of the buyer's decided positions (`mine`) or of every buyer's (`all`): active ones and the newest
    paid ones (the archive)."""
    return _run(lambda: {"scope": scope, **purchase_board.board(uow(), lines, principal.actor, all_buyers=scope == "all")})


@router.get("/letters")
def buy_letters(
    principal: Viewer, uow: Uow, lines: Lines,
    line_ids: Annotated[list[UUID], Query(min_length=1, max_length=200)],
) -> dict:
    """«Запросить счёт»: the intent-to-buy letter per supplier for these positions (with the КП and, once formed, our
    order number)."""
    return _run(lambda: rfq_invoice_requests.purchase_letters(uow(), lines, line_ids))
