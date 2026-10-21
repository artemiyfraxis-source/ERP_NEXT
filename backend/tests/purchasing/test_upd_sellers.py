"""Seller INN correction of the UPD registry (cases from Roller_Buyer domain/upd-sellers.test.ts, live registry)."""

from __future__ import annotations

from app.modules.purchasing.upd_sellers import SellerFacts, SellerVerdict, clean_inn, resolve_sellers, seller_name_key

OWN = frozenset({"5040191181"})


def doc(name: str, inn: str, buyer: str = "5040191181") -> SellerFacts:
    return SellerFacts(name, inn, buyer)


def test_a_digit_misread_joins_the_seller_the_registry_really_has():
    kroks = [doc('ООО "Крокс Плюс"', "3662192701") for _ in range(3)]
    docs = [*kroks, doc('ООО "Крокс Плюс"', "3862192701"), doc('ООО "КРОКС ПЛЮС"', "3662152701")]
    verdicts = resolve_sellers(docs, [('ООО "КРОКС ПЛЮС"', "3662192701")], OWN)
    assert [v.inn for v in verdicts] == ["3662192701"] * 5
    assert verdicts[0].note == ""
    assert verdicts[3].note == "ИНН продавца прочитан с ошибкой: 3862192701"


def test_a_missing_digit_a_lone_inn_valid_by_chance_and_an_inn_not_read():
    docs = [
        doc('ООО "ДАРТ ХОЛДИНГ"', "7727836753"),
        doc('ООО "ДАРТ ХОЛДИНГ"', "772836753"),
        doc('ООО "Галерея Коннект"', "7719471918"),
        doc('Общество с ограниченной ответственностью "Галерея Коннект" (ООО "Галерея Коннект")', "7719117191"),
        doc('ООО "Галерея Коннект"', ""),
    ]
    verdicts = resolve_sellers(docs, [('ООО "Галерея Коннект"', "7719471918")], OWN)
    assert [(v.inn, v.note) for v in verdicts] == [
        ("7727836753", ""),
        ("7727836753", "ИНН продавца прочитан с ошибкой: 772836753"),
        ("7719471918", ""),
        ("7719471918", "ИНН продавца прочитан с ошибкой: 7719117191"),
        ("7719471918", "ИНН продавца не прочитан — найден по названию"),
    ]


def test_our_company_in_the_seller_place_takes_the_buyer_column_strangers_stay_unknown():
    verdicts = resolve_sellers(
        [doc('ООО "ЗЭП "ФРАКСИС"', "50400191181", "772421669330"), doc("КК ЭЛЕСТИМ-КАРДИО", "1111111111"),
         doc('ООО "Ромашка"', "7701000019")],
        [], OWN,
    )
    assert verdicts[0] == SellerVerdict("772421669330", "продавцом прочитана наша организация — продавец взят из графы покупателя")
    assert verdicts[1] == SellerVerdict("", "")
    assert verdicts[2] == SellerVerdict("7701000019", ""), "a valid INN of its own stays even when seen once"


def test_a_misread_inn_under_another_name_stays_unknown_without_a_name_the_one_inn_a_digit_away_takes_it():
    verdicts = resolve_sellers(
        [doc('ООО "Альфа Снаб"', "7701000019"), doc('ООО "Бета Снаб"', "7701000018"), doc("", "7701000018")], [], OWN,
    )
    assert [v.inn for v in verdicts] == ["7701000019", "", "7701000019"]


def test_name_key_and_inn_cleaning():
    assert seller_name_key('ООО "Галерея Коннект"') == seller_name_key(
        'Общество с ограниченной ответственностью "Галерея Коннект" (ООО "Галерея Коннект")'
    ) == "галереяконнект"
    assert seller_name_key("ИП Ёж") == ""
    assert clean_inn("772646440772401001") == "772646440"
    assert clean_inn(" 77-01 000019 ") == "7701000019"
