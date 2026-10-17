import pytest

from app.modules.references.domain import (
    ReferenceEntry,
    ReferenceKind,
    ReferenceValidationError,
    gost_awg_map,
    normalized_manufacturer_key,
    normalized_section_key,
    normalized_supplier_key,
    validate_entries,
)


def test_supplier_key_is_reduced_to_canonical_domain():
    assert normalized_supplier_key(" HTTPS://www.Example.RU/catalog?id=1 ") == "example.ru"
    assert normalized_supplier_key("www.chipdip.ru/product/1") == "chipdip.ru"


def test_supplier_key_without_a_domain_is_a_literal():
    """Schema.gs supplierKey_ (RF-1): «Склад», «Фраксис», «Вода», «КабельРФ» are keys as typed, lower case."""
    assert normalized_supplier_key(" Склад ") == "склад"
    assert normalized_supplier_key("КабельРФ") == "кабельрф"
    assert normalized_supplier_key("Кабель РФ.ру") == "кабель рф.ру"


def test_supplier_key_without_domain_is_the_literal_text_as_supplymap():
    assert normalized_supplier_key("  КабельРФ ") == "кабельрф"
    assert normalized_supplier_key("Склад") == "склад"


def test_manufacturer_key_is_case_and_spacing_insensitive():
    assert normalized_manufacturer_key("  АО   Fraxis  ") == "ао fraxis"


def test_duplicate_normalized_keys_are_rejected():
    entries = (
        ReferenceEntry.create(ReferenceKind.SUPPLIER, "https://www.example.ru/a", "Первый", 0),
        ReferenceEntry.create(ReferenceKind.SUPPLIER, "EXAMPLE.RU", "Второй", 1),
    )
    with pytest.raises(ReferenceValidationError, match="повторяющийся"):
        validate_entries(entries)


def test_gost_section_key_is_a_positive_decimal_and_value_an_integer_awg():
    assert normalized_section_key(" 0,35 ") == "0.35"
    assert normalized_section_key("1,0") == "1"
    for bad in ("", "abc", "0", "-0,5"):
        with pytest.raises(ReferenceValidationError, match="Сечение ГОСТ"):
            normalized_section_key(bad)
    for bad in ("0", "41", "22.5", "двадцать"):
        with pytest.raises(ReferenceValidationError, match="AWG"):
            ReferenceEntry.create(ReferenceKind.GOST_AWG, "0,35", bad, 0)

    entries = (
        ReferenceEntry.create(ReferenceKind.GOST_AWG, "0,35", "022", 0),
        ReferenceEntry.create(ReferenceKind.GOST_AWG, "1,0", "17", 1),
        ReferenceEntry.create(ReferenceKind.COUNTRY, "Fraxis", "Россия", 0),
    )
    assert gost_awg_map(entries) == {"0.35": 22, "1": 17}
    with pytest.raises(ReferenceValidationError, match="повторяющийся"):
        validate_entries(entries + (ReferenceEntry.create(ReferenceKind.GOST_AWG, "0.35", "20", 2),))


def test_country_seed_is_sorted_unique_by_key_and_merged_under_saved():
    from app.modules.references.domain import COUNTRY_BY_MANUFACTURER, merge_country_pairs, normalized_manufacturer_key

    keys = [normalized_manufacturer_key(name) for name, _ in COUNTRY_BY_MANUFACTURER]
    assert len(keys) == len(set(keys)) and all(country.strip() for _, country in COUNTRY_BY_MANUFACTURER)
    assert [name.casefold() for name, _ in COUNTRY_BY_MANUFACTURER] == sorted(name.casefold() for name, _ in COUNTRY_BY_MANUFACTURER)

    merged = merge_country_pairs([("  HIROSE ", "Китай"), ("Fraxis", "Россия")])
    assert merged[:2] == [("  HIROSE ", "Китай"), ("Fraxis", "Россия")]
    assert ("Hirose", "Япония") not in merged and ("Molex", "США") in merged
    assert len(merged) == len(COUNTRY_BY_MANUFACTURER) + 1
