"""EDO XML reader and upload unpacking (cases from Roller_Buyer domain/edo-xml.test.ts and test/doc-chain.test.ts).

Fixtures are synthetic documents shaped like real Diadoc files (invented companies and numbers)."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.purchasing.edo_xml import (
    LIMITS,
    EdoDoc,
    EdoHeader,
    Unsupported,
    UploadRejected,
    decode_edo_xml,
    edo_date_to_iso,
    edo_doc_ref,
    edo_document_id,
    parse_edo_xml,
    unpack_edo_upload,
)

FIXTURES = Path(__file__).parent / "fixtures" / "edo"


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


UPD = raw("upd-503.xml").decode("cp1251")
SF = raw("sf-501.xml").decode("utf-8")


def doc(result: EdoDoc | Unsupported) -> EdoDoc:
    assert isinstance(result, EdoDoc), result
    return result


def test_upd_503_in_windows_1251_header_every_basis_and_payment_order_totals_in_kopecks():
    d = doc(parse_edo_xml(UPD, "upd.xml"))
    assert d.id_file.startswith("ON_NSCHFDOPPR_2BM-5040191181-")
    assert (d.format, d.version, d.func, d.kind) == ("ON_NSCHFDOPPR", "5.03", "СЧФДОП", "УПД")
    assert d.header == EdoHeader(
        number="РЭ-0915", date="2026-09-15", seller_name='ООО "Ромашка Электро"', seller_inn="7701000019",
        seller_kpp="770101001", buyer_name='ООО "ЗЭП "ФРАКСИС"', buyer_inn="5040191181",
        basis="Счет на оплату № РЭ-777 от 08.09.2026; Договор поставки № 12/26 от 01.02.2026",
        payment_doc="№ 901 от 10.09.2026, № 915 от 12.09.2026", total_net=377905, total_vat=83139, total_gross=461044,
    )
    assert d.issues == []
    assert d.formed_at == "2026-09-15T10:15:00"


def test_upd_503_lines_customs_country_service_flag_and_no_marking_codes():
    d = doc(parse_edo_xml(UPD, "upd.xml"))
    assert len(d.lines) == 3
    first = d.lines[0]
    assert (first.seq, first.name, first.article, first.product_type_code, first.unit_code, first.unit) == (
        1, "Разъём тестовый РТ-2, 2 контакта", "RT-2@TEST", "8536690004", "796", "шт",
    )
    assert (first.quantity, first.price, first.net, first.excise, first.vat_rate, first.vat, first.gross) == (
        Decimal("100"), Decimal("12.5"), 125000, "без акциза", "22%", 27500, 152500,
    )
    assert (first.country_code, first.country, first.declaration, first.is_service) == (
        "156", "Китай", "10000000/010126/0000001", False,
    )
    wire = d.lines[1]
    assert (wire.quantity, wire.price, wire.net, wire.vat, wire.gross, wire.unit, wire.declaration) == (
        Decimal("250.5"), Decimal("8.1"), 202905, 44639, 247544, "м", "",
    )
    assert [line.is_service for line in d.lines] == [False, False, True]  # ПрТовРаб 3 — услуга
    assert "TESTMARKCODE" not in repr(d)  # КИЗ never leaves the parser


def test_invoice_501_old_attribute_names_correction_number_article_falls_back_to_artikul():
    d = doc(parse_edo_xml(SF, "sf.xml"))
    assert (d.version, d.func, d.kind) == ("5.01", "СЧФ", "Счёт-фактура")
    h = d.header
    assert (h.number, h.date, h.seller_name, h.seller_inn, h.basis, h.payment_doc) == (
        "345", "2025-03-12", 'АО "Тест-Кабель"', "5001000027", "Счет № 77 от 03.03.2025", "№ 210 от 05.03.2025",
    )
    assert (h.total_net, h.total_vat, h.total_gross) == (550000, 110000, 660000)
    assert d.issues == ["исправленный документ: исправление № 1 от 14.03.2025"]
    cable, work = d.lines
    assert (cable.article, cable.unit, cable.country, cable.country_code, cable.declaration, cable.is_service) == (
        "KT-2x075", "м", "Беларусь", "112", "10000000/020225/0000002", False,
    )
    assert (work.article, work.unit_code, work.unit, work.excise, work.is_service) == ("00-0007", "0000", "-", "0.00", True)


def test_sole_proprietor_without_vat_name_from_fio_delivery_without_kind_is_a_service():
    d = doc(parse_edo_xml(raw("upd-ip.xml").decode("utf-8"), "ip.xml"))
    assert (d.func, d.kind) == ("ДОП", "УПД")
    h = d.header
    assert (h.seller_name, h.seller_inn, h.seller_kpp, h.basis, h.payment_doc) == (
        "ИП Тестовый Семён Семёнович", "500100000400", "", "Без документа-основания", "",
    )
    assert (h.total_net, h.total_vat, h.total_gross) == (75000, 0, 75000)
    assert [(line.article, line.vat_rate, line.vat, line.gross, line.is_service) for line in d.lines] == [
        ("СТ-200", "без НДС", 0, 45000, False),
        ("", "без НДС", 0, 30000, True),
    ]
    assert d.issues == []


def test_correction_invoice_header_only_totals_are_the_signed_difference():
    d = doc(parse_edo_xml(raw("ukd-501.xml").decode("utf-8"), "ukd.xml"))
    assert (d.format, d.func, d.kind, len(d.lines)) == ("ON_NKORSCHFDOPPR", "КСЧФДИС", "Корректировочный СФ", 0)
    assert d.header == EdoHeader(
        number="К-12", date="2026-10-01", seller_name='ООО "Северный Контакт"', seller_inn="7811000036",
        seller_kpp="781101001", buyer_name='ООО "ЗЭП "ФРАКСИС"', buyer_inn="5040191181",
        basis="Счёт-фактура № 4410 от 18.09.2026", payment_doc="", total_net=-100000, total_vat=-22000,
        total_gross=-122000,
    )
    assert d.issues == []


def test_advance_invoices_are_refused_they_do_not_close_an_invoice():
    advance = Unsupported("авансовый счёт-фактура (на предоплату) — не закрывающий документ")
    assert parse_edo_xml(SF.replace('Значен="Реализация"', 'Значен="НаАванс"'), "a.xml") == advance
    calc_rate = SF.replace('Значен="Реализация"', 'Значен=""').replace('НалСт="20%"', 'НалСт="20/120"')
    assert parse_edo_xml(calc_rate, "a.xml") == advance
    # The same rates in a shipping document are not an advance.
    assert doc(parse_edo_xml(calc_rate.replace('Функция="СЧФ"', 'Функция="СЧФДОП"'), "a.xml")).kind == "УПД"


def test_numbers_that_do_not_add_up_or_do_not_parse_become_issues_not_guesses():
    off = doc(parse_edo_xml(UPD.replace('СтТовУчНалВсего="4610.44"', 'СтТовУчНалВсего="4610.45"'), "u.xml"))
    assert off.issues == ["сумма строк 4610.44 ≠ итог документа 4610.45"]
    broken = doc(parse_edo_xml(UPD.replace('КолТов="250.5"', 'КолТов="25о.5"'), "u.xml"))
    assert broken.lines[1].quantity is None
    assert broken.issues == ["строка 2: не число в «КолТов»: 25о.5"]
    usd = doc(parse_edo_xml(UPD.replace('КодОКВ="643"', 'КодОКВ="840"'), "u.xml"))
    assert usd.issues == ["валюта документа — код 840, суммы не в рублях"]
    odd = doc(parse_edo_xml(
        UPD.replace('Функция="СЧФДОП"', 'Функция="СвЗК"').replace('ДатаДок="15.09.2026"', 'ДатаДок="31.09.2026"'), "u.xml",
    ))
    assert (odd.kind, odd.header.date) == ("УПД", None)
    assert odd.issues == ["функция документа «СвЗК» не распознана — считаю УПД", "дата документа не распознана: 31.09.2026"]


def test_other_files_get_a_clear_refusal():
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
            '<Файл ИдФайл="ON_NSCHFDOPPR_1">&lol2;</Файл>')
    assert parse_edo_xml(bomb, "bomb.xml") == Unsupported("XML с DOCTYPE не принимается")
    cut = parse_edo_xml(UPD[:2000], "cut.xml")
    assert isinstance(cut, Unsupported) and cut.reason.startswith("XML повреждён"), cut
    card = '<?xml version="1.0"?><Card xmlns="http://api-invoice.taxcom.ru/card"><Description Title="Товарная накладная"/></Card>'
    assert parse_edo_xml(card, "card.xml") == Unsupported("не документ ЭДО в формате ФНС")

    def other(prefix: str):
        return parse_edo_xml(f'<Файл ИдФайл="{prefix}_2BM-1_2BM-2_20260101_x" ВерсФорм="5.01"><Документ/></Файл>', "x.xml")

    assert other("DP_REZRUISP") == Unsupported("формат DP_REZRUISP (акт) пока не поддерживается")
    assert other("ON_NSCHFDOPPOK") == Unsupported("ON_NSCHFDOPPOK — ответ покупателя, данные берутся из документа продавца")
    assert other("DP_IZVPOL") == Unsupported("DP_IZVPOL — служебный файл ЭДО, не документ")
    no_id = UPD.replace(UPD[UPD.index('ИдФайл="'):UPD.index('" ВерсФорм') + 1], "")
    assert parse_edo_xml(no_id, "ON_NSCHFDOPPR_x.xml") == Unsupported("в файле нет ИдФайл — не документ ЭДО")


def test_references_no_repeated_number_or_date_and_a_trailing_number_sign_is_respected():
    assert edo_doc_ref("Счет", "2/1190", "26.06.2026") == "Счет № 2/1190 от 26.06.2026"
    assert edo_doc_ref("По счету 4226 от 26.06.2026", "4226", "26.06.2026") == "По счету 4226 от 26.06.2026"
    assert edo_doc_ref("Акцепт счета-оферты по Договору № ТС-1 Счет-оферта №", "123456Z", "02.07.2026") == (
        "Акцепт счета-оферты по Договору № ТС-1 Счет-оферта № 123456Z от 02.07.2026"
    )
    assert edo_doc_ref("Счет №53/23-17.08 от 22.04.2026", "Счет №53/23-17.08", "22.04.2026") == "Счет №53/23-17.08 от 22.04.2026"
    assert edo_doc_ref("Счет 15", "1", "") == "Счет 15 № 1"  # a number inside another number does not count
    assert edo_doc_ref("", "587", "03.07.2026") == "№ 587 от 03.07.2026"
    assert edo_doc_ref("", "", "") == ""
    assert edo_date_to_iso("29.02.2028") == "2028-02-29"
    assert edo_date_to_iso("29.02.2026") is None
    assert edo_date_to_iso("2026-02-01") is None


def test_document_id_is_short_stable_and_url_safe():
    d = doc(parse_edo_xml(UPD, "upd.xml"))
    key = edo_document_id(d.id_file)
    assert key == edo_document_id(d.id_file) and key.startswith("edo-") and len(key) == 36
    assert all(char.isalnum() or char == "-" for char in key)


def test_encoding_follows_the_bom_or_the_declaration_and_falls_back_to_windows_1251():
    assert decode_edo_xml(raw("upd-503.xml")) == UPD
    assert decode_edo_xml("﻿<a>ё</a>".encode("utf-8")) == "<a>ё</a>"
    assert decode_edo_xml('<?xml version="1.0" encoding="utf-8"?><a>ё</a>'.encode("cp1251")).endswith("<a>ё</a>")
    assert decode_edo_xml('<?xml version="1.0" encoding="x-unknown"?><a>ё</a>'.encode("cp1251")).endswith("<a>ё</a>")


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_diadoc_zip_gives_only_the_documents():
    archive = _zip({
        "docs/ON_NSCHFDOPPR_test.xml": raw("upd-503.xml"),
        "docs/ON_NSCHFDOPPR_test.xml.sig": b"\x01\x02\x03",
        "card.xml": b"<card/>",
        "Счет.pdf": b"%PDF",
    })
    found = unpack_edo_upload("Diadoc.Documents.zip", archive)
    assert [item.name for item in found] == ["ON_NSCHFDOPPR_test.xml"]
    assert found[0].content == raw("upd-503.xml") and found[0].xml == UPD


def test_uploads_that_are_not_edo_are_refused_in_words():
    with pytest.raises(UploadRejected, match="нужен XML или ZIP из ЭДО"):
        unpack_edo_upload("photo.png", b"\x89PNG")
    with pytest.raises(UploadRejected, match="нет XML-документов ЭДО"):
        unpack_edo_upload("empty.zip", _zip({"readme.txt": b"x"}))
    with pytest.raises(UploadRejected, match="не читается"):
        unpack_edo_upload("broken.zip", b"PK\x03\x04garbage")


def test_zip_bombs_are_stopped_by_declared_sizes_and_entry_count(monkeypatch):
    monkeypatch.setitem(LIMITS, "entries", 3)
    with pytest.raises(UploadRejected, match="больше 500 файлов"):
        unpack_edo_upload("many.zip", _zip({f"ON_NSCHFDOPPR_{n}.xml": b"<a/>" for n in range(4)}))
    monkeypatch.setitem(LIMITS, "entries", 500)
    monkeypatch.setitem(LIMITS, "unzipped_bytes", 1000)
    # 2 000 zero bytes compress to a few dozen: the declared size, not the archive's, decides.
    with pytest.raises(UploadRejected, match="распаковывается больше"):
        unpack_edo_upload("bomb.zip", _zip({"ON_NSCHFDOPPR_bomb.xml": b"0" * 2000}))
    monkeypatch.setitem(LIMITS, "unzipped_bytes", 100 * 1024 * 1024)
    monkeypatch.setitem(LIMITS, "xml_bytes", 100)
    with pytest.raises(UploadRejected, match="в архиве «big.zip» больше 20 МБ"):
        unpack_edo_upload("big.zip", _zip({"ON_NSCHFDOPPR_big.xml": b"0" * 200}))
    with pytest.raises(UploadRejected, match="больше 20 МБ"):
        unpack_edo_upload("big.xml", b"0" * 200)


def test_a_lying_size_header_cannot_inflate_more_than_it_declares():
    archive = bytearray(_zip({"ON_NSCHFDOPPR_lie.xml": b"<a>" + b"0" * 5000 + b"</a>"}))
    # Shrink the declared size in the central directory (offset 24 of the record) and the local header (22).
    central = archive.rindex(b"PK\x01\x02")
    archive[central + 24:central + 28] = (10).to_bytes(4, "little")
    archive[22:26] = (10).to_bytes(4, "little")
    with pytest.raises(UploadRejected):
        unpack_edo_upload("lie.zip", bytes(archive))
