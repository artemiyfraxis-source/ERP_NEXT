from datetime import date
from decimal import Decimal

import pytest

from app.modules.procurement.money import (
    EMPTY_MONEY,
    ExchangeRate,
    FactMoney,
    MoneyInput,
    MoneyValidationError,
    apply_currency_and_fact,
    compute_fact,
    convert_to_rub,
    parse_number_loose,
    pay_gate_missing,
    rate_day,
    reconvert,
    rescale_to_quantity,
)


def d(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("1234,56", "1234.56"),
        ("1234.56", "1234.56"),
        ("1 234,56", "1234.56"),
        ("1,234,567", "1234567"),
        ("1.234.567", "1234567"),
        ("$1,234.56", "1234.56"),
        ("-1,5", "-1.5"),
        ("22%", "22"),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_number_loose_matches_supplymap_vectors(raw: str, expected: str | None) -> None:
    assert parse_number_loose(raw) == d(expected)


@pytest.mark.parametrize(
    ("driver", "net", "vat", "gross", "qty", "total", "expected"),
    [
        ("net", "100", "20", None, "3", None, (None, "120", "360")),
        ("gross", None, "20", "120", "2", None, ("100", None, "240")),
        ("vat", "100", "22", None, "1", None, (None, "122", "122")),
        ("vat", None, "20", "120", None, None, ("100", None, None)),
        ("qty", None, "20", "120", "5", None, (None, None, "600")),
        ("net", "1000.50", "20", None, "2", None, (None, "1200.6", "2401.2")),
        ("net", "100", None, None, "2", None, (None, None, None)),
        ("sum", None, "22", None, "100", "9394", ("77", "93.94", None)),
        ("sum", None, None, None, "100", "9394", (None, "93.94", None)),
        ("sum", None, "22", None, None, "9394", (None, None, None)),
        ("vat", None, "22", None, "100", "9394", ("77", "93.94", "9394")),
        ("net", "100", "0", None, "2", None, (None, "100", "200")),
    ],
)
def test_compute_fact_matches_supplymap_vectors(driver, net, vat, gross, qty, total, expected) -> None:
    result = compute_fact(driver, d(net), d(vat), d(gross), d(qty), d(total))
    assert (result.net, result.gross, result.sum) == tuple(d(value) for value in expected)


def test_derived_money_is_rounded_half_up_to_kopecks() -> None:
    result = compute_fact("gross", None, Decimal("22"), Decimal("100"), Decimal("3"), None)
    assert result.net == Decimal("81.97")  # 100 / 1.22 = 81.967…
    assert result.sum == Decimal("300.00")
    assert compute_fact("net", Decimal("0.125"), Decimal("0"), None, Decimal("1"), None).gross == Decimal("0.13")


def test_convert_to_rub_applies_cbr_rate_and_markup() -> None:
    assert convert_to_rub(Decimal("100"), Decimal("90")) == Decimal("9585.00")
    assert convert_to_rub(Decimal("10.5"), Decimal("12"), Decimal("0")) == Decimal("126.00")
    assert convert_to_rub(Decimal("100"), Decimal("0")) is None
    assert convert_to_rub(None, Decimal("90")) is None


def test_rub_input_derives_missing_fields_without_rate() -> None:
    money = apply_currency_and_fact(
        MoneyInput(currency="руб", price=Decimal("100"), vat_rate=Decimal("22")), Decimal("3"), None,
    )
    assert (money.currency, money.price, money.price_with_vat, money.sum) == (
        "RUB", Decimal("100"), Decimal("122.00"), Decimal("366.00"),
    )
    assert money.source_currency is None and money.exchange_rate is None


def test_foreign_input_is_converted_then_derived_and_keeps_source_amounts() -> None:
    rate = ExchangeRate(Decimal("90"), date(2026, 9, 20))
    money = apply_currency_and_fact(
        MoneyInput(currency="usd", price=Decimal("100"), vat_rate=Decimal("22")), Decimal("2"), rate,
    )
    assert money.currency == "RUB"
    assert money.price == Decimal("9585.00")
    assert money.price_with_vat == Decimal("11693.70")
    assert money.sum == Decimal("23387.40")
    assert (money.source_currency, money.source_price, money.driver) == ("USD", Decimal("100"), "net")
    assert (money.exchange_rate, money.exchange_rate_date, money.exchange_rate_manual) == (
        Decimal("90"), date(2026, 9, 20), False,
    )


def test_foreign_sum_is_converted_too() -> None:
    money = apply_currency_and_fact(
        MoneyInput(currency="USD", vat_rate=Decimal("22"), sum=Decimal("1000")),
        Decimal("10"),
        ExchangeRate(Decimal("90"), date(2026, 9, 20)),
    )
    assert money.sum == Decimal("95850.00")
    assert money.price_with_vat == Decimal("9585.00")
    assert money.price == Decimal("7856.56")


def test_currency_stays_foreign_when_there_is_nothing_to_convert() -> None:
    money = apply_currency_and_fact(
        MoneyInput(currency="EUR", vat_rate=Decimal("22")), Decimal("1"),
        ExchangeRate(Decimal("100"), date(2026, 9, 20)),
    )
    assert money.currency == "EUR"
    assert money.source_currency is None


def test_foreign_input_without_rate_is_rejected() -> None:
    with pytest.raises(MoneyValidationError, match="Нет курса USD"):
        apply_currency_and_fact(MoneyInput(currency="USD", price=Decimal("1")), Decimal("1"), None)


def test_negative_money_is_rejected() -> None:
    with pytest.raises(MoneyValidationError, match="отрицательной"):
        apply_currency_and_fact(MoneyInput(currency="RUB", price=Decimal("-1")), Decimal("1"), None)


def test_reconvert_replays_source_input_with_new_rate() -> None:
    first = apply_currency_and_fact(
        MoneyInput(currency="USD", price=Decimal("100"), vat_rate=Decimal("22")), Decimal("2"),
        ExchangeRate(Decimal("80"), date(2026, 9, 20), manual=True),
    )
    replayed = reconvert(first, Decimal("2"), ExchangeRate(Decimal("90"), date(2026, 9, 20)))
    assert replayed.price == Decimal("9585.00")
    assert replayed.exchange_rate_manual is False
    assert reconvert(FactMoney(currency="RUB", price=Decimal("1")), Decimal("1"), first.rate) == FactMoney(
        currency="RUB", price=Decimal("1"),
    )


def test_rescale_keeps_prices_and_rate_and_prices_the_remainder() -> None:
    paid = apply_currency_and_fact(
        MoneyInput(currency="USD", vat_rate=Decimal("22"), sum=Decimal("1000"), driver="sum"), Decimal("10"),
        ExchangeRate(Decimal("90"), date(2026, 9, 20)),
    )
    rest = rescale_to_quantity(paid, Decimal("10"), Decimal("2.5"))
    assert rest.price_with_vat == paid.price_with_vat
    assert rest.sum == Decimal("23962.50")
    assert (rest.source_price_with_vat, rest.source_sum, rest.driver) == (
        Decimal("100.00"), Decimal("250.00"), "gross",
    )
    assert reconvert(rest, Decimal("2.5"), rest.rate).sum == rest.sum


def test_pay_gate_lists_missing_fields_in_sheet_order() -> None:
    assert pay_gate_missing(
        EMPTY_MONEY, quantity=None, unit=" ", invoice_item_name=None, expected_arrival=None,
    ) == [
        "ВАЛЮТА", "ЦЕНА (факт)", "ЦЕНА С НДС (факт)", "КОЛ-ВО (факт)", "ЕД.ИЗМ (факт)",
        "СУММА (факт)", "НАИМЕНОВАНИЕ (Счет)", "ОЖИД. ДАТА",
    ]
    complete = FactMoney(currency="RUB", price=Decimal("1"), price_with_vat=Decimal("1.22"), sum=Decimal("1.22"))
    assert pay_gate_missing(
        complete, quantity=Decimal("1"), unit="шт", invoice_item_name="Разъём", expected_arrival=date(2026, 10, 1),
    ) == []


def test_rate_day_prefers_payment_then_invoice_then_stored_then_today() -> None:
    today = date(2026, 9, 28)
    assert rate_day(paid_on=date(2026, 9, 25), invoice_date=date(2026, 9, 1), stored=None, today=today) == date(2026, 9, 25)
    assert rate_day(paid_on=None, invoice_date=date(2026, 9, 1), stored=date(2026, 9, 10), today=today) == date(2026, 9, 1)
    assert rate_day(paid_on=None, invoice_date=None, stored=date(2026, 9, 10), today=today) == date(2026, 9, 10)
    assert rate_day(paid_on=None, invoice_date=None, stored=None, today=today) == today
