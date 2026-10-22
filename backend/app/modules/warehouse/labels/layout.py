"""The GRN label layout (Roller_Buyer `labels/grn-label.ts` + `raster.ts`), drawn straight in printer dots with Pillow.

Sizes are in dots, one to one: at 300 dpi (11.81 dots per mm) the default 58 × 40 mm canvas is 685 × 472 dots, and what
the preview shows is exactly the 1-bit raster the printer gets (luma < 128 → a dot). Upper block: the QR of the ГРН on
the left, «ГРН», its number and the quantity on the right. Lower block, full width: the name (up to two lines), the
article, «место · склад» and the received date, the series when it fits. A low label (58 × 30) drops the series,
then the article, then the name's second line, and only then shrinks the type. The wide 100 × 50 label (owner,
22.10.2026) is `_wide_plan`: a large QR on the left, the text stacked on the right."""

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
LABEL_HEIGHT_MM = 40
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
    place: str = ""  # the storage place as it is now, e.g. «1F4»; "" — not put away yet
    warehouse: str = ""  # «ЦАР» / «ЖУК»


@dataclass(frozen=True)
class LabelOptions:
    width_mm: float = LABEL_WIDTH_MM
    height_mm: float = LABEL_HEIGHT_MM
    dpi: int = 300
    code_type: str = "qr"

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


def wrap_text(text: str, max_width: int, font: ImageFont.FreeTypeFont, max_lines: int) -> list[str]:
    """Greedy word wrap into at most `max_lines` lines; what does not fit even then ends the last line with an ellipsis.
    A word wider than the line is broken by letters."""
    words = text.split()
    lines: list[str] = []
    current = ""
    index = 0
    while index < len(words):
        word = words[index]
        candidate = f"{current} {word}".strip()
        if text_width(candidate, font) <= max_width:
            current = candidate
            index += 1
        elif current:
            lines.append(current)
            current = ""
        else:
            # A single word wider than the line: put as many letters as fit.
            cut = len(word)
            while cut > 1 and text_width(word[:cut], font) > max_width:
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
            lines.append(fit_text(f"{last} {rest}", max_width, font))
            return lines
    if current:
        lines.append(current)
    return lines


def _line_height(font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    ascent, descent = font.getmetrics()
    return ascent, ascent + descent


# Text sizes in dots at 300 dpi and the steps the layout shrinks by when the label is low.
SIZE_CAPTION, SIZE_GRN, SIZE_QUANTITY = 24, 54, 46
SIZE_NAME, SIZE_ARTICLE, SIZE_PLACE, SIZE_DATE = 30, 25, 32, 25
SHRINK_STEPS = (1.0, 0.85, 0.7)


def _sized(name: str, base: int, scale: float) -> ImageFont.FreeTypeFont:
    return _font(name, max(8, js_round(base * scale)))


def _fit_font(text: str, name: str, base: int, max_width: int, scale: float) -> tuple[ImageFont.FreeTypeFont, str]:
    """The biggest size of the line, not under 60 % of the base, in which it fits; at the floor it is cut."""
    size = max(8, js_round(base * scale))
    floor = max(8, js_round(size * 0.6))
    while size > floor and text_width(text, _font(name, size)) > max_width:
        size -= 1
    font = _font(name, size)
    return font, fit_text(text, max_width, font)


def _attempt(data: GrnLabelData, options: LabelOptions, width: int, height: int, dots_per_mm: float, step: float):
    """One try of the layout at one shrink step: the plan, or None when the text does not fit."""
    scale = dots_per_mm / DOTS_PER_MM * step
    margin = js_round(dots_per_mm)
    shapes: list[tuple[int, int, int, int]] = []  # rectangles to fill
    bar_module_dots: int | None = None
    limit = height - margin

    # --- Code128: a strip along the bottom, full width ----------------------------------------------------------
    if options.code_type != "qr":
        bars = encode_code128(data.grn)
        modules = sum(bars) + 2 * CODE128_QUIET_MODULES
        # A quarter-millimetre module, but not wider than fits with the light fields.
        bar_module_dots = min(js_round(0.25 * dots_per_mm), (width - 2 * margin) // modules)
        if bar_module_dots < 2:
            raise ValueError("Этикетка слишком узкая для штрихкода: увеличьте ширину или выберите QR")
        bar_height = js_round(5 * dots_per_mm * step)
        code_top = height - margin - bar_height
        x = js_round((width - modules * bar_module_dots) / 2) + CODE128_QUIET_MODULES * bar_module_dots
        for index, bar in enumerate(bars):
            if index % 2 == 0:  # even — bars, odd — the spaces between them
                shapes.append((x, code_top, x + bar * bar_module_dots - 1, code_top + bar_height - 1))
            x += bar * bar_module_dots
        limit = code_top - margin

    # --- QR on the left of the upper block -----------------------------------------------------------------------
    qr_module_dots: int | None = None
    qr_modules: int | None = None
    text_x = margin
    block = 0
    if options.code_type != "code128":
        matrix = qr_matrix(data.grn)
        size = len(matrix)
        qr_module_dots = js_round(QR_DOTS_PER_MODULE * scale)
        if qr_module_dots < 2:
            raise ValueError("Этикетка слишком низкая для QR-кода: увеличьте высоту или выберите Code128")
        quiet = QR_QUIET_MODULES * qr_module_dots
        block = size * qr_module_dots + 2 * quiet
        if margin + block > limit:
            return None
        for row, cells in enumerate(matrix):
            for column, dark in enumerate(cells):
                if dark:
                    left, upper = margin + quiet + column * qr_module_dots, margin + quiet + row * qr_module_dots
                    shapes.append((left, upper, left + qr_module_dots - 1, upper + qr_module_dots - 1))
        qr_modules = size
        # The text starts behind the code's light field — it must not run into it.
        text_x = margin + block
    column_width = width - text_x - margin
    full_width = width - 2 * margin

    # --- Right column: «ГРН», its number, the quantity ---------------------------------------------------------
    texts: list[tuple[str, ImageFont.FreeTypeFont, int, int]] = []  # text, font, x, baseline
    grn_font, grn = _fit_font(data.grn, MONO_BOLD, SIZE_GRN, column_width, scale)
    quantity_text = f"{data.quantity}{f' {data.unit}' if data.unit else ''}"
    quantity_font, quantity = _fit_font(quantity_text, SANS_SEMIBOLD, SIZE_QUANTITY, column_width, scale)
    y = margin
    gap = js_round(6 * scale)
    for text, font in (("ГРН", _sized(SANS_SEMIBOLD, SIZE_CAPTION, scale)), (grn, grn_font), (quantity, quantity_font)):
        if not text:
            continue
        ascent, line = _line_height(font)
        texts.append((text, font, text_x, y + ascent))
        y += line + gap
    y = max(y - gap, margin + block)

    # --- Lower block, full width: name, article, place, date. Short on height: the article goes first, then the
    # second line of the name; the series only rides on the date line when it fits there. -----------------------
    name_font = _sized(SANS_SEMIBOLD, SIZE_NAME, scale)
    article_font = _sized(SANS, SIZE_ARTICLE, scale)
    place_font = _sized(SANS_SEMIBOLD, SIZE_PLACE, scale)
    date_font = _sized(SANS, SIZE_DATE, scale)
    place = " · ".join(part for part in (data.place, data.warehouse) if part)
    date_line = f"поступило {data.received_on.strftime('%d.%m.%Y')}" if data.received_on else ""
    if data.manufacturer_batch:
        # The warehouse's СЕРИЯ is the manufacturer's series, not our batch number.
        series = f"серия {data.manufacturer_batch}"
        joined = f"{date_line} · {series}" if date_line else series
        date_line = joined if text_width(joined, date_font) <= full_width else date_line
    # Names made from price books already carry the part number — no second copy (Roller_Buyer).
    article = data.sku if data.sku and data.sku not in data.item_name else ""
    top = y + js_round(12 * scale)
    tries = [(2, True), (2, False), (1, False)] if article else [(2, False), (1, False)]
    for name_lines, with_article in tries:
        rows = [(line, name_font) for line in wrap_text(data.item_name, full_width, name_font, name_lines)]
        if with_article:
            rows.append((fit_text(article, full_width, article_font), article_font))
        for extra, font in ((place, place_font), (date_line, date_font)):
            if extra:
                rows.append((fit_text(extra, full_width, font), font))
        heights = [_line_height(font) for _text, font in rows]
        used = sum(line for _a, line in heights)
        if top + used <= limit:
            row_gap = min(js_round(16 * scale), (limit - top - used) // max(1, len(rows)))
            y = top
            for (text, font), (ascent, line) in zip(rows, heights):
                texts.append((text, font, margin, y + ascent))
                y += line + row_gap
            return shapes, texts, qr_module_dots, qr_modules, bar_module_dots
    return None


# The wide label (100 × 50) starts here: from this width the QR goes large on the left and the text stacks on the right.
WIDE_FROM_MM = 90
# Text sizes of the wide label, dots at 300 dpi.
WIDE_CAPTION, WIDE_GRN, WIDE_QUANTITY, WIDE_NAME, WIDE_SMALL = 30, 76, 64, 36, 30
WIDE_STEPS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5)


def _wide_plan(data: GrnLabelData, options: LabelOptions, width: int, height: int, dots_per_mm: float):
    """100 × 50: a large QR on the left (the ТСД scanners are 2D); on the right «ГРН», the number, the quantity, the name
    in full (up to three lines), the article, «место · склад» and the received date with the series. Code128, when
    asked, is a strip along the bottom. The text is set from the top; the type shrinks until the stack fits."""
    margin = js_round(dots_per_mm)
    shapes: list[tuple[int, int, int, int]] = []
    bar_module_dots: int | None = None
    limit = height - margin
    if options.code_type != "qr":
        bars = encode_code128(data.grn)
        modules = sum(bars) + 2 * CODE128_QUIET_MODULES
        bar_module_dots = min(js_round(0.25 * dots_per_mm), (width - 2 * margin) // modules)
        if bar_module_dots < 2:
            raise ValueError("Этикетка слишком узкая для штрихкода: увеличьте ширину или выберите QR")
        bar_height = js_round(5 * dots_per_mm)
        code_top = height - margin - bar_height
        x = js_round((width - modules * bar_module_dots) / 2) + CODE128_QUIET_MODULES * bar_module_dots
        for index, bar in enumerate(bars):
            if index % 2 == 0:
                shapes.append((x, code_top, x + bar * bar_module_dots - 1, code_top + bar_height - 1))
            x += bar * bar_module_dots
        limit = code_top - margin

    qr_module_dots: int | None = None
    qr_modules: int | None = None
    text_x = margin
    if options.code_type != "code128":
        matrix = qr_matrix(data.grn)
        size = len(matrix)
        # Up to 1 mm a module, no more than 45 % of the width, whole dots.
        by_height = (limit - margin) // (size + 2 * QR_QUIET_MODULES)
        by_width = (js_round(width * 0.45) - margin) // (size + 2 * QR_QUIET_MODULES)
        qr_module_dots = min(js_round(dots_per_mm), by_height, by_width)
        if qr_module_dots < 2:
            raise ValueError("Этикетка слишком низкая для QR-кода: увеличьте высоту или выберите Code128")
        quiet = QR_QUIET_MODULES * qr_module_dots
        side = size * qr_module_dots
        y0 = margin + max(0, js_round((limit - margin - side - 2 * quiet) / 2)) + quiet
        x0 = margin + quiet
        for row, cells in enumerate(matrix):
            for column, dark in enumerate(cells):
                if dark:
                    left, upper = x0 + column * qr_module_dots, y0 + row * qr_module_dots
                    shapes.append((left, upper, left + qr_module_dots - 1, upper + qr_module_dots - 1))
        qr_modules = size
        text_x = margin + quiet + side + quiet
    column = width - text_x - margin

    quantity = f"{data.quantity}{f' {data.unit}' if data.unit else ''}"
    article = data.sku if data.sku and data.sku not in data.item_name else ""
    place = " · ".join(part for part in (data.place, data.warehouse) if part)
    footer = " · ".join(part for part in (
        f"поступило {data.received_on.strftime('%d.%m.%Y')}" if data.received_on else "",
        f"серия {data.manufacturer_batch}" if data.manufacturer_batch else "") if part)

    available = limit - margin
    for step in WIDE_STEPS:
        scale = dots_per_mm / DOTS_PER_MM * step
        grn_font, grn = _fit_font(data.grn, MONO_BOLD, WIDE_GRN, column, scale)
        quantity_font, qty = _fit_font(quantity, SANS_SEMIBOLD, WIDE_QUANTITY, column, scale)
        name_font = _sized(SANS_SEMIBOLD, WIDE_NAME, scale)
        small_font = _sized(SANS, WIDE_SMALL, scale)
        rows: list[tuple[str, ImageFont.FreeTypeFont]] = [
            ("ГРН", _sized(SANS, WIDE_CAPTION, scale)), (grn, grn_font), (qty, quantity_font)]
        rows += [(line, name_font) for line in wrap_text(data.item_name, column, name_font, 3)]
        name_end = len(rows)
        rows += [(fit_text(text, column, small_font), small_font) for text in (article, place, footer) if text]
        heights = [_line_height(font) for _t, font in rows]
        air = js_round(10 * scale)
        used = sum(line for _a, line in heights) + 3 * air
        if used > available and step != WIDE_STEPS[-1]:
            continue
        y = margin + max(0, (available - used) // 2)
        texts: list[tuple[str, ImageFont.FreeTypeFont, int, int]] = []
        for index, ((text, font), (ascent, line)) in enumerate(zip(rows, heights)):
            if text:
                texts.append((text, font, text_x, y + ascent))
            y += line
            # A little air after the number, after the quantity and after the last name line.
            if index in (1, 2, name_end - 1):
                y += air
        return shapes, texts, qr_module_dots, qr_modules, bar_module_dots
    return None


def build_grn_label(data: GrnLabelData, options: LabelOptions = LabelOptions()) -> LabelLayout:
    """The label in printer dots. ValueError — the code does not fit (or Code128 cannot carry the ГРН)."""
    dots_per_mm = options.dpi / 25.4
    width = js_round(options.width_mm * dots_per_mm)
    height = js_round(options.height_mm * dots_per_mm)
    plan = None
    if options.width_mm >= WIDE_FROM_MM:
        plan = _wide_plan(data, options, width, height, dots_per_mm)
    else:
        for step in SHRINK_STEPS:
            plan = _attempt(data, options, width, height, dots_per_mm, step)
            if plan:
                break
    if plan is None:
        raise ValueError("Этикетка слишком низкая: увеличьте высоту или выберите QR")
    shapes, texts, qr_module_dots, qr_modules, bar_module_dots = plan
    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)
    for box in shapes:
        draw.rectangle(box, fill=0)
    for text, font, x, baseline in texts:
        draw.text((x, baseline), text, font=font, fill=0, anchor="ls")

    # Threshold in the middle: the layout is black and white, the smoothed edges of letters become a dot or not.
    image = canvas.point(lambda value: 0 if value < 128 else 255).convert("1", dither=Image.Dither.NONE)
    return LabelLayout(
        image=image, width=width, height=height, qr_module_dots=qr_module_dots, qr_modules=qr_modules,
        bar_module_dots=bar_module_dots, texts=tuple(text for text, _f, _x, _y in texts),
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
