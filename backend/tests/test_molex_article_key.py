"""The comparison policy is additive and never changes display spelling."""

import pytest

from app.shared.article_key import article_key, is_molex_article


@pytest.mark.parametrize("article", ["0510210400", "51021-0400", "510210400", " 051 021 0400 ", "51021–0400", "51021\u00a00400"])
def test_known_family_spellings(article):
    assert article_key(article) == "510210400"
    assert is_molex_article(article)


@pytest.mark.parametrize("context", [{"manufacturer": "Molex"}, {"manufacturer": "МOLЕХ"}, {"catalog_molex": True}])
def test_confirmed_identity_allows_other_numeric_series(context):
    assert article_key("001234-5678", **context) == article_key("12345678", **context) == "12345678"


@pytest.mark.parametrize("article", ["0012345678", "1234-5678", "12345678", "１２３４５", "abc-01"])
def test_unknown_or_non_ascii_numeric_is_not_inferred(article):
    assert article_key(article) == article
    assert not is_molex_article(article)


@pytest.mark.parametrize("article", ["0510210400-LF", "51021-0400(N)", "0510210400 (N)", "0510210400.1", "51021/0400"])
def test_suffix_and_other_punctuation_remain_distinct(article):
    assert article_key(article, manufacturer="Molex") == article
    assert article_key(article, manufacturer="Molex") != article_key("510210400", manufacturer="Molex")


@pytest.mark.parametrize("article", ["0510210400", "51021-0400", "0012345678"])
def test_other_manufacturer_vetoes_family_and_catalogue(article):
    assert article_key(article, manufacturer="JST", catalog_molex=True) == article
    assert not is_molex_article(article, manufacturer="JST", catalog_molex=True)


@pytest.mark.parametrize("article", [None, "", "  ", "0", "0000000000", "00-00", "00 00", 0])
def test_empty_and_zero_are_not_identities(article):
    assert article_key(article) == ""
    assert article_key(article, manufacturer="Molex") == ""


def test_display_and_manufacturer_arguments_are_not_modified():
    article, maker = "051021-0400", "Molex"
    assert article_key(article, manufacturer=maker) == "510210400"
    assert (article, maker) == ("051021-0400", "Molex")


@pytest.mark.parametrize("article", [
    "105307-1204", "200277-1104", "206461-0400", "172708-0010", "172256-1104",
    "2053410204", "5055650600", "2145260600", "87439-0400", "22-01-2041",
    "09-50-8051", "0511630400", "50-57-9404", "501646-1000", "35023-0004",
])
def test_other_known_numeric_catalogue_families(article):
    key = article.replace("-", "").lstrip("0")
    assert article_key(article) == article_key("0" + key) == key
    assert article_key(article, manufacturer="JST") == article


def test_family_prefix_alone_is_not_confirmation():
    assert not is_molex_article("5102112345")
    assert not is_molex_article("43645-0409")
