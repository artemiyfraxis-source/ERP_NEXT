"""«XHP-4@JST» → article «XHP-4», manufacturer «JST» (owner 23.10.2026): the shared rule on every real article of the
production warehouse that carries «@» or «##» (export of 23.10.2026), and its tricky cases."""

import json
from pathlib import Path

import pytest

from app.shared.article_maker import (
    ArticleSplit, normalize_maker, split_article_maker, split_extras, with_extras,
)

FIXTURE = Path(__file__).parent / "fixtures" / "prod-maker-articles-1023.json"


def test_every_real_article_splits_as_the_snapshot_says():
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(snapshot) == 209
    for article, expected in snapshot.items():
        assert list(split_article_maker(article)) == expected, article
    makers = {expected[1] for expected in snapshot.values()}
    assert {"JST", "MOLEX", "CONNFLY", "RUICHI", "Китай", "SCONDAR"} <= makers
    assert not {"CONFLY", "Molex", "china", "RUICHI,", "CONDAR"} & makers
    # Only the ten «…@MOLEX SH12501-NY china (на тесты)» keep their «@»: the tail is not a maker.
    left = [article for article, expected in snapshot.items() if not expected[1] and "@" in article]
    assert len(left) == 10 and all("(на тесты)" in article for article in left)


@pytest.mark.parametrize(("text", "expected"), [
    ("XHP-4@JST", ("XHP-4", "JST", None, "")),
    ("0500588001 @MOLEX", ("0500588001", "MOLEX", None, "")),
    ("0039012085@Molex", ("0039012085", "MOLEX", None, "")),
    ("0510210400@МOLЕХ", ("0510210400", "MOLEX", None, "")),  # Russian М, Е, Х
    ("I-DS1071-01-SCB02x8@CONFLY", ("I-DS1071-01-SCB02x8", "CONNFLY", None, "")),
    ("XHP-13@china", ("XHP-13", "Китай", None, "")),
    ("SXH-001T-P0.6##6000@JST", ("SXH-001T-P0.6", "JST", 6000, "")),
    ("T-DS1069-SC600##6000@CONNFLY", ("T-DS1069-SC600", "CONNFLY", 6000, "")),
    ("SPH-002T-P0.5L##8000@JST упаковка 8000 шт", ("SPH-002T-P0.5L", "JST", 8000, "")),
    ("SHF-001T-0.8BS упаковка 5 000 шт.", ("SHF-001T-0.8BS", "", 5000, "")),
    ("SMA-LC174P@RUICHI, SMA-Male R/A RG174/RG316", ("SMA-LC174P", "RUICHI", None, "SMA-Male R/A RG174/RG316")),
    ("RA-5011@JST.", ("RA-5011", "JST", None, "")),
    ("0430250400 @ TE Connectivity", ("0430250400", "TE CONNECTIVITY", None, "")),
    # Not a maker: kept as written.
    ("0510210700@MOLEX SH12501-7Y china (на тесты)", ("0510210700@MOLEX SH12501-7Y china (на тесты)", "", None, "")),
    ("info@mail.ru", ("info@mail.ru", "", None, "")),
    ("XHP-4@J", ("XHP-4@J", "", None, "")),
    ("ABC@123", ("ABC@123", "", None, "")),
    ("@JST", ("@JST", "", None, "")),
    ("A@B@JST", ("A@B@JST", "", None, "")),
    ("0022552401©MOLEX", ("0022552401©MOLEX", "", None, "")),
    # No tail: trimmed only, Molex zeros and hyphens untouched.
    ("  0510210300 ", ("0510210300", "", None, "")),
    ("51021-0300", ("51021-0300", "", None, "")),
    ("", ("", "", None, "")),
    (None, ("", "", None, "")),
])
def test_split_cases(text, expected):
    assert tuple(split_article_maker(text)) == expected


def test_maker_normalisation_and_extras():
    assert normalize_maker(" molex ") == "MOLEX"
    assert normalize_maker("RUICHI,") == "RUICHI"
    assert normalize_maker("Китай") == "Китай"
    assert normalize_maker("") == ""
    split = ArticleSplit("SMA-LC174P", "RUICHI", 8000, "SMA-Male R/A")
    assert split_extras(split) == "SMA-Male R/A; упаковка 8000 шт"
    assert with_extras("", "упаковка 8000 шт") == "упаковка 8000 шт"
    assert with_extras("контакт", "упаковка 8000 шт") == "контакт; упаковка 8000 шт"
    assert with_extras("Упаковка 8000 шт, лента", "упаковка 8000 шт") == "Упаковка 8000 шт, лента"
    assert with_extras(" контакт ", "") == "контакт"
