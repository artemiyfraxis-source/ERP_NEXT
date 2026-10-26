"""Roller_Buyer `invoice-lines.test.ts`, one to one: pages of real invoices as word coordinates."""

import re

from app.shared.invoice_lines import InvoiceLine, PdfWord, parse_invoice_lines, parse_invoice_table


def page(rows: str) -> list[PdfWord]:
    """A page from «y: x:text | x:text~width» lines, as the scratch dumps of real invoices print them."""
    words = []
    for row in rows.strip().split("\n"):
        y = float(row[:row.index(":")])
        for cell in row[row.index(":") + 2:].split(" | "):
            x = float(cell[:cell.index(":")])
            text, width = re.fullmatch(r"(.*?)(?:~(\d+))?", cell[cell.index(":") + 1:]).groups()
            words.append(PdfWord(text, x, y, float(width) if width else None))
    return words


def line(seq, name, article, qty, unit, price, total):
    return InvoiceLine(seq=seq, name=name, article=article, qty=qty, unit=unit, price=price, sum=total)


def test_numbers_centred_in_tall_rows_seller_code_left_of_the_name_vseinstrumenty():
    lines = parse_invoice_lines([page("""
407: 42:№ | 72:Код | 129:Товары (работы, услуги) | 283:Статус | 391:Кол-во | 432:Ед. | 469:Цена | 522:Сумма
402: 338:поставки
390: 108:Сервис Ключ Набор шайб
380: 44:1 | 61:16072095 | 108:уплотнительных 279 предметов | 277:в наличии | 338:в наличии | 419:2 | 434:шт | 478:571,00 | 529:1 142,00
370: 108:73279
359: 108:VENTION Кабель-удлинитель USB
349: 44:2 | 61:25069257 | 108:3.0 AM/AF - 1.5м Black Edition | 277:в наличии | 338:в наличии | 419:1 | 434:шт | 478:621,00 | 536:621,00
339: 108:VAS-A45-B150
322: 414:* Включая НДС: 317,92 ₽""")])
    assert lines == [
        line(1, "Сервис Ключ Набор шайб уплотнительных 279 предметов 73279", "16072095", 2, "шт", 57100, 114200),
        line(2, "VENTION Кабель-удлинитель USB 3.0 AM/AF - 1.5м Black Edition VAS-A45-B150", "25069257", 1, "шт", 62100, 62100),
    ]


def test_discount_column_three_line_header_and_delivery_on_the_next_page_mpo():
    header = """
299: 277:Цена | 302:( | 305:руб | 318:) | 344:Скид | 360:- | 445:НДС | 509:Сумма
291: 16:№ | 76:Наименование | 191:Ед | 201:. | 218:Кол | 232:- | 235:во | 349:ка | 379:Сумма | 419:став | 434:- | 451:Сумма | 511:с | 517:НДС
283: 254:без | 269:скидки | 299:со | 310:скидкой | 348:% | 383:( | 385:руб | 398:) | 420:ка | 428:% | 455:( | 457:руб | 470:) | 513:( | 516:руб | 529:)"""
    lines = parse_invoice_lines([
        page(f"""{header}
266: 17:1 | 29:Т | 34:2920. | 57:Трубка | 84:20-2001 2/1 | 128:мм | 193:м | 226:75 | 276:10.66 | 321:10.20 | 355:4 | 391:765.00 | 424:20 | 464:153.00 | 527:918.00
253: 29:термоусаживаемая | 100:белая | 122:L=1 | 137:м | 145:(Rexant)"""),
        page(f"""{header}
266: 17:2 | 29:Доставка | 64:товара | 196:- | 231:- | 271:415.83 | 317:415.83 | 355:0 | 391:415.83 | 424:20 | 469:83.17 | 527:499.00
253: 29:Адрес | 53:доставки | 84:: | 91:Москва
239: 311:Итого | 342:: | 383:1 180.83 | 464:236.17 | 519:1 417.00"""),
    ])
    assert lines == [
        line(1, "Т 2920. Трубка 20-2001 2/1 мм термоусаживаемая белая L=1 м (Rexant)", "", 75, "м", 1020, 76500),
        # No quantity printed: the price and the sum come from the columns row 1 showed, net like it.
        line(2, "Доставка товара Адрес доставки : Москва", "", 1, "", 41583, 41583),
    ]


def test_unnumbered_rows_sum_printed_elsewhere_note_across_the_table_elitan():
    lines = parse_invoice_lines([page("""
322: 31:Товары и услуги~60 | 343:УАМ~18 | 371:Срок~18 | 405:Кол-во~28 | 445:Цена, руб.~40 | 510:Сумма, руб.~45
308: 31:Электроэлемент XT60H-M@AMASS~110 | 348:Нет~14 | 375:21 рд~20 | 422:3828~18 | 485:32,48~20 | 537:см. ниже~30
294: 31:Цены и суммы в таблице рассчитаны без налога НДС (20%), налог подлежит оплате дополнительно к ним.~480
280: 31:Всего наименований 1, на общую сумму с НДС: Сто сорок девять тысяч сто восемьдесят восемь рублей 10 коп.~500""")])
    assert lines == [line(1, "Электроэлемент XT60H-M@AMASS", "", 3828, "", 3248, 12433344)]


def test_hash_for_numbers_article_column_service_with_quantity_0_invoice_contains_ends_it_komus():
    lines = parse_invoice_lines([page("""
611: 449:Цена с НДС | 504:Сумма с НДС
607: 46:# | 69:Артикул | 202:Название товара | 366:Ед. изм. | 409:Кол-во
602: 464:РУБ | 521:РУБ
587: 46:1 | 69:1398452 | 113:Термоэтикетки ТОП 100х50, 200 шт/рул, вт. 25 | 374:рул | 417:30 | 478:87,00 | 525:2610,00
572: 46:2 | 74:@009 | 113:Услуга по доставке | 379:- | 420:0 | 473:490,00 | 530:490,00
557: 201:Счет содержит: Два наименования | 525:3100,00""")])
    assert lines == [
        line(1, "Термоэтикетки ТОП 100х50, 200 шт/рул, вт. 25", "1398452", 30, "рул", 8700, 261000),
        line(2, "Услуга по доставке", "@009", 1, "", 49000, 49000),
    ]


def test_row_number_glued_to_the_name_in_one_piece_of_text_promelektronika():
    lines = parse_invoice_lines([page("""
564: 26:№ | 194:Ед. | 284:Условия | 469:Сумма | 531:Всего с
558: 83:Наименование | 231:Кол-во | 349:НДС | 409:НДС
553: 26:№ | 192:изм. | 282:поставки | 461:НДС (руб.) | 525:НДС (руб.)
548: 346:(руб.) | 407:(руб.)
524: 37:1 5-1814832-2 TE Connectivity | 187:шт | 256:225 | 289:склад | 361:97,52 | 408:21 942,00 | 478:4 388,40 | 535:26 330,40
513: 456:Итого | 531:21 942,00""")])
    assert lines == [line(1, "5-1814832-2 TE Connectivity", "", 225, "шт", 9752, 2194200)]


def test_no_table_header_no_positions():
    assert parse_invoice_lines([page("500: 31:Счёт № 5 от 01.10.2026\n480: 31:Итого: | 400:1 000,00")]) == []


def test_the_header_words_come_with_the_table():
    table = parse_invoice_table([page("""
611: 449:Цена с НДС | 504:Сумма с НДС
607: 46:# | 69:Артикул | 202:Название товара | 366:Ед. изм. | 409:Кол-во
587: 46:1 | 69:1398452 | 113:Термоэтикетки | 374:рул | 417:30 | 478:87,00 | 525:2610,00""")])
    assert " ".join(word.s for word in table.header) == "Цена с НДС Сумма с НДС # Артикул Название товара Ед. изм. Кол-во"
