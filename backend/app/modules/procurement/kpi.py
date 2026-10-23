"""the buyer's indicators over a period, computed on read.

Ported from Roller_Buyer (`apps/api/src/domain/kpi.ts`: computeKpi / computeSupplierRatings / topSavings) onto ERP's
procurement lines. Pure functions over line objects; money stays Decimal and is rounded only in the output.

Population: lines paid in the period (`paid_at` in Moscow time) — the endpoint reads only those. Roller counts money by
the invoice date and deliveries by the promised date; ERP's line keeps the paid date and the current ОЖИД. ДАТА.
«В срок» is measured against `expected_arrival`, which the buyer may move until the goods are in (the first promise
lives in the audit log, not on the line), hence the label «к ожидаемой дате». «Получено» = `actual_arrival` is set.
A line past its expected date without goods counts as late (Roller's `late`), a line before it as still open.

Savings: the price the line was calculated at (`calculated_price_with_vat`) against the price paid; Roller's baseline
(the cheapest quote of a key supplier) lives in purchasing's RFQ tables, which a procurement line does not reach.

Facts a procurement line does not carry (receiving defects and returns, the КП decision and its baseline, the quotes
asked and answered, the previous purchase, delivery billed on orders) come in `PurchasingFacts`, read by
`app.workflows.purchasing_kpi`; without them (or where their source is empty) the metric stays None («нет данных»,
never a made-up zero). «Экономия к КП» is the Roller baseline: the cheapest КП of a key supplier at the moment of the
decision (`purchasing_decisions.baseline_unit_gross`) against the price paid; «к расчёту» stays beside it. Never
available: the defect quantity (receiving stores the nature of the nonconformity, not how many pieces) — «Брак, ₽» —
and «взято → счёт» (a line stores no «taken» timestamp)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.shared.clock import MOSCOW, as_utc, moscow_now

from .domain import ProcurementLine

TOTAL = "Итого"
NO_BUYER = "Без закупщика"
HIGHLIGHTS = 5
ZERO = Decimal("0")


@dataclass(frozen=True)
class LineFacts:
    """What other modules know about one paid line. `received` = the goods went through the entrance inspection."""

    received: bool = False
    defect: bool = False
    returned_quantity: Decimal = Decimal("0")
    kp_baseline_unit: Decimal | None = None  # cheapest КП of a key supplier at the decision, in the line's unit
    previous_unit: Decimal | None = None  # gross price of the previous purchase of the same item, in the line's unit


@dataclass(frozen=True)
class SupplierFacts:
    """A supplier card as the buyer's requests saw it in the period."""

    name: str
    is_key: bool
    asked: int = 0
    answered: int = 0
    price_vs_best_pct: Decimal | None = None


@dataclass(frozen=True)
class DecisionFacts:
    """Decisions made in the period (by `decided_at`, Moscow day) and what they were made against."""

    decided: int = 0
    priced_total: int = 0
    with_non_key: int = 0
    overridden: int = 0


@dataclass(frozen=True)
class PurchasingFacts:
    lines: dict[UUID, LineFacts] = field(default_factory=dict)
    decisions: DecisionFacts = DecisionFacts()
    suppliers: dict[str, SupplierFacts] = field(default_factory=dict)  # by the casefolded supplier text of a line
    unplaced: tuple[SupplierFacts, ...] = ()  # asked or quoted in the period, no paid line
    delivery_spend: Decimal | None = None
    new_suppliers: int | None = None


def paid_day(line: ProcurementLine) -> date | None:
    if line.paid_at is None:
        return None
    return as_utc(line.paid_at).astimezone(MOSCOW).date()


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _amount(quantity: Decimal, unit_price: Decimal) -> Decimal:
    """Roller `amount(quantity, unit)`: one line's money, rounded to the kopeck."""
    return (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pct(part: int | Decimal, whole: int | Decimal) -> str | None:
    """Roller `pct`: a share rounded to 0.1; None when there is nothing to measure."""
    if whole <= 0:
        return None
    return str((Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _mean(values: list[int]) -> str | None:
    if not values:
        return None
    return str((Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _average(values: list[int]) -> dict:
    return {"days": _mean(values), "n": len(values)}


def _name(line: ProcurementLine) -> str:
    return (line.invoice_item_name or "").strip() or line.calculated_name


# ---- savings: the price the line was calculated at against the price paid ------------------------------------------

def _comparable(line: ProcurementLine) -> bool:
    return (
        line.calculated_price_with_vat is not None
        and line.money.price_with_vat is not None
        and line.actual_quantity is not None
        and bool(line.actual_unit)
        and line.actual_unit == line.calculated_unit
    )


def _need(line: ProcurementLine) -> Decimal | None:
    """The request's need still to buy; more may be bought (MOQ, whole reels). None = unknown, no cap."""
    if not line.actual_unit or line.actual_unit != line.calculated_unit:
        return None
    return line.to_buy


def _counted(line: ProcurementLine) -> Decimal:
    """Roller `counted`: savings are counted on the need, not on everything bought — 1000 m bought for an 87 m need
    are 913 m of stock, not 913 m of savings."""
    quantity = line.actual_quantity or ZERO
    need = _need(line)
    return quantity if need is None else min(quantity, need)


def _saving(line: ProcurementLine) -> Decimal | None:
    """(calculated gross unit price − actual gross unit price) × counted quantity; None when not comparable."""
    if not _comparable(line):
        return None
    counted = _counted(line)
    return _amount(counted, line.calculated_price_with_vat) - _amount(counted, line.money.price_with_vat)


def _surplus(line: ProcurementLine) -> Decimal | None:
    """Spent above the need: stock, not savings (Roller `surplusSpend`)."""
    if line.actual_quantity is None or line.money.price_with_vat is None:
        return None
    return _amount(line.actual_quantity - _counted(line), line.money.price_with_vat)


def _kp_saving(line: ProcurementLine, facts: LineFacts | None) -> Decimal | None:
    """(КП baseline unit − paid unit) × counted quantity; None without a decision baseline or a paid price."""
    if facts is None or facts.kp_baseline_unit is None:
        return None
    if line.money.price_with_vat is None or line.actual_quantity is None or not line.actual_unit:
        return None
    counted = _counted(line)
    return _amount(counted, facts.kp_baseline_unit) - _amount(counted, line.money.price_with_vat)


def _vs_last(line: ProcurementLine, facts: LineFacts | None) -> Decimal | None:
    """Roller `vsLastPurchase`: what the previous price would have cost minus what was paid, on the counted need."""
    if facts is None or facts.previous_unit is None or line.money.price_with_vat is None:
        return None
    if line.actual_quantity is None or not line.actual_unit:
        return None
    counted = _counted(line)
    return _amount(counted, facts.previous_unit) - _amount(counted, line.money.price_with_vat)


# ---- delivery -------------------------------------------------------------------------------------------------------

def _delivery(line: ProcurementLine, today: date) -> tuple[bool | None, int]:
    """(on time, days late). on time None = not measurable yet: no expected date, or goods still on the way before it."""
    expected = line.expected_arrival
    if expected is None:
        return None, 0
    if line.actual_arrival is not None:
        late = (line.actual_arrival - expected).days
        return late <= 0, max(late, 0)
    if expected < today:
        return False, (today - expected).days
    return None, 0


def _delivery_block(lines: list[ProcurementLine], today: date) -> dict:
    states = [_delivery(line, today) for line in lines]
    due = [state for state in states if state[0] is not None]
    ok = sum(1 for state in due if state[0])
    late = [days for flag, days in due if flag is False]
    ordered = [(line.order_date - line.request_date).days for line in lines if line.order_date is not None]
    delivered = [(line.actual_arrival - line.request_date).days for line in lines if line.actual_arrival is not None]
    return {
        "due": len(due),
        "on_time": ok,
        "on_time_pct": _pct(ok, len(due)),
        "avg_delay_days": _mean(late),
        "avg_request_to_order_days": _mean(ordered),
        "avg_request_to_delivery_days": _mean(delivered),
    }


# ---- the buyer's sheet (Roller KpiPage cards) -----------------------------------------------------------------------

def _supplier_key(line: ProcurementLine) -> str:
    return (line.supplier or "").strip().casefold()


def _spend_by_supplier(lines: list[ProcurementLine]) -> list[Decimal]:
    spend: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in lines:
        key = _supplier_key(line)
        if key and line.money.sum is not None:
            spend[key] += line.money.sum
    return sorted(spend.values(), reverse=True)


def _quality(lines: list[ProcurementLine], facts: PurchasingFacts | None) -> dict:
    """Defects and returns exist only for lines that went through receiving; none received = «нет данных»."""
    block = {
        "lines": len(lines), "received_lines": None, "lines_with_defect": None, "defect_pct": None,
        "defect_amount": None, "returned_amount": None,
    }
    if facts is None:
        return block
    seen = [(line, facts.lines.get(line.id)) for line in lines]
    seen = [(line, own) for line, own in seen if own is not None and own.received]
    if not seen:
        return block
    defective = sum(1 for _, own in seen if own.defect)
    returned = sum(
        (_amount(own.returned_quantity, line.money.price_with_vat) for line, own in seen
         if own.returned_quantity > 0 and line.money.price_with_vat is not None),
        ZERO,
    )
    block.update(
        received_lines=len(seen), lines_with_defect=defective, defect_pct=_pct(defective, len(seen)),
        returned_amount=_money(returned),
    )
    return block


def _mean_of(total: int, count: int) -> str | None:
    if count <= 0:
        return None
    return str((Decimal(total) / Decimal(count)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def compute_kpi(
    lines: list[ProcurementLine], start: date, end: date, today: date, facts: PurchasingFacts | None = None,
) -> dict:
    """Roller `computeKpi` for the lines already filtered to the period; None = «нет данных»."""
    pairs = [(line, _saving(line)) for line in lines]
    compared = [(line, value) for line, value in pairs if value is not None]
    baseline_spend = sum((_amount(_counted(line), line.calculated_price_with_vat) for line, _ in compared), ZERO)
    saved = sum((value for _, value in compared), ZERO)
    own = facts.lines if facts is not None else {}
    kp = [(line, value) for line in lines if (value := _kp_saving(line, own.get(line.id))) is not None]
    kp_baseline = sum((_amount(_counted(line), own[line.id].kp_baseline_unit) for line, _ in kp), ZERO)
    kp_saved = sum((value for _, value in kp), ZERO)
    last = [value for line in lines if (value := _vs_last(line, own.get(line.id))) is not None]
    spend = sum((line.money.sum for line in lines if line.money.sum is not None), ZERO)
    surplus = [value for value in (_surplus(line) for line in lines) if value is not None]
    shares = _spend_by_supplier(lines)
    total = sum(shares, ZERO)
    decisions = facts.decisions if facts is not None else None
    delivery = facts.delivery_spend if facts is not None else None
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "money": {
            "spend": _money(spend),
            "compared_spend": _money(baseline_spend - saved),
            "baseline_spend": _money(baseline_spend),
            "savings": _money(saved),
            "savings_pct": _pct(saved, baseline_spend),
            "compared_lines": len(compared),
            "kp_baseline_spend": _money(kp_baseline) if kp else None,
            "kp_savings": _money(kp_saved) if kp else None,
            "kp_savings_pct": _pct(kp_saved, kp_baseline),
            "kp_compared_lines": len(kp),
            "surplus_spend": _money(sum(surplus, ZERO)) if surplus else None,
            "vs_last_purchase": _money(sum(last, ZERO)) if last else None,
            "compared_with_last": len(last),
            "delivery_spend": _money(delivery) if delivery is not None else None,
        },
        "delivery": _delivery_block(lines, today),
        "quality": _quality(lines, facts),
        "competition": {
            "decisions": decisions.decided if decisions is not None else None,
            "avg_offers": _mean_of(decisions.priced_total, decisions.decided) if decisions is not None else None,
            "with_non_key_pct": _pct(decisions.with_non_key, decisions.decided) if decisions is not None else None,
            "override_pct": _pct(decisions.overridden, decisions.decided) if decisions is not None else None,
            "top1_share": _pct(shares[0], total) if shares else None,
            "top3_share": _pct(sum(shares[:3], ZERO), total) if shares else None,
            "new_suppliers": facts.new_suppliers if facts is not None else None,
        },
    }


def _defect_pct(own: list[ProcurementLine], facts: PurchasingFacts | None) -> str | None:
    if facts is None:
        return None
    seen = [facts.lines[line.id] for line in own if line.id in facts.lines and facts.lines[line.id].received]
    return _pct(sum(1 for item in seen if item.defect), len(seen))


def _rating(
    name: str, own: list[ProcurementLine], today: date, card: SupplierFacts | None, facts: PurchasingFacts | None,
) -> dict:
    best = card.price_vs_best_pct if card is not None else None
    return {
        "supplier": name,
        "is_key": card.is_key if card is not None else None,
        "asked": card.asked if card is not None else None,
        "answered": card.answered if card is not None else None,
        "response_pct": _pct(card.answered, card.asked) if card is not None else None,
        "order_lines": len(own),
        "spend": _money(sum((line.money.sum for line in own if line.money.sum is not None), ZERO)),
        "on_time_pct": _delivery_block(own, today)["on_time_pct"] if own else None,
        "defect_pct": _defect_pct(own, facts),
        "price_vs_best_pct": str(best.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) if best is not None else None,
    }


def compute_supplier_ratings(
    lines: list[ProcurementLine], today: date, facts: PurchasingFacts | None = None,
) -> list[dict]:
    """Roller `computeSupplierRatings`: per supplier of the period, plus the suppliers asked or quoted in it that were
    paid nothing. Request answers and the price against the best offer come from the supplier's card (`facts`)."""
    groups: dict[str, list[ProcurementLine]] = defaultdict(list)
    names: dict[str, str] = {}
    for line in lines:
        key = _supplier_key(line)
        if key:
            groups[key].append(line)
            names.setdefault(key, line.supplier.strip())
    cards = facts.suppliers if facts is not None else {}
    rows = [_rating(names[key], own, today, cards.get(key), facts) for key, own in groups.items()]
    if facts is not None:
        rows += [_rating(card.name, [], today, card, facts) for card in facts.unplaced]
    rows.sort(key=lambda row: (-Decimal(row["spend"]), row["supplier"].casefold()))
    return rows


def top_savings(lines: list[ProcurementLine], limit: int = HIGHLIGHTS) -> list[dict]:
    """Roller `topSavings`: the lines that saved most against their calculated price."""
    pairs = [(line, _saving(line)) for line in lines]
    found = [(line, value) for line, value in pairs if value is not None and value > 0]
    found.sort(key=lambda pair: pair[1], reverse=True)
    return [
        {
            "line_id": str(line.id),
            "order_number": line.order_number,
            "order_date": line.order_date.isoformat() if line.order_date else None,
            "supplier": line.supplier,
            "item": _name(line),
            "quantity": str(line.actual_quantity),
            "unit": line.actual_unit,
            "unit_price": _money(line.money.price_with_vat),
            "baseline_unit_price": _money(line.calculated_price_with_vat),
            "savings": _money(value),
        }
        for line, value in found[:limit]
    ]


# ---- the per-buyer table --------------------------------------------------------------------------------------------

def _row(buyer: str, lines: list[ProcurementLine], today: date) -> dict:
    paid_sum = sum((line.money.sum for line in lines if line.money.sum is not None), ZERO)
    received = [line for line in lines if line.actual_arrival is not None]
    timed = [state for state in (_delivery(line, today)[0] for line in lines) if state is not None]
    on_time = sum(1 for state in timed if state)
    invoice_to_paid = [(paid_day(line) - line.invoice_date).days for line in lines if line.invoice_date is not None]
    paid_to_received = [(line.actual_arrival - paid_day(line)).days for line in received]
    comparable = [value for value in (_saving(line) for line in lines) if value is not None]
    return {
        "buyer": buyer,
        "lines": len(lines),
        # СУММА (факт): unit price with VAT × quantity, summed over the lines that have it.
        "paid_sum": _money(paid_sum),
        "received": len(received),
        "on_time": {"n": len(timed), "ok": on_time, "percent": _pct(on_time, len(timed))},
        "invoice_to_paid": _average(invoice_to_paid),
        "paid_to_received": _average(paid_to_received),
        "savings": {"sum": _money(sum(comparable, ZERO)), "n": len(comparable), "excluded": len(lines) - len(comparable)},
    }


def period_lines(
    lines: Iterable[ProcurementLine], start: date, end: date, buyer: str | None = None,
) -> dict[str, list[ProcurementLine]]:
    """The lines paid within [start, end] by buyer name (one buyer when `buyer` is given)."""
    groups: dict[str, list[ProcurementLine]] = defaultdict(list)
    for line in lines:
        day = paid_day(line)
        if day is None or not (start <= day <= end):
            continue
        name = line.buyer.name if line.buyer else NO_BUYER
        if buyer and name != buyer:
            continue
        groups[name].append(line)
    return groups


def build_kpi(
    lines: Iterable[ProcurementLine], start: date, end: date, buyer: str | None = None, today: date | None = None,
    facts: PurchasingFacts | None = None,
) -> dict:
    """Rows per buyer (by name) plus the total row and Roller's sheet (cards, supplier rating, top savings), for lines
    paid within [start, end]. `today` closes «late»: a line past its expected date without goods. `facts` — what the
    purchasing and receiving modules add for the same period (`app.workflows.purchasing_kpi`)."""
    today = today or moscow_now().date()
    groups = period_lines(lines, start, end, buyer)
    rows = [_row(name, groups[name], today) for name in sorted(groups, key=str.casefold)]
    everyone = [line for name in groups for line in groups[name]]
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "rows": rows,
        "total": _row(TOTAL, everyone, today),
        "kpi": compute_kpi(everyone, start, end, today, facts),
        "suppliers": compute_supplier_ratings(everyone, today, facts),
        "highlights": top_savings(everyone),
    }
