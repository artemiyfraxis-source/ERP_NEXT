"""«Счета и поставки» (stage option procurement.orders): the board / table of supplier orders, the order card, the
order's own facts, «Дожать» and «Новый счёт» from open decisions.

Reads open on view («Лист закупки» on view sees every buyer's orders); writes take edit and answer 403 to a viewer before
the option's 404 (view-mode contract). The stage moves themselves (invoice, payment, expected date) stay procurement's
commands — the screen calls them; only the facts procurement has no place for are written here. Procurement,
receiving and the audit log are read through app/workflows/purchasing_orders.py."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.shared import stage_options
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, procurement_edit_principal, procurement_view_principal
from app.workflows import purchasing_orders as port
from app.workflows.procurement_rfq_followups import rfq_followups

from . import orders as service
from . import order_upds
from .deps import purchasing_uow_factory, require_option
from .infrastructure import ORDER_NOTE_MAX, SHIPMENT_NOTE_MAX

OPTION = "procurement.orders"

router = APIRouter(prefix="/api/purchasing/orders", tags=["purchasing"])

Uow = Annotated[Callable, Depends(purchasing_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]
Viewer = Annotated[Principal, Depends(procurement_view_principal)]
Editor = Annotated[Principal, Depends(procurement_edit_principal)]
OrderNumber = Annotated[str, Path(min_length=1, max_length=32)]


def _errors(operation):
    try:
        return operation()
    except service.OrderError as error:
        detail: object = str(error)
        if error.field:
            detail = {"message": str(error), "field": error.field}
        raise HTTPException(status_code=error.status, detail=detail) from None
    except (IntegrityError, StaleDataError) as error:
        raise HTTPException(status_code=409, detail="Заказ изменили — обновите и повторите") from error


@router.get("")
def list_orders(
    principal: Viewer, make_uow: Uow, now: Now, scope: Literal["mine", "all"] = "mine",
) -> dict:
    """Every order of the buyer (`mine`) or of every buyer (`all`), lines grouped, column computed, facts attached.
    A fixed number of queries whatever the number of orders (see app/workflows/purchasing_orders.py)."""
    require_option(OPTION)
    uow = make_uow()
    with uow:  # the facts rows are read inside: the unit of work expires them on exit
        lines = port.order_lines(uow.session, buyer_id=principal.actor.id if scope == "mine" else None)
        facts = service.load_facts(uow.session, {line.order_number for line in lines})
        orders = service.build_orders(lines, facts, now.date(), principal.actor.id)
    return {
        "today": now.date().isoformat(),
        "scope": scope,
        "columns": [{"key": key, "title": title} for key, title in service.COLUMNS],
        "orders": orders,
    }


def _one(session, number: str, today: date, viewer) -> tuple[dict, list[port.OrderLineFacts]]:
    lines = port.order_lines(session, order_numbers=[number])
    facts = service.load_facts(session, [number])
    order = service.order_json(number, lines, facts.get(number), today, viewer) if lines else None
    if order is None:
        raise HTTPException(status_code=404, detail=f"Заказ {number} не найден")
    return order, lines


def _qty(value: Decimal | None) -> str | None:
    return service._num(value)


@router.get("/followups")
def order_followups(principal: Viewer, make_uow: Uow, now: Now) -> dict:
    """«Дожать»: the buyer's own requests for quotes nobody answered, deliveries late or arriving, invoices not paid —
    ERP's «Сегодня» rules (`procurement.today`), grouped per order and per request."""
    require_option(OPTION)
    uow = make_uow()
    with uow:
        rfq = rfq_followups(uow.session, principal.actor.id, now.date()) if stage_options.enabled("procurement.rfq") else None
        return port.followups(uow.session, principal.actor, now.date(), rfq)


@router.get("/decisions")
def open_decisions(principal: Viewer, make_uow: Uow) -> dict:
    """«Новый счёт»: the buyer's lines with an applied request-for-quotes decision that are not ordered yet, by
    supplier — the decision's quantity, price with VAT, MOQ / pack, the supplier's minimum order and delivery."""
    require_option(OPTION)
    uow = make_uow()
    with uow:
        found = port.open_decisions(uow.session, principal.actor.id)
    suppliers: dict[str, dict] = {}
    for item in found:
        group = suppliers.setdefault(item.supplier, {
            "supplier": item.supplier, "currency": item.currency, "min_order_sum": None, "delivery_cost": None,
            "lines": [],
        })
        if item.min_order_sum is not None and (group["min_order_sum"] is None or item.min_order_sum > Decimal(group["min_order_sum"])):
            group["min_order_sum"] = _qty(item.min_order_sum)
        if item.delivery_cost is not None and item.delivery_cost > 0 and group["delivery_cost"] is None:
            group["delivery_cost"] = _qty(item.delivery_cost)
        if item.currency != group["currency"]:
            group["currency"] = "mixed"
        group["lines"].append({
            "line_id": str(item.line_id), "legacy_id": item.legacy_id, "version": item.version, "name": item.name,
            "article": item.article, "project": item.project, "quantity": _qty(item.quantity), "unit": item.unit,
            "need": _qty(item.need), "decided_quantity": _qty(item.decided_quantity), "unit_gross": _qty(item.unit_gross),
            "total_gross": _qty(item.total_gross), "vat_rate": _qty(item.vat_rate), "vat_included": item.vat_included,
            "unit_price": _qty(item.unit_price), "moq": _qty(item.moq), "pack": _qty(item.pack),
            "lead_days": item.lead_days, "currency": item.currency, "rfq_id": str(item.rfq_id),
            "rfq_number": item.rfq_number, "quote_supplier": item.supplier_name, "blockers": list(item.blockers),
        })
    return {"suppliers": sorted(suppliers.values(), key=lambda group: group["supplier"].casefold())}


@router.get("/{order_number}")
def order_card(order_number: OrderNumber, principal: Viewer, make_uow: Uow, now: Now) -> dict:
    """The order card: the order as on the board, every delivery row of its lines (receiving's own words, read-only),
    the UPDs that name its invoice (option documents.upd)."""
    require_option(OPTION)
    uow = make_uow()
    with uow:
        order, lines = _one(uow.session, order_number, now.date(), principal.actor.id)
        rows = port.receiving_rows(uow.session, [line.id for line in lines])
        upds = order_upds.for_order(uow.session, order, rows) if stage_options.enabled("documents.upd") else None
    legacy = {line.id: line.legacy_id for line in lines}
    return {
        "order": order,
        "receiving": [{
            "id": str(row.id), "procurement_line_id": str(row.procurement_line_id),
            "line_legacy_id": legacy.get(row.procurement_line_id), "legacy_id": row.legacy_id, "status": row.status,
            "unit": row.unit, "ordered_quantity": _qty(row.ordered_quantity),
            "document_quantity": _qty(row.document_quantity), "fact_quantity": _qty(row.fact_quantity),
            "accepted_quantity": _qty(row.accepted_quantity), "backorder_quantity": _qty(row.backorder_quantity),
            "verdict": row.verdict, "nonconformities": list(row.nonconformities),
            "nonconformity_description": row.nonconformity_description, "grn": row.grn, "package": row.package,
            "storage_location": row.storage_location,
            "inspected_on": row.inspected_on.isoformat() if row.inspected_on else None, "inspector": row.inspector,
            "document_number": row.document_number,
            "document_date": row.document_date.isoformat() if row.document_date else None,
            "supplier_returns": row.supplier_returns, "archived": row.archived,
        } for row in rows],
        "upds": upds,
    }


class FactsInput(BaseModel):
    version: int = Field(ge=0)
    sent_to_pay_on: date | None = None
    shipped_on: date | None = None
    shipment_note: str | None = Field(default=None, max_length=SHIPMENT_NOTE_MAX)
    delivery_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    note: str | None = Field(default=None, max_length=ORDER_NOTE_MAX)


@router.patch("/{order_number}/facts")
def save_facts(order_number: OrderNumber, payload: FactsInput, principal: Editor, make_uow: Uow, now: Now) -> dict:
    """Передан в оплату / отгружен / трек / доставка / заметка of one order — only the fields sent; null clears.
    Its buyer only; `version` is the order's facts version (0 — none yet), a stale one is 409."""
    require_option(OPTION)
    fields = {name: getattr(payload, name) for name in payload.model_fields_set if name != "version"}
    change = service.FactsChange(version=payload.version, **fields)

    def run():
        uow = make_uow()
        with uow:
            lines = port.order_lines(uow.session, order_numbers=[order_number])
        service.save_facts(make_uow(), order_number, lines, change, principal.actor, now)
        uow = make_uow()
        with uow:
            order, _ = _one(uow.session, order_number, now.date(), principal.actor.id)
        return {"order": order}

    return _errors(run)
