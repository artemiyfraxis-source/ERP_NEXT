"""what a scanned or typed code means — a ГРН (with or without its package) or a
storage place. Pure: no database, the caller looks the result up."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.shared.warehouse_location import normalize_storage_location

# Cyrillic letters that look like Latin ones (a ГРН is Latin; «FRХ» with a Cyrillic Х is still FRX).
_LATIN_TWINS = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X",
    "У": "Y",
})
# A scanner types key codes: with the Russian layout on, «FRX.26.0000001» arrives as «АКЧю26ю0000001».
_RUSSIAN_KEYS = str.maketrans(
    "ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ.?",
    "QWERTYUIOP[]ASDFGHJKL;'ZXCVBNM,./&",
)
_GRN = re.compile(r"^(R&D|[A-Z]{3})\.(\d{2})\.(\d{7})(?:[\s\-/_]*(\S.*))?$")


def _upper(code: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(code or "")).upper().split())


def fold_key(value: str) -> str:
    """ГРН / package as compared: upper case, no spaces, Cyrillic look-alikes as Latin."""
    return re.sub(r"\s+", "", _upper(value)).translate(_LATIN_TWINS)


@dataclass(frozen=True)
class GrnCode:
    grn: str  # folded, e.g. «FRX.26.0000001»
    package: str  # folded, "" — every package of the ГРН
    tail: str  # «.26.0000001»: what the stored ГРН ends with whatever its prefix is written in


@dataclass(frozen=True)
class ScanCode:
    text: str  # the code as typed, trimmed
    grn: GrnCode | None
    place: str | None  # a rack code «12C7B», when the code is no ГРН


def _grn(text: str) -> GrnCode | None:
    match = _GRN.fullmatch(text.translate(_LATIN_TWINS))
    if match is None:
        return None
    prefix, year, number, package = match.groups()
    return GrnCode(f"{prefix}.{year}.{number}", fold_key(package or ""), f".{year}.{number}")


def _place(text: str) -> str | None:
    try:
        return normalize_storage_location(text)
    except ValueError:
        return None


def parse_scan(code: str) -> ScanCode:
    """A ГРН first (as typed, then as if typed on the Russian layout), then a place (the same order)."""
    text = _upper(code)
    on_russian_keys = text.translate(_RUSSIAN_KEYS)
    grn = _grn(text) or _grn(on_russian_keys)
    place = None if grn else (_place(text) or _place(on_russian_keys))
    return ScanCode(" ".join(str(code or "").split()), grn, place)


def lot_matches(code: GrnCode, grn: str, package: str) -> bool:
    return fold_key(grn) == code.grn and (not code.package or fold_key(package) == code.package)


def place_matches(place: str, location: str) -> bool:
    """A place with a cell («12C7B») is that cell; without one («12C7») — the place and every cell of it."""
    lying = _place(location) if location else None
    if lying is None:
        return False
    if place[-1].isalpha():
        return lying == place
    return lying == place or (lying[:-1] == place and lying[-1].isalpha())
