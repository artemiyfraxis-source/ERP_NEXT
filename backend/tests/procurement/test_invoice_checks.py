"""Проверки загруженного счёта против заказа: продавец (ИНН из справочника закупки), сумма, позиции."""

from dataclasses import replace
from decimal import Decimal

from app.modules.procurement.domain import create_line_from_requested
from app.modules.procurement.invoice_checks import INFO, OK, WARNING, invoice_checks, org_key, rub, same_org
from app.modules.procurement.money import FactMoney
from app.shared.invoice_lines import InvoiceLine
from app.shared.invoice_header import InvoiceHeader
from app.shared.invoice_reading import InvoiceReading
from tests.procurement.test_api import message

HEADER = InvoiceHeader("12", "2026-10-20", 'ООО "Поставка"', "7701234567", 122000, 22000, False)


def reading(*lines: tuple[str, float], header: InvoiceHeader = HEADER) -> InvoiceReading:
    positions = tuple(
        InvoiceLine(seq, name, "", qty, "шт", None, None) for seq, (name, qty) in enumerate(lines, start=1)
    )
    return InvoiceReading(header, positions, "текст", False, ())


def line(suffix: int, name: str, quantity: str, total: str | None, currency: str = "RUB"):
    base = create_line_from_requested(message(suffix))
    return replace(
        base, legacy_id=f"SM-{suffix:02d}", calculated_name=name, supplier="ООО Поставка",
        actual_quantity=Decimal(quantity), invoice_item_name=name,
        money=FactMoney(currency=currency, sum=Decimal(total) if total is not None else None),
    )


class Directory:
    def __init__(self, by_name=None, by_inn=None):
        self.by_name, self.by_inn = by_name or {}, by_inn or {}

    def inn_of(self, supplier):
        return self.by_name.get(supplier)

    def supplier_of_inn(self, inn):
        return self.by_inn.get(inn)


def kinds(checks):
    return [(check.kind, check.level) for check in checks]


def test_everything_agrees():
    lines = [line(1, "Разъём SMA-J", "10", "500.00"), line(2, "Кабель RG-58", "12", "720.00")]
    checks = invoice_checks(reading(("Разъём SMA-J", 10), ("Кабель RG-58", 12)), "ООО Поставка", lines,
                            Directory(by_name={"ООО Поставка": "7701234567"}))
    assert kinds(checks) == [("seller", OK), ("total", OK), ("lines", OK)]
    assert checks[1].text == "Сумма счёта 1 220,00 ₽ (НДС 220,00 ₽) = сумме строк заказа"
    assert checks[2].text == "Позиций в счёте: 2; все строки заказа (2) нашлись в счёте"


def test_seller_by_the_directory_inn_by_its_owner_or_by_name():
    lines = [line(1, "Разъём SMA-J", "10", "1220.00")]
    one = reading(("Разъём SMA-J", 10))
    other = invoice_checks(one, "ООО Поставка", lines, Directory(by_name={"ООО Поставка": "7709999999"}))[0]
    assert other.level == WARNING and "ИНН 7709999999" in other.text
    owner = invoice_checks(one, "ООО Поставка", lines, Directory(by_inn={"7701234567": "АО Другая"}))[0]
    assert owner.level == WARNING and "у «АО Другая»" in owner.text
    by_name = invoice_checks(one, "ООО Поставка", lines, None)[0]
    assert by_name.level == OK and "в справочнике не записан" in by_name.text
    stranger = invoice_checks(one, "ИП Сидоров", lines, None)[0]
    assert stranger.level == INFO and "сверьте" in stranger.text
    no_inn = invoice_checks(reading(("Разъём SMA-J", 10), header=replace(HEADER, seller_inn=None)), "ООО Поставка",
                            lines, None)[0]
    assert no_inn.level == INFO and "не прочитан" in no_inn.text


def test_total_differs_is_not_priced_yet_or_is_foreign():
    one = reading(("Разъём SMA-J", 10))
    differs = invoice_checks(one, "ООО Поставка", [line(1, "Разъём SMA-J", "10", "1000.00")])[1]
    assert differs.level == WARNING and differs.text.endswith("строк заказа — 1 000,00 ₽ (разница 220,00 ₽)")
    unpriced = invoice_checks(one, "ООО Поставка", [line(1, "Разъём SMA-J", "10", None)])[1]
    assert unpriced.level == INFO and "(0 из 1)" in unpriced.text
    foreign = invoice_checks(one, "ООО Поставка", [line(1, "Разъём SMA-J", "10", "10.00", "USD")])[1]
    assert foreign.level == INFO and "в валюте" in foreign.text
    free = invoice_checks(reading(("Разъём SMA-J", 10), header=replace(HEADER, vat=0, vat_free=True)),
                          "ООО Поставка", [line(1, "Разъём SMA-J", "10", "1220.00")])[1]
    assert free.text == "Сумма счёта 1 220,00 ₽ (без НДС) = сумме строк заказа"


def test_positions_missing_lines_other_quantities_and_extras():
    lines = [line(1, "Разъём SMA-J", "10", "500.00"), line(2, "Кабель RG-58", "12", "720.00")]
    checks = invoice_checks(reading(("Разъём SMA-J", 8), ("Доставка до адреса", 1)), "ООО Поставка", lines)
    texts = {check.text for check in checks if check.kind == "lines"}
    assert "Позиций в счёте: 2; не нашлись в счёте строки заказа: SM-02" in texts
    assert "Кол-во не совпадает: SM-01 — в заказе 10, в счёте 8" in texts
    assert "В счёте сверх заказа: Доставка до адреса" in texts


def test_a_scan_has_no_checks_and_helpers():
    assert invoice_checks(InvoiceReading(), "ООО Поставка", [line(1, "Разъём", "1", "1")]) == []
    assert rub(-123456) == "−1 234,56 ₽" and org_key('ООО "Чип и Дип"') == "чипидип"
    assert same_org('ООО "ЧИП и ДИП"', "АО Чип и Дип") and not same_org("ООО Поставка", "ООО Доставка")
