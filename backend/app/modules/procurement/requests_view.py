"""Stage option `procurement.requests_rb`: Roller_Buyer's «Заявки» over procurement lines, computed on read.

The procurement line stays the one entity; RB's «заявка» is a view of it: project (`domain.line_project`) → request batch
(`domain.batch_key`: the lines sharing НАЗНАЧЕНИЕ) → lines. Each line gets RB's status (`domain/status.ts`
`deriveLineStatus`), rebuilt from the line's stage and the facts other modules hand in through `app/workflows` ports:
its place in a request for quotes (`RfqState`), the order facts of the orders slice (`OrderState`, optional), the free
stock, the last purchase. Pure functions — nothing is stored for this view."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlparse
from uuid import UUID

from .domain import (
    FACT_PRICE_VAT,
    FACTS_EDITABLE,
    STOCK_COVER_STAGES,
    LineProject,
    ProcurementStatus,
    batch_key,
    is_backorder_id,
    is_web_link,
    line_project,
    supplier_from_link,
    supplier_key,
)

# RB's LINE_STATUSES order (packages/shared/src/requests.ts) without «Давальческое» (no such fact in ERP).
RB_STATUSES = (
    "new", "requote", "from_stock", "in_rfq", "quoted", "decided", "ordered", "to_pay", "paid", "shipped", "partial",
    "delivered", "cancelled",
)
# Lines a request for quotes is made of: to buy and in no live request now (RB `selectable`).
RFQ_READY = frozenset({"new", "requote"})
_PROJECT_KIND_ORDER = {"project": 0, "purpose": 1, "none": 2}


@dataclass(frozen=True)
class RfqState:
    """Where the line stands in the requests for quotes (purchasing, via `app/workflows`)."""

    live: bool = False  # in a request now (`purchasing_rfq_lines.removed_at` empty)
    rfq_id: UUID | None = None
    rfq_number: str | None = None
    quoted: bool = False  # a quote with a price on the live request line
    decided: bool = False  # a supplier chosen on it
    requote: bool = False  # taken out of a request to be asked again and in none now
    requote_from: str | None = None  # the request it left


@dataclass(frozen=True)
class OrderState:
    """Order facts of the orders slice (`order_number` → sent to pay / shipped); absent until that slice is wired."""

    sent_to_pay: bool = False
    shipped: bool = False


@dataclass(frozen=True)
class ViewLine:
    """The columns of a procurement line this view reads (a projection, never a whole 80-column row)."""

    id: UUID
    legacy_id: str
    version: int
    status: str
    buyer_id: UUID | None
    buyer_name: str | None
    source_request_id: UUID
    created_at: datetime
    request_date: date
    planned_date: date
    initiator: str
    designation: str
    project_reference: str | None
    item_type: str
    category: str
    name: str
    article: str | None
    quantity: Decimal
    unit: str
    stock_covered: Decimal = Decimal("0")
    price: Decimal | None = None
    price_with_vat: Decimal | None = None
    link: str | None = None
    supplier: str | None = None
    paid: bool = False
    invoiced: bool = False
    requested: bool = False
    order_number: str | None = None

    @property
    def to_buy(self) -> Decimal:
        return max(self.quantity - self.stock_covered, Decimal("0"))

    @property
    def backorder(self) -> bool:
        return is_backorder_id(self.legacy_id)


@dataclass(frozen=True)
class LastPurchase:
    price: Decimal  # roubles per unit with VAT
    on: date
    supplier: str | None
    unit: str | None
    source: str  # "procurement" — a paid procurement line, "upd" — a UPD of the registry


def rb_status(line: ViewLine, rfq: RfqState | None = None, order: OrderState | None = None) -> str:
    """`deriveLineStatus` on ERP facts: the furthest fact wins.

    Отменено → cancelled; Готово closed by «Со склада» without payment → from_stock, otherwise delivered; Карантин and a
    `-Д` remainder waiting in «Согласование» → partial; В пути → paid (shipped with the order fact or a confirmed `-Д`
    resend); В оплату → ordered (to_pay once the invoice was handed over for payment); Жду счет → decided. On the buyer's
    desk the request for quotes decides: decided / quoted / in_rfq, out of a request to be asked again → requote,
    else new."""
    status = line.status
    if status == ProcurementStatus.CANCELLED.value:
        return "cancelled"
    if status == ProcurementStatus.DONE.value:
        return "from_stock" if not line.paid and line.stock_covered > 0 else "delivered"
    if status == ProcurementStatus.QUARANTINE.value:
        return "partial"
    parked = status == ProcurementStatus.APPROVAL.value
    if parked and line.backorder:
        return "partial"
    if status == ProcurementStatus.IN_TRANSIT.value:
        return "shipped" if line.backorder or (order is not None and order.shipped) else "paid"
    if status == ProcurementStatus.TO_PAY.value or (parked and line.invoiced):
        return "to_pay" if order is not None and order.sent_to_pay else "ordered"
    if status == ProcurementStatus.AWAITING_INVOICE.value or (parked and line.requested):
        return "decided"
    if rfq is not None and rfq.live:
        if rfq.decided:
            return "decided"
        return "quoted" if rfq.quoted else "in_rfq"
    if rfq is not None and rfq.requote:
        return "requote"
    return "new"


def plan_unit_gross(line: ViewLine) -> Decimal | None:
    """ЦЕНА С НДС (Расчет); without it the net calc price plus 22 % (as `budget_unit_gross` of the RFQ bridge)."""
    if line.price_with_vat is not None:
        return line.price_with_vat
    if line.price is not None:
        return (line.price * (100 + FACT_PRICE_VAT) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return None


def plan_label(link: str | None, suppliers: Mapping[str, str]) -> str:
    """Where the calculation meant to buy: the directory's supplier for the link's domain, the domain itself, or the
    word written instead of a link («Фраксис»). Empty — no plan."""
    text = " ".join(str(link or "").split())
    if not text:
        return ""
    if is_web_link(text):
        known = supplier_from_link(text, dict(suppliers))
        if known:
            return known
        host = (urlparse(text).hostname or "").removeprefix("www.")
        return host or supplier_key(text)
    return text[:80]


@dataclass
class _Group:
    counts: dict[str, int] = field(default_factory=dict)
    lines: int = 0

    def add(self, status: str) -> None:
        self.counts[status] = self.counts.get(status, 0) + 1
        self.lines += 1

    def ordered_counts(self) -> dict[str, int]:
        return {status: self.counts[status] for status in RB_STATUSES if status in self.counts}


def _project_result(project: LineProject) -> dict:
    return {"key": project.key, "kind": project.kind, "value": project.value}


def selectable(line: ViewLine, status: str, actor_id: UUID | None) -> bool:
    """RB `selectable` for several buyers: one's own line on the desk, with something to buy, in no live request."""
    return (
        actor_id is not None
        and line.buyer_id == actor_id
        and line.status in {stage.value for stage in FACTS_EDITABLE}
        and status in RFQ_READY
        and line.to_buy > 0
    )


def build_summary(
    lines: Sequence[ViewLine],
    purposes: Mapping[UUID, str],
    rfq: Mapping[UUID, RfqState],
    actor_id: UUID | None,
    *,
    project: str | None = None,
    orders: Mapping[str, OrderState] | None = None,
    focus: UUID | None = None,
) -> dict:
    """Projects of the scope with their «Ход закупки», and the request batches of the chosen project (all without one),
    newest first. `focus` (a `&line=` link) names the batch and the project holding that line."""
    projects: dict[str, tuple[LineProject, _Group]] = {}
    batches: dict[str, dict] = {}
    found: dict | None = None
    for line in lines:
        where = line_project(line.project_reference, line.designation, purposes.get(line.source_request_id))
        status = rb_status(line, rfq.get(line.id), (orders or {}).get(line.order_number or ""))
        projects.setdefault(where.key, (where, _Group()))[1].add(status)
        key = batch_key(where, line.designation)
        if focus is not None and line.id == focus:
            found = {"project": where.key, "batch": key}
        if project and where.key != project:
            continue
        batch = batches.get(key)
        if batch is None:
            batch = batches[key] = {
                "key": key, "project": _project_result(where), "designation": line.designation,
                "request_date": line.request_date, "created_at": line.created_at, "planned_date": line.planned_date,
                "initiators": set(), "group": _Group(), "mine": 0, "selectable": 0, "to_take": 0,
            }
        batch["request_date"] = min(batch["request_date"], line.request_date)
        batch["created_at"] = max(batch["created_at"], line.created_at)
        batch["planned_date"] = min(batch["planned_date"], line.planned_date)
        if line.initiator:
            batch["initiators"].add(line.initiator)
        batch["group"].add(status)
        if actor_id is not None and line.buyer_id == actor_id:
            batch["mine"] += 1
        if selectable(line, status, actor_id):
            batch["selectable"] += 1
        if line.buyer_id is None and line.status == ProcurementStatus.NEW.value:
            batch["to_take"] += 1
    ordered = sorted(
        projects.values(), key=lambda item: (_PROJECT_KIND_ORDER.get(item[0].kind, 3), item[0].value.casefold()),
    )
    return {
        "projects": [
            {**_project_result(where), "lines": group.lines, "counts": group.ordered_counts()} for where, group in ordered
        ],
        "batches": [
            {
                "key": batch["key"],
                "project": batch["project"],
                "designation": batch["designation"],
                "request_date": batch["request_date"].isoformat(),
                "planned_date": batch["planned_date"].isoformat(),
                "initiators": sorted(batch["initiators"], key=str.casefold),
                "lines": batch["group"].lines,
                "counts": batch["group"].ordered_counts(),
                "mine": batch["mine"],
                "selectable": batch["selectable"],
                "to_take": batch["to_take"],
            }
            for batch in sorted(batches.values(), key=lambda item: (item["created_at"], item["key"]), reverse=True)
        ],
        "focus": found,
    }


def batch_lines(
    lines: Iterable[ViewLine],
    batch: str,
    purposes: Mapping[UUID, str],
) -> list[ViewLine]:
    """The lines of one batch, in the order they were created (RB `orderBy(requestLines.id)`)."""
    chosen = [
        line for line in lines
        if batch_key(line_project(line.project_reference, line.designation, purposes.get(line.source_request_id)),
                     line.designation) == batch
    ]
    return sorted(chosen, key=lambda line: (line.created_at, line.legacy_id))


def _num(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def line_results(
    lines: Sequence[ViewLine],
    rfq: Mapping[UUID, RfqState],
    actor_id: UUID | None,
    *,
    suppliers: Mapping[str, str] | None = None,
    free_stock: Mapping[UUID, Decimal] | None = None,
    last: Mapping[UUID, LastPurchase] | None = None,
    reasons: Mapping[UUID, str] | None = None,
    orders: Mapping[str, OrderState] | None = None,
) -> list[dict]:
    """The rows of RB's `RequestLinesTable`: plan (price with VAT and where), last purchase, free stock, status, and what
    the viewer may do with the line."""
    rows = []
    for line in lines:
        state = rfq.get(line.id)
        order = (orders or {}).get(line.order_number or "")
        status = rb_status(line, state, order)
        mine = actor_id is not None and line.buyer_id == actor_id
        gross = plan_unit_gross(line)
        label = plan_label(line.link, suppliers or {})
        bought = (last or {}).get(line.id)
        free = (free_stock or {}).get(line.id)
        rows.append({
            "id": str(line.id),
            "legacy_id": line.legacy_id,
            "version": line.version,
            "status": line.status,
            "rb_status": status,
            "name": line.name,
            "article": line.article,
            "item_type": line.item_type,
            "category": line.category,
            "quantity": _num(line.quantity),
            "to_buy": _num(line.to_buy),
            "stock_covered": _num(line.stock_covered),
            "unit": line.unit,
            "planned_date": line.planned_date.isoformat(),
            "buyer": line.buyer_name,
            "mine": mine,
            "source_request_id": str(line.source_request_id),
            "plan": {"price_with_vat": _num(gross), "link": line.link if line.link and is_web_link(line.link) else None,
                     "label": label} if gross is not None or label else None,
            "last": {"price": _num(bought.price), "date": bought.on.isoformat(), "supplier": bought.supplier,
                     "unit": bought.unit, "source": bought.source} if bought is not None else None,
            "free_stock": _num(free) if free is not None and free > 0 else None,
            "rfq": {"id": str(state.rfq_id), "number": state.rfq_number} if state is not None and state.live else None,
            "requote_from": state.requote_from if state is not None and status == "requote" else None,
            "cancel_reason": (reasons or {}).get(line.id) if status == "cancelled" else None,
            "selectable": selectable(line, status, actor_id),
            "can_take": line.buyer_id is None and line.status == ProcurementStatus.NEW.value,
            "can_split": mine and line.status in {stage.value for stage in FACTS_EDITABLE} and line.to_buy > 0,
            "can_cover": mine and line.status in {stage.value for stage in STOCK_COVER_STAGES} and line.to_buy > 0,
            "can_cancel": mine and status not in {"cancelled", "paid", "shipped", "partial", "delivered", "from_stock"}
            and not line.backorder,
            "can_resend": mine and status == "cancelled",
        })
    return rows
