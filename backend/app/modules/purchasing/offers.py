"""Comparing supplier offers (port of Roller_Buyer domain/offers.ts + domain/decision.ts), pure and in Decimal.

Offers are compared only with VAT and by what covering the need costs: the quantity is raised to the MOQ, rounded up
to whole packs, and priced at that quantity's break — «10 ₽/м from 1000 m» costs more than «16 ₽/м» when 87 m are
needed. A supplier may re-quote; only its latest valid offer on a line counts. The baseline for savings is the
cheapest offer of a supplier that was key WHEN the quote was entered (`supplier_is_key` snapshot), never the
supplier's current flag."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from uuid import UUID

UNIT_STEP = Decimal("0.000001")
MONEY_STEP = Decimal("0.01")
HUNDRED = Decimal("100")


def gross_unit(price: Decimal, vat_included: bool, vat_rate: Decimal) -> Decimal:
    """Unit price with VAT: as is when the price includes VAT, else net × (1 + rate)."""
    if vat_included:
        return price.quantize(UNIT_STEP, ROUND_HALF_UP)
    return (price * (HUNDRED + vat_rate) / HUNDRED).quantize(UNIT_STEP, ROUND_HALF_UP)


def buy_quantity(need: Decimal, moq: Decimal | None, pack: Decimal | None) -> Decimal:
    """87 m, MOQ 100 → 100; 87 m, reel 50 → 100; 120, MOQ 100, reel 50 → 150; 87 m, nothing → 87."""
    quantity = max(need, moq or Decimal(0))
    if pack is not None and pack > 0:
        quantity = (quantity / pack).to_integral_value(ROUND_CEILING) * pack
    return quantity


@dataclass(frozen=True)
class Tier:
    from_quantity: Decimal
    price: Decimal


def read_tiers(raw: Iterable[dict] | None) -> tuple[Tier, ...]:
    """Stored breaks ([{"from": "1000", "price": "10.5"}]) → tiers; a broken entry is skipped, never fatal."""
    tiers: list[Tier] = []
    for entry in raw or ():
        try:
            start, price = Decimal(str(entry["from"])), Decimal(str(entry["price"]))
        except Exception:  # noqa: BLE001 — stored JSON from older clients; a bad break is ignored
            continue
        if start > 0 and price > 0:
            tiers.append(Tier(start, price))
    return tuple(sorted(tiers, key=lambda tier: tier.from_quantity))


def tier_price(base: Decimal, tiers: Sequence[Tier], quantity: Decimal) -> Decimal:
    """The price of the highest break not above the quantity, else the base price."""
    price, at = base, Decimal(-1)
    for tier in tiers:
        if tier.from_quantity <= quantity and tier.from_quantity > at:
            price, at = tier.price, tier.from_quantity
    return price


@dataclass(frozen=True)
class Cover:
    quantity: Decimal
    unit_gross: Decimal
    total_gross: Decimal


def cover(
    need: Decimal,
    *,
    price: Decimal | None,
    vat_included: bool,
    vat_rate: Decimal,
    moq: Decimal | None = None,
    pack: Decimal | None = None,
    tiers: Sequence[Tier] = (),
) -> Cover | None:
    """Covering the need with one offer: quantity bought, unit price with VAT at it, the total. None — no price."""
    if price is None:
        return None
    quantity = buy_quantity(need, moq, pack)
    unit = gross_unit(tier_price(price, tiers, quantity), vat_included, vat_rate)
    return Cover(quantity, unit, (quantity * unit).quantize(MONEY_STEP, ROUND_HALF_UP))


@dataclass(frozen=True)
class Offer:
    quote_line_id: UUID
    supplier_id: UUID
    is_key: bool  # the quote's snapshot, not the supplier's current flag
    received_on: date
    gross_unit: Decimal | None
    total: Decimal | None = None  # cost of covering the need; None → compared by the unit price
    declined: bool = False
    analog: bool = False
    valid_until: date | None = None
    entered_at: datetime | None = None  # tie-break of two quotes received the same day

    @property
    def priced(self) -> bool:
        return not self.declined and self.gross_unit is not None

    @property
    def cost(self) -> Decimal:
        assert self.gross_unit is not None
        return self.total if self.total is not None else self.gross_unit


def _arrival(offer: Offer) -> tuple:
    return (offer.received_on, offer.entered_at or datetime.min, str(offer.quote_line_id))


def expired(offer: Offer, today: date) -> bool:
    return offer.valid_until is not None and offer.valid_until < today


def current_offers(offers: Iterable[Offer], today: date) -> list[Offer]:
    """The latest valid offer of each supplier (a later decline supersedes an earlier price); expired ones drop."""
    latest: dict[UUID, Offer] = {}
    for offer in sorted(offers, key=_arrival):
        if not expired(offer, today):
            latest[offer.supplier_id] = offer
    return sorted(latest.values(), key=_arrival)


def _priced(offers: Iterable[Offer]) -> list[Offer]:
    return [offer for offer in offers if offer.priced]


def cheapest(offers: Iterable[Offer]) -> Offer | None:
    """Lowest cost of covering the need; a tie goes to the earlier offer. Declines never count."""
    priced = _priced(offers)
    return min(priced, key=lambda offer: (offer.cost, _arrival(offer))) if priced else None


def baseline(offers: Iterable[Offer]) -> Offer | None:
    """The «convenient» price savings are measured against: the cheapest offer from a key supplier (by the quote's
    snapshot), or — when no key supplier answered — the first offer received."""
    priced = _priced(offers)
    key = [offer for offer in priced if offer.is_key]
    if key:
        return cheapest(key)
    return min(priced, key=_arrival) if priced else None


@dataclass(frozen=True)
class Gate:
    priced: int
    required: int
    has_non_key: bool
    require_non_key: bool
    passes: bool


def gate_status(offers: Iterable[Offer], min_quotes: int, require_non_key: bool) -> Gate:
    """Expects current offers (one per supplier): enough priced offers and, when required, one from a non-key one."""
    priced = _priced(offers)
    has_non_key = any(not offer.is_key for offer in priced)
    passes = len(priced) >= min_quotes and (has_non_key or not require_non_key)
    return Gate(len(priced), min_quotes, has_non_key, require_non_key, passes)


@dataclass(frozen=True)
class Reasons:
    gate_override_reason: str | None = None
    not_cheapest_reason: str | None = None
    analog_approved_by: str | None = None


@dataclass(frozen=True)
class Violation:
    field: str
    message: str


def _filled(value: str | None) -> bool:
    return bool((value or "").strip())


def gate_message(gate: Gate) -> str:
    parts = [f"Нужно КП: {gate.required}, есть {gate.priced}"]
    if gate.require_non_key and not gate.has_non_key:
        parts.append("нет ни одного КП от неключевого поставщика")
    return f"{'; '.join(parts)}. Укажите причину обхода."


def _pricier(chosen: Offer, offers: Sequence[Offer]) -> bool:
    best = cheapest(offers)
    return best is not None and chosen.priced and chosen.cost > best.cost


def check_decision(
    offers: Sequence[Offer], chosen_id: UUID, *, today: date, min_quotes: int, require_non_key: bool, reasons: Reasons,
) -> list[Violation]:
    """Every rule the choice breaks. Empty — the decision may be saved."""
    current = current_offers(offers, today)
    chosen = next((offer for offer in current if offer.quote_line_id == chosen_id), None)
    if chosen is None:
        stale = next((offer for offer in offers if offer.quote_line_id == chosen_id), None)
        if stale is not None and expired(stale, today):
            return [Violation("quote_line_id", "Срок действия этого КП истёк")]
        return [Violation("quote_line_id", "Это КП не актуально: у поставщика есть более свежее предложение")]
    if not chosen.priced:
        return [Violation("quote_line_id", "Поставщик отказался поставить эту позицию")]
    violations: list[Violation] = []
    gate = gate_status(current, min_quotes, require_non_key)
    if not gate.passes and not _filled(reasons.gate_override_reason):
        violations.append(Violation("gate_override_reason", gate_message(gate)))
    if _pricier(chosen, current) and not _filled(reasons.not_cheapest_reason):
        violations.append(Violation("not_cheapest_reason", "Выбрано не самое дешёвое КП — укажите причину"))
    if chosen.analog and not _filled(reasons.analog_approved_by):
        violations.append(Violation("analog_approved_by", "Аналог: укажите, с кем согласована замена"))
    return violations


def normalize_decision(
    offers: Sequence[Offer], chosen_id: UUID, *, today: date, min_quotes: int, require_non_key: bool, reasons: Reasons,
) -> Reasons:
    """Keeps only the reasons the situation required, trimmed — so the statistics stay honest."""
    current = current_offers(offers, today)
    chosen = next((offer for offer in current if offer.quote_line_id == chosen_id), None)
    gate = gate_status(current, min_quotes, require_non_key)
    pricier = chosen is not None and _pricier(chosen, current)
    clean = lambda value: (value or "").strip() or None  # noqa: E731
    return replace(
        reasons,
        gate_override_reason=None if gate.passes else clean(reasons.gate_override_reason),
        not_cheapest_reason=clean(reasons.not_cheapest_reason) if pricier else None,
        analog_approved_by=clean(reasons.analog_approved_by) if chosen is not None and chosen.analog else None,
    )


def delta_pct(price: Decimal | None, budget: Decimal | None) -> Decimal | None:
    """(price − budget) / budget, %, one decimal; None without either side."""
    if price is None or budget is None or budget <= 0:
        return None
    return ((price - budget) * HUNDRED / budget).quantize(Decimal("0.1"), ROUND_HALF_UP)
