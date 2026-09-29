"""Shared physical storage-address value object and the rack layout it must fit."""

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache


# Rack 1..99, shelf A..Z, number 1..999, optional slot A..Z. Leading zeros are read and dropped
# («01A1» → «1A1»), as SUPPLYMAP placeParse_/placeCode_ do.
_PLACE = re.compile(r"^([0-9]{1,2})([A-Z])([0-9]{1,4})([A-Z])?$")
_HOMOGLYPHS = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H",
    "К": "K", "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X",
})
STORAGE_LOCATION_MAX_LENGTH = 255
SHELF_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PLACE_MAX_NUMBER = 999  # SUPPLYMAP STOCK05_PLACE_MAX_NUM_: packages that fit on one shelf
RACK_LAYOUT_ENV = "ERP_WAREHOUSE_RACK_LAYOUT"


def _parse(value: str, max_number: int = PLACE_MAX_NUMBER) -> tuple[str, str, str, str] | None:
    normalized = re.sub(r"\s+", "", str(value or "").upper()).translate(_HOMOGLYPHS)
    match = _PLACE.fullmatch(normalized)
    if match is None:
        return None
    rack, number = int(match.group(1)), int(match.group(3))
    if rack < 1 or not 1 <= number <= max_number:
        return None
    return str(rack), match.group(2), str(number), match.group(4) or ""


@dataclass(frozen=True)
class RackLayout:
    """SUPPLYMAP Stock05.gs `stock05_places` (Script Properties): how many racks the warehouse has,
    how many shelves (letters from A) a rack has, and racks with their own shelf count."""

    racks: int = 17  # STOCK05_RACKS_DEFAULT_ (owner, 2026-08-26)
    shelves: int = 26  # STOCK05_SHELVES_DEFAULT_: A..Z (owner, 2026-09-22; was 7)
    per_rack: Mapping[int, int] = field(default_factory=dict)

    def shelves_in(self, rack: int) -> int:
        return self.per_rack.get(rack, self.shelves)


DEFAULT_RACK_LAYOUT = RackLayout()


def rack_layout_from_config(config: Mapping[str, object]) -> RackLayout:
    """Validate a layout as SUPPLYMAP stock05PlacesSave_ does. A per-rack count equal to the common one is
    dropped, so changing the common count reaches every rack that has no real override."""
    if not isinstance(config, Mapping):
        raise ValueError("Раскладка стеллажей — объект {racks, shelves, perRack}.")

    def whole(value: object) -> int:
        try:
            return int(float(value))  # Math.floor(Number(v)) for the positive values that pass
        except (TypeError, ValueError, OverflowError):
            return 0

    racks = whole(config.get("racks", DEFAULT_RACK_LAYOUT.racks))
    shelves = whole(config.get("shelves", DEFAULT_RACK_LAYOUT.shelves))
    letters = len(SHELF_LETTERS)
    if not 1 <= racks <= 99:
        raise ValueError("Стеллажей должно быть от 1 до 99.")
    if not 1 <= shelves <= letters:
        raise ValueError(f"Полок должно быть от 1 до {letters} (буквы A…Z).")
    raw_per_rack = config.get("perRack") or {}
    if not isinstance(raw_per_rack, Mapping):
        raise ValueError("perRack — объект {номер стеллажа: полок}.")
    per_rack: dict[int, int] = {}
    for key, value in raw_per_rack.items():
        rack, count = whole(key), whole(value)
        if not 1 <= rack <= racks:
            raise ValueError(f"Стеллажа №{key} нет (всего {racks}).")
        if not 1 <= count <= letters:
            raise ValueError(f"Стеллаж {rack}: полок должно быть от 1 до {letters}.")
        if count != shelves:
            per_rack[rack] = count
    return RackLayout(racks=racks, shelves=shelves, per_rack=per_rack)


@cache
def configured_rack_layout() -> RackLayout:
    """The layout of this installation: `ERP_WAREHOUSE_RACK_LAYOUT` as SUPPLYMAP JSON
    {"racks": 17, "shelves": 26, "perRack": {"5": 12}}, otherwise the SUPPLYMAP defaults. A broken value
    stops the app instead of silently falling back: a wrong layout rejects real places or lets typos in.
    RuntimeError, not ValueError: a setup fault must not read as a refused place (and poison an outbox message)."""
    raw = os.getenv(RACK_LAYOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_RACK_LAYOUT
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{RACK_LAYOUT_ENV}: не JSON ({error.msg}).") from error
    try:
        return rack_layout_from_config(config)
    except ValueError as error:
        raise RuntimeError(f"{RACK_LAYOUT_ENV}: {error}") from error


def storage_location_fits(value: str, layout: RackLayout) -> bool:
    """SUPPLYMAP placeFits_: the rack exists, the shelf is in that rack, № on the shelf ≤ 999. A slot is any
    letter A..Z (STOCK05_SLOTS_ = 26). An empty place or an old address is not a rack code: nothing to fit."""
    parts = _parse(value, max_number=9999)
    if parts is None:
        return True
    rack, shelf, number, _slot = parts
    return (
        int(rack) <= layout.racks
        and SHELF_LETTERS.index(shelf) < layout.shelves_in(int(rack))
        and int(number) <= PLACE_MAX_NUMBER
    )


def existing_storage_location(value: str, layout: RackLayout | None = None) -> str:
    """A rack code that must exist in the layout (receipt, quick entry, move) — Stock05.gs 751-752, 845, 933.
    A number past 999 reads as a code and is refused as a missing place, as placeParse_ + placeFits_ do."""
    parts = _parse(value, max_number=9999)
    if parts is None:
        raise ValueError("Место хранения должно иметь вид 1F4, 17C120 или 12C7B")
    code = "".join(parts)
    if not storage_location_fits(code, layout or configured_rack_layout()):
        raise ValueError(f"Места {code} нет: проверь номер стеллажа и полку.")
    return code


_SHELF = re.compile(r"^([0-9]{1,2})([A-Z])$")


def existing_shelf(value: str, layout: RackLayout | None = None) -> str:
    """A shelf code «12C» (rack + shelf letter) that exists in the layout, leading zeros dropped."""
    normalized = re.sub(r"\s+", "", str(value or "").upper()).translate(_HOMOGLYPHS)
    match = _SHELF.fullmatch(normalized)
    if match is None or int(match.group(1)) < 1:
        raise ValueError("Полка должна иметь вид 12C: номер стеллажа и буква полки")
    code = f"{int(match.group(1))}{match.group(2)}"
    if not storage_location_fits(f"{code}1", layout or configured_rack_layout()):
        raise ValueError(f"Полки {code} нет: проверь номер стеллажа и полку.")
    return code


def places_on_shelf(occupied: Iterable[str], shelf: str) -> list[str]:
    """SUPPLYMAP stock05UsedPlaces_ + placeNextNum_ (Stock05.gs:322-344, PortalApp.html:8365): the distinct rack
    codes on `shelf` («12C») among the occupied places, by number then cell. An old address is no rack code."""
    found: set[tuple[int, str]] = set()
    for value in occupied:
        parts = _parse(value, max_number=9999)
        if parts is not None and f"{parts[0]}{parts[1]}" == shelf:
            found.add((int(parts[2]), parts[3]))
    return [f"{shelf}{number}{slot}" for number, slot in sorted(found)]


def next_free_number(occupied: Iterable[str], shelf: str) -> int | None:
    """The smallest № 1..999 on the shelf where nothing lies, whatever the cell letter; None when all are taken."""
    taken = {int(_parse(code, max_number=9999)[2]) for code in places_on_shelf(occupied, shelf)}  # type: ignore[index]
    return next((number for number in range(1, PLACE_MAX_NUMBER + 1) if number not in taken), None)


def normalize_storage_location(value: str) -> str:
    parts = _parse(value)
    if parts is None:
        raise ValueError("Место хранения должно иметь вид 1F4, 17C120 или 12C7B")
    return "".join(parts)


def storage_location_as_is(value: str) -> str:
    """Manual correction and old data: blank is «no place», our code is normalized,
    any other text (an old address like «A-01») is kept as typed."""
    text = str(value or "").strip()
    parts = _parse(text)
    if parts is not None:
        return "".join(parts)
    if len(text) > STORAGE_LOCATION_MAX_LENGTH:
        raise ValueError(f"Место хранения длиннее {STORAGE_LOCATION_MAX_LENGTH} символов")
    return text


def storage_location_has_slot(value: str) -> bool:
    parts = _parse(value)
    return bool(parts and parts[3])


def remap_storage_location(value: str, source: str, target: str) -> str | None:
    # An empty place or an old address is not part of any rack place: it never moves with one.
    current, source_parts, target_parts = _parse(value), _parse(source), _parse(target)
    if current is None or source_parts is None or target_parts is None:
        return None
    if current[:3] != source_parts[:3]:
        return None
    source_slot, current_slot = source_parts[3], current[3]
    if source_slot and current_slot != source_slot:
        return None
    slot = (target_parts[3] or current_slot) if source_slot else current_slot
    return "".join((*target_parts[:3], slot))
