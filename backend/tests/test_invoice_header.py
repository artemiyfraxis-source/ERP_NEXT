"""Roller_Buyer `invoice-parse.test.ts`, one to one (fragments of real Kaiten invoices 2025–2026, text layer as pdf.js
returns it). The payment-order file names are tested with ERP's own `purchasing.doc_refs`."""

from app.shared.invoice_header import (
    InvoiceHeader, amount_in_words, parse_invoice_text, parse_invoice_title, ru_date_to_iso, words_to_number,
)

SNAB = 'ЧУВАШСКОЕ ОТДЕЛЕНИЕ N8613 ПАО СБЕРБАНК г. Чебоксары БИК 049706609 Сч. № 30101810300000000609 Банк получателя ИНН 2128037304 КПП 213001001 Сч. № 40702810675000002225 ООО "СнабКабель" Получатель Счет на оплату № 43851 от 15 сентября 2025 г. Поставщик (Исполнитель): ООО "СнабКабель", ИНН 2128037304, КПП 213001001, 428003, Чебоксары Покупатель (Заказчик): ООО "ЗЭП "ФРАКСИС", ИНН 5040191181 Итого: 4 815,00 В том числе НДС 20%: 802,50 Всего к оплате: 4 815,00 Всего наименований 3, на сумму 4 815,00 руб. Четыре тысячи восемьсот пятнадцать рублей 00 копеек Оплатить не позднее 18.09.2025'  # noqa: E501
MPO = 'Продавец : ЗАО " МПО Электромонтаж ", ИНН 7719079370, КПП 773301001 1. Продавец обязуется передать СЧЕТ - ДОГОВОР № 57148683 от 29.01.2026 ( Бнал ) Покупатель : ООО "ЗЭП "ФРАКСИС" ИНН 5040191181 Всего: Сорок шесть тысяч шестьсот одиннадцать рублей 15 копеек'  # noqa: E501
CHIP = 'г. Москва, Варшавское шоссе, 268, стр.1 16406334 Счет-договор № Поставщик: ИНН/КПП Р/с: Покупатель: АО "ЧИП и ДИП" 7729108750 / 770201001 40702810738090119924 ПАО СБЕРБАНК ООО "ЗЭП "ФРАКСИС" 5040191181 / 504001001 Всего за товар: Сумма НДС (20%): Итого включая налоги: 1 583.33 316.67 1 900.00 К оплате: Одна тысяча девятьсот рублей 00 копеек'  # noqa: E501
ELITAN = 'Компания Элитан(ТМ) - с 1999 года Продавец: ООО "Элитан Трейд" 426028, Удмуртская Республика ИНН 1831096455 Счет № 684803Z от 02.10.2025 Итого подлежит оплате: 2 057,94 руб., в том числе НДС 342,99 руб.'  # noqa: E501
KROKS = 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "КРОКС ПЛЮС" 394005, Воронеж, Московский пр-кт, дом 133 ИНН 3662192701 Счет на оплату № 0312251655 от 18.12.2025 Сто пятьдесят восемь тысяч рублей 00 копеек'  # noqa: E501
IP = 'Оплату необходимо произвести до 10 октября 2025 г. Получатель Банк получателя ИНН 500408815010 Сч. № ИП ЗАЛАЗНАЯ ЕЛЕНА ВЛАДИМИРОВНА Счет на оплату № 44 от 03.10.25 Одна тысяча девятьсот пятьдесят рублей 00 копеек'  # noqa: E501


def test_1c_invoice_seller_block_number_date_amount_in_words_vat():
    assert parse_invoice_text(SNAB) == InvoiceHeader(
        number="43851", date="2025-09-15", seller_name='ООО "СнабКабель"', seller_inn="2128037304", total=481500,
        vat=80250, vat_free=False,
    )


def test_other_layouts_seller_after_label_table_inn_kpp_full_legal_form_entrepreneur():
    mpo = parse_invoice_text(MPO)
    assert [mpo.seller_name, mpo.seller_inn, mpo.number, mpo.date, mpo.total] == [
        'ЗАО "МПО Электромонтаж"', "7719079370", "57148683", "2026-01-29", 4661115,
    ]
    chip = parse_invoice_text(CHIP)
    assert [chip.seller_name, chip.seller_inn, chip.total] == ['АО "ЧИП и ДИП"', "7729108750", 190000]
    elitan = parse_invoice_text(ELITAN)
    assert [elitan.seller_name, elitan.total, elitan.vat] == ['ООО "Элитан Трейд"', 205794, 34299]
    kroks = parse_invoice_text(KROKS)
    assert [kroks.seller_name, kroks.total] == ['ООО "КРОКС ПЛЮС"', 15800000]
    ip = parse_invoice_text(IP)
    assert [ip.seller_inn, ip.seller_name, ip.date] == ["500408815010", "ИП ЗАЛАЗНАЯ ЕЛЕНА ВЛАДИМИРОВНА", "2025-10-03"]


def test_amounts_in_words_dates_titles():
    assert words_to_number(["Двадцать", "шесть", "тысяч", "триста", "тридцать"]) == 26330
    assert words_to_number(["Один", "миллион", "двести", "тысяч"]) == 1_200_000
    assert amount_in_words("Итого Две тысячи пятьдесят семь рублей 94 коп.") == 205794
    assert amount_in_words("без суммы прописью") is None
    # The VAT spelled after the total does not replace it.
    assert amount_in_words(
        "Всего к оплате: Шесть тысяч семьсот рублей 00 копеек. В т.ч. НДС: Одна тысяча сто шестнадцать рублей 66 копеек"
    ) == 670000
    # The address after the name is cut off.
    assert parse_invoice_text(
        'Поставщик: ООО "ДАРТ ХОЛДИНГ", 111024, г. Москва, ул. Авиамоторная, ИНН 7722734553 Одна тысяча рублей 00 копеек'
    ).seller_name == 'ООО "ДАРТ ХОЛДИНГ"'
    assert ru_date_to_iso("03.08.2026 г.") == "2026-08-03", "four-digit year is not read as «20»"
    assert ru_date_to_iso("17 сентября 2025") == "2025-09-17"
    assert parse_invoice_title("Счет ПП25-062156 от 19.11.2025") == ("ПП25-062156", "2025-11-19")
    assert parse_invoice_title("Счет-оферта 696419Z от 31.10.2025") == ("696419Z", "2025-10-31")
    assert parse_invoice_title("SMA-RG178 R/A") == (None, None)


def test_vat_free_and_our_own_inn_is_never_the_seller():
    text = (
        'Счет на оплату № 7 от 01.10.2026 Поставщик: ООО "ЗЭП "ФРАКСИС", ИНН 5040191181 '
        'Продавец ИП Иванов Пётр Сергеевич ИНН 771234567890 Без налога (НДС) Пятьсот рублей 00 копеек'
    )
    header = parse_invoice_text(text)
    assert (header.seller_inn, header.seller_name) == ("771234567890", "ИП Иванов Пётр Сергеевич")
    assert (header.total, header.vat, header.vat_free) == (50000, 0, True)
