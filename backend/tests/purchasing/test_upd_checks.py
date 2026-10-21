"""Pure rules of the UPD registry: arithmetic and document checks, status, whose document, number key, twins
(cases from Roller_Buyer domain/upd.test.ts, domain/upd-buyer.test.ts and test/doc-chain.test.ts «twin choice»)."""

from __future__ import annotations

from decimal import Decimal

from app.modules.purchasing.upd_checks import (
    LineFacts,
    classify_buyers,
    doc_issues,
    line_issues,
    norm_doc_number,
    registry_status,
    vat_rate,
)
from app.modules.purchasing.upd_twins import TwinFacts, compute_twins

OWN = frozenset({"5040191181"})


def line(**over) -> LineFacts:
    values = {"quantity": Decimal(1), "price": Decimal("409.84"), "net": 40_984, "vat_rate": "22", "vat": 9_016,
              "gross": 50_000}
    values.update(over)
    return LineFacts(**values)


def test_vat_rate_reads_percent_words_and_fractions():
    assert vat_rate("22") == 22 and vat_rate("22%") == 22 and vat_rate("без НДС") == 0
    assert vat_rate("0.2") == 20 and vat_rate("") is None and vat_rate("нет") is None


def test_line_arithmetic_with_kopeck_tolerance():
    assert line_issues(line()) == []
    assert line_issues(line(quantity=Decimal(10), price=Decimal("77.87"), net=77_869, vat=17_131, gross=95_000)) == []
    assert line_issues(line(gross=60_000)) == ["без НДС + НДС ≠ с НДС"]
    assert line_issues(line(quantity=Decimal(2))) == ["кол-во × цена ≠ без НДС"]
    assert line_issues(line(vat=5_000, gross=45_984)) == ["НДС не равен ставке 22%"]
    assert line_issues(line(vat_rate="без НДС", vat=None, gross=40_984)) == []


def test_document_issues_required_fields_inn_totals():
    header = {"number": "2937", "date": "2026-04-21", "seller_name": "Дарт", "seller_inn": "7707083893",
              "total_gross": 50_000}
    assert doc_issues("УПД", header, [line()]) == []
    assert doc_issues("УПД", {**header, "seller_inn": "971038484"}, [line()]) == [
        "ИНН продавца не проходит проверку: 971038484",
    ]
    assert doc_issues("УПД", {**header, "total_gross": 60_000}, [line()]) == ["сумма строк 500 ≠ итог документа 600"]
    assert doc_issues("УПД", {**header, "number": ""}, []) == ["нет ни одной позиции", "нет номера документа"]
    no_money = line(price=None, net=None, vat=None, gross=None)
    assert doc_issues("М-15", {**header, "seller_inn": ""}, [no_money]) == []
    assert doc_issues("УПД", header, [line(gross=60_000)]) == [
        "сумма строк 600 ≠ итог документа 500", "строк с замечаниями: 1",
    ]


def test_status_follows_the_parser_and_the_buyers_review():
    assert registry_status("Разобран", None, None) == "parsed"
    assert registry_status("В очереди", None, None) == "waiting"
    assert registry_status("Требует проверки", None, None) == "needs_review"
    assert registry_status("Ошибка", None, None) == "needs_review"
    assert registry_status("Требует проверки", "2026-09-24T10:00", None) == "verified"
    assert registry_status("Дубликат", "2026-09-24T10:00", None) == "duplicate"
    assert registry_status("Не документ закупки", None, None) == "not_purchase"
    # The same document twice (PDF and EDO XML): the copy is a duplicate, verified or not.
    assert registry_status("Разобран", "2026-09-24T10:00", "edo-x") == "duplicate"


def test_document_number_key_folds_signs_separators_and_look_alikes():
    assert norm_doc_number("АНС/2337") == norm_doc_number("АНС 2337")
    assert norm_doc_number("K47285201") == norm_doc_number("К47285201")
    assert norm_doc_number("0VT/34") == norm_doc_number("OVT/34")
    assert norm_doc_number("№ 12-5") == "125" and norm_doc_number("No.77") == "77"
    assert norm_doc_number("TP-1284") != norm_doc_number("ТР-1277")


def kinds(docs):
    return [(verdict.kind, verdict.inn) for verdict in classify_buyers(docs, OWN)]


def doc(buyer: str, seller: str = "7707083893") -> tuple[str, str]:
    return buyer, seller


def test_our_inn_also_read_with_a_slip_by_ocr():
    assert kinds([doc("5040191181"), doc("5040191161"), doc("504019118"), doc("5010191181")]) == [("ours", "5040191181")] * 4
    assert "с ошибкой: 5040191161" in classify_buyers([doc("5040191161")], OWN)[0].note


def test_parties_swapped_by_ocr_our_company_as_the_seller_is_our_document():
    verdict = classify_buyers([doc("25371807371", "5040191181")], OWN)[0]
    assert (verdict.kind, verdict.inn) == ("ours", "5040191181") and "перепутаны" in verdict.note
    # Even when the seller INN itself has an extra digit.
    assert classify_buyers([doc("772421669330", "50400191181")], OWN)[0].kind == "ours"


def test_another_organisation_misread_copies_join_the_inn_the_registry_really_has():
    docs = [doc("7726465440"), doc("7726465440"), doc("7726165440"), doc("7726165410"), doc("2127006737"), doc(""),
            doc("7726165110")]
    assert kinds(docs) == [
        ("other", "7726465440"), ("other", "7726465440"), ("other", "7726465440"), ("other", "7726465440"),
        ("other", "2127006737"), ("unknown", None),
        # Checksum fails, three digits off: still the organisation the registry has.
        ("other", "7726465440"),
    ]


def test_an_inn_with_the_kpp_glued_on_is_read_without_it():
    assert kinds([doc("7726465440"), doc("7726465440"), doc("772646440772401001")])[-1] == ("other", "7726465440")
    assert kinds([doc("5040191181504001001")]) == [("ours", "5040191181")]


def twin(id: str, origin: str, gross: int | None, parser_updated: str = "", **over) -> TwinFacts:
    values = {"id": id, "origin": origin, "status": "Разобран", "kind": "УПД", "number": "5", "date": "2026-09-01",
              "seller_inn": "7701000019", "total_gross": gross, "parser_updated": parser_updated}
    values.update(over)
    return TwinFacts(**values)


def test_twin_choice_received_copy_stays_edo_over_pdf_later_resend_wins_different_sums_are_two_documents():
    assert compute_twins([twin("pdf", "google", 100), twin("xml", "edo", 100)], {"pdf"}) == {"xml": "pdf"}
    assert compute_twins([twin("pdf", "google", 100), twin("xml", "edo", 100)]) == {"pdf": "xml"}
    resent = [twin("x1", "edo", 100, "2026-09-01T10:00:00"), twin("x2", "edo", 100, "2026-09-02T10:00:00")]
    assert compute_twins(resent) == {"x1": "x2"}
    assert compute_twins([twin("x1", "edo", 100), twin("x2", "edo", 200)]) == {}


def test_twins_group_by_the_corrected_inn_and_prefer_the_checked_copy():
    rows = [
        twin("a", "google", 1473000, status="Требует проверки"),
        twin("b", "google", 1473000, seller_inn="3862192701", seller_org_inn="7701000019"),
        twin("c", "google", 1473000, checked=True, status="Требует проверки"),
    ]
    assert compute_twins(rows) == {"a": "c", "b": "c"}


def test_an_act_and_a_delivery_note_under_one_number_with_different_sums_are_two_documents():
    act = twin("act", "google", 81_300, kind="Акт", number="26002614")
    note = twin("tn", "google", 1_927_900, kind="ТН + СФ", number="26002614")
    assert compute_twins([act, note]) == {}
    # Same kind, a slightly different reading of the sum: still one document read twice.
    assert compute_twins([twin("p1", "google", 100_000), twin("p2", "google", 100_050)]) == {"p2": "p1"}


def test_a_separate_invoice_pdf_beside_the_delivery_note_is_a_twin_another_day_is_not():
    seller = {"seller_inn": "4029060031", "date": "2025-07-04"}
    rows = [
        twin("nv-sf", "google", 137800, kind="Счёт-фактура", number="TP-1284", **seller),
        twin("nv-tn", "google", 137800, kind="ТН + СФ", number="ТР-1277", **seller),
        twin("nv-sf2", "google", 137800, kind="Счёт-фактура", number="TP-1300", seller_inn="4029060031", date="2025-07-10"),
    ]
    assert compute_twins(rows) == {"nv-sf": "nv-tn"}


def test_corrections_m15_other_kinds_and_parser_duplicates_never_twin_closing_documents():
    assert compute_twins([twin("u", "google", 100), twin("k", "edo", 100, kind="Корректировочный СФ")]) == {}
    assert compute_twins([twin("u", "google", 100), twin("m", "google", 100, kind="М-15")]) == {}
    assert compute_twins([twin("o1", "google", 100, kind="Прочее"), twin("o2", "google", 100, kind="Прочее")]) == {}
    assert compute_twins([twin("u", "google", 100), twin("d", "google", 100, status="Дубликат")]) == {}
