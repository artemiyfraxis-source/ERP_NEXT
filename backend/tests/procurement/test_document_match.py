"""Строки КП / счёта → строки заказа: артикул точно, затем слова названия, количество — при равенстве."""

from decimal import Decimal

import pytest

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


@pytest.mark.parametrize("spelling", ["0510210400", "051 021 0400", "051021–0400"])
@pytest.mark.parametrize("in_name", [False, True])
def test_molex_comparison_matches_article_column_and_name_tokens(spelling, in_name):
    item = DocumentItem(name=f"Корпус {spelling}" if in_name else "Корпус", article="" if in_name else spelling)
    assert result([item], [line("a", "Разъём", article="51021-0400")]) == ([("a", 0, "article")], [], [])


def test_explicit_molex_maker_confirms_other_numeric_articles():
    items = [DocumentItem(name="Molex", article="0901430010")]
    assert result(items, [line("a", "Molex", article="90143-0010")]) == ([("a", 0, "article")], [], [])


@pytest.mark.parametrize("item_name, order_name", [("Molex", "Корпус"), ("Корпус", "Molex")])
@pytest.mark.parametrize("item_article, order_article", [("0901430010", "90143-0010"), ("90143-0010", "0901430010")])
def test_one_sided_molex_identity_confirms_numeric_equivalence(item_name, order_name, item_article, order_article):
    items = [DocumentItem(name=item_name, article=item_article)]
    assert result(items, [line("a", order_name, article=order_article)]) == ([("a", 0, "article")], [], [])


@pytest.mark.parametrize("article, name", [
    ("0012345678", "Корпус"), ("0510210400-LF", "Корпус"),
    ("051021/0400", "Корпус"), ("051021.0400", "Корпус"),
    ("0510210400", "JST"), ("0510210400", "Molex JST"), ("0510210400", "RUICHI"),
])
def test_unknown_suffix_punctuation_and_conflicting_makers_do_not_fold(article, name):
    target = "12345678" if article == "0012345678" else "51021-0400"
    order_name = "Корпус" if article == "0012345678" else "Molex"
    assert result([DocumentItem(name=name, article=article)], [line("a", order_name, article=target)]) == ([], [0], ["a"])


def test_canonical_collisions_are_not_distinct_article_matches():
    items = [DocumentItem(name="Позиция", article="0510210400")]
    lines = [line("a", "Первая", article="51021-0400"), line("b", "Вторая", article="0510210400")]
    assert result(items, lines) == ([], [0], ["a", "b"])


@pytest.mark.parametrize("item_name, order_name", [("AMP", "TE"), ("TE AMP", "TE AMP")])
def test_maker_labels_preserve_legacy_exact_te_matching(item_name, order_name):
    items = [DocumentItem(name=item_name, article="174047-2")]
    assert result(items, [line("a", order_name, article="174047-2")]) == ([("a", 0, "article")], [], [])


@pytest.mark.parametrize("maker", ["JST", "RUICHI"])
def test_other_maker_on_unpadded_side_vetoes_new_equivalence(maker):
    items = [DocumentItem(name="Корпус", article="0510210400")]
    assert result(items, [line("a", maker, article="51021-0400")]) == ([], [0], ["a"])


@pytest.mark.parametrize("item_name, order_name", [("Molex", "Корпус"), ("Корпус", "Molex")])
def test_one_sided_maker_label_preserves_exact_unknown_family_article(item_name, order_name):
    items = [DocumentItem(name=item_name, article="0901430010")]
    assert result(items, [line("a", order_name, article="0901430010")]) == ([("a", 0, "article")], [], [])


@pytest.mark.parametrize("item_name", ["Неизвестная позиция", "Molex"])
def test_mixed_maker_evidence_does_not_make_a_duplicate_article_unique(item_name):
    items = [DocumentItem(name=item_name, article="0901430010")]
    lines = [line("a", "Molex", article="0901430010"), line("b", "Корпус", article="0901430010")]
    matches, _, _ = result(items, lines)
    assert all(confidence != "article" for _, _, confidence in matches)


@pytest.mark.parametrize("item_article", ["0901430010", "90143-0010"])
@pytest.mark.parametrize("item_name, first_name", [("Molex", "Корпус"), ("Неизвестная позиция", "Molex")])
def test_confirmed_molex_aliases_remain_ambiguous_across_order_rows(item_article, item_name, first_name):
    items = [DocumentItem(name=item_name, article=item_article)]
    lines = [line("a", first_name, article="0901430010"), line("b", "Корпус", article="90143-0010")]
    matches, _, _ = result(items, lines)
    assert all(confidence != "article" for _, _, confidence in matches)


@pytest.mark.parametrize("malformed", ["51021/0400", "51021.0400", "51021_0400"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("order_name", ["Другое", "Позиция"])
def test_new_molex_equivalence_requires_numeric_identity_on_both_sides(malformed, reverse, order_name):
    item_article, order_article = (malformed, "0510210400") if reverse else ("0510210400", malformed)
    # A decimal-looking item article is not an is_code token; identical names
    # still receive the legacy name-only match, never an article match.
    expected = ([("a", 0, "name")], [], []) if malformed == "51021.0400" and reverse and order_name == "Позиция" else ([], [0], ["a"])
    assert result([DocumentItem(name="Позиция", article=item_article)], [
        line("a", order_name, article=order_article),
    ]) == expected


@pytest.mark.parametrize("article", ["51021/0400", "51021.0400", "51021_0400"])
@pytest.mark.parametrize("prefix", ["", "0"])
@pytest.mark.parametrize("reverse", [False, True])
def test_legacy_punctuation_equivalence_still_works(article, prefix, reverse):
    item_article, order_article = prefix + article, prefix + "51021-0400"
    if reverse:
        item_article, order_article = order_article, item_article
    assert result([DocumentItem(name="Позиция", article=item_article)], [
        line("a", "Другое", article=order_article),
    ]) == ([("a", 0, "article")], [], [])
