"""«Наименование» on «Снабжение»: ERP's references first, then the spec, the warehouse, the supplier site (owner, 12.10.2026)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.modules.references.load_mirror import BUNDLED_MIRROR, read_mirror
from app.workflows.item_names import REF, SITE, SPEC, STOCK, ReferenceNames, StockNames, name_report, resolve_name


@pytest.fixture(scope="module")
def book() -> ReferenceNames:
    """The references as the server has them: the bundled mirror of БД.ТЕР / БД.КОАКС / БД.ПРОВ / БД.МАТ."""
    snapshot = read_mirror(BUNDLED_MIRROR)
    return ReferenceNames(record for records in snapshot.tables.values() for record in records)


@dataclass
class Lot:
    article: str
    name: str
    grn: str = ""


@pytest.mark.parametrize(("article", "name"), [
    # JST XH and CNLINKO LP-12: the owner's «—» rows, all known to ERP's connector families and «Терминалы».
    ("XHP-4", "Корпус JST XH 4 конт."),
    ("SXH-001T-P0.6", "Контакт обжимной JST XH гнездо"),
    ("LP-12-J04SX-02-401", "Разъём CNLINKO LP-12 розетка 4 конт."),
    ("LP-12-C04PE-02-001", "Разъём CNLINKO LP-12 вилка 4 конт."),
    ("LP-12-C02PE-02-001", "Разъём CNLINKO LP-12 вилка 2 конт."),
    # Hookup wires: series + section + colour from БД.ПРОВ.
    ("ПВАМ 0,35 красный", "Провод ПВАМ 0,35 мм² красный"),
    ("ПВАМ 0,35 чёрный", "Провод ПВАМ 0,35 мм² чёрный"),
    ("ПВАМ 0,35 жёлтый", "Провод ПВАМ 0,35 мм² жёлтый"),
    ("ПВАМ 0,35 белый", "Провод ПВАМ 0,35 мм² белый"),
    ("НВ-4 0,2 белый", "Провод НВ-4 0,20 мм² белый"),
    ("UL1061 28AWG красный", "Провод UL1061 28AWG красный"),
    # Cables: every word of the article inside one mark.
    ("U/UTP 2x2x0,48", "Кабель ParLan Patch U/UTP Cat5e 2x2x0,48"),
    ("RG316", "Кабель RG316"),
    # Coverings from БД.МАТ: Ø before/after, colour, the maker of the matching row.
    ("ТУТ 3,0/1,5мм, чёрная", "Трубка термоусадочная ТУТ 3/1,5 мм, чёрная (Rexant)"),
    ("ТТК 6/2мм, чёрная", "Трубка термоусадочная клеевая ТТК 6/2 мм, чёрная (КВТ Калуга)"),
    ("ТУТ 7/3,5мм, синяя", "Трубка термоусадочная ТУТ 7/3,5 мм, синяя"),  # the group is known, the size is not
    ("Оплётка XS-10", "Оплётка XS-10"),
    # Unknown to the references.
    ("BL2018TS", ""),
    ("Изделие заказчика", ""),
    ("", ""),
])
def test_reference_names(book, article, name):
    assert book.name(article) == name


def test_order_references_then_spec_then_stock_then_site(book):
    stock = StockNames([Lot("xhp4", "Колодка XH 4 (склад)", "FRX.26.0000001"), Lot("QQ-77", "Деталь QQ", "FRX.26.0000002")])
    site = "https://www.chipdip.ru/product/xhp-4"

    by_ref = resolve_name("XHP-4", "JST XHP-4 корпус купить", site, book, stock)
    assert (by_ref.name, by_ref.source) == ("Корпус JST XH 4 конт.", REF)

    typed = resolve_name("QQ-77", "Деталь по чертежу", "", book, stock)
    assert (typed.name, typed.source, typed.hint) == ("Деталь по чертежу", SPEC, "Из спецификации проекта")

    lot = resolve_name("QQ-77", "Деталь QQ-77 купить оптом", "https://shop.example/qq", book, stock)
    assert (lot.name, lot.source) == ("Деталь QQ", STOCK)
    assert "FRX.26.0000002" in lot.hint

    from_site = resolve_name(
        "ZZ-1", "Деталь ZZ-1 жёлтая, купить оптом и в розницу", "https://www.shop.example/zz", book, stock,
    )
    assert (from_site.name, from_site.source) == ("Деталь ZZ-1 жёлтая", SITE)
    assert from_site.hint == "С сайта shop.example; на сайте: «Деталь ZZ-1 жёлтая, купить оптом и в розницу»"


def test_unknown_article_says_why(book):
    nothing = resolve_name("ZZ-2", "", "https://www.shop.example/zz", book, StockNames())
    assert (nothing.name, nothing.source) == ("", "")
    assert nothing.hint == "Артикул неизвестен: нет в справочниках ERP, на складе не найдено, сайт shop.example не отдал название"
    no_link = resolve_name("ZZ-2", "", "", book, StockNames())
    assert no_link.hint.endswith("ссылки на сайт нет")


def test_a_replaced_article_is_looked_up_by_the_article_actually_used(book):
    """533К.11: КД BL2018TS, the estimator wrote the original SXH-001T-P0.6 into «Наименование» (`kit_article`)."""
    shown = resolve_name("BL2018TS", "SXH-001T-P0.6", "", book, StockNames())
    assert (shown.name, shown.source) == ("Контакт обжимной JST XH гнездо", REF)
    assert "SXH-001T-P0.6" in shown.hint
    unknown = resolve_name("BL2018TS", "QWE-123", "", book, StockNames())
    assert (unknown.name, unknown.source) == ("QWE-123", SPEC)


def test_stock_matches_letters_and_digits_only():
    stock = StockNames([Lot("GHR 04V-S", "Колодка GH 4"), Lot("GHR-04V-S", "Другая партия"), Lot("X-1", "X-1")])
    assert stock.get("ghr04vs").name == "Колодка GH 4"  # the first lot wins
    assert stock.get("X1") is None  # a lot whose name is its article tells nothing


def test_report_lists_stored_and_shown(book):
    lines = name_report([("708К.4", "XHP-4", "", ""), ("708К.4", "ZZ-3", "Деталь", "")], book, StockNames())
    assert lines[0].split("\t")[:6] == ["708К.4", "XHP-4", "—", "→", "Корпус JST XH 4 конт.", "справочник"]
    assert lines[1].split("\t")[:6] == ["708К.4", "ZZ-3", "Деталь", "=", "Деталь", "спецификация"]
