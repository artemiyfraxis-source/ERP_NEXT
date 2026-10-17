"""МАССА 1 ШТ, Г (SK-6): the expected values are what SUPPLYMAP massPerUnit_ / massFieldsFrom_ return in JS."""

from decimal import Decimal

import pytest

from app.shared.unit_mass import mass_fields_from, mass_per_unit, parse_number


@pytest.mark.parametrize(("fields", "stored"), [
    (("5", "", ""), "5.00000"),
    (("", "12,5", ""), "1.25000"),  # ten pieces, divided by 10; a comma is the decimal point
    (("", "", "0,123"), "0.00123"),  # a hundred pieces, divided by 100
    (("5", "40", "123"), "1.23000"),  # several filled: the largest sample wins
    (("", "2", "abc"), "0.20000"),  # not a number is skipped, the next sample is taken
    (("3", "", "0"), "3.00000"),  # zero is skipped
    (("3", "", "-1"), "3.00000"),  # negative is skipped
    (("", "", "0.0012345"), "0.00001"),  # 5 decimals
    (("", "", "1.23456789"), "0.01235"),
    (("", "", "0.0025"), "0.00003"),  # a tie rounds up (JS Math.round), not to even
    (("0.000025", "", ""), "0.00003"),
    (("0.000035", "", ""), "0.00003"),  # binary 3.4999… stays down, as in JS
    (("12г", "", ""), "12.00000"),  # parseFloat takes the leading number
    ((" 1 234,5 ", "", ""), "1234.50000"),  # whitespace (thousands) is dropped
    (("1,2,3", "", ""), "1.20000"),  # only the first comma is a decimal point
    ((0.5, None, None), "0.50000"),  # a number as well as a typed string
    (("", "", "0.0000001"), "0.00000"),  # rounds to zero: returned as zero, as SUPPLYMAP does
])
def test_mass_per_unit_follows_supplymap(fields, stored):
    assert mass_per_unit(*fields) == Decimal(stored)
    assert str(mass_per_unit(*fields)) == stored


@pytest.mark.parametrize("fields", [
    ("", "", ""), (None, None, None), ("abc", "-", "0"), ("-5", "0,0", " "), ("1e400", "", ""), ("Infinity", "", ""),
])
def test_nothing_usable_gives_no_mass(fields):
    assert mass_per_unit(*fields) is None


def test_a_mass_beyond_the_column_is_refused():
    with pytest.raises(ValueError, match="Масса 1 шт слишком большая"):
        mass_per_unit("1e13")
    assert mass_per_unit("", "", "9999999999999") == Decimal("99999999999.99000")


def test_parse_number_is_icr_num():
    assert parse_number("  0,5 г") == 0.5
    assert parse_number(".5") == 0.5
    assert parse_number("1e") == 1
    assert parse_number(True) != parse_number(True)  # NaN, as String(true) in JS


@pytest.mark.parametrize(("mass", "fields"), [
    (Decimal("0.00123"), {"mass_1": "", "mass_10": "", "mass_100": "0.123"}),
    ("0.001234", {"mass_1": "", "mass_10": "", "mass_100": "0.123"}),
    ("0.0099999", {"mass_1": "", "mass_10": "", "mass_100": "1"}),
    ("0.01", {"mass_1": "", "mass_10": "0.1", "mass_100": ""}),
    ("0.05", {"mass_1": "", "mass_10": "0.5", "mass_100": ""}),
    ("0.1", {"mass_1": "0.1", "mass_10": "", "mass_100": ""}),
    (Decimal("1.50000"), {"mass_1": "1.5", "mass_10": "", "mass_100": ""}),
    (None, {"mass_1": "", "mass_10": "", "mass_100": ""}),
    ("abc", {"mass_1": "", "mass_10": "", "mass_100": ""}),
    (Decimal("0"), {"mass_1": "", "mass_10": "", "mass_100": ""}),
])
def test_mass_fields_prefill_the_sample_it_was_likely_weighed_with(mass, fields):
    assert mass_fields_from(mass) == fields
