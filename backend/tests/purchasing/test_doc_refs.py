"""References in a UPD's free text and payment orders of a Kaiten card (Roller_Buyer domain/doc-refs.test.ts)."""

from app.modules.purchasing.doc_refs import (
    PaymentOrder,
    PaymentRef,
    invoice_payments,
    loose_doc_number,
    money_to_kop,
    norm_doc_number,
    parse_basis_refs,
    parse_payment_file_name,
    parse_payment_refs,
    ru_date_to_iso,
)


def refs(text: str) -> list[tuple]:
    return [(r.kind, r.number, r.date) for r in parse_basis_refs(text)]


def test_basis_invoices_orders_and_contracts_as_upds_write_them():
    assert refs("Счет № 77639 от 26.10.2025") == [("invoice", "77639", "2025-10-26")]
    assert refs("Счет-договор № 15956237 от 29.09.2025") == [("invoice", "15956237", "2025-09-29")]
    assert refs("Заказ клиента № АУ-4496 от 7 октября 2025 г.") == [("order", "АУ-4496", "2025-10-07")]
    assert refs("Счет 25100024 от 20.07.2025, Заказ 08968582 от 20.07.2025") == [
        ("invoice", "25100024", "2025-07-20"),
        ("order", "08968582", "2025-07-20"),
    ]
    assert refs("Договор поставки №55 от 04.02.2021") == [("contract", "55", "2021-02-04")]
    assert refs("СЧЕТ №MC-000198215 ОТ 25.03.2026 г.") == [("invoice", "MC-000198215", "2026-03-25")]
    assert refs("Счет №26003913 от 21.04.26") == [("invoice", "26003913", "2026-04-21")]
    assert refs("Заказ покупателя REM-00843 от 17.10.2025 15:01:17") == [("order", "REM-00843", "2025-10-17")]


def test_basis_the_edo_rendering_and_elitan_offers_keep_the_contract_apart():
    assert refs("Счет на оплату № РЭ-777 от 08.09.2026; Договор поставки № 12/26 от 01.02.2026") == [
        ("invoice", "РЭ-777", "2026-09-08"),
        ("contract", "12/26", "2026-02-01"),
    ]
    assert refs("Акцепт счёта-оферты в рамках Договора поставки № ЭТ-2025-МАРТ Счёт-оферта № 762861Z от 24.04.2026") == [
        ("contract", "ЭТ-2025-МАРТ", None),
        ("invoice", "762861Z", "2026-04-24"),
    ]


def test_basis_a_bare_number_with_a_date_is_of_unknown_kind_words_alone_are_none():
    assert refs("327 от 15.05.2025") == [("other", "327", "2025-05-15")]
    assert refs("Основной договор") == []
    assert refs("Счет-договор") == []
    assert refs("") == []


def test_payment_orders_ocr_slips_of_ot_included():
    assert parse_payment_refs("Пл. пор. № 1036 от 27.10.2025") == [PaymentRef("1036", "2025-10-27")]
    assert parse_payment_refs("330 от 03.06.2025, 331 от 03.06.2025, 621 от 09.07.2025") == [
        PaymentRef("330", "2025-06-03"), PaymentRef("331", "2025-06-03"), PaymentRef("621", "2025-07-09"),
    ]
    # The invoice named next to the payment is not a payment.
    assert parse_payment_refs("№ 930 от 30.09.2025 (Счет-договор № 15956237)") == [PaymentRef("930", "2025-09-30")]
    assert parse_payment_refs("№ 3650m21.07.2025") == [PaymentRef("365", "2025-07-21")]
    assert parse_payment_refs("ПП №282 от 16.05.2025") == [PaymentRef("282", "2025-05-16")]
    assert parse_payment_refs("№ 1268 от 16.12.2025г") == [PaymentRef("1268", "2025-12-16")]
    assert parse_payment_refs("№ 406 от 20/04/26") == [PaymentRef("406", "2026-04-20")]
    assert parse_payment_refs("") == []


def test_ocr_mangled_ot_glued_or_latin():
    assert parse_payment_refs("147OM13.03.2026") == [PaymentRef("147", "2026-03-13")]
    assert parse_payment_refs("910 or 25.09.2025") == [PaymentRef("910", "2025-09-25")]


def test_document_number_keys_strong_folds_look_alikes_and_separators_loose_keeps_significant_digits():
    assert norm_doc_number("АНС/2337") == norm_doc_number("АНС 2337")
    # Latin K in one, Cyrillic К in the other.
    assert norm_doc_number("K47285201") == norm_doc_number("К47285201")
    assert norm_doc_number("№ УТ-1714") == "УТ1714"
    assert norm_doc_number("0VT/34") == norm_doc_number("OVT/34")
    assert loose_doc_number("Т-000155") == "155"
    assert loose_doc_number("ЭР03086279") == "3086279"
    assert loose_doc_number("Б/Н") == ""


def test_payment_orders_of_an_invoice_card_come_from_the_file_names_a_copy_is_one_payment():
    names = [
        "Платежное_поручение_№880_от_16.09.2025_на_сумму_4815.pdf",
        "Платежное_поручение_№880_от_16.09.2025_на_сумму_4815-1.pdf",
        "Счет № 5694 от 11.12.2025.pdf",
    ]
    assert invoice_payments(names) == [PaymentOrder("880", "2025-09-16", 481500)]
    assert parse_payment_file_name("Платёжное поручение №12 от 20_11_2025 на сумму 1 500,50.pdf") == PaymentOrder("12", "2025-11-20", 150050)
    assert parse_payment_file_name("Счет.pdf") is None


def test_dates_and_money_as_invoices_print_them():
    assert ru_date_to_iso("15 сентября 2025 г.") == "2025-09-15"
    assert ru_date_to_iso("03.08.2026") == "2026-08-03"
    assert ru_date_to_iso("15.09.25") == "2025-09-15"
    assert ru_date_to_iso("вчера") is None
    assert money_to_kop("4 815,00") == 481500
    assert money_to_kop("4815.5") == 481550
    assert money_to_kop("12,345") is None
