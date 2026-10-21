"""Which invoices a UPD closes (Roller_Buyer domain/doc-match.test.ts)."""

from dataclasses import replace

from app.modules.purchasing.doc_match import (
    DocConflict,
    DocSuggestion,
    MatchDecision,
    MatchInvoice,
    MatchPayment,
    MatchUpd,
    match_documents,
)
from app.modules.purchasing.upd_sellers import seller_name_key

INN = "7701000019"
OTHER = "7702000017"
UPD = MatchUpd(
    id="u1", seller_inn=INN, seller_name='ООО "Ромашка"', supplier_id=None, date="2026-04-10", total_gross=100000,
    basis="", payment_doc="",
)
INV = MatchInvoice(
    id=1, seller_inn=INN, seller_name='ООО "Ромашка"', supplier_id=None, number="4512", date="2026-04-01", total=100000,
)


def upd(**over) -> MatchUpd:
    return replace(UPD, **over)


def inv(**over) -> MatchInvoice:
    return replace(INV, **over)


def pays(*items: tuple[str, str, int]) -> tuple[MatchPayment, ...]:
    return tuple(MatchPayment(number, day, total) for number, day, total in items)


def links(result) -> list[tuple]:
    return [(link.upd_id, link.invoice_id, link.method) for link in result.links]


def test_the_invoice_named_in_the_basis_with_its_date_links_with_another_date_only_by_a_long_number():
    assert links(match_documents([upd(basis="Счет № 4512 от 01.04.2026")], [inv()], [])) == [("u1", 1, "basis_date")]
    assert links(match_documents([upd(basis="Счет № 4512 от 02.04.2026")], [inv()], [])) == [("u1", 1, "basis")]
    # «Счет 8» recurs every year: a short number needs its date.
    assert links(match_documents([upd(basis="Счет № 8 от 02.04.2026")], [inv(number="8")], [])) == []
    # Zero padding and series do not hide the number when the date agrees.
    assert links(match_documents([upd(basis="Счет на оплату Т-000155 от 01.04.2026")], [inv(number="155")], [])) == [("u1", 1, "basis_date")]


def test_another_seller_with_the_same_invoice_number_is_never_linked():
    assert links(match_documents([upd(basis="Счет № 4512 от 01.04.2026")], [inv(seller_inn=OTHER, seller_name='ООО "Лютик"')], [])) == []


def test_our_payment_order_links_its_invoice_the_seller_may_date_it_a_few_days_later():
    invoice = inv(number="АБ-1", payments=pays(("808", "2026-04-01", 100000)))
    assert links(match_documents([upd(payment_doc="№ 808 от 01.04.2026")], [invoice], [])) == [("u1", 1, "payment")]
    assert links(match_documents([upd(payment_doc="№ 00000808 от 03.04.2026")], [invoice], [])) == [("u1", 1, "payment")]
    assert links(match_documents([upd(payment_doc="№ 808 от 09.04.2026")], [invoice], [])) == []


def test_a_payment_of_another_seller_is_a_conflict_only_on_the_exact_date_other_accounts_number_alike():
    foreign = inv(seller_inn=OTHER, seller_name='ООО "Лютик"', payments=pays(("729", "2026-08-21", 1)))
    exact = match_documents([upd(payment_doc="№ 729 от 21.08.2026")], [foreign], [])
    assert [c.code for c in exact.conflicts] == ["seller_mismatch"]
    assert exact.conflicts == [DocConflict("u1", 1)]
    assert match_documents([upd(payment_doc="№ 729 от 24.08.2026")], [foreign], []).conflicts == []


def test_an_inn_misread_by_ocr_the_same_seller_name_still_matches():
    result = match_documents(
        [upd(seller_inn="7701000091", seller_name='Общество с ограниченной ответственностью "Ромашка" (ООО "Ромашка")', basis="Счет № 4512 от 01.04.2026")],
        [inv()],
        [],
    )
    assert links(result) == [("u1", 1, "basis_date")]
    assert seller_name_key("ООО «Ромашка»") == seller_name_key('Общество с ограниченной ответственностью "Ромашка"')
    # Too short a name says nothing.
    assert seller_name_key('ООО "ПК"') == ""


def test_marketplace_the_platform_bills_a_shipping_seller_issues_the_upd_for_that_long_invoice_number():
    platform = inv(id=7, seller_inn=OTHER, seller_name='ООО "ЗЭП"', number="0215864011-0012", date="2026-06-02", total=None)
    result = match_documents([upd(basis="Счет-Оферта № 0215864011-0012 от 02.06.2026", payment_doc="№ 501 от 02.06.2026")], [platform], [])
    assert links(result) == [("u1", 7, "basis_date")]
    assert result.conflicts == []


def test_decisions_confirmed_always_links_rejected_never():
    named = upd(basis="Счет № 4512 от 01.04.2026")
    assert links(match_documents([named], [inv()], [MatchDecision("u1", 1, "rejected")])) == []
    assert links(match_documents([upd()], [inv(id=3, number="X-1")], [MatchDecision("u1", 3, "confirmed")])) == [("u1", 3, "manual")]


def test_amount_alone_is_a_suggestion_and_only_when_it_is_the_one_candidate():
    result = match_documents([upd(total_gross=251000)], [inv(total=251050, number="Z-9")], [])
    assert result.links == []
    assert result.suggestions == [DocSuggestion("u1", 1)]
    two = match_documents([upd(total_gross=251000)], [inv(total=251000), inv(id=2, total=251000, number="4513")], [])
    assert two.suggestions == []


def test_the_named_invoice_wins_over_the_days_other_payment_the_seller_lists_too():
    named = inv(id=1, number="913", total=100000, payments=pays(("913", "2026-04-01", 100000)))
    neighbour = inv(id=2, number="912", total=50000, payments=pays(("912", "2026-04-01", 50000)))
    result = match_documents([upd(basis="Счет № 913 от 01.04.2026", payment_doc="912 от 01.04.2026, 913 от 01.04.2026")], [named, neighbour], [])
    assert links(result) == [("u1", 1, "basis_date")]


def test_a_scanned_invoice_without_a_seller_is_pinned_by_a_long_number_on_the_same_day():
    scan = inv(id=5, seller_inn=None, seller_name=None, number="77639", date="2026-04-01")
    assert links(match_documents([upd(basis="Счет № 77639 от 01.04.2026")], [scan], [])) == [("u1", 5, "basis_date")]
    # A short number on a scan says nothing.
    assert links(match_documents([upd(basis="Счет № 776 от 01.04.2026")], [replace(scan, number="776")], [])) == []


def test_one_ocr_slip_between_a_letter_and_a_digit_in_a_long_number():
    assert links(match_documents([upd(basis="Счет № 80000000703 от 01.04.2026")], [inv(number="Д0000000703")], [])) == [("u1", 1, "basis_date")]
