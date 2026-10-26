"""Item rows of a paper УПД read by OCR (owner 23.10.2026, УПД № 1788 of 20.10.2026: «не считал артикул из
наименования, не считал кол-во в наименовании и не умножил на кол-во бухт»). The fixture is the item part of that
scan's tesseract text, prices and customs numbers scrambled."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.warehouse.upd_lines import (
    UpdLine, find_pack, item_line, line_answer, name_article, parse_upd_lines, quantity_of, snap_articles, snap_key,
    unit_of,
)

ROWS = (Path(__file__).parent / "fixtures" / "upd-1788-ocr-rows.txt").read_text(encoding="utf-8")


def test_the_real_scan_gives_six_lines_with_their_articles_tape_in_pieces():
    lines = parse_upd_lines(ROWS)
    assert [(line.article, line_answer(line)["quantity"], line.unit) for line in lines] == [
        ("МТ-2К 6,4/3,2 белая", "300", "м"),
        ("МТ-2К 4,8/2,4 белая", "300", "м"),
        ("МТ-2К 3,2/1,6 белая", "600", "м"),
        ("МТ-2К 2,4/1,2 белая", "600", "м"),
        ("Тр-I090EW", "2", "шт"),  # owner 26.10: tape stays in rolls / шт, «9мм*16м» is a size, not a pack
        ("Тр-I120EW", "2", "шт"),
    ]
    first = line_answer(lines[0])
    assert first == {
        "article": "МТ-2К 6,4/3,2 белая",
        "name": "Маркировочная трубка термоусаживаемая МТ-2К 6,4/3,2мм белая (в рулоне 100 м)",
        "upd_name": "Маркировочная трубка термоусаживаемая МТ-2К 6,4/3,2мм белая (в рулоне 100 м)",
        "item_type": "", "manufacturer": "",  # tesseract rows: no model, our name is the printed one
        "quantity": "300", "unit": "м", "invoice_quantity": "3", "invoice_unit": "шт",  # as invoiced: 3 rolls «шт»
        "packs": "3", "pack": "100 м", "pack_kind": "рул.",
        "supplier_code": "00-00042353", "source": "скан УПД",
    }
    assert [line.supplier_code for line in lines] == [
        "00-00042353", "00-00042352", "00-00042427", "00-00024027", "00-00026162", "00-00026163"]
    assert lines[3].name == "Маркировочная трубка термоусаживаемая МТ-2К 2,4/1,2 мм белая (в рулоне 200 м)"  # «- i акциза» gone
    assert lines[5].name == "Тр-I120EW Самоклеющаяся лента, 12мм*16м, белая"  # the customs number and «акциза 070» gone
    assert (lines[4].packs, lines[4].pack, lines[4].invoice_quantity, lines[4].invoice_unit) == (None, "", Decimal(2), "шт")
    assert [line.number for line in lines] == ["1", "2", "3", "4", "5", "6"]


def test_rows_stop_at_the_footer_and_take_a_form_without_the_supplier_code():
    text = "\n".join([
        "1 |Наконечник НШВИ 0,5-8 (упаковка 100 шт) 796 |шт 2,000 10,00 20,00 без НДС",
        "2 Кабель UTP 4x2x0,5 (в бухте 305 м) 006 м 305,000 50,00 15 250,00",
        "Всего к оплате 15 270,00",
        "Товар (груз) передал",
    ])
    lines = parse_upd_lines(text)
    assert [(line.quantity, line.unit, line.packs, line.supplier_code) for line in lines] == [
        (Decimal(200), "шт", Decimal(2), ""),
        (Decimal("305.000"), "м", Decimal(1), ""),  # already in metres: not multiplied, one coil
    ]
    assert lines[0].name == "Наконечник НШВИ 0,5-8 (упаковка 100 шт)"


def test_no_item_rows_in_a_header_only_text():
    assert parse_upd_lines("Универсальный передаточный документ № 5 от 01.10.2026\nПродавец: ООО Ромашка") == []
    assert parse_upd_lines("") == []


@pytest.mark.parametrize(("name", "size", "unit", "kind"), [
    ("Трубка (в рулоне 100 м)", "100", "м", "рул."),
    ("Кабель КСПВ 4х0,5 в бухте 305 м", "305", "м", "бух."),
    ("Провод МГТФ 0,12 (рулон 50 м)", "50", "м", "рул."),
    ("Лента 9мм*16м, белая", "16", "м", "рул."),
    ("Лента 12мм x 16м", "16", "м", "рул."),
    ("Лента 9мм16м, белая", "16", "м", "рул."),  # the «*» lost by OCR
    ("Наконечник НШВИ упаковка 6000 шт", "6000", "шт", "уп."),
    ("Стяжка 2,5х100 уп. 100 шт", "100", "шт", "уп."),
    ("Хомут (100 шт/уп)", "100", "шт", "уп."),
    ("SXH-001T-P0.6##6000", "6000", "шт", "уп."),
])
def test_each_pack_pattern(name, size, unit, kind):
    pack = find_pack(name)
    assert pack is not None and (pack.size, pack.unit, pack.kind) == (Decimal(size), unit, kind)


def test_no_pack_keeps_the_invoiced_quantity():
    lines = parse_upd_lines("1 |Разъём XHP-4 796 |шт 25,000 3,00 75,00")
    assert (lines[0].article, lines[0].quantity, lines[0].unit, lines[0].packs, lines[0].pack) == (
        "XHP-4", Decimal(25), "шт", None, "")
    assert line_answer(lines[0])["packs"] == ""


def test_pieces_of_a_pack_in_pieces_multiply_only_below_one_pack():
    two = parse_upd_lines("1 |Наконечник SXH-001T-P0.6 упаковка 6000 шт 796 шт 2,000 1,00 2,00")[0]
    many = parse_upd_lines("1 |Стяжка 2,5х100 уп. 100 шт 796 шт 150,000 1,00 150,00")[0]
    assert (two.quantity, two.packs) == (Decimal(12000), Decimal(2))
    assert (many.quantity, many.packs) == (Decimal(150), None)
    packs = parse_upd_lines("1 |Стяжка 2,5х100 (уп. 100 шт) 778 упак 3,000 1,00 3,00")[0]
    assert (packs.quantity, packs.unit, packs.packs) == (Decimal(300), "шт", Decimal(3))


@pytest.mark.parametrize(("name", "article"), [
    ("Тр-1090Е\\М/ Самоклеющаяся лента, 9мм*16м, белая", "Тр-I090EW"),  # W read as «\М/», I as 1 (width 9 мм)
    ("Тр-1120Е\\\\/ Самоклеющаяся лента, 12мм*16м", "Тр-I120EW"),  # W read as «\\/»
    ("Тр-1090ЕVV лента 9мм", "Тр-I090EW"),
    ("Разъём XHP-1O2 корпус", "XHP-102"),  # O between digits
    ("Корпус SHR-1l02 белый", "SHR-1102"),  # l between digits
    ("Маркировочная трубка МТ-2К 3,2/1,6 мм белая (в рулоне 200 м)", "МТ-2К 3,2/1,6 белая"),
    ("Термоусадка ТУТнг-LS 4/2 черная", "ТУТнг-LS 4/2 черная"),
    ("Лента ЛСЕ-1 12мм*16м", "ЛСЕ-1 12"),  # the width stays, the roll length is the pack
    ("Контакт 0430250600 Molex", "0430250600"),
    ("Провод монтажный", ""),
])
def test_article_from_the_name_with_ocr_fixes(name, article):
    assert name_article(name)[0] == article


def test_a_genuine_leading_one_stays_when_no_width_spells_it():
    assert name_article("Тр-1090EW лента 12мм")[0] == "Тр-1090EW"


def test_the_name_carries_the_fixed_code():
    assert name_article("Тр-1090Е\\М/ Самоклеющаяся лента, 9мм*16м")[1] == "Тр-I090EW Самоклеющаяся лента, 9мм*16м"


def _line(article: str, colour: str = "") -> UpdLine:
    return UpdLine(article=article, name="", quantity=Decimal(1), unit="шт", invoice_quantity=Decimal(1),
                   invoice_unit="шт", colour=colour)


def test_snap_takes_the_known_spelling_by_key_first_source_wins():
    lines = [_line("Тр-1090EW"), _line("МТ-2К 6,4/3,2 белая", "белая"), _line("XHP-4"), _line("ZZZ-1")]
    known = [
        ("TP-I090EW", "партии склада"),  # Latin look-alikes, I for 1 — the same key
        ("Тр-I090EW", "номенклатуры закупок"),
        ("MT-2K 6,4/3,2", "справочника"),  # without the colour
        ("xhp-4", "номенклатуры закупок"),
    ]
    snapped = snap_articles(lines, known)
    assert [(line.article, line.source) for line in snapped] == [
        ("TP-I090EW", "скан УПД, артикул из партии склада"),
        ("MT-2K 6,4/3,2", "скан УПД, артикул из справочника"),
        ("xhp-4", "скан УПД, артикул из номенклатуры закупок"),
        ("ZZZ-1", "скан УПД"),
    ]
    assert snap_articles(lines, []) == lines
    assert snap_key("SHR-O2V") == snap_key("SHR-02V")  # O next to a digit is a zero
    assert snap_key("ТВ-40 4 белая") != snap_key("ТВ-40 4 бе1ая")  # a word keeps its letters


def test_a_pack_said_in_words_multiplies_a_size_form_does_not():
    """Owner 26.10: «в рулоне 100 м» × 3 рулона = 300 м; «Тр-I090EW … 9мм*16м» 2 шт stays 2 шт."""
    roll = item_line("Трубка МТ-2К 6,4/3,2мм белая (в рулоне 100 м)", Decimal(3), "шт")
    assert (roll.quantity, roll.unit, roll.packs, roll.pack, roll.pack_kind) == (Decimal(300), "м", Decimal(3), "100 м",
                                                                               "рул.")
    tape = item_line("Тр-1090EW Самоклеющаяся лента, 9мм*16м, белая", Decimal(2), "шт", supplier_code="00-26162")
    assert (tape.article, tape.quantity, tape.unit, tape.packs, tape.pack) == ("Тр-I090EW", Decimal(2), "шт", None, "")
    assert tape.supplier_code == "00-26162"
    coil = item_line("Кабель UTP 4x2x0,5 в бухте 305 м", Decimal(305), "м")
    assert (coil.quantity, coil.unit, coil.packs) == (Decimal(305), "м", Decimal(1))
    hashes = item_line("SXH-001T-P0.6##6000", Decimal(2), "шт")
    assert (hashes.quantity, hashes.unit) == (Decimal(12000), "шт")
    unread = item_line("Разъём XHP-4", None, "шт")
    assert (unread.quantity, line_answer(unread)["quantity"]) == (None, "")


@pytest.mark.parametrize(("text", "unit"), [
    ("шт", "шт"), ("шт.", "шт"), ("м", "м"), ("упак", "упак"), ("796", "шт"), ("006", "м"), ("", "шт"),
    ("компл.", "компл"), ("бобина", "бобина"),
])
def test_the_unit_a_model_read(text, unit):
    assert unit_of(text) == unit


@pytest.mark.parametrize(("text", "value"), [
    ("3", Decimal(3)), ("3.000", Decimal("3.000")), ("1 000,000", Decimal("1000.000")), ("", None), ("—", None),
    ("0", None),
])
def test_the_quantity_a_model_read(text, value):
    assert quantity_of(text) == value


def test_packs_of_a_name_that_counts_pieces_multiply_only_in_packs():
    # 26.10.2026, УПД ВсеИнструменты № 3789518: 7 упак «…, 100 шт» — Алексей принял 700 шт
    name = "Vell Наконечник штыревой втулочный, НШВ 6,0-12, 100 шт (VLnhv-6.0-12(100)) 1396474"
    packs = item_line(name, Decimal(7), "упак")
    assert (packs.quantity, packs.unit, packs.packs, packs.invoice_quantity, packs.invoice_unit) == (
        Decimal(700), "шт", Decimal(7), Decimal(7), "упак")
    pieces = item_line(name, Decimal(700), "шт")
    assert (pieces.quantity, pieces.unit, pieces.packs) == (Decimal(700), "шт", None)
    one = item_line("Набор отвёрток, 1 шт", Decimal(2), "упак")
    assert (one.quantity, one.unit) == (Decimal(2), "упак")
