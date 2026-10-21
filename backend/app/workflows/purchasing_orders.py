"""«Счета и поставки» (option procurement.orders): what purchasing reads of procurement, receiving and the audit log.

An ERP supplier order is the procurement lines sharing one № ЗАКАЗА. Purchasing never imports those modules; this
workflow hands it plain facts, read-only, in a fixed number of queries whatever the number of orders:

* `order_lines` — the order lines (one column select), their promised date derived from the audit of ОЖИД. ДАТА (one
  query), what receiving accepted of them (one query per 500 paid lines) and, for the lines «В оплату», what the pay
  gate still misses (one query);
* `receiving_rows` — the delivery rows of one order for its card;
* `open_decisions` — the buyer's lines with an applied request-for-quotes decision that are not ordered yet;
* «Дожать» lives with «Сегодня» (`app/workflows/procurement_today.py`): one source of each rule.

The promised date («обещано», Roller_Buyer's immutable `promised_date`) is not stored anywhere: ERP keeps only the
current ОЖИД. ДАТА on the line, but every change of it after the invoice is audited with the previous value
(`procurement.expected_arrival_set`, `previous_expected_arrival`). So the date the line had when it got its invoice is
the previous value of its first audited change made from «В оплату» on, and the current date when there was none."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.procurement.domain import ProcurementStatus, is_backorder_id
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementRepository
from app.modules.purchasing.infrastructure import (
    DecisionRow,
    QuoteLineRow,
    QuoteRow,
    RfqLineRow,
    RfqRow,
    SupplierRow,
)
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.shared.persistence import AuditEventRow

_MOSCOW = ZoneInfo("Europe/Moscow")
_CHUNK = 500
_ARCHIVE = "Архив ВК"
_ACCEPTED = "Принято"

# The stages a supplier order goes through, in order; «Согласование» holds only the order's `-Д` backorders here.
ORDER_STAGES = (
    ProcurementStatus.AWAITING_INVOICE,
    ProcurementStatus.TO_PAY,
    ProcurementStatus.IN_TRANSIT,
    ProcurementStatus.QUARANTINE,
    ProcurementStatus.DONE,
)
_READ_STATUSES = [status.value for status in (*ORDER_STAGES, ProcurementStatus.APPROVAL)]
# From these stages on the line has its invoice: a change of ОЖИД. ДАТА is a reschedule, not the first promise.
_INVOICED = frozenset(status.value for status in ORDER_STAGES[1:])
EXPECTED_SET = "procurement.expected_arrival_set"


@dataclass(frozen=True)
class Receipt:
    """What receiving holds of one procurement line (live rows, batches; never the archive)."""

    accepted: Decimal = Decimal("0")
    counted: Decimal = Decimal("0")
    rows: int = 0
    inspected_on: date | None = None


@dataclass(frozen=True)
class OrderLineFacts:
    id: UUID
    legacy_id: str
    version: int
    status: str
    order_number: str
    order_date: date | None
    buyer_id: UUID | None
    buyer_name: str
    supplier: str
    name: str
    calculated_name: str
    invoice_item_name: str | None
    article: str | None
    project: str | None
    quantity: Decimal | None
    unit: str
    price_with_vat: Decimal | None
    actual_sum: Decimal | None
    calculated_sum: Decimal | None
    currency: str | None
    source_currency: str | None
    source_sum: Decimal | None
    expected_arrival: date | None
    promised: date | None
    invoice_number: str | None
    invoice_date: date | None
    invoice_url: str | None
    paid_on: date | None
    actual_arrival: date | None
    created_on: date
    note: str | None
    is_backorder: bool
    receipt: Receipt = Receipt()
    pay_missing: tuple[str, ...] = ()


def _day(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(_MOSCOW).date() if value.tzinfo is not None else value.date()
    return value


def _chunks(values: Sequence, size: int = _CHUNK) -> Iterable[Sequence]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _promised(session: Session, invoiced: dict[UUID, date | None]) -> dict[UUID, date | None]:
    """Line id → the ОЖИД. ДАТА it had when it got its invoice (see the module doc). One query over the audit's
    reschedules; the event type is indexed and the rows are few (one per change of a date)."""
    if not invoiced:
        return {}
    first: dict[UUID, tuple[datetime, dict]] = {}
    rows = session.execute(
        select(AuditEventRow.aggregate_id, AuditEventRow.occurred_at, AuditEventRow.details)
        .where(AuditEventRow.event_type == EXPECTED_SET)
    )
    for aggregate_id, occurred_at, details in rows:
        if aggregate_id not in invoiced or not isinstance(details, dict):
            continue
        if str(details.get("from_status") or "") not in _INVOICED:
            continue
        moment = occurred_at.replace(tzinfo=None) if isinstance(occurred_at, datetime) else occurred_at
        known = first.get(aggregate_id)
        if known is None or moment < known[0]:
            first[aggregate_id] = (moment, details)
    out: dict[UUID, date | None] = {}
    for line_id, current in invoiced.items():
        found = first.get(line_id)
        if found is None:
            out[line_id] = current
            continue
        details = found[1]
        previous = str(details.get("previous_expected_arrival") or "") or str(details.get("expected_arrival") or "")
        try:
            out[line_id] = date.fromisoformat(previous[:10]) if previous else current
        except ValueError:
            out[line_id] = current
    return out


def _receipts(session: Session, line_ids: Sequence[UUID]) -> dict[UUID, Receipt]:
    R = ReceivingLineRow
    sums: dict[UUID, dict] = {}
    for chunk in _chunks(list(line_ids)):
        rows = session.execute(
            select(R.procurement_line_id, R.status, R.fact_quantity, R.accepted_quantity, R.inspected_on)
            .where(R.procurement_line_id.in_(chunk), R.status != _ARCHIVE)
        )
        for line_id, status, counted, accepted, inspected_on in rows:
            entry = sums.setdefault(line_id, {"accepted": Decimal("0"), "counted": Decimal("0"), "rows": 0, "on": None})
            entry["rows"] += 1
            if status == _ACCEPTED and accepted is not None:
                entry["accepted"] += accepted
            if counted is not None:
                entry["counted"] += counted
            if inspected_on is not None and (entry["on"] is None or inspected_on > entry["on"]):
                entry["on"] = inspected_on
    return {
        line_id: Receipt(entry["accepted"], entry["counted"], entry["rows"], entry["on"]) for line_id, entry in sums.items()
    }


def _pay_missing(session: Session, line_ids: set[UUID]) -> dict[UUID, tuple[str, ...]]:
    """What the pay gate still misses on the lines «В оплату» (the domain rule itself, so it never drifts)."""
    if not line_ids:
        return {}
    wanted = list(line_ids)
    # One query per 500 rows; the repository's get() then reads the session's identity map (the rows are held here).
    held = [row for chunk in _chunks(wanted) for row in session.scalars(select(ProcurementLineRow).where(ProcurementLineRow.id.in_(chunk)))]
    repository = SqlAlchemyProcurementRepository(session)
    out = {}
    for line_id in wanted:
        line = repository.get(line_id)
        if line is not None and line.status is ProcurementStatus.TO_PAY:
            out[line_id] = tuple(line.pay_missing())
    del held
    return out


def order_lines(
    session: Session, *, buyer_id: UUID | None = None, order_numbers: Sequence[str] | None = None,
    with_receipts: bool = True, with_pay_missing: bool = True,
) -> list[OrderLineFacts]:
    """The lines of supplier orders, «Жду счет» … «Готово» plus the orders' backorders; cancelled lines never.
    `buyer_id` — one buyer's orders only; `order_numbers` — only these orders. `with_receipts` / `with_pay_missing` off
    — the order facts port (promised dates only) skips what receiving accepted and the pay gate."""
    R = ProcurementLineRow
    columns = (
        R.id, R.legacy_id, R.version, R.status, R.order_number, R.order_date, R.buyer_id, R.buyer_name, R.supplier,
        R.calculated_name, R.invoice_item_name, R.design_article, R.project_reference, R.actual_quantity,
        R.calculated_quantity, R.actual_unit, R.calculated_unit, R.actual_price_with_vat, R.actual_sum,
        R.calculated_sum, R.currency, R.source_currency, R.source_sum, R.expected_arrival, R.invoice_number,
        R.invoice_date, R.invoice_url, R.paid_at, R.actual_arrival, R.created_at, R.note,
    )
    statement = select(*columns).where(R.order_number.is_not(None), R.status.in_(_READ_STATUSES))
    if buyer_id is not None:
        statement = statement.where(R.buyer_id == buyer_id)
    rows = []
    if order_numbers is not None:
        wanted = sorted({number for number in order_numbers if number})
        for chunk in _chunks(wanted):
            rows.extend(session.execute(statement.where(R.order_number.in_(chunk))).all())
    else:
        rows = list(session.execute(statement.order_by(R.order_number, R.legacy_id)).all())
    rows = [row for row in rows if (row.order_number or "").strip()]
    # A `-Д` row in «Согласование» is a backorder; any other «Согласование» row is not an order line.
    rows = [row for row in rows if row.status != ProcurementStatus.APPROVAL.value or is_backorder_id(row.legacy_id)]
    invoiced = {row.id: row.expected_arrival for row in rows if row.status in _INVOICED and not is_backorder_id(row.legacy_id)}
    promised = _promised(session, invoiced)
    in_receiving = [row.id for row in rows if row.status in _INVOICED and row.status != ProcurementStatus.TO_PAY.value]
    receipts = _receipts(session, in_receiving) if with_receipts else {}
    to_pay = {row.id for row in rows if row.status == ProcurementStatus.TO_PAY.value}
    missing = _pay_missing(session, to_pay) if with_pay_missing else {}
    out = []
    for row in rows:
        quantity = row.actual_quantity if row.actual_quantity is not None else row.calculated_quantity
        out.append(OrderLineFacts(
            id=row.id, legacy_id=row.legacy_id, version=row.version, status=row.status,
            order_number=row.order_number.strip(), order_date=_day(row.order_date), buyer_id=row.buyer_id,
            buyer_name=row.buyer_name or "", supplier=(row.supplier or "").strip(),
            name=(row.invoice_item_name or "").strip() or row.calculated_name, calculated_name=row.calculated_name,
            invoice_item_name=row.invoice_item_name, article=row.design_article, project=row.project_reference,
            quantity=quantity, unit=row.actual_unit or row.calculated_unit, price_with_vat=row.actual_price_with_vat,
            actual_sum=row.actual_sum, calculated_sum=row.calculated_sum, currency=row.currency,
            source_currency=row.source_currency, source_sum=row.source_sum, expected_arrival=_day(row.expected_arrival),
            promised=promised.get(row.id), invoice_number=row.invoice_number, invoice_date=_day(row.invoice_date),
            invoice_url=row.invoice_url, paid_on=_day(row.paid_at), actual_arrival=_day(row.actual_arrival),
            created_on=_day(row.created_at), note=row.note, is_backorder=is_backorder_id(row.legacy_id),
            receipt=receipts.get(row.id, Receipt()), pay_missing=missing.get(row.id, ()),
        ))
    return out


def order_numbers_of(session: Session, line_ids: Sequence[UUID]) -> dict[UUID, str]:
    """Procurement line id → its № ЗАКАЗА (lines without one are left out)."""
    R = ProcurementLineRow
    out: dict[UUID, str] = {}
    for chunk in _chunks(list(line_ids)):
        for line_id, number in session.execute(select(R.id, R.order_number).where(R.id.in_(chunk))):
            if (number or "").strip():
                out[line_id] = number.strip()
    return out


# ---- the order card: deliveries ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ReceivingRow:
    id: UUID
    procurement_line_id: UUID
    legacy_id: str
    status: str
    unit: str
    ordered_quantity: Decimal
    document_quantity: Decimal | None
    fact_quantity: Decimal | None
    accepted_quantity: Decimal | None
    backorder_quantity: Decimal
    verdict: str | None
    nonconformities: tuple[str, ...]
    nonconformity_description: str
    grn: str | None
    package: str
    storage_location: str | None
    inspected_on: date | None
    inspector: str | None
    document_number: str
    document_date: date | None
    supplier_returns: int
    archived: bool


def receiving_rows(session: Session, line_ids: Sequence[UUID]) -> list[ReceivingRow]:
    """Every delivery row of the lines, the archived ВК revisions last (they are history, not receipts)."""
    R = ReceivingLineRow
    out: list[ReceivingRow] = []
    for chunk in _chunks(list(line_ids)):
        for row in session.scalars(select(R).where(R.procurement_line_id.in_(chunk))):
            out.append(ReceivingRow(
                id=row.id, procurement_line_id=row.procurement_line_id, legacy_id=row.legacy_id, status=row.status,
                unit=row.unit, ordered_quantity=row.ordered_quantity, document_quantity=row.document_quantity,
                fact_quantity=row.fact_quantity, accepted_quantity=row.accepted_quantity,
                backorder_quantity=row.backorder_quantity, verdict=row.verdict,
                nonconformities=tuple(row.nonconformities or ()), nonconformity_description=row.nonconformity_description or "",
                grn=row.grn, package=row.package or "", storage_location=row.storage_location,
                inspected_on=row.inspected_on, inspector=row.inspector_name, document_number=row.document_number or "",
                document_date=row.document_date, supplier_returns=row.supplier_returns or 0,
                archived=row.status == _ARCHIVE,
            ))
    out.sort(key=lambda row: (row.archived, row.legacy_id, row.grn or "", row.package))
    return out


# ---- «Новый счёт»: applied decisions not ordered yet ---------------------------------------------------------------

@dataclass(frozen=True)
class OpenDecision:
    line_id: UUID
    legacy_id: str
    version: int
    name: str
    article: str | None
    project: str | None
    supplier: str
    quantity: Decimal | None  # КОЛ-ВО (факт) of the line: what the supplier request orders
    unit: str
    need: Decimal  # what the request for quotes asked for
    decision_id: UUID
    decided_quantity: Decimal
    unit_gross: Decimal
    total_gross: Decimal
    vat_rate: Decimal
    vat_included: bool
    unit_price: Decimal | None
    moq: Decimal | None
    pack: Decimal | None
    lead_days: int | None
    currency: str
    min_order_sum: Decimal | None
    delivery_cost: Decimal | None
    quote_id: UUID
    rfq_id: UUID
    rfq_number: str
    supplier_name: str  # the directory's name of the quote's supplier
    blockers: tuple[str, ...] = field(default=())


def open_decisions(session: Session, buyer_id: UUID) -> list[OpenDecision]:
    """The buyer's lines «Готов к запросу» without № ЗАКАЗА whose request-for-quotes decision is applied (the supplier
    and the quantity are in the line). Three queries: decisions with their quote, the lines, nothing per line."""
    rows = session.execute(
        select(DecisionRow, RfqLineRow, QuoteLineRow, QuoteRow, SupplierRow.name, RfqRow.number)
        .join(RfqLineRow, RfqLineRow.id == DecisionRow.rfq_line_id)
        .join(QuoteLineRow, QuoteLineRow.id == DecisionRow.quote_line_id)
        .join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id)
        .join(SupplierRow, SupplierRow.id == QuoteRow.supplier_id)
        .join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
        .where(DecisionRow.applied_at.is_not(None), RfqLineRow.removed_at.is_(None))
    ).all()
    if not rows:
        return []
    by_line = {rfq_line.procurement_line_id: (decision, rfq_line, quote_line, quote, name, number)
               for decision, rfq_line, quote_line, quote, name, number in rows}
    R = ProcurementLineRow
    columns = (
        R.id, R.legacy_id, R.version, R.status, R.buyer_id, R.order_number, R.supplier, R.calculated_name,
        R.design_article, R.project_reference, R.actual_quantity, R.actual_unit, R.calculated_unit, R.delivery_point,
        R.replacement, R.replacement_decision,
    )
    out: list[OpenDecision] = []
    for chunk in _chunks(list(by_line)):
        lines = session.execute(select(*columns).where(
            R.id.in_(chunk), R.buyer_id == buyer_id, R.status == ProcurementStatus.READY.value,
        ))
        for line in lines:
            if (line.order_number or "").strip():
                continue
            decision, rfq_line, quote_line, quote, supplier_name, rfq_number = by_line[line.id]
            blockers = []
            if not (line.delivery_point or "").strip():
                blockers.append("не указана точка отгрузки (склад)")
            if line.actual_quantity is None or line.actual_quantity <= 0 or not (line.actual_unit or "").strip():
                blockers.append("нет количества или единицы (факт)")
            if line.replacement is True and line.replacement_decision != "Разрешено":
                blockers.append("замена без решения «Разрешено»")
            out.append(OpenDecision(
                line_id=line.id, legacy_id=line.legacy_id, version=line.version, name=line.calculated_name,
                article=line.design_article, project=line.project_reference, supplier=(line.supplier or "").strip(),
                quantity=line.actual_quantity, unit=line.actual_unit or line.calculated_unit, need=rfq_line.quantity,
                decision_id=decision.id, decided_quantity=decision.cover_quantity, unit_gross=decision.unit_gross,
                total_gross=decision.total_gross, vat_rate=quote_line.vat_rate, vat_included=quote_line.vat_included,
                unit_price=quote_line.unit_price, moq=quote_line.moq, pack=quote_line.pack, lead_days=quote_line.lead_days,
                currency=quote.currency, min_order_sum=quote.min_order_sum, delivery_cost=quote.delivery_cost,
                quote_id=quote.id, rfq_id=rfq_line.rfq_id, rfq_number=rfq_number, supplier_name=supplier_name,
                blockers=tuple(blockers),
            ))
    out.sort(key=lambda item: (item.supplier.casefold(), item.legacy_id))
    return out


def by_order(lines: Iterable[OrderLineFacts]) -> dict[str, list[OrderLineFacts]]:
    grouped: dict[str, list[OrderLineFacts]] = defaultdict(list)
    for line in lines:
        grouped[line.order_number].append(line)
    return dict(grouped)
