"""Reads of «Заявки» закупщика (option procurement.requests_rb) over procurement's own tables, as column projections.

A whole-row hydration of procurement lines costs ~0.45 s on 5 000 lines (see `app/workflows/purchasing_procurement.py`);
this view needs a few columns of every line of the scope, so it selects only those, once."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.shared.clock import MOSCOW, as_utc
from app.shared.persistence import AuditEventRow

from .domain import ProcurementStatus
from .infrastructure import ProcurementLineRow as R
from .price_history import PriceCandidate, PriceItem, article_key, name_key, past_purchases, unit_price_with_vat
from .requests_view import LastPurchase, ViewLine

_CHUNK = 500
_VIEW_COLUMNS = (
    R.id, R.legacy_id, R.version, R.status, R.buyer_id, R.buyer_name, R.source_request_id, R.created_at,
    R.request_date, R.planned_date, R.initiator, R.designation, R.project_reference, R.item_type, R.category,
    R.calculated_name, R.design_article, R.calculated_quantity, R.calculated_unit, R.stock_covered, R.calculated_price,
    R.calculated_price_with_vat, R.link, R.supplier, R.paid_at, R.invoice_url, R.supplier_request_reference,
    R.order_number,
)
# «Сегодня» (option procurement.today) reads the dates of the stages on top.
_TODAY_COLUMNS = (
    *_VIEW_COLUMNS, R.expected_arrival, R.actual_arrival, R.invoice_date, R.order_date, R.invoice_number,
    R.invoice_item_name,
)
_CLOSED = (ProcurementStatus.DONE.value, ProcurementStatus.CANCELLED.value)
_PAID_COLUMNS = (
    R.id, R.design_article, R.calculated_name, R.invoice_item_name, R.supplier, R.paid_at, R.currency, R.actual_price,
    R.actual_vat_rate, R.actual_price_with_vat, R.source_price, R.source_price_with_vat, R.exchange_rate,
    R.actual_unit, R.actual_quantity, R.order_number,
)


def _view(row) -> ViewLine:
    return ViewLine(
        id=row.id, legacy_id=row.legacy_id, version=row.version, status=row.status, buyer_id=row.buyer_id,
        buyer_name=row.buyer_name, source_request_id=row.source_request_id, created_at=row.created_at,
        request_date=row.request_date, planned_date=row.planned_date, initiator=row.initiator,
        designation=row.designation, project_reference=row.project_reference, item_type=row.item_type,
        category=row.category, name=row.calculated_name, article=row.design_article,
        quantity=row.calculated_quantity, unit=row.calculated_unit, stock_covered=row.stock_covered,
        price=row.calculated_price, price_with_vat=row.calculated_price_with_vat, link=row.link,
        supplier=row.supplier, paid=row.paid_at is not None, invoiced=bool(row.invoice_url),
        requested=bool(row.supplier_request_reference), order_number=row.order_number,
    )


def _local_day(value) -> date | None:
    """A stored moment as the Moscow day it happened on (a payment at 01:30 MSK belongs to that day)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value).astimezone(MOSCOW).date()
    return value


def _today_view(row) -> ViewLine:
    return replace(
        _view(row), expected_arrival=row.expected_arrival, actual_arrival=row.actual_arrival,
        invoice_date=row.invoice_date, paid_on=_local_day(row.paid_at), order_date=row.order_date,
        invoice_number=row.invoice_number, invoice_name=row.invoice_item_name,
    )


def own_open_lines(session: Session, buyer_id: UUID) -> list[ViewLine]:
    """«Сегодня» (option procurement.today): the buyer's own lines that are not closed («Готово», «Отменено»), with
    the dates of their stages — one column select."""
    statement = select(*_TODAY_COLUMNS).where(R.buyer_id == buyer_id, R.status.not_in(_CLOSED))
    return [_today_view(row) for row in session.execute(statement.order_by(R.created_at, R.id))]


def view_lines(session: Session, buyer_id: UUID | None) -> list[ViewLine]:
    """«Мои» (`buyer_id`): the buyer's lines plus the unclaimed «Новый» ones he may take; None — every line («Все»)."""
    statement = select(*_VIEW_COLUMNS)
    if buyer_id is not None:
        statement = statement.where(or_(
            R.buyer_id == buyer_id,
            and_(R.buyer_id.is_(None), R.status == ProcurementStatus.NEW.value),
        ))
    return [_view(row) for row in session.execute(statement.order_by(R.created_at, R.id))]


def cancel_reasons(session: Session, line_ids: Iterable[UUID]) -> dict[UUID, str]:
    """The reason of the latest cancellation of each line («Отменить выбранные» keeps it in the audit event)."""
    wanted = list(dict.fromkeys(line_ids))
    found: dict[UUID, tuple[datetime, str]] = {}
    for start in range(0, len(wanted), _CHUNK):
        rows = session.execute(
            select(AuditEventRow.aggregate_id, AuditEventRow.occurred_at, AuditEventRow.details).where(
                AuditEventRow.aggregate_id.in_(wanted[start:start + _CHUNK]),
                AuditEventRow.event_type == "procurement.cancelled",
            )
        )
        for aggregate_id, occurred_at, details in rows:
            reason = str((details or {}).get("reason") or "").strip()
            moment = occurred_at.replace(tzinfo=None) if isinstance(occurred_at, datetime) else occurred_at
            if reason and (aggregate_id not in found or found[aggregate_id][0] <= moment):
                found[aggregate_id] = (moment, reason)
    return {line_id: reason for line_id, (_, reason) in found.items()}


def _day(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def last_paid_purchases(session: Session, lines: Sequence[ViewLine]) -> dict[UUID, LastPurchase]:
    """«Покупали»: the latest paid procurement line of the same item (part number, else the name — `price_history`),
    read once and indexed, not one query per line."""
    if not lines:
        return {}
    by_article: dict[str, list[PriceCandidate]] = {}
    by_name: dict[str, list[PriceCandidate]] = {}
    for row in session.execute(select(*_PAID_COLUMNS).where(R.paid_at.is_not(None))):
        candidate = PriceCandidate(
            item=PriceItem(row.id, row.design_article, (row.calculated_name, row.invoice_item_name or "")),
            paid_at=row.paid_at, supplier=row.supplier,
            unit_price_with_vat=unit_price_with_vat(
                currency=row.currency, price=row.actual_price, vat_rate=row.actual_vat_rate,
                price_with_vat=row.actual_price_with_vat, source_price=row.source_price,
                source_price_with_vat=row.source_price_with_vat, exchange_rate=row.exchange_rate,
            ),
            unit=row.actual_unit, quantity=row.actual_quantity, order_number=row.order_number,
        )
        key = article_key(row.design_article)
        if key:
            by_article.setdefault(key, []).append(candidate)
        for name in {name_key(text) for text in candidate.item.names if name_key(text)}:
            by_name.setdefault(name, []).append(candidate)
    result: dict[UUID, LastPurchase] = {}
    for line in lines:
        target = PriceItem(line.id, line.article, (line.name,))
        key = article_key(line.article)
        pool = by_article.get(key, []) if key else by_name.get(name_key(line.name), [])
        found = past_purchases(target, pool, limit=1)
        if found:
            last = found[0]
            result[line.id] = LastPurchase(
                price=last.price_with_vat, on=_day(last.paid_at), supplier=last.supplier, unit=last.unit,
                source="procurement",
            )
    return result
