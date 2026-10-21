"""«Сегодня» закупщика (option procurement.today) and «Дожать» of «Счета и поставки»: the one place their facts are read.

Procurement builds the page (`procurement.today`, pure) and never imports purchasing; this workflow hands it, on the
caller's session (read-only), in a fixed number of queries whatever the number of lines:

* the buyer's own open lines with the dates of their stages (one column select) and their project purposes;
* where they stand in the requests for quotes (`procurement_requests_view.rfq_states` — the same status «Заявки» shows),
  the chosen quotes and the silent suppliers (`procurement_rfq_followups`);
* the order facts of «Счета и поставки» (`purchasing_order_facts.order_facts`: sent to pay, shipped, promised date);
* for «Не успевает к сроку»: every supplier's average payment → arrival and the buyer's request → invoice (one column
  select over the paid lines), and the price-book lead of the items of only those lines nothing nearer is known for;
* Kaiten invoices «В оплату» (option documents.chain) and the UPDs to check (option documents.upd) — shared, not per
  buyer; the count of free lines («Новые доступные»)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.procurement.domain import ProcurementStatus, supplier_key
from app.modules.procurement.infrastructure import ProcurementLineRow as R
from app.modules.procurement.requests_infrastructure import own_open_lines
from app.modules.procurement.requests_view import ViewLine
from app.modules.procurement.today import (
    KaitenInvoice,
    OrderFacts,
    TodayFacts,
    TodaySettings,
    average,
    build_today,
    median,
    order_followups as build_order_followups,
    parse_lead_text,
    pricebook_wanted,
)
from app.modules.purchasing import settings as purchasing_settings
from app.modules.purchasing.infrastructure import InvoiceRow, PriceRefRow, UpdDocumentRow
from app.shared import stage_options
from app.shared.clock import MOSCOW, as_utc

from .procurement_requests_view import rfq_states
from .procurement_rfq_followups import decisions, first_sent, silent_suppliers
from .purchasing_order_facts import order_facts

RFQ_OPTION = "procurement.rfq"
INVOICES_OPTION = "documents.chain"
UPD_OPTION = "documents.upd"
UPD_TO_CHECK = "Требует проверки"
_CHUNK = 500

PurposesOf = Callable[[Sequence[ViewLine]], dict[UUID, str]]


def _day(value) -> date | None:
    if value is None:
        return None
    return as_utc(value).astimezone(MOSCOW).date() if isinstance(value, datetime) else value


def _iso(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def settings_of(session: Session) -> TodaySettings:
    values = purchasing_settings.load(session)
    return TodaySettings(
        quote_wait_days=int(values["quote_wait_days"]), delivery_warn_days=int(values["delivery_warn_days"]),
        board_order_days=int(values["board_order_days"]), board_lead_days=int(values["board_lead_days"]),
    )


def _orders(session: Session, lines: Sequence[ViewLine]) -> dict[str, OrderFacts]:
    facts = order_facts(session, [line.order_number for line in lines if line.order_number])
    return {
        number: OrderFacts(
            sent_to_pay_on=_iso(entry["sent_to_pay_at"]), shipped_on=_iso(entry["shipped_at"]),
            promised=_iso(entry["promised_date"]),
        )
        for number, entry in facts.items()
    }


def _history(session: Session, buyer_id: UUID) -> tuple[dict[str, int], int | None]:
    """Every supplier's average payment → arrival (lines paid and received, any buyer) and this buyer's average
    request → invoice — RB `leadsBySupplier` and `orderDays`, one column select."""
    leads: dict[str, list[int]] = {}
    order_days: list[int] = []
    rows = session.execute(
        select(R.supplier, R.paid_at, R.actual_arrival, R.buyer_id, R.request_date, R.invoice_date)
        .where(R.paid_at.is_not(None))
    )
    for supplier, paid_at, arrival, owner, requested, invoiced in rows:
        paid = _day(paid_at)
        key = supplier_key(supplier)
        if key and arrival is not None and paid is not None:
            leads.setdefault(key, []).append(max(0, (arrival - paid).days))
        if owner == buyer_id and invoiced is not None and requested is not None:
            order_days.append(max(0, (invoiced - requested).days))
    return {key: value for key, value in ((key, average(days)) for key, days in leads.items()) if value is not None}, average(order_days)


def _pricebook(session: Session, lines: Sequence[ViewLine]) -> dict[UUID, int]:
    """Line id → the median lead of its nomenclature item in the price books (RB `pricebookLead`)."""
    if not lines:
        return {}
    # The request pool's attribution of a procurement line to a nomenclature item (link, part number, names).
    from app.workflows.purchasing_rfq_stats import _Nomenclature

    nomenclature = _Nomenclature(session)
    item_of: dict[UUID, UUID] = {}
    for line in lines:
        item_id, _how = nomenclature.match(line.id, line.article, line.name)
        if item_id is not None:
            item_of[line.id] = item_id
    leads: dict[UUID, list[int]] = {}
    items = sorted(set(item_of.values()), key=str)
    for start in range(0, len(items), _CHUNK):
        for item_id, text in session.execute(
            select(PriceRefRow.item_id, PriceRefRow.lead_time)
            .where(PriceRefRow.item_id.in_(items[start:start + _CHUNK]), PriceRefRow.lead_time != "")
        ):
            days = parse_lead_text(text)
            if days is not None:
                leads.setdefault(item_id, []).append(days)
    out = {}
    for line_id, item_id in item_of.items():
        value = median(leads.get(item_id, []))
        if value is not None:
            out[line_id] = value
    return out


def _kaiten(session: Session) -> list[KaitenInvoice] | None:
    if not stage_options.enabled(INVOICES_OPTION):
        return None
    rows = session.execute(
        select(InvoiceRow.title, InvoiceRow.seller_name, InvoiceRow.total, InvoiceRow.card_created)
        .where(InvoiceRow.status == "to_pay")
    )
    # Kaiten stamps cards in UTC: a card made at 01:30 MSK belongs to that local day, not the previous one.
    return [KaitenInvoice(title=title, seller=seller, total=total, created_on=_day(created)) for title, seller, total, created in rows]


def _upd_to_check(session: Session) -> int | None:
    if not stage_options.enabled(UPD_OPTION):
        return None
    # A twin (the PDF of a document that came as EDO XML) needs no review: the XML is in force.
    return int(session.scalar(select(func.count()).select_from(UpdDocumentRow).where(
        UpdDocumentRow.status == UPD_TO_CHECK, UpdDocumentRow.verified_at.is_(None), UpdDocumentRow.twin_of.is_(None),
    )) or 0)


def _available(session: Session) -> int:
    return int(session.scalar(
        select(func.count()).select_from(R).where(R.status == ProcurementStatus.NEW.value)
    ) or 0)


def today_facts(
    session: Session, buyer_id: UUID, today: date, settings: TodaySettings, purposes_of: PurposesOf | None = None,
    *, full: bool = True,
) -> TodayFacts:
    """`full` off — «Дожать»: only the lines, their requests and orders (no lead history, no documents)."""
    lines = own_open_lines(session, buyer_id)
    ids = [line.id for line in lines]
    states = rfq_states(session, ids)
    rfq_on = stage_options.enabled(RFQ_OPTION)
    facts = TodayFacts(
        lines=lines,
        purposes=purposes_of(lines) if purposes_of is not None and full else {},
        rfq=states,
        orders=_orders(session, lines),
        decisions=decisions(session, ids),
        rfq_sent=first_sent(session, {state.rfq_id for state in states.values() if state.rfq_id}) if full else {},
        silent=silent_suppliers(session, buyer_id) if rfq_on else (),
        rfq_on=rfq_on,
    )
    if not full:
        return facts
    leads, order_days = _history(session, buyer_id)
    facts = replace(facts, supplier_leads=leads, order_days=order_days)
    wanted = pricebook_wanted(facts, today, settings)
    return replace(
        facts, pricebook_leads=_pricebook(session, wanted), kaiten=_kaiten(session),
        upd_to_check=_upd_to_check(session), available=_available(session),
    )


def today_page(session: Session, buyer_id: UUID, today: date, purposes_of: PurposesOf | None = None) -> dict:
    settings = settings_of(session)
    return build_today(today_facts(session, buyer_id, today, settings, purposes_of), today, settings)


def order_followups(session: Session, buyer_id: UUID, today: date) -> dict:
    """«Дожать» of «Счета и поставки»: the same rules as «Сегодня» (`procurement.today.followups`)."""
    settings = settings_of(session)
    return build_order_followups(today_facts(session, buyer_id, today, settings, full=False), today, settings)
