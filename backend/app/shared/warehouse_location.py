"""Shared physical storage-address value object."""

import re


_PLACE = re.compile(r"^([1-9]\d?)([A-Z])([1-9]\d{0,2})([A-Z])?$")
_HOMOGLYPHS = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H",
    "К": "K", "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X",
})


def normalize_storage_location(value: str) -> str:
    normalized = re.sub(r"\s+", "", str(value or "").upper()).translate(_HOMOGLYPHS)
    if not _PLACE.fullmatch(normalized):
        raise ValueError("Место хранения должно иметь вид 1F4, 17C120 или 12C7B")
    return normalized


def storage_location_has_slot(value: str) -> bool:
    match = _PLACE.fullmatch(normalize_storage_location(value))
    return bool(match and match.group(4))


def remap_storage_location(value: str, source: str, target: str) -> str | None:
    current_match = _PLACE.fullmatch(normalize_storage_location(value))
    source_match = _PLACE.fullmatch(normalize_storage_location(source))
    target_match = _PLACE.fullmatch(normalize_storage_location(target))
    if current_match is None or source_match is None or target_match is None:
        return None
    if current_match.groups()[:3] != source_match.groups()[:3]:
        return None
    source_slot = source_match.group(4)
    current_slot = current_match.group(4)
    if source_slot and current_slot != source_slot:
        return None
    slot = (target_match.group(4) or current_slot) if source_slot else current_slot
    return "".join((*target_match.groups()[:3], slot or ""))
