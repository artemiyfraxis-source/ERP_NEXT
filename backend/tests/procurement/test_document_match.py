"""Строки КП / счёта → строки заказа: артикул точно, затем слова названия, количество — при равенстве."""

from decimal import Decimal

from app.modules.procurement.document_items import DocumentItem, parse_document_text
from app.modules.procurement.document_match import OrderLineCandidate, code_key, match_document_items
from tests.procurement.test_document_items import ELITAN_INVOICE, KP_NET


def line(key, *names, article=None, quantity=None):
    return OrderLineCandidate(key=key, article=article, names=names, quantity=Decimal(quantity) if quantity else None)


def result(items, lines):
    matched, free_items, free_lines = match_document_items(items, lines)
    return [(m.key, m.item_index, m.confidence) for m in matched], free_items, free_lines


def test_part_numbers_match_exactly_after_spaces_dashes_and_case():
    items = parse_document_text(ELITAN_INVOICE).items
    lines = [
        line("contact", "Контакт", article="sshl-002t-p0.2"),
        line("molex", "Разъём", article="510471000"),
        line("housing", "Корпус 2 конт.", article="SHR 02V-S-B"),
        line("other", "Клемма WAGO", article="221-413"),
    ]

    assert result(items, lines) == (
        [("contact", 2, "article"), ("molex", 1, "article"), ("housing", 0, "article")], [], ["other"],
    )


def test_a_close_but_different_part_number_does_not_match():
    items = [DocumentItem(name="Корпус разъёма SHR-03V-S-B (JST)", quantity=Decimal("200"))]

    assert result(items, [line("a", "Корпус разъёма SHR-02V-S-B", article="SHR-02V-S-B")]) == ([], [0], ["a"])


def test_cyrillic_look_alikes_in_a_typed_article_still_match():
    assert code_key("СР-1А") == code_key("CP-1A")
    items = [DocumentItem(name="Предохранитель CP-1A")]

    assert result(items, [line("a", "Предохранитель", article="СР-1А")]) == ([("a", 0, "article")], [], [])


def test_names_tell_colours_apart_and_each_line_takes_one_row():
    items = parse_document_text(KP_NET, "kp").items
    lines = [
        line("black", "Провод ПуГВ 1х0,75 черный"),
        line("red", "Провод ПуГВ 1х0,75 красный"),
        line("tube", "Термоусадка 6/3 черная"),
    ]

    matched, free_items, free_lines = result(items, lines)

    assert matched[:2] == [("black", 1, "name"), ("red", 0, "name")]
    assert matched[2] == ("tube", 2, "name")
    assert (free_items, free_lines) == ([], [])


def test_quantity_breaks_a_tie_between_identical_names():
    items = [
        DocumentItem(name="Хомут нейлоновый 2,5х100", quantity=Decimal("300")),
        DocumentItem(name="Хомут нейлоновый 2,5х100", quantity=Decimal("100")),
    ]
    lines = [line("small", "Хомут нейлоновый 2,5х100", quantity="100"), line("big", "Хомут нейлоновый 2,5х100", quantity="300")]

    assert result(items, lines)[0] == [("small", 1, "name"), ("big", 0, "name")]


def test_a_part_number_two_lines_share_is_not_an_article_match():
    items = [DocumentItem(name="Кабель UNITRONIC LiYY 4x0,25 серый", quantity=Decimal("50"))]
    lines = [
        line("grey", "Кабель UNITRONIC LiYY 4x0,25 серый"),
        line("black", "Кабель UNITRONIC LiYY 4x0,25 черный"),
    ]

    assert result(items, lines)[0] == [("grey", 0, "name")]


def test_unrelated_rows_stay_unmatched():
    items = [DocumentItem(name="Доставка до терминала ТК")]

    assert result(items, [line("a", "Разъём SMA")]) == ([], [0], ["a"])


def test_a_few_shared_words_are_a_weak_match():
    items = [DocumentItem(name="Гнездо питания DC-005 на плату")]

    assert result(items, [line("a", "Разъём питания DC")]) == ([("a", 0, "weak")], [], [])
