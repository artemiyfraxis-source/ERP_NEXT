"""What a GRN label is drawn with, in printer dots (Roller_Buyer `labels/grn-label.ts` + `raster.ts`): the data of a
label, the fonts, the QR symbol, measuring and wrapping text, the finished raster.

Sizes are in dots, one to one: at 300 dpi (11.81 dots per mm) a 58 × 40 mm canvas is 685 × 472 dots, and what the
preview shows is exactly the 1-bit raster the printer gets (luma < 128 → a dot). Where things go on the label is no
longer code: a template (`template.py`, the built-in ones in `defaults.py`) is drawn by `render.py`."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import segno
from PIL import Image, ImageFont

DOTS_PER_MM = 300 / 25.4

# Dots per QR module at 300 dpi of today's labels — whole numbers: a fractional module gives uneven cells and scanner
# misses. Six dots are 0.5 mm.
QR_DOTS_PER_MODULE = 6
# The mandatory light field around the QR, in modules. Less — the code does not read.
QR_QUIET_MODULES = 4

FONTS_DIR = Path(__file__).with_name("fonts")
SANS = "IBMPlexSans-Regular.ttf"
# Roller_Buyer asks resvg for weights 600 and 700 of IBM Plex Sans; only SemiBold is shipped, so both draw SemiBold.
SANS_SEMIBOLD = "IBMPlexSans-SemiBold.ttf"
MONO_BOLD = "IBMPlexMono-Bold.ttf"


def js_round(value: float) -> int:
    """JavaScript `Math.round` (half up): the layout's numbers must match Roller_Buyer's dot for dot, and Python's
    `round` rounds a half to even."""
    return math.floor(value + 0.5)


@dataclass(frozen=True)
class GrnLabelData:
    grn: str  # what the codes carry and the big line shows: the ГРН, «-<пакет>» when the lot is a package
    sku: str  # АРТИКУЛ; empty when it only repeats the name
    item_name: str
    quantity: str  # the current balance as shown, e.g. «338» / «2.5»
    unit: str
    manufacturer_batch: str  # СЕРИЯ изготовителя, "" — none
    received_on: date | None
    place: str = ""  # the storage place as it is now, e.g. «1F4»; "" — not put away yet
    warehouse: str = ""  # «ЦАР» / «ЖУК»
    manufacturer: str = ""  # ПРОИЗВОДИТЕЛЬ of the lot passport
    purpose: str = ""  # НАЗНАЧЕНИЕ: the project the lot is for


@dataclass(frozen=True)
class LabelLayout:
    image: Image.Image  # mode "1": 0 — a dot to print, 255 — the tape stays clean
    width: int
    height: int
    # Dots per QR module and modules in the code; dots per bar module — whole numbers, checked by the tests. The first
    # code of its kind on the label; None — none drawn.
    qr_module_dots: int | None
    qr_modules: int | None
    bar_module_dots: int | None
    texts: tuple[str, ...]  # the text lines as drawn (tests read them)
    # Ids of text elements whose text did not fit their box even at the smallest size (cut at the box's edge).
    overflow: tuple[str, ...] = ()
    # Ids of Code128 elements left empty: set B cannot carry this ГРН (Cyrillic).
    skipped: tuple[str, ...] = ()


@lru_cache(maxsize=256)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS_DIR / name), size)


def text_width(text: str, face: ImageFont.FreeTypeFont) -> int:
    """Width of the line's ink in dots — what will be printed (Roller_Buyer measures the rightmost dark dot)."""
    if not text:
        return 0
    _left, _top, right, _bottom = face.getbbox(text, anchor="ls")
    return max(0, math.ceil(right))


def fit_text(text: str, max_width: int, face: ImageFont.FreeTypeFont) -> str:
    """Cut the line to the width with an ellipsis (Roller_Buyer `fitText`)."""
    if text_width(text, face) <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = math.ceil((low + high) / 2)
        if text_width(f"{text[:middle].rstrip()}…", face) <= max_width:
            low = middle
        else:
            high = middle - 1
    return "" if low == 0 else f"{text[:low].rstrip()}…"


def qr_symbol(text: str) -> segno.QRCode:
    """The QR with error correction M as in Roller_Buyer: segno would otherwise raise the level when it fits — a fixed
    level keeps the module size predictable."""
    if text.isascii():
        return segno.make(text, error="m", boost_error=False, micro=False)
    # Cyrillic goes as UTF-8 bytes (as Roller_Buyer's encoder does): left to itself segno would pick the Kanji mode,
    # since Shift_JIS has Cyrillic, and a scanner would read Japanese-encoded bytes.
    return segno.make(text, error="m", boost_error=False, micro=False, mode="byte", encoding="utf-8")


def qr_matrix(text: str) -> tuple[tuple[bool, ...], ...]:
    """The QR's own modules, no border."""
    return tuple(tuple(bool(cell) for cell in row) for row in qr_symbol(text).matrix)


def wrap_text(text: str, max_width: int, face: ImageFont.FreeTypeFont, max_lines: int) -> list[str]:
    """Greedy word wrap into at most `max_lines` lines; what does not fit even then ends the last line with an ellipsis.
    A word wider than the line is broken by letters."""
    words = text.split()
    lines: list[str] = []
    current = ""
    index = 0
    while index < len(words):
        word = words[index]
        candidate = f"{current} {word}".strip()
        if text_width(candidate, face) <= max_width:
            current = candidate
            index += 1
        elif current:
            lines.append(current)
            current = ""
        else:
            # A single word wider than the line: put as many letters as fit.
            cut = len(word)
            while cut > 1 and text_width(word[:cut], face) > max_width:
                cut -= 1
            lines.append(word[:cut])
            words[index] = word[cut:]
            if not words[index]:
                index += 1
        if len(lines) == max_lines:
            # Out of lines with text left: the last line takes the rest and ends with an ellipsis.
            rest = " ".join(([current] if current else []) + words[index:])
            if not rest:
                return lines
            last = lines.pop()
            lines.append(fit_text(f"{last} {rest}", max_width, face))
            return lines
    if current:
        lines.append(current)
    return lines


def label_png(layout: LabelLayout) -> bytes:
    buffer = BytesIO()
    layout.image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
