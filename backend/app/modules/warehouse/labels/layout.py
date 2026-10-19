"""The GRN label layout (Roller_Buyer `labels/grn-label.ts` + `raster.ts`), drawn straight in printer dots with Pillow.

Sizes are in dots, one to one: the canvas is 685 × 354 dots — 58 × 30 mm at 300 dpi (11.81 dots per mm) — and what
the preview shows is exactly the 1-bit raster the printer gets. Roller_Buyer draws an SVG and rasterises it with
resvg; here the same geometry is drawn with Pillow and thresholded the same way (luma < 128 → a dot).

No storage place on the label: it is printed at marking, before putaway, and a package's place changes when boxes are
rearranged — a printed address would start to lie."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import segno
from PIL import Image, ImageDraw, ImageFont

from .code128 import QUIET_MODULES as CODE128_QUIET_MODULES, encode_code128
from .settings import PrintSettings

LABEL_WIDTH_MM = 58
LABEL_HEIGHT_MM = 30
DOTS_PER_MM = 300 / 25.4

# Dots per QR module at 300 dpi — a whole number, the one place of the layout that must not be rounded by eye: a
# fractional module gives uneven cells and scanner misses. Six dots are 0.5 mm.
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


@dataclass(frozen=True)
class LabelOptions:
    width_mm: float = LABEL_WIDTH_MM
    height_mm: float = LABEL_HEIGHT_MM
    dpi: int = 300
    code_type: str = "both"

    @classmethod
    def of(cls, settings: PrintSettings) -> LabelOptions:
        return cls(settings.width_mm, settings.height_mm, settings.dpi, settings.code_type)


@dataclass(frozen=True)
class LabelLayout:
    image: Image.Image  # mode "1": 0 — a dot to print, 255 — the tape stays clean
    width: int
    height: int
    # Dots per QR module and modules in the code; dots per bar module — whole numbers, checked by the tests.
    qr_module_dots: int | None
    qr_modules: int | None
    bar_module_dots: int | None
    texts: tuple[str, ...]  # the text lines as drawn (tests read them)


@lru_cache(maxsize=64)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS_DIR / name), size)


def text_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    """Width of the line's ink in dots — what will be printed (Roller_Buyer measures the rightmost dark dot)."""
    if not text:
        return 0
    left, _top, right, _bottom = font.getbbox(text, anchor="ls")
    return max(0, math.ceil(right))


def fit_text(text: str, max_width: int, font: ImageFont.FreeTypeFont) -> str:
    """Cut the line to the width with an ellipsis (Roller_Buyer `fitText`)."""
    if text_width(text, font) <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = math.ceil((low + high) / 2)
        if text_width(f"{text[:middle].rstrip()}…", font) <= max_width:
            low = middle
        else:
            high = middle - 1
    return "" if low == 0 else f"{text[:low].rstrip()}…"


def qr_matrix(text: str) -> tuple[tuple[bool, ...], ...]:
    """The QR's own modules (no border), error correction M as in Roller_Buyer: segno would otherwise raise the
    level when it fits — a fixed level keeps the module size predictable."""
    code = segno.make(text, error="m", boost_error=False, micro=False)
    return tuple(tuple(bool(cell) for cell in row) for row in code.matrix)


def build_grn_label(data: GrnLabelData, options: LabelOptions = LabelOptions()) -> LabelLayout:
    """The label in printer dots. ValueError — the code does not fit (or Code128 cannot carry the ГРН)."""
    dots_per_mm = options.dpi / 25.4
    width = js_round(options.width_mm * dots_per_mm)
    height = js_round(options.height_mm * dots_per_mm)
    # Everything designed in dots at 300 dpi is recomputed for the head.
    scale = dots_per_mm / DOTS_PER_MM
    margin = js_round(dots_per_mm)
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)

    # --- Code128: a strip along the bottom, full width ----------------------------------------------------------
    code_top = height - margin
    bar_module_dots: int | None = None
    if options.code_type != "qr":
        bars = encode_code128(data.grn)
        modules = sum(bars) + 2 * CODE128_QUIET_MODULES
        # A quarter-millimetre module, but not wider than fits with the light fields.
        bar_module_dots = min(js_round(0.25 * dots_per_mm), (width - 2 * margin) // modules)
        if bar_module_dots < 2:
            raise ValueError("Этикетка слишком узкая для штрихкода: увеличьте ширину или выберите QR")
        bar_height = js_round(5 * dots_per_mm)
        code_top = height - margin - bar_height
        x = js_round((width - modules * bar_module_dots) / 2) + CODE128_QUIET_MODULES * bar_module_dots
        for index, bar in enumerate(bars):
            # Even — bars, odd — the spaces between them.
            if index % 2 == 0:
                draw.rectangle((x, code_top, x + bar * bar_module_dots - 1, code_top + bar_height - 1), fill=0)
            x += bar * bar_module_dots

    # --- QR: on the left of the upper part ----------------------------------------------------------------------
    top = code_top - margin
    text_x = margin
    qr_module_dots: int | None = None
    qr_modules: int | None = None
    if options.code_type != "code128":
        matrix = qr_matrix(data.grn)
        size = len(matrix)
        fits = (top - margin) // (size + 2 * QR_QUIET_MODULES)
        qr_module_dots = min(js_round(QR_DOTS_PER_MODULE * scale), fits)
        if qr_module_dots < 2:
            raise ValueError("Этикетка слишком низкая для QR-кода: увеличьте высоту или выберите Code128")
        quiet = QR_QUIET_MODULES * qr_module_dots
        side = size * qr_module_dots
        y = margin + max(0, js_round((top - margin - side - 2 * quiet) / 2)) + quiet
        x = margin + quiet
        for row, cells in enumerate(matrix):
            for column, dark in enumerate(cells):
                if dark:
                    left, upper = x + column * qr_module_dots, y + row * qr_module_dots
                    draw.rectangle((left, upper, left + qr_module_dots - 1, upper + qr_module_dots - 1), fill=0)
        qr_modules = size
        # The text starts behind the code's light field — it must not run into it.
        text_x = margin + quiet + side + quiet

    # --- Text: number, item, quantity ---------------------------------------------------------------------------
    factor = scale * min(1, top / scale / 300)

    def size_of(base: int) -> int:
        return max(8, js_round(base * factor))

    def at(fraction: float) -> int:
        return margin + js_round((top - margin) * fraction)

    text_width_dots = width - text_x - margin
    head_font = _font(SANS, size_of(22))
    grn_font = _font(MONO_BOLD, size_of(50))
    title_font = _font(SANS_SEMIBOLD, size_of(26))
    quantity_font = _font(SANS_SEMIBOLD, size_of(40))
    footer_font = _font(SANS, size_of(22))

    grn = fit_text(data.grn, text_width_dots, grn_font)
    # Names made from price books already carry the part number — no second copy (Roller_Buyer).
    heading = f"{data.sku} · {data.item_name}" if data.sku and data.sku not in data.item_name else data.item_name
    title = fit_text(heading, text_width_dots, title_font)
    quantity = f"{data.quantity}{f' {data.unit}' if data.unit else ''}"
    # The warehouse's СЕРИЯ is the manufacturer's series, not our batch number.
    footer = fit_text(
        " · ".join(part for part in (
            f"серия {data.manufacturer_batch}" if data.manufacturer_batch else "",
            data.received_on.strftime("%d.%m.%Y") if data.received_on else "",
        ) if part),
        text_width_dots,
        footer_font,
    )
    lines = (
        ("GRN", head_font, at(0.14)),
        (grn, grn_font, at(0.36)),
        (title, title_font, at(0.56)),
        (quantity, quantity_font, at(0.8)),
        (footer, footer_font, at(0.96)),
    )
    for text, font, baseline in lines:
        if text:
            draw.text((text_x, baseline), text, font=font, fill=0, anchor="ls")

    # Threshold in the middle: the layout is black and white, the smoothed edges of letters become a dot or not.
    image = canvas.point(lambda value: 0 if value < 128 else 255).convert("1", dither=Image.Dither.NONE)
    return LabelLayout(
        image=image, width=width, height=height, qr_module_dots=qr_module_dots, qr_modules=qr_modules,
        bar_module_dots=bar_module_dots, texts=tuple(text for text, _font_, _y in lines),
    )


def render_label(data: GrnLabelData, settings: PrintSettings) -> LabelLayout:
    """The label with the chosen code; Code128 set B knows only ASCII — a ГРН with Cyrillic (or a code that does not
    fit) falls back to the QR alone, as Roller_Buyer does."""
    options = LabelOptions.of(settings)
    try:
        return build_grn_label(data, options)
    except ValueError:
        if options.code_type == "qr":
            raise
        return build_grn_label(data, LabelOptions(options.width_mm, options.height_mm, options.dpi, "qr"))


def label_png(layout: LabelLayout) -> bytes:
    buffer = BytesIO()
    layout.image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
