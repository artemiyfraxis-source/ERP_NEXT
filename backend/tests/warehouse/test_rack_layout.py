"""SK-9: a place must exist in the rack layout (SUPPLYMAP Stock05.gs placeFits_, stock05PlacesSave_)."""

import re

import pytest

from app.shared.warehouse_location import (
    DEFAULT_RACK_LAYOUT,
    RACK_LAYOUT_ENV,
    RackLayout,
    configured_rack_layout,
    existing_storage_location,
    rack_layout_from_config,
    storage_location_fits,
)


@pytest.fixture
def layout_env(monkeypatch):
    def set_layout(value: str):
        monkeypatch.setenv(RACK_LAYOUT_ENV, value)
        configured_rack_layout.cache_clear()

    yield set_layout
    monkeypatch.delenv(RACK_LAYOUT_ENV, raising=False)
    configured_rack_layout.cache_clear()


def test_default_layout_is_supplymap_17_racks_of_shelves_a_to_z():
    assert (DEFAULT_RACK_LAYOUT.racks, DEFAULT_RACK_LAYOUT.shelves, dict(DEFAULT_RACK_LAYOUT.per_rack)) == (17, 26, {})


@pytest.mark.parametrize("code", ["1A1", "17Z999", "17Z999Z", "9M12B"])
def test_codes_inside_the_default_layout_fit(code):
    assert storage_location_fits(code, DEFAULT_RACK_LAYOUT)
    assert existing_storage_location(code, DEFAULT_RACK_LAYOUT) == code


@pytest.mark.parametrize("code", ["18A1", "99C3", "1A1000", "17Z1000B"])
def test_missing_rack_or_number_past_999_is_a_missing_place(code):
    assert not storage_location_fits(code, DEFAULT_RACK_LAYOUT)
    with pytest.raises(ValueError, match=f"^Места {code} нет: проверь номер стеллажа и полку.$"):
        existing_storage_location(code, DEFAULT_RACK_LAYOUT)


def test_per_rack_shelf_count_overrides_the_common_one():
    layout = RackLayout(racks=17, shelves=7, per_rack={3: 5, 5: 12})
    assert storage_location_fits("3E1", layout) and not storage_location_fits("3F1", layout)
    assert storage_location_fits("5L1", layout) and not storage_location_fits("5M1", layout)
    assert storage_location_fits("4G1", layout) and not storage_location_fits("4H1", layout)


@pytest.mark.parametrize("value", ["", "A-01", "0A1", "12C3 угол"])
def test_empty_place_and_old_address_have_no_rack_to_check(value):
    assert storage_location_fits(value, RackLayout(racks=1, shelves=1))


@pytest.mark.parametrize("value", ["", "A-01", "0A1", "1A01000", "1Д1"])
def test_a_required_place_still_needs_the_code_format(value):
    with pytest.raises(ValueError, match="Место хранения должно иметь вид"):
        existing_storage_location(value, DEFAULT_RACK_LAYOUT)


def test_existing_place_is_normalized_like_any_code():
    assert existing_storage_location(" 01в0012с ", DEFAULT_RACK_LAYOUT) == "1B12C"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"racks": 0}, "Стеллажей должно быть от 1 до 99."),
        ({"racks": 100}, "Стеллажей должно быть от 1 до 99."),
        ({"shelves": 27}, "Полок должно быть от 1 до 26 (буквы A…Z)."),
        ({"racks": 17, "perRack": {"18": 3}}, "Стеллажа №18 нет (всего 17)."),
        ({"perRack": {"2": 0}}, "Стеллаж 2: полок должно быть от 1 до 26."),
    ],
)
def test_layout_config_is_validated_like_the_supplymap_screen(config, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        rack_layout_from_config(config)


def test_layout_config_drops_a_per_rack_count_equal_to_the_common_one():
    layout = rack_layout_from_config({"racks": 20, "shelves": 7, "perRack": {"3": 5, "4": 7}})
    assert layout == RackLayout(racks=20, shelves=7, per_rack={3: 5})
    assert rack_layout_from_config({}) == DEFAULT_RACK_LAYOUT


def test_installation_layout_comes_from_the_environment(layout_env):
    layout_env('{"racks": 20, "shelves": 26, "perRack": {"19": 2}}')
    assert configured_rack_layout() == RackLayout(racks=20, per_rack={19: 2})
    assert existing_storage_location("19B1") == "19B1"
    with pytest.raises(ValueError, match="Места 19C1 нет"):
        existing_storage_location("19C1")


def test_broken_environment_layout_is_refused_not_ignored(layout_env):
    layout_env("{racks: 20")
    with pytest.raises(RuntimeError, match=f"{RACK_LAYOUT_ENV}: не JSON"):
        configured_rack_layout()
    layout_env('{"racks": 200}')
    with pytest.raises(RuntimeError, match="Стеллажей должно быть от 1 до 99"):
        configured_rack_layout()
    with pytest.raises(RuntimeError):  # not a WarehouseValidationError: the outbox must not poison on it
        existing_storage_location("1A1")
