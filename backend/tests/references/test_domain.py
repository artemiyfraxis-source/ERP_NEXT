import pytest

from app.modules.references.domain import (
    ReferenceEntry,
    ReferenceKind,
    ReferenceValidationError,
    normalized_manufacturer_key,
    normalized_supplier_key,
    validate_entries,
)


def test_supplier_key_is_reduced_to_canonical_domain():
    assert normalized_supplier_key(" HTTPS://www.Example.RU/catalog?id=1 ") == "example.ru"


def test_manufacturer_key_is_case_and_spacing_insensitive():
    assert normalized_manufacturer_key("  АО   Fraxis  ") == "ао fraxis"


def test_duplicate_normalized_keys_are_rejected():
    entries = (
        ReferenceEntry.create(ReferenceKind.SUPPLIER, "https://www.example.ru/a", "Первый", 0),
        ReferenceEntry.create(ReferenceKind.SUPPLIER, "EXAMPLE.RU", "Второй", 1),
    )
    with pytest.raises(ReferenceValidationError, match="повторяющийся"):
        validate_entries(entries)
