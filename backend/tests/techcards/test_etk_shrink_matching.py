"""Conservative material identity: never trade size, colour or explicit maker for a temperature."""

from copy import deepcopy

import pytest

from app.modules.techcards.etk_heat_shrink import match_shrink_material


def row(**fields):
    return {"_reference_id": "tube", "Наименование": "ТУТ(HF) 6/3 черная", "Серия": "ТУТ(HF) (83017)",
            "Производитель": "КВТ Калуга", "Группа": "ТУТ", "Клеевой слой": "нет",
            "Размер до, мм": "6", "Размер после, мм": "3", "Цвет": "чёрный", **fields}


def match(text, *rows, article=""):
    return match_shrink_material({"article": article, "name": text}, tuple(rows))


def default_row(**fields):
    return row(**{"Наименование": "ТУТнг-LS 6/3 черная", "Серия": "ТУТнг-LS", **fields})


@pytest.mark.parametrize("text", [
    "Трубка 83017", "КВТ ТУТ (HF)-6/3 чёрная", "ТУТ-HF-6/3 черная КВТ",
    "ТУТ (HF) 6,0/3,0 черн. КВТ Калуга",
    "Трубка серии ТУТ (HF) 6/3 черная КВТ",
])
def test_number_or_series_identifies_the_catalogue_row(text):
    method, found = match(text, row())
    assert method in {"catalogue", "series"} and found == [row()]


@pytest.mark.parametrize("text", [
    "ТУТ 6/3мм, черная", "ТУТ 6,0 / 3,0 мм, чёрная", "ТУТ 6/3", "ТУТ 6/3, черная",
])
def test_plain_description_uses_the_default(text):
    assert match(text, row(), default_row()) == ("default", [default_row()])


@pytest.mark.parametrize("series", ["ТТ-150", "ТНТ", "ТУТ(HF)"])
def test_generic_black_tube_never_substitutes_another_series(series):
    assert not match("ТУТ 6/3мм черная", row(**{"Серия": series}))[1]


def test_transparent_default_selects_hf_only():
    hf = row(**{"Цвет": "прозрачный"})
    ls = default_row(**{"Цвет": "прозрачный"})
    assert match("ТУТ 6/3мм прозрачная", ls, hf) == ("default", [hf])


@pytest.mark.parametrize("text", [
    "ТУТ 6/3 Rexant", "ТУТ 6/3 ДругойЗавод", "ТУТ (HF) 6/3 черная",
    "КВТ ТУТ 6/3 черная", "ТУТ XYZ-6/3 черная", "ТУТ 6/2 черная", "ТТК 6/3 черная",
    "ТУТ 6/3 красная", "ТУТ 6/3 черная белая", "ТУТ 6/3 и 8/4 черная", "", "ТУТ",
    "ТУТ 6/3 артикул A83017B", "ТУТ 6/3 1830179", "ТУТ 6/3 20-83017",
])
def test_unknown_or_conflicting_identity_is_not_defaulted(text):
    assert not match(text, row())[1]


@pytest.mark.parametrize("text", [
    "Трубка 83017 ТУТ(HF) 4/2 черная КВТ", "Трубка 83017 ТУТ(HF) 6/3 белая КВТ",
    "Трубка 83017 ТУТнг-LS 6/3 черная КВТ", "Трубка 83017 ТТК 6/3 черная КВТ",
    "ТУТ(HF) 6/3 розовая КВТ", "ТУТ(HF) 6/3 с клеем КВТ",
])
def test_catalogue_number_does_not_override_other_identifying_attributes(text):
    assert not match(text, row())[1]


def test_number_and_explicit_other_maker_never_become_kvt():
    other = row(**{"Производитель": "Rexant", "Наименование": "Другая трубка"})
    assert not match("Трубка 83017 Rexant", row(), other)[1]


def test_article_and_name_must_agree_on_size_and_colour():
    assert not match("КВТ ТУТ(HF) 6/3 черная", row(), article="ТУТ 4/2мм, черная")[1]
    assert not match("КВТ ТУТ(HF) 6/3 черная", row(), article="ТУТ 6/3мм, белая")[1]
    assert not match("КВТ ТУТ(HF) 6/3 черная", row(), article="ТТК 6/3мм, черная")[1]


def test_absent_colour_requires_only_one_available_colour():
    white = row(**{"Цвет": "белый", "Наименование": "ТУТ(HF) 6/3 белая"})
    unknown = row(**{"Цвет": "", "Наименование": "ТУТ(HF) 6/3"})
    assert not match("ТУТ 6/3", row(), white)[1]
    assert not match("КВТ ТУТ(HF) 6/3", row(), white)[1]
    assert not match("ТУТ 6/3", unknown)[1]
    default_white = default_row(**{"Цвет": "белый"})
    assert match("ТУТ 6/3мм белая", default_row(), default_white)[1] == [default_white]


@pytest.mark.parametrize("description,colour", [("синяя", "синий"), ("жёлто-зелёная", "жёлто-зелёный"),
                                                ("прозр.", "прозрачный"), ("серая", "серый")])
def test_colour_inflections_are_not_different_colours(description, colour):
    tube = (row if colour == "прозрачный" else default_row)(**{"Цвет": colour})
    assert match(f"ТУТ 6/3мм, {description}", tube)[1] == [tube]


@pytest.mark.parametrize("size,before,after", [("4,8/1,6", "4.8", "1.6"), ("1,0/0,5", "1", "0.5")])
def test_decimal_sizes_are_compared_numerically(size, before, after):
    family = "ТТК" if size.startswith("4") else "ТУТ"
    tube = row(**{"Серия": "ТТК(3:1)" if family == "ТТК" else "ТУТнг-LS",
                  "Группа": family, "Клеевой слой": "да" if family == "ТТК" else "нет",
                  "Размер до, мм": before, "Размер после, мм": after})
    assert match(f"{family} {size}мм, черная", tube) == ("default", [tube])


def test_glue_word_identifies_adhesive_family_without_a_series():
    tube = row(**{"Серия": "ТТК(3:1)", "Размер после, мм": "2", "Группа": "ТТК", "Клеевой слой": "да"})
    assert match("Трубка термоусадочная клеевая 6/2 черная", tube)[1] == [tube]
    assert not match("Трубка термоусадочная клеевая 6/2 черная", row())[1]


@pytest.mark.parametrize("size,series", [("8/2", "ТТК(4:1)"), ("12/4", "ТТК(3:1)")])
def test_adhesive_default_series_follows_the_size_ratio(size, series):
    before, after = size.split("/")
    tube = row(**{"Серия": series, "Размер до, мм": before, "Размер после, мм": after,
                  "Группа": "ТТК", "Клеевой слой": "да"})
    assert match(f"ТТК {size}мм черная", tube) == ("default", [tube])


@pytest.mark.parametrize("size,series", [("6/2", "ТТК(4:1)"), ("8/2", "ТТК(3:1)"), ("6/3", "ТТК(2:1)"),
                                       ("8/3", "ТТК(3:1)"), ("8/2", "СТТК (79826)"),
                                       ("6/2", "ТТК2(3:1)"), ("6/2", 'ТТК "Flex"')])
def test_adhesive_default_never_changes_ratio_or_series(size, series):
    before, after = size.split("/")
    tube = row(**{"Серия": series, "Размер до, мм": before, "Размер после, мм": after,
                  "Группа": "ТТК", "Клеевой слой": "да"})
    assert not match(f"ТТК {size}мм черная", tube)[1]


def test_exact_identity_wins_even_for_other_makers():
    other = row(**{"Артикул": "SKU", "Производитель": "Rexant"})
    assert match("ТУТ 6/3мм, черная", row(), other, article="SKU") == ("exact", [other])


def test_matching_is_stable_and_does_not_mutate_rows():
    other = row(**{"_reference_id": "other", "Серия": "ТУТнг-LS"})
    rows = [row(), other]
    before = deepcopy(rows)
    assert match("ТУТ 6/3мм, черная", *rows) == match("ТУТ 6/3мм, черная", *reversed(rows))
    assert rows == before
