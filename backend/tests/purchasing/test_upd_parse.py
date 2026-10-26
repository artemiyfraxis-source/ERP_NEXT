"""The registry's PDF parser rules (purchasing.upd_parse): the cases of Roller_Buyer's google/upd-parser/test/core.test.mjs,
which this module ports, plus the registry values."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.purchasing import upd_parse as p


def money(qty=None, price=None, net=None, rate=None, no_vat=False, vat=None, gross=None) -> p.LineMoney:
    return p.LineMoney(qty, price, net, rate, no_vat, vat, gross)


@pytest.mark.parametrize(("name", "tag"), [
    ("Арисмо М15 221К.5 01.04.26.pdf", "M15"),
    ("M-15 Элестим 135.pdf", "M15"),
    ("Давальческое Элестим Кардио.pdf", "M15"),
    ("ТН Диалог.pdf", "TN"),
    ("TH+СФ Диалог 8 400.pdf", "TN"),
    ("Инт Техника 13 100.pdf", ""),
    ("ТНТ Электро 1 000.pdf", ""),  # ТН must be a whole word
    ("Схема М150.pdf", ""),  # М15 must not match М150
])
def test_file_tag(name, tag):
    assert p.file_tag(name) == tag


@pytest.mark.parametrize(("name", "amount"), [
    ("Инт Техника 13 100.pdf", 13100), ("Мицар 895.pdf", 895), ("Эльво-Связь 30 000 (2).pdf", 30000),
    ("Польза природы 1 949 26г.pdf", 1949), ("Меттатрон 2 499 (Допоставка).pdf", 2499),
    ("Галлерея Коннект 105 124 СФ.pdf", 105124), ("Деловые Линии 2 500 Акт.pdf", 2500),
    ("Протон 1 220 ТОРГ-12.pdf", 1220), ("Акомпани Акт сверки.pdf", None),
    ("Галлерея коннект 102 624 + Акт 2 500.pdf", None), ("Протон Накладная №381А.pdf", None),
    ("Сканировать1.PDF", None), ("Арисмо М15 221К.5 01.04.26.pdf", None),
])
def test_file_amount(name, amount):
    assert p.file_amount(name) == amount


def test_numbers_rates_and_dates_in_real_shapes():
    assert p.parse_num("1 234,56") == 1234.56
    assert p.parse_num("4083.33") == 4083.33
    assert p.parse_num("1.234,56") == 1234.56
    assert p.parse_num("—") is None and p.parse_num("") is None and p.parse_num(None) is None
    assert p.parse_vat_rate("22%") == (22, False)
    assert p.parse_vat_rate(0.2) == (20, False)
    assert p.parse_vat_rate("Без НДС") == (0, True)
    assert p.parse_date("№ 2937 от 21 апреля 2026 г.") == date(2026, 4, 21)
    assert p.parse_date("21 мая 2026") == date(2026, 5, 21)
    assert p.parse_date("3 марта 2026") == date(2026, 3, 3)
    assert p.parse_date("29.01.2025") == date(2025, 1, 29)
    assert p.parse_date("01.04.26") == date(2026, 4, 1)
    assert p.parse_date("2026-02-30") is None


def test_repair_vat_sitting_in_the_with_vat_column():
    line, fixes, issues = p.repair_line(money(1, 409.84, 409.84, 22, gross=90.16))
    assert (line.vat, line.gross, issues) == (90.16, 500, [])
    assert fixes


def test_repair_kopeck_rounding_in_qty_times_price():
    line, _, issues = p.repair_line(money(10, 77.87, 778.69, 22, gross=171.31))
    assert (line.gross, issues) == (950, [])


def test_repair_fills_a_missing_total_but_reports_what_does_not_add_up():
    assert p.repair_line(money(25, 125, 3125, 20, vat=625))[0].gross == 3750
    assert p.repair_line(money(2, 100, 500, 22, vat=110, gross=610))[2] == ["кол-во × цена ≠ без НДС"]


def test_repair_splits_vat_out_of_the_total():
    line, _, issues = p.repair_line(money(3, rate=22, gross=1220))
    assert (line.vat, line.net, line.price, issues) == (220, 1000, 333.33, [])
    no_vat = p.repair_line(money(1, rate=0, no_vat=True, gross=700))[0]
    assert (no_vat.net, no_vat.vat) == (700, 0)
    assert p.repair_line(money(1, gross=700))[0].net is None  # without a rate nothing can be split


def test_repair_price_with_vat_only_where_the_form_allows_it():
    line = money(2, 600, rate=20, gross=1200)
    act, fixes, issues = p.repair_line(line, price_may_include_vat=True)
    assert (act.price, act.net, act.vat, issues) == (500, 1000, 200, [])
    assert "цена указана с НДС" in fixes
    assert p.repair_line(money(2, 600, rate=20, vat=200, gross=1200), price_may_include_vat=True)[2] == []
    assert p.repair_line(line)[2]  # in a УПД the price is net by the form: a misread


def test_clean_upd_is_parsed():
    doc = p.normalize({
        "docKind": "UPD", "number": "№ 2937", "date": "21.04.2026", "sellerName": "ООО «Дарт»",
        "sellerInn": "7707083893", "totalGross": 500,
        "lines": [{"seq": 1, "name": "Разъём SMA-J", "qty": 1, "price": 409.84, "net": 409.84, "vatRate": "22%",
                   "vat": 90.16, "gross": 500}],
    }, "", "Дарт 500.pdf")
    assert (doc.number, doc.doc_date, doc.issues, doc.status) == ("2937", date(2026, 4, 21), [], p.OK)


def test_file_amount_and_line_sum_are_checked_against_the_total():
    doc = p.normalize({
        "docKind": "UPD", "number": "1", "date": "2026-01-19", "sellerName": "ООО Инт Техника",
        "sellerInn": "7707083893", "totalGross": 950,
        "lines": [{"seq": 1, "name": "Разъём", "qty": 10, "price": 77.87, "net": 778.69, "vatRate": "22%",
                   "gross": 171.31}],
    }, "", "Инт Техника 13 100.pdf")
    assert doc.lines[0].gross == 950
    assert any("сумма в имени файла 13100" in issue for issue in doc.issues)
    assert doc.status == p.CHECK


def test_missing_seller_and_inn_are_reported_and_delivery_is_a_service():
    doc = p.normalize({"docKind": "UPD", "number": "5", "date": "2026-02-10", "lines": [
        {"seq": 1, "name": "Доставка СДЭК НП", "qty": 1, "price": 666.67, "net": 666.67, "vatRate": "5%",
         "vat": 33.33, "gross": 700}]}, "", "ЛинкМаг 700.pdf")
    assert "нет продавца" in doc.issues and "нет ИНН продавца" in doc.issues
    assert doc.lines[0].is_service


def test_the_file_tag_overrides_the_model_and_m15_needs_no_prices_or_inn():
    doc = p.normalize({"docKind": "UPD", "number": "135", "date": "01.04.2026", "sellerName": "ООО Элестим-Кардио",
                       "lines": [{"seq": 1, "name": "Плата", "qty": 5, "unit": "шт"}]}, "M15", "М15 Элестим 135.pdf")
    assert (doc.kind, doc.issues) == ("M15", [])


def test_not_a_purchase_document():
    doc = p.normalize({"docKind": "OTHER", "number": ""}, "", "Акомпани Акт сверки.pdf")
    assert (doc.status, doc.lines, doc.issues) == (p.OTHER, [], [])


def test_duplicate_lines_are_flagged_and_numbers_stay_unique():
    line = {"seq": 1, "name": "Провод НВ-4 0,75", "qty": 2, "price": 1319.67, "net": 2639.34, "vatRate": "22%",
            "vat": 580.66, "gross": 3220}
    doc = p.normalize({"docKind": "UPD", "number": "264", "date": "17.03.2026", "sellerName": "ЧипДип",
                       "sellerInn": "7707083893", "lines": [line, {**line, "seq": 2}, line]}, "", "ЧипДип 6 440.pdf")
    assert any(issue.startswith("повтор строки") for issue in doc.lines[2].issues)
    assert [line.seq for line in doc.lines] == [1, 2, 3]  # the registry keeps one line per number


def test_every_kind_of_the_schema_has_a_label():
    assert sorted(p.response_schema()["properties"]["docKind"]["enum"]) == sorted(p.KIND_LABELS)
    assert (p.KIND_LABELS["ACT"], p.KIND_LABELS["TORG12"]) == ("Акт", "Товарная накладная")


def test_an_act_keeps_its_lines_as_services_and_splits_prices_with_vat():
    doc = p.normalize({
        "docKind": "act", "number": "118", "date": "14.03.2026", "sellerName": "ООО Деловые Линии",
        "sellerInn": "7707083893", "basis": "Договор № 12 от 01.02.2025; Счет № 345 от 10.03.2026", "totalVat": 250,
        "totalGross": 1500, "lines": [
            {"seq": 1, "name": "Изготовление печатных плат по чертежу", "qty": 2, "unit": "шт", "price": 600,
             "gross": 1200, "vatRate": "20%"},
            {"seq": 2, "name": "Срочность", "qty": 1, "unit": "усл", "price": 300, "gross": 300, "vatRate": "20%"},
        ],
    }, "", "Деловые Линии 1 500 Акт.pdf")
    assert doc.kind == "ACT" and doc.basis == "Договор № 12 от 01.02.2025; Счет № 345 от 10.03.2026"
    assert all(line.is_service for line in doc.lines)
    assert (doc.lines[0].net, doc.lines[0].vat, doc.lines[0].price) == (1000, 200, 500)
    assert (doc.issues, doc.status) == ([], p.OK)


def test_torg12_without_an_invoice_and_the_owner_tag_still_wins():
    raw = {
        "docKind": "TORG12", "number": "381А", "date": "05.03.2026", "sellerName": "АО Протон",
        "sellerInn": "7707083893", "basis": "Договор поставки № 55 от 04.02.2021", "totalGross": 1342,
        "crossCheckTotal": 999, "lines": [
            {"seq": 1, "name": "Розетка СНП268", "unitCode": "796", "unit": "шт", "qty": 10, "price": 100,
             "net": 1000, "vatRate": "22%", "vat": 220, "gross": 1220},
            {"seq": 2, "name": "Доставка", "qty": 1, "price": 100, "net": 100, "vatRate": "22%", "vat": 22,
             "gross": 122},
        ],
    }
    doc = p.normalize(raw, "", "Протон Накладная №381А.pdf")
    assert doc.kind == "TORG12" and [line.is_service for line in doc.lines] == [False, True] and doc.issues == []
    tagged = p.normalize(raw, "TN", "ТН Протон 1 342.pdf")
    assert tagged.kind == "TORG12_INVOICE"
    assert any(issue.startswith("итог ТОРГ-12 999") for issue in tagged.issues)
    assert p.normalize({**raw, "docKind": "ACT"}, "M15", "М15 Протон.pdf").kind == "M15"


def test_our_own_inn_in_the_seller_means_seller_and_buyer_are_swapped():
    doc = p.normalize({"docKind": "UPD", "number": "41", "date": "2026-01-10", "sellerName": "ООО «ЗЭП «ФРАКСИС»",
                       "sellerInn": "5040191181", "buyerInn": "7707083893", "lines": [
                           {"seq": 1, "name": "Кабель", "qty": 1, "price": 100, "net": 100, "vatRate": "без НДС",
                            "gross": 100}]}, "", "Амперкот 100.pdf")
    assert any("перепутаны" in issue for issue in doc.issues) and doc.status == p.CHECK


def test_registry_values_in_kopecks_and_rubles():
    doc = p.normalize({"docKind": "UPD", "number": "7", "date": "2026-10-01", "sellerName": "ООО Ромашка",
                       "sellerInn": "7707083893", "lines": [
                           {"seq": 1, "name": "Разъём XLR", "article": "ACG-3F", "qty": 3, "price": 333.333,
                            "vatRate": "22%"}]}, "", "Ромашка.pdf")
    header, line = p.header_values(doc), p.line_values(doc.lines[0])
    assert header["kind"] == "УПД" and header["total_gross"] == 122_000 and header["status"] == p.OK
    assert (line["quantity"], line["price"], line["net"], line["vat"], line["gross"]) == (
        Decimal("3"), Decimal("333.333"), 100_000, 22_000, 122_000)
    assert (line["vat_rate"], line["fixes"]) == ("22", "без НДС = кол-во × цена; НДС = без НДС × ставка; "
                                                       "с НДС = без НДС + НДС")


def test_prompt_per_tag_and_the_text_layer_hint():
    assert "M15 — " in p.prompt("") and "TORG12_INVOICE" in p.prompt("")
    assert "ДВА документа" in p.prompt("TN") and "М-15" in p.prompt("M15")
    for tag in ("", "TN", "M15"):
        assert "paymentDoc — КАЖДЫЙ" in p.prompt(tag) and "basis — ВСЕ" in p.prompt(tag)
    hinted = p.prompt("", "Счет-фактура № 12   от 01.10.2026\n" + "я" * 40_000)
    assert "<<<ТЕКСТ PDF\nСчет-фактура № 12 от 01.10.2026" in hinted
    assert len(hinted) < len(p.prompt("")) + p.TEXT_LAYER_LIMIT + 400
    assert "ТЕКСТ PDF" not in p.prompt("")
