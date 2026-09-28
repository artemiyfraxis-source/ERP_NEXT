"""Pure money rules of a procurement line: fact prices, VAT, currency conversion, pay gate.

Ports SUPPLYMAP `Currency.gs` (`parseNumLoose_`, `round2_`, `vatMult_`, `computeFact_`, `convToRub_`),
`Kp.gs` (`kpApplyCurrencyAndFact_`) and `Config.gs` `PUR_PAY_REQUIRED`. Arithmetic is Decimal with
ROUND_HALF_UP instead of JS float `Math.round`; results match except float artefacts such as 1.005.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CURRENCY_MARKUP = Decimal("0.065")
RUB = "RUB"
_RUB_CODES = {"RUB", "РУБ"}
DRIVERS = ("net", "gross", "vat", "qty", "sum")
_KOPECK = Decimal("0.01")


class MoneyValidationError(ValueError):
    """Money input violates a procurement money rule."""


def parse_number_loose(value: object) -> Decimal | None:
    """`parseNumLoose_`: the last separator is the decimal one; thousands separators and symbols drop."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    text = re.sub(r"[^0-9.,-]", "", str(value))
    dot, comma = text.rfind("."), text.rfind(",")
    if dot >= 0 and comma >= 0:
        text = text.replace(",", "") if dot > comma else text.replace(".", "").replace(",", ".", 1)
    elif comma >= 0:
        text = text.replace(",", "") if text.count(",") > 1 else text.replace(",", ".", 1)
    elif dot >= 0 and text.count(".") > 1:
        text = text.replace(".", "")
    if text in {"", "-", "."}:
        return None
    match = re.match(r"-?\d*\.?\d*", text)
    number = match.group(0) if match else ""
    if number in {"", "-", ".", "-."}:
        return None
    try:
        return Decimal(number)
    except InvalidOperation:
        return None


def round_kopecks(value: Decimal) -> Decimal:
    return value.quantize(_KOPECK, rounding=ROUND_HALF_UP)


def vat_multiplier(vat_rate: Decimal | None) -> Decimal | None:
    """`vatMult_`: 22 → 1.22; no rate → None (0 is a rate: multiplier 1)."""
    if vat_rate is None:
        return None
    return 1 + vat_rate / 100


@dataclass(frozen=True)
class FactPrices:
    net: Decimal | None = None
    gross: Decimal | None = None
    sum: Decimal | None = None


def compute_fact(
    driver: str,
    net: Decimal | None,
    vat_rate: Decimal | None,
    gross: Decimal | None,
    quantity: Decimal | None,
    total: Decimal | None,
) -> FactPrices:
    """`computeFact_`: derive the other money fields from the edited one (None = not recalculated)."""
    if driver not in DRIVERS:
        raise MoneyValidationError(f"Неизвестное ведущее поле: {driver}")
    multiplier = vat_multiplier(vat_rate)
    quantity_ok = quantity is not None and quantity != 0
    out_net: Decimal | None = None
    out_gross: Decimal | None = None
    out_sum: Decimal | None = None

    if driver == "net" and net is not None and multiplier:
        out_gross = round_kopecks(net * multiplier)
    elif driver == "gross" and gross is not None and multiplier:
        out_net = round_kopecks(gross / multiplier)
    elif driver == "sum" and total is not None and quantity_ok:
        out_gross = round_kopecks(total / quantity)
        if multiplier:
            out_net = round_kopecks(out_gross / multiplier)
    elif driver == "vat" and multiplier:
        if net is not None:
            out_gross = round_kopecks(net * multiplier)
        elif gross is not None:
            out_net = round_kopecks(gross / multiplier)
        elif total is not None and quantity_ok:
            out_gross = round_kopecks(total / quantity)
            out_net = round_kopecks(out_gross / multiplier)

    if driver != "sum":
        if out_gross is not None:
            unit_gross = out_gross
        elif gross is not None:
            unit_gross = gross
        elif multiplier and net is not None:
            unit_gross = round_kopecks(net * multiplier)
        else:
            unit_gross = None
        if unit_gross is not None and quantity is not None:
            out_sum = round_kopecks(unit_gross * quantity)
    return FactPrices(net=out_net, gross=out_gross, sum=out_sum)


def convert_to_rub(amount: Decimal | None, rate: Decimal | None, markup: Decimal = CURRENCY_MARKUP) -> Decimal | None:
    """`convToRub_`: amount × CBR rate × (1 + markup), to kopecks; nothing to convert → None."""
    if amount is None or not rate:
        return None
    return round_kopecks(amount * rate * (1 + markup))


def normalize_currency(code: str | None) -> str | None:
    value = str(code or "").strip().upper()
    if not value:
        return None
    return RUB if value in _RUB_CODES else value


def is_foreign(code: str | None) -> bool:
    normalized = normalize_currency(code)
    return normalized is not None and normalized != RUB


@dataclass(frozen=True)
class ExchangeRate:
    """RUB per one unit of foreign currency on `day`; `manual` when the buyer typed it (CBR was down)."""

    value: Decimal
    day: date
    manual: bool = False


@dataclass(frozen=True)
class MoneyInput:
    """What the buyer typed, in the invoice currency."""

    currency: str | None
    price: Decimal | None = None
    vat_rate: Decimal | None = None
    price_with_vat: Decimal | None = None
    sum: Decimal | None = None
    driver: str | None = None


@dataclass(frozen=True)
class FactMoney:
    """Fact money stored on a line. Amounts are in `currency` (RUB once converted).

    `source_*` keep the typed invoice-currency amounts and `driver`, so a conversion can be replayed
    with another rate (payment day, CBR reconciliation) without compounding rounding.
    """

    currency: str | None = None
    price: Decimal | None = None
    vat_rate: Decimal | None = None
    price_with_vat: Decimal | None = None
    sum: Decimal | None = None
    driver: str | None = None
    source_currency: str | None = None
    source_price: Decimal | None = None
    source_price_with_vat: Decimal | None = None
    source_sum: Decimal | None = None
    exchange_rate: Decimal | None = None
    exchange_rate_date: date | None = None
    exchange_rate_manual: bool = False

    @property
    def rate(self) -> ExchangeRate | None:
        if self.exchange_rate is None or self.exchange_rate_date is None:
            return None
        return ExchangeRate(self.exchange_rate, self.exchange_rate_date, self.exchange_rate_manual)

    def source_input(self) -> MoneyInput:
        return MoneyInput(
            currency=self.source_currency,
            price=self.source_price,
            vat_rate=self.vat_rate,
            price_with_vat=self.source_price_with_vat,
            sum=self.source_sum,
            driver=self.driver,
        )


EMPTY_MONEY = FactMoney()


def _default_driver(entry: MoneyInput) -> str:
    # Kp.gs:171-175: net present → 'net'; otherwise gross → 'gross'; otherwise 'sum'.
    if entry.price is not None:
        return "net"
    if entry.price_with_vat is not None:
        return "gross"
    return "sum"


def _validate_input(entry: MoneyInput) -> None:
    for label, value in (
        ("ЦЕНА (факт)", entry.price),
        ("ЦЕНА С НДС (факт)", entry.price_with_vat),
        ("СУММА (факт)", entry.sum),
        ("НДС (факт)", entry.vat_rate),
    ):
        if value is not None and value < 0:
            raise MoneyValidationError(f"{label} не может быть отрицательной")
    if entry.driver is not None and entry.driver not in DRIVERS:
        raise MoneyValidationError(f"Неизвестное ведущее поле: {entry.driver}")


def apply_currency_and_fact(
    entry: MoneyInput,
    quantity: Decimal | None,
    rate: ExchangeRate | None,
) -> FactMoney:
    """`kpApplyCurrencyAndFact_`: convert foreign amounts to RUB, then derive the missing fields."""
    _validate_input(entry)
    currency = normalize_currency(entry.currency)
    driver = entry.driver or _default_driver(entry)
    price, gross, total = entry.price, entry.price_with_vat, entry.sum
    source = FactMoney()

    if is_foreign(currency):
        if rate is None:
            raise MoneyValidationError(f"Нет курса {currency} для пересчёта в рубли")
        converted = [convert_to_rub(value, rate.value) for value in (price, gross, total)]
        if any(value is not None for value in converted):
            source = FactMoney(
                source_currency=currency,
                source_price=price,
                source_price_with_vat=gross,
                source_sum=total,
                exchange_rate=rate.value,
                exchange_rate_date=rate.day,
                exchange_rate_manual=rate.manual,
            )
            price, gross, total = converted
            currency = RUB

    derived = compute_fact(driver, price, entry.vat_rate, gross, quantity, total)
    return replace(
        source,
        currency=currency,
        price=derived.net if derived.net is not None else price,
        vat_rate=entry.vat_rate,
        price_with_vat=derived.gross if derived.gross is not None else gross,
        sum=derived.sum if derived.sum is not None else total,
        driver=driver if source.source_currency else None,
    )


def reconvert(money: FactMoney, quantity: Decimal | None, rate: ExchangeRate) -> FactMoney:
    """Replay the stored invoice-currency input with another rate (payment day or CBR check)."""
    if not money.source_currency:
        return money
    return apply_currency_and_fact(money.source_input(), quantity, rate)


def rescale_to_quantity(money: FactMoney, from_quantity: Decimal, to_quantity: Decimal) -> FactMoney:
    """Same unit prices and rate, sum for another quantity (backorder remainder).

    A sum-driven source input is rewritten as its unit price, so a later replay (CBR check of a manual
    rate) prices the new quantity instead of re-spreading the old total.
    """
    rub_sum = compute_fact("qty", money.price, money.vat_rate, money.price_with_vat, to_quantity, None).sum
    if not money.source_currency:
        return replace(money, sum=rub_sum)
    source_gross, driver = money.source_price_with_vat, money.driver
    if driver == "sum":
        source_gross = (
            round_kopecks(money.source_sum / from_quantity)
            if money.source_sum is not None and from_quantity
            else None
        )
        driver = "gross"
    source_sum = compute_fact("qty", money.source_price, money.vat_rate, source_gross, to_quantity, None).sum
    return replace(
        money,
        sum=rub_sum,
        source_price_with_vat=source_gross,
        source_sum=source_sum,
        driver=driver,
    )


PAY_REQUIRED = (
    "ВАЛЮТА",
    "ЦЕНА (факт)",
    "ЦЕНА С НДС (факт)",
    "КОЛ-ВО (факт)",
    "ЕД.ИЗМ (факт)",
    "СУММА (факт)",
    "НАИМЕНОВАНИЕ (Счет)",
    "ОЖИД. ДАТА",
)


def pay_gate_missing(
    money: FactMoney,
    *,
    quantity: Decimal | None,
    unit: str | None,
    invoice_item_name: str | None,
    expected_arrival: date | None,
) -> list[str]:
    """`purchasePayMissing_` with ENFORCE_QUALITY_DOC=false: fields still empty, in sheet order."""
    filled = {
        "ВАЛЮТА": bool(money.currency),
        "ЦЕНА (факт)": money.price is not None,
        "ЦЕНА С НДС (факт)": money.price_with_vat is not None,
        "КОЛ-ВО (факт)": quantity is not None and quantity > 0,
        "ЕД.ИЗМ (факт)": bool(str(unit or "").strip()),
        "СУММА (факт)": money.sum is not None,
        "НАИМЕНОВАНИЕ (Счет)": bool(str(invoice_item_name or "").strip()),
        "ОЖИД. ДАТА": expected_arrival is not None,
    }
    return [name for name in PAY_REQUIRED if not filled[name]]


def rate_day(*, paid_on: date | None, invoice_date: date | None, stored: date | None, today: date) -> date:
    """Rate date: payment day, else invoice date, else the date already stored, else today.

    Deviation from SUPPLYMAP (always today) — agent decision, to be checked with the accountant.
    Falling back to the stored date keeps the sum from drifting on every re-save.
    """
    return paid_on or invoice_date or stored or today
