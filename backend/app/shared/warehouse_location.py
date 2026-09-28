"""Shared physical storage-address value object."""

import re


# Rack 1..99, shelf A..Z, number 1..999, optional slot A..Z. Leading zeros are read and dropped
# («01A1» → «1A1»), as SUPPLYMAP placeParse_/placeCode_ do.
_PLACE = re.compile(r"^([0-9]{1,2})([A-Z])([0-9]{1,4})([A-Z])?$")
_HOMOGLYPHS = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H",
    "К": "K", "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X",
})
STORAGE_LOCATION_MAX_LENGTH = 255


def _parse(value: str) -> tuple[str, str, str, str] | None:
    normalized = re.sub(r"\s+", "", str(value or "").upper()).translate(_HOMOGLYPHS)
    match = _PLACE.fullmatch(normalized)
    if match is None:
        return None
    rack, number = int(match.group(1)), int(match.group(3))
    if rack < 1 or not 1 <= number <= 999:
        return None
    return str(rack), match.group(2), str(number), match.group(4) or ""


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
