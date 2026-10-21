"""Stage option `procurement.today`: Roller_Buyer's «Сегодня» over ERP facts, computed on read.

Port of RB `apps/api/src/today.ts` (sections), `followups.ts` («Дожать»: silent suppliers, deliveries due, invoices not
paid) and `domain/board.ts` (`lineEta`: when a line is expected on the shelf). Pure: the facts arrive from
`app/workflows/procurement_today.py` as `TodayFacts` — the buyer's own open procurement lines (`ViewLine`), their place in
the requests for quotes, the order facts of «Счета и поставки», the chosen quotes, lead-time history, Kaiten invoices to
pay, the UPDs to check. The line status is the one «Заявки» shows (`requests_view.rb_status`), and the project the one it
groups by (`domain.line_project`), so both screens always agree.

Sections come in the order of doing; empty ones are not sent. An item is {title, detail, link, days(, lines)}: `link` is
the place of the next step as `?section=procurement` params (CONTRACTS.md deep links), `days` — late / waiting, it sorts
the section (the oldest first). A card of a project or an order carries its positions (`lines`) and opens in place.

ERP differences, each said where it happens: «Давальческое — ждём М-15» is left out (ERP has no tolling fact); ERP's
own «Карантин / ожидание приёмки» and «Новые доступные» stay at the end; deliveries are one card per order (an ERP order
holds many lines), a single-line order reads exactly as RB's row."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from .domain import LineProject, ProcurementStatus, is_backorder_id, line_project, supplier_key
from .requests_view import RFQ_READY, OrderState, RfqState, ViewLine, rb_status, selectable

ITEMS_SENT = 8  # RB shows eight items of a section, then «и ещё N»
LINES_SENT = 30  # positions inside one card; the rest open in «Заявки» / «Счета и поставки»
NO_PROJECT_TITLE = "Без проекта"

# The supplier order's stages a delivery is awaited in (the orders board's «просрочено» reads the same ones);
# a `-Д` backorder in «Согласование» waits for the rest of a paid line.
DELIVERY_STAGES = frozenset({
    ProcurementStatus.AWAITING_INVOICE.value, ProcurementStatus.TO_PAY.value, ProcurementStatus.IN_TRANSIT.value,
})
_QUARANTINE = ProcurementStatus.QUARANTINE.value
_APPROVAL = ProcurementStatus.APPROVAL.value
_ETA_SOURCES = {
    "order": "дата в заказе", "quote": "срок из КП", "history": "по истории поставщика", "pricebook": "по прайсу",
    "default": "обычный срок",
}


# ---- words -------------------------------------------------------------------------------------------------------

def plural(n: int, one: str, few: str, many: str) -> str:
    d, h = n % 10, n % 100
    if d == 1 and h != 11:
        return one
    if 2 <= d <= 4 and (h < 12 or h > 14):
        return few
    return many


def dm(day: date | None) -> str:
    """«05.10» — how the buyer reads dates."""
    return f"{day:%d.%m}" if day else "—"


def days_word(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def positions_word(n: int) -> str:
    return f"{n} {plural(n, 'позиция', 'позиции', 'позиций')}"


def _qty(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def rub(kopecks: int) -> str:
    """RB `(total / 100).toLocaleString('ru-RU')`: «1 234,5» — groups by a no-break space, up to three decimals."""
    value = (Decimal(kopecks) / 100).quantize(Decimal("0.001")).normalize()
    whole, _, fraction = format(value, "f").partition(".")
    sign = "-" if whole.startswith("-") else ""
    digits = whole.lstrip("-")
    groups = []
    while digits:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    text = sign + " ".join(groups or ["0"])
    return f"{text},{fraction}" if fraction else text


# ---- lead times (RB domain/board.ts, shared/board.ts) -------------------------------------------------------------

_RANGE = re.compile(r"(\d+(?:\.\d+)?)(?:\s*[-–]\s*(\d+(?:\.\d+)?))?")
# JS `\b` without the u flag is an ASCII word boundary: «\bд\b» matches only between ASCII word characters.
_DAYS = re.compile(r"дн|day|д\.?$|\bд\b", re.ASCII)


def _round(value: float) -> int:
    """JS Math.round: halves go up."""
    return math.floor(value + 0.5)


def parse_lead_text(text: str | None) -> int | None:
    """RB `parseLeadText`: «3-5 недель» → 28, «2 р.д.» → 3, «33р.д.» → 46, «4 месяца» → 120, «сегодня» → 0,
    «под заказ» → None. Working days become calendar days (×7/5); a range takes its middle."""
    t = str(text or "").lower().replace(",", ".", 1).strip()
    if not t:
        return None
    if re.search(r"сегодня|в наличии|наличи", t):
        return 0
    m = _RANGE.search(t)
    if not m:
        return None
    n = (float(m.group(1)) + float(m.group(2))) / 2 if m.group(2) else float(m.group(1))
    if re.search(r"нед|week", t):
        days = n * 7
    elif re.search(r"мес|month", t):
        days = n * 30
    elif re.search(r"р\.?\s*д|раб", t):
        days = n * 7 / 5
    elif _DAYS.search(t):
        days = n
    else:
        return None
    return _round(days)


def average(values: Sequence[float]) -> int | None:
    return _round(sum(values) / len(values)) if values else None


def median(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else _round((ordered[middle - 1] + ordered[middle]) / 2)


@dataclass(frozen=True)
class EtaInput:
    """When a line is expected on the shelf (RB `EtaInput` on ERP facts).

    `expected` — ОЖИД. ДАТА of the line (an order's promise, a reschedule replaces it; for an order line without one —
    the order's promised date); `on_order` — the line is in a supplier order (№ ЗАКАЗА); `decided` / `quote_lead_days` —
    the chosen quote of its request for quotes; `started` — the day the supplier's lead runs from (paid, else the
    invoice); `supplier_lead_days` — this supplier's average payment → arrival; `pricebook_lead_days` — the item's lead
    in price books; `order_days` — the buyer's average request → invoice."""

    status: str
    requested: date
    today: date
    default_order_days: int
    default_lead_days: int
    expected: date | None = None
    on_order: bool = False
    decided: date | None = None
    quote_lead_days: int | None = None
    started: date | None = None
    supplier_lead_days: int | None = None
    pricebook_lead_days: int | None = None
    order_days: int | None = None
    delivered: date | None = None


def line_eta(i: EtaInput) -> tuple[date | None, str | None]:
    """RB `lineEta`: the date chosen at the order wins; otherwise the average supply time. Every estimate says its
    source. A date on a line not in an order yet counts only while it is ahead (a past one is stale)."""
    if i.status == "delivered":
        return i.delivered or i.today, "delivered"
    if i.status in ("cancelled", "from_stock"):
        return None, None
    if i.expected is not None and (i.on_order or i.expected >= i.today):
        return i.expected, "order"
    if i.decided is not None and i.quote_lead_days is not None:
        # The invoice still has to come and be paid: the quote's lead runs from today at the earliest.
        return max(i.decided, i.today) + timedelta(days=i.quote_lead_days), "quote"
    order_days = i.order_days if i.order_days is not None else i.default_order_days
    start = i.started or max(i.today, i.requested + timedelta(days=order_days))
    if i.supplier_lead_days is not None:
        return start + timedelta(days=i.supplier_lead_days), "history"
    if i.pricebook_lead_days is not None:
        return start + timedelta(days=i.pricebook_lead_days), "pricebook"
    return start + timedelta(days=i.default_lead_days), "default"


# ---- facts --------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TodaySettings:
    """«Настройки закупки» (purchasing_settings): RB quoteWaitDays, deliveryWarnDays, boardOrderDays, boardLeadDays."""

    quote_wait_days: int = 3
    delivery_warn_days: int = 3
    board_order_days: int = 5
    board_lead_days: int = 14


@dataclass(frozen=True)
class OrderFacts:
    """The order facts of «Счета и поставки» (`purchasing_order_facts.order_facts`)."""

    sent_to_pay_on: date | None = None
    shipped_on: date | None = None
    promised: date | None = None

    def state(self) -> OrderState:
        return OrderState(sent_to_pay=self.sent_to_pay_on is not None, shipped=self.shipped_on is not None)


@dataclass(frozen=True)
class Decision:
    """The quote chosen for a line in its live request for quotes."""

    rfq_id: UUID
    rfq_number: str
    decided_on: date
    lead_days: int | None
    supplier: str


@dataclass(frozen=True)
class SilentSupplier:
    """RB `awaitingQuotes`: a supplier asked in one of the buyer's requests (sent, not declined) who sent no quote while
    a line asked of him is still undecided; `lines` — how many such lines."""

    rfq_id: UUID
    rfq_number: str
    supplier: str
    sent_on: date
    lines: int


@dataclass(frozen=True)
class KaitenInvoice:
    title: str
    seller: str | None
    total: int | None  # kopecks
    created_on: date  # the Moscow day the Kaiten card was made


@dataclass(frozen=True)
class TodayFacts:
    lines: Sequence[ViewLine] = ()  # the buyer's own lines, «Готово» and «Отменено» left out
    purposes: Mapping[UUID, str] = field(default_factory=dict)  # source request id → ЦЕЛЬ
    rfq: Mapping[UUID, RfqState] = field(default_factory=dict)
    orders: Mapping[str, OrderFacts] = field(default_factory=dict)
    decisions: Mapping[UUID, Decision] = field(default_factory=dict)  # procurement line id → its chosen quote
    rfq_sent: Mapping[UUID, date] = field(default_factory=dict)  # request for quotes id → first sent
    silent: Sequence[SilentSupplier] = ()
    supplier_leads: Mapping[str, int] = field(default_factory=dict)  # `supplier_key` → average payment → arrival
    pricebook_leads: Mapping[UUID, int] = field(default_factory=dict)  # procurement line id → price-book lead
    order_days: int | None = None
    kaiten: Sequence[KaitenInvoice] | None = None  # None — option documents.chain is off
    upd_to_check: int | None = None  # None — option documents.upd is off
    available: int = 0
    rfq_on: bool = False
    received_orders: frozenset[str] = frozenset()  # orders with a line already received (Карантин, Готово)


@dataclass(frozen=True)
class _Line:
    view: ViewLine
    status: str  # RB status, as «Заявки» shows it
    project: LineProject
    order: OrderFacts | None

    @property
    def name(self) -> str:
        return (self.view.invoice_name or "").strip() or self.view.name

    @property
    def project_title(self) -> str:
        return self.project.value or NO_PROJECT_TITLE

    @property
    def due(self) -> date | None:
        """The delivery date: ОЖИД. ДАТА of the line, else the order's promised date."""
        return self.view.expected_arrival or (self.order.promised if self.order else None)


def _lines(facts: TodayFacts) -> list[_Line]:
    out = []
    for view in facts.lines:
        order = facts.orders.get(view.order_number or "") if view.order_number else None
        out.append(_Line(
            view=view,
            status=rb_status(view, facts.rfq.get(view.id), order.state() if order else None),
            project=line_project(view.project_reference, view.designation, facts.purposes.get(view.source_request_id)),
            order=order,
        ))
    return out


# ---- «Дожать» (RB followups.ts) -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Delivery:
    line: _Line
    due: date
    days_left: int


@dataclass(frozen=True)
class Payment:
    order_number: str | None
    supplier: str
    invoiced_on: date
    sent_to_pay: bool
    days: int
    lines: tuple[_Line, ...]
    received: bool = False  # another line of the order is already received


@dataclass(frozen=True)
class Followups:
    awaiting_quotes: list[tuple[SilentSupplier, int]]  # with the days since sending
    deliveries: list[Delivery]
    payments: list[Payment]
    decided: list[tuple[_Line, date]]  # lines waiting for the invoice, with the day the supplier was chosen


def _in_delivery(line: _Line) -> bool:
    status = line.view.status
    return status in DELIVERY_STAGES or (status == _APPROVAL and is_backorder_id(line.view.legacy_id))


def _order_key(line: _Line) -> str:
    return line.view.order_number or f"~{supplier_key(line.view.supplier)}"


def followups(facts: TodayFacts, today: date, settings: TodaySettings, lines: Sequence[_Line] | None = None) -> Followups:
    """RB `loadFollowups`: silent suppliers waiting ≥ quote_wait_days, deliveries late or due within
    delivery_warn_days, invoices not paid (one per order), and — for «Ждём счёт» — the lines with a supplier chosen."""
    lines = _lines(facts) if lines is None else lines
    awaiting = []
    if facts.rfq_on:
        for silent in facts.silent:
            waited = (today - silent.sent_on).days
            if waited >= settings.quote_wait_days and silent.lines > 0:
                awaiting.append((silent, waited))
        awaiting.sort(key=lambda entry: -entry[1])
    deliveries = []
    for line in lines:
        due = line.due
        if due is None or not _in_delivery(line):
            continue
        left = (due - today).days
        if left <= settings.delivery_warn_days:
            deliveries.append(Delivery(line, due, left))
    deliveries.sort(key=lambda entry: entry.days_left)
    unpaid: dict[str, list[_Line]] = {}
    for line in lines:
        if line.status in ("ordered", "to_pay"):
            unpaid.setdefault(_order_key(line), []).append(line)
    payments = []
    for group in unpaid.values():
        view = group[0].view
        invoiced = min(
            (line.view.invoice_date or line.view.order_date or line.view.created_at.date() for line in group),
        )
        payments.append(Payment(
            order_number=view.order_number, supplier=next((line.view.supplier for line in group if line.view.supplier), "") or "",
            invoiced_on=invoiced, sent_to_pay=any(line.status == "to_pay" for line in group),
            days=(today - invoiced).days, lines=tuple(group),
            received=bool(view.order_number) and view.order_number in facts.received_orders,
        ))
    payments.sort(key=lambda entry: -entry.days)
    decided = []
    for line in lines:
        if line.status != "decided":
            continue
        decision = facts.decisions.get(line.view.id)
        on = decision.decided_on if decision else (line.view.order_date or line.view.created_at.date())
        decided.append((line, on))
    decided.sort(key=lambda entry: entry[1])
    return Followups(awaiting, deliveries, payments, decided)


# ---- sections (RB today.ts) ---------------------------------------------------------------------------------------

SECTIONS = (
    # key, title, action, tone — RB's words; the last two are ERP's own.
    ("late", "Просрочены поставки", "написать поставщику, перенести ожидаемую дату в счёте", "danger"),
    ("arrive", "Приедет сегодня–завтра", "подготовить приёмку", "accent"),
    ("risk", "Не успевает к сроку", "поискать поставщика быстрее или предупредить проект", "warning"),
    ("rfq", "Запросить КП", "выбрать строки в заявке и запросить КП у трёх поставщиков", "accent"),
    ("quotes", "Нет ответа на запрос КП", "напомнить поставщику", "warning"),
    ("choose", "Выбрать поставщика", "сравнить КП в матрице и выбрать", "accent"),
    ("invoice", "Ждём счёт", "запросить счёт у выбранного поставщика — письмо готово в запросе КП", "warning"),
    ("pay", "Счета не оплачены", "передать в оплату, проверить оплату", "warning"),
    ("kaiten", "В оплату (Kaiten)", "проверить, что бухгалтерия оплатила", "warning"),
    ("upd", "УПД требуют проверки", "сверить с PDF и отметить проверенными", "neutral"),
    ("quarantine", "Карантин / ожидание приёмки", "проверить результат входного контроля", "neutral"),
    ("available", "Новые доступные", "взять в работу", "neutral"),
)


def _by_days(item: dict) -> int:
    """RB `byDays`: the longest wait first; items without days after those with them, in their own order."""
    return -(item["days"] if item["days"] is not None else -1)


def _sorted(items: Iterable[dict]) -> list[dict]:
    return sorted(items, key=_by_days)


def _card(title: str, detail: str, link: dict, lines: list[dict], *, project: LineProject | None = None) -> dict:
    """A card holding positions; it waits as long as its oldest line (RB `grouped`)."""
    inside = _sorted(lines)
    card = {
        "title": title, "detail": detail, "link": link, "days": inside[0]["days"] if inside else None,
        "lines": inside[:LINES_SENT], "lines_count": len(inside),
    }
    if project is not None:
        card["project"] = {"key": project.key, "kind": project.kind, "value": project.value}
    return card


def _grouped(lines: Iterable[_Line], key: Callable[[_Line], str]) -> list[list[_Line]]:
    groups: dict[str, list[_Line]] = {}
    for line in lines:
        groups.setdefault(key(line), []).append(line)
    return list(groups.values())


def _requests_link(line: _Line, with_line: bool = False) -> dict:
    link = {"tab": "requests", "project": line.project.key}
    if with_line:
        link["line"] = str(line.view.id)
    return link


def _order_link(line: _Line) -> dict:
    return {"tab": "orders", "order": line.view.order_number} if line.view.order_number else {"tab": "orders"}


def _order_title(line: _Line) -> str:
    supplier = (line.view.supplier or "").strip() or "поставщик не указан"
    number = line.view.order_number
    return f"{supplier} · заказ {number}" if number else f"{supplier} · без № заказа"


def _per_order(entries: list[tuple[_Line, dict]], card_detail: Callable[[list[tuple[_Line, dict]]], str],
               single_detail: Callable[[_Line, dict], str]) -> list[dict]:
    """One item per supplier order: a single-line order reads as RB's row (the item and «поставщик · заказ N · …»),
    a larger one as a card with its positions inside."""
    groups: dict[str, list[tuple[_Line, dict]]] = {}
    for line, extra in entries:
        groups.setdefault(_order_key(line), []).append((line, extra))
    items = []
    for group in groups.values():
        first = group[0][0]
        if len(group) == 1:
            items.append({"title": first.name, "detail": single_detail(first, group[0][1]), "link": _order_link(first),
                          "days": group[0][1]["days"]})
            continue
        inside = [{"title": line.name, "detail": extra["line_detail"], "link": _order_link(line), "days": extra["days"]}
                  for line, extra in group]
        items.append(_card(_order_title(first), card_detail(group), _order_link(first), inside))
    return items


def _late(follow: Followups) -> list[dict]:
    entries = [
        (d.line, {"days": -d.days_left, "due": d.due,
                  "line_detail": f"{_qty(d.line.view.quantity)} {d.line.view.unit} · обещали {dm(d.due)}"})
        for d in follow.deliveries if d.days_left < 0
    ]
    return _per_order(
        entries,
        lambda group: f"обещали {dm(min(extra['due'] for _, extra in group))} · {positions_word(len(group))}",
        lambda line, extra: f"{_order_title(line)} · обещали {dm(extra['due'])}",
    )


def _when(left: int) -> str:
    return "сегодня" if left == 0 else f"через {days_word(left)}"


def _arrive(follow: Followups) -> list[dict]:
    entries = [
        (d.line, {"days": None, "left": d.days_left,
                  "line_detail": f"{_qty(d.line.view.quantity)} {d.line.view.unit} · {_when(d.days_left)}"})
        for d in follow.deliveries if d.days_left >= 0
    ]
    return _per_order(
        entries,
        lambda group: f"{_when(min(extra['left'] for _, extra in group))} · {positions_word(len(group))}",
        lambda line, extra: f"{_order_title(line)} · {_when(extra['left'])}",
    )


def eta_of(line: _Line, facts: TodayFacts, today: date, settings: TodaySettings,
           pricebook: bool = True) -> tuple[date | None, str | None]:
    view = line.view
    decision = facts.decisions.get(view.id)
    supplier = supplier_key(view.supplier or (decision.supplier if decision else ""))
    return line_eta(EtaInput(
        status=line.status, requested=view.request_date, today=today,
        default_order_days=settings.board_order_days, default_lead_days=settings.board_lead_days,
        expected=line.due, on_order=bool(view.order_number),
        decided=decision.decided_on if decision else None, quote_lead_days=decision.lead_days if decision else None,
        started=view.paid_on or view.invoice_date,
        supplier_lead_days=facts.supplier_leads.get(supplier) if supplier else None,
        pricebook_lead_days=facts.pricebook_leads.get(view.id) if pricebook else None,
        order_days=facts.order_days,
    ))


def _risk_candidate(line: _Line) -> bool:
    # Goods in «Карантин» are already here (on the inspection table): not a supplier's date any more.
    return line.status not in ("delivered", "cancelled", "from_stock") and line.view.status != _QUARANTINE


def pricebook_wanted(facts: TodayFacts, today: date, settings: TodaySettings) -> list[ViewLine]:
    """The lines whose date would come from the price books (nothing nearer is known) — only these are matched to the
    nomenclature, the price books are read for their items only."""
    out = []
    for line in _lines(facts):
        if _risk_candidate(line) and eta_of(line, facts, today, settings, pricebook=False)[1] == "default":
            out.append(line.view)
    return out


def _risk(lines: Sequence[_Line], facts: TodayFacts, today: date, settings: TodaySettings, late: set[UUID]) -> list[dict]:
    at_risk: list[tuple[_Line, date, str]] = []
    for line in lines:
        if line.view.id in late or not _risk_candidate(line):
            continue
        eta, source = eta_of(line, facts, today, settings)
        need = line.view.planned_date
        if eta is not None and need is not None and eta > need:
            at_risk.append((line, eta, source or ""))
    found = {entry[0].view.id: entry for entry in at_risk}
    items = []
    for group in _grouped([entry[0] for entry in at_risk], lambda line: line.project.key):
        first = group[0]
        inside = []
        worst: tuple[int, date, date] | None = None
        for line in group:
            _, eta, source = found[line.view.id]
            slip = (eta - line.view.planned_date).days
            if worst is None or slip > worst[0]:
                worst = (slip, line.view.planned_date, eta)
            inside.append({
                "title": line.name,
                "detail": f"{line.view.legacy_id} · нужно к {dm(line.view.planned_date)} · ожидаем {dm(eta)}"
                          f" ({_ETA_SOURCES.get(source, source)})",
                "link": _requests_link(line, True), "days": slip,
            })
        detail = f"{positions_word(len(group))} · нужно к {dm(worst[1])} · ожидаем {dm(worst[2])}" if worst else ""
        items.append(_card(first.project_title, detail, _requests_link(first), inside, project=first.project))
    return items


def _to_quote(lines: Sequence[_Line], today: date) -> list[dict]:
    wanted = [line for line in lines if line.status in RFQ_READY and selectable(line.view, line.status, line.view.buyer_id)]
    items = []
    for group in _grouped(wanted, lambda line: line.project.key):
        first = group[0]
        requote = sum(1 for line in group if line.status == "requote")
        detail = " · ".join(part for part in (positions_word(len(group)), f"перезапрос: {requote}" if requote else "") if part)
        inside = [{
            "title": line.name,
            "detail": f"{line.view.legacy_id} · {_qty(line.view.to_buy)} {line.view.unit}"
                      f"{' · перезапрос' if line.status == 'requote' else ''}",
            "link": _requests_link(line, True), "days": (today - line.view.request_date).days,
        } for line in group]
        items.append(_card(first.project_title, detail, _requests_link(first), inside, project=first.project))
    return items


def _quotes(follow: Followups) -> list[dict]:
    return [{
        "title": silent.supplier,
        "detail": f"запрос {silent.rfq_number} от {dm(silent.sent_on)} · {positions_word(silent.lines)}",
        "link": {"tab": "rfq", "rfq": str(silent.rfq_id)}, "days": waited,
    } for silent, waited in follow.awaiting_quotes]


def _choose(lines: Sequence[_Line], facts: TodayFacts, today: date) -> list[dict]:
    quoted = [line for line in lines if line.status == "quoted"]
    items = []
    for group in _grouped(quoted, lambda line: line.project.key):
        first = group[0]
        rfqs: dict[UUID, str] = {}
        inside = []
        for line in group:
            state = facts.rfq.get(line.view.id)
            rfq_id = state.rfq_id if state else None
            if rfq_id is not None:
                rfqs.setdefault(rfq_id, state.rfq_number or "")
            sent = facts.rfq_sent.get(rfq_id) if rfq_id else None
            inside.append({
                "title": line.name,
                "detail": f"{line.view.legacy_id} · {_qty(line.view.to_buy)} {line.view.unit}"
                          f"{f' · запрос {state.rfq_number}' if rfq_id else ''}",
                "link": {"tab": "rfq", "rfq": str(rfq_id)} if rfq_id else {"tab": "rfq"},
                "days": (today - sent).days if sent else None,
            })
        detail = " · ".join(part for part in (
            positions_word(len(group)), f"запрос {', '.join(rfqs.values())}" if rfqs else "",
        ) if part)
        link = {"tab": "rfq", "rfq": str(next(iter(rfqs)))} if len(rfqs) == 1 else {"tab": "rfq"}
        items.append(_card(first.project_title, detail, link, inside, project=first.project))
    return items


def _invoice(follow: Followups, facts: TodayFacts, today: date) -> list[dict]:
    items = []
    for line, on in follow.decided:
        state = facts.rfq.get(line.view.id)
        decision = facts.decisions.get(line.view.id)
        rfq_id = state.rfq_id if state is not None and state.live else (decision.rfq_id if decision else None)
        supplier = (line.view.supplier or "").strip() or (decision.supplier if decision else "")
        # The request for quotes opens with its invoice-request letters.
        link = {"tab": "rfq", "rfq": str(rfq_id), "invoices": "1"} if rfq_id else _order_link(line)
        items.append({"title": line.name, "detail": f"{supplier} · {line.project_title}", "link": link,
                      "days": (today - on).days})
    return items


def _pay(follow: Followups) -> list[dict]:
    items = []
    for payment in follow.payments:
        first = payment.lines[0]
        detail = "в оплате" if payment.sent_to_pay else "не передан в оплату"
        if len(payment.lines) > 1:
            detail = f"{detail} · {positions_word(len(payment.lines))}"
        if payment.received:
            # RB says «товар уже на складе» when the whole order came before the payment; in ERP a line is paid before
            # it travels, so only the order's other (paid) lines can be here already.
            detail = f"{detail} · часть товара уже на складе"
        items.append({"title": _order_title(first), "detail": detail, "link": _order_link(first), "days": payment.days})
    return items


def _kaiten(invoices: Sequence[KaitenInvoice], today: date) -> list[dict]:
    return [{
        "title": invoice.title,
        "detail": f"{invoice.seller or 'поставщик не прочитан'}{f' · {rub(invoice.total)} ₽' if invoice.total is not None else ''}",
        "link": {"tab": "invoices", "status": "to_pay"},
        "days": (today - invoice.created_on).days,
    } for invoice in invoices]


def _upd(count: int) -> list[dict]:
    if count <= 0:
        return []
    return [{
        "title": f"{count} {plural(count, 'документ', 'документа', 'документов')}",
        "detail": "разборщик сомневается в реквизитах или суммах",
        "link": {"tab": "upd", "status": "needs_review"}, "days": None,
    }]


def _quarantine(lines: Sequence[_Line], today: date) -> list[dict]:
    entries = []
    for line in lines:
        if line.view.status != _QUARANTINE:
            continue
        since = line.view.actual_arrival or line.view.expected_arrival
        days = (today - since).days if since else None
        entries.append((line, {"days": days, "since": since,
                               "line_detail": f"{_qty(line.view.quantity)} {line.view.unit} · на приёмке с {dm(since)}"}))
    return _per_order(
        entries,
        lambda group: f"на приёмке · {positions_word(len(group))}",
        lambda line, extra: f"{_order_title(line)} · на приёмке с {dm(extra['since'])}",
    )


def _available(count: int) -> list[dict]:
    if count <= 0:
        return []
    return [{"title": f"Свободных позиций: {count}", "detail": "откройте «Очереди» и возьмите в работу",
             "link": {"tab": "queues"}, "days": None}]


def build_today(facts: TodayFacts, today: date, settings: TodaySettings) -> dict:
    """The page: {today, sections: [{key, title, action, tone, count, positions?, items}], calm}. A section sends its
    first `ITEMS_SENT` items (the oldest first) and the full `count`; `positions` — how many lines its cards hold."""
    lines = _lines(facts)
    follow = followups(facts, today, settings, lines)
    late_ids = {d.line.view.id for d in follow.deliveries if d.days_left < 0}
    built: dict[str, list[dict]] = {
        "late": _late(follow),
        "arrive": _arrive(follow),
        "risk": _risk(lines, facts, today, settings, late_ids),
        "rfq": _to_quote(lines, today),
        "quotes": _quotes(follow),
        "choose": _choose(lines, facts, today) if facts.rfq_on else [],
        "invoice": _invoice(follow, facts, today),
        "pay": _pay(follow),
        "kaiten": _kaiten(facts.kaiten, today) if facts.kaiten is not None else [],
        "upd": _upd(facts.upd_to_check) if facts.upd_to_check is not None else [],
        "quarantine": _quarantine(lines, today),
        "available": _available(facts.available),
    }
    sections = []
    for key, title, action, tone in SECTIONS:
        items = built[key]
        if not items:
            continue
        if key != "arrive":  # «Приедет»: nearest first, already in that order (RB: no days, the sort keeps it)
            items = _sorted(items)
        section = {"key": key, "title": title, "action": action, "tone": tone, "count": len(items),
                   "items": items[:ITEMS_SENT]}
        if any("lines" in item for item in items):
            section["positions"] = sum(item.get("lines_count", 1) for item in items)
        sections.append(section)
    return {"today": today.isoformat(), "sections": sections, "calm": not sections}


# ---- «Дожать» on the orders board (purchasing api_orders /followups) ----------------------------------------------

def order_followups(facts: TodayFacts, today: date, settings: TodaySettings) -> dict:
    """«Дожать» of «Счета и поставки» from the same rules as «Сегодня» (`followups`), grouped per order and per
    request for quotes: {awaiting_quotes, late, arriving, unpaid, awaiting_invoice}."""
    follow = followups(facts, today, settings)

    def per_order(entries: Iterable[tuple[_Line, date | None, int | None]], worst: Callable[[int, int], bool]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for line, day, days in entries:
            entry = grouped.setdefault(_order_key(line), {
                "order_number": line.view.order_number, "supplier": line.view.supplier, "date": day, "days": days,
                "count": 0, "names": [],
            })
            entry["count"] += 1
            if len(entry["names"]) < 3:
                entry["names"].append(line.name)
            if days is not None and (entry["days"] is None or worst(days, entry["days"])):
                entry["days"], entry["date"] = days, day
        return [{**entry, "date": entry["date"].isoformat() if entry["date"] else None} for entry in grouped.values()]

    quotes: dict[UUID, dict] = {}
    for silent, waited in follow.awaiting_quotes:
        entry = quotes.setdefault(silent.rfq_id, {
            "rfq_id": str(silent.rfq_id), "rfq_number": silent.rfq_number, "names": [], "date": silent.sent_on,
            "days": waited, "count": 0,
        })
        entry["names"].append(silent.supplier)
        entry["count"] = max(entry["count"], silent.lines)
        if waited > entry["days"]:
            entry["days"], entry["date"] = waited, silent.sent_on
    awaiting_quotes = [{
        "rfq_id": entry["rfq_id"], "rfq_number": entry["rfq_number"],
        "hint": f"{entry['rfq_number']}: нет ответа — {', '.join(sorted(entry['names']))}",
        "date": entry["date"].isoformat(), "days": entry["days"], "count": entry["count"],
    } for entry in quotes.values()]
    later = lambda days, known: days > known  # noqa: E731
    sooner = lambda days, known: days < known  # noqa: E731
    late = per_order(((d.line, d.due, -d.days_left) for d in follow.deliveries if d.days_left < 0), later)
    arriving = per_order(((d.line, d.due, d.days_left) for d in follow.deliveries if d.days_left >= 0), sooner)
    unpaid = per_order(((line, payment.invoiced_on, payment.days) for payment in follow.payments for line in payment.lines), later)
    waiting = per_order(((line, on, (today - on).days) for line, on in follow.decided), later)
    return {
        "awaiting_quotes": sorted(awaiting_quotes, key=lambda entry: -(entry["days"] or 0)),
        "late": sorted(late, key=lambda entry: -(entry["days"] or 0)),
        "arriving": sorted(arriving, key=lambda entry: entry["days"] if entry["days"] is not None else 0),
        "unpaid": sorted(unpaid, key=lambda entry: -(entry["days"] or 0)),
        "awaiting_invoice": sorted(waiting, key=lambda entry: -(entry["days"] or 0)),
    }
