"""The one label renderer: a template (`template.py`) and a label's content → the 1-bit raster at the printer's dpi.
The preview PNG and the TSPL `BITMAP` both come from here, so the preview is exactly what prints.

Rules the designer relies on:
- every element is drawn inside its own box and cut at its edges — nothing spills onto a neighbour;
- text: the capital letters are `font_mm` high; lines stack from the box's top, `max_lines` at most and as many as the
  box's height holds; «shrink» lowers the size down to half to fit, then ends the last line with «…»;
- QR: whole dots per module and the 4-module light field inside the box, as big as the box's shorter side allows,
  centred vertically, placed by `align` horizontally; fewer than 2 dots a module → ValueError;
- Code128 (set B): bars the full height of the box, whole dots per module (0.25 mm at most, less when the box is
  narrow) and the 10-module light fields inside the box, placed by `align`; fewer than 2 dots a module → ValueError;
  a ГРН set B cannot carry (Cyrillic) leaves the box empty (`skipped`);
- line: the box filled (a zero side is drawn `STROKE_MM` thick); box: an outline `STROKE_MM` thick."""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw

from .code128 import QUIET_MODULES as CODE128_QUIET_MODULES, encode_code128
from .layout import (
    MONO_BOLD, QR_QUIET_MODULES, SANS, SANS_SEMIBOLD, LabelLayout, font, js_round, qr_matrix, text_width, wrap_text,
)
from .settings import PrintSettings
from .template import Element, LabelContent, LabelTemplate

# IBM Plex (Sans and Mono): capitals are 0.698 em; the ink of «Й» rises to 0.952 em, descenders reach 0.2 em.
CAP_HEIGHT_EM = 0.698
INK_TOP_EM = 0.96
INK_BOTTOM_EM = 0.21
SHRINK_FLOOR = 0.5
MIN_FONT_DOTS = 6
STROKE_MM = 0.3
MIN_MODULE_DOTS = 2
BAR_MODULE_MM = 0.25


def font_dots(font_mm: float, dots_per_mm: float) -> int:
    """The font size in dots whose capitals are `font_mm` high."""
    return max(MIN_FONT_DOTS, js_round(font_mm * dots_per_mm / CAP_HEIGHT_EM))


def line_metrics(size: int) -> tuple[int, int]:
    """(dots from a line's top to its baseline, dots from one line's top to the next)."""
    top = math.ceil(INK_TOP_EM * size)
    return top, top + math.ceil(INK_BOTTOM_EM * size)


def _face(element: Element) -> str:
    if element.mono:
        return MONO_BOLD  # the only monospace face shipped
    return SANS_SEMIBOLD if element.bold else SANS


def _lines_fit(lines: list[str], limit: int, width: int, face) -> bool:
    return len(lines) <= limit and all(text_width(line, face) <= width for line in lines)


def _set_text(text: str, element: Element, width: int, height: int, dots_per_mm: float):
    """(lines, size, overflow): the text broken into the box's lines at the size it gets."""
    name = _face(element)
    base = font_dots(element.font_mm, dots_per_mm)

    def capacity(size: int) -> int:
        return max(1, min(element.max_lines, height // line_metrics(size)[1]))

    def fits(size: int) -> bool:
        face = font(name, size)
        if height < line_metrics(size)[1]:
            return False
        # A word broken by letters does not count as fitting: shrink it first.
        if any(text_width(word, face) > width for word in text.split()):
            return False
        return _lines_fit(wrap_text(text, width, face, 10_000), capacity(size), width, face)

    size = base
    if element.fit == "shrink" and not fits(base):
        floor = max(MIN_FONT_DOTS, math.ceil(base * SHRINK_FLOOR))
        low, high = floor, base - 1  # the biggest size that fits, or the floor
        size = floor
        while low <= high:
            middle = (low + high) // 2
            if fits(middle):
                size, low = middle, middle + 1
            else:
                high = middle - 1
    face = font(name, size)
    lines = wrap_text(text, width, face, capacity(size))
    pitch = line_metrics(size)[1]
    overflow = len(lines) * pitch > height or any(text_width(line, face) > width for line in lines)
    return lines, size, overflow


class _Canvas:
    def __init__(self, width: int, height: int, dots_per_mm: float):
        self.width, self.height, self.dots_per_mm = width, height, dots_per_mm
        self.image = Image.new("L", (width, height), 255)
        self.draw = ImageDraw.Draw(self.image)

    def box(self, element: Element) -> tuple[int, int, int, int]:
        """The element's box in dots, [x0, x1) × [y0, y1), cut to the label."""
        scale = self.dots_per_mm
        x0, y0 = min(self.width, js_round(element.x_mm * scale)), min(self.height, js_round(element.y_mm * scale))
        x1 = min(self.width, js_round((element.x_mm + element.w_mm) * scale))
        y1 = min(self.height, js_round((element.y_mm + element.h_mm) * scale))
        return x0, y0, x1, y1

    def fill(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Black over [x0, x1) × [y0, y1)."""
        if x1 > x0 and y1 > y0:
            self.draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=0)

    def paste_darker(self, piece: Image.Image, x0: int, y0: int) -> None:
        area = (x0, y0, x0 + piece.width, y0 + piece.height)
        self.image.paste(ImageChops.darker(self.image.crop(area), piece), area)


def _offset(align: str, room: int, used: int) -> int:
    if align == "center":
        return max(0, (room - used) // 2)
    if align == "right":
        return max(0, room - used)
    return 0


def _draw_text(canvas: _Canvas, element: Element, text: str, texts: list[str], overflow: list[str]) -> None:
    x0, y0, x1, y1 = canvas.box(element)
    width, height = x1 - x0, y1 - y0
    if not text.strip() or width <= 0 or height <= 0:
        return
    lines, size, cut = _set_text(" ".join(text.split()), element, width, height, canvas.dots_per_mm)
    if cut:
        overflow.append(element.id)
    face = font(_face(element), size)
    top, pitch = line_metrics(size)
    # Drawn on the element's own piece: whatever does not fit is cut at the box's edge.
    piece = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(piece)
    for index, line in enumerate(lines):
        if not line:
            continue
        x = _offset(element.align, width, text_width(line, face))
        draw.text((x, top + index * pitch), line, font=face, fill=0, anchor="ls")
        texts.append(line)
    canvas.paste_darker(piece, x0, y0)


def _draw_qr(canvas: _Canvas, element: Element, code: str) -> tuple[int, int]:
    x0, y0, x1, y1 = canvas.box(element)
    matrix = qr_matrix(code)
    size = len(matrix)
    module = min(x1 - x0, y1 - y0) // (size + 2 * QR_QUIET_MODULES)
    if module < MIN_MODULE_DOTS:
        raise ValueError(f"QR-код не помещается в элемент «{element.id}»: увеличьте его")
    side = module * (size + 2 * QR_QUIET_MODULES)
    left = x0 + _offset(element.align, x1 - x0, side) + QR_QUIET_MODULES * module
    upper = y0 + _offset("center", y1 - y0, side) + QR_QUIET_MODULES * module
    for row, cells in enumerate(matrix):
        for column, dark in enumerate(cells):
            if dark:
                x, y = left + column * module, upper + row * module
                canvas.fill(x, y, x + module, y + module)
    return module, size


def _draw_code128(canvas: _Canvas, element: Element, code: str) -> int | None:
    """Dots per module; None — set B cannot carry this ГРН, the box stays empty."""
    try:
        bars = encode_code128(code)
    except ValueError:
        return None
    x0, y0, x1, y1 = canvas.box(element)
    modules = sum(bars) + 2 * CODE128_QUIET_MODULES
    # A quarter-millimetre module (proven on the shop's scanners), narrower when the box is: the box is the most the
    # code may take, a short ГРН leaves it partly empty.
    module = min(js_round(BAR_MODULE_MM * canvas.dots_per_mm), (x1 - x0) // modules)
    if module < MIN_MODULE_DOTS:
        raise ValueError(f"Штрихкод не помещается в элемент «{element.id}»: расширьте его или выберите QR")
    x = x0 + _offset(element.align, x1 - x0, modules * module) + CODE128_QUIET_MODULES * module
    for index, bar in enumerate(bars):
        if index % 2 == 0:  # even — bars, odd — the spaces between them
            canvas.fill(x, y0, x + bar * module, y1)
        x += bar * module
    return module


def _draw_rule(canvas: _Canvas, element: Element) -> None:
    x0, y0, x1, y1 = canvas.box(element)
    stroke = max(1, js_round(STROKE_MM * canvas.dots_per_mm))
    if element.kind == "line":
        canvas.fill(x0, y0, max(x1, x0 + stroke), max(y1, y0 + stroke))
        return
    for left, top, right, bottom in (
        (x0, y0, x1, y0 + stroke), (x0, y1 - stroke, x1, y1), (x0, y0, x0 + stroke, y1), (x1 - stroke, y0, x1, y1),
    ):
        canvas.fill(max(left, x0), max(top, y0), min(right, x1), min(bottom, y1))


def render(template: LabelTemplate, content: LabelContent, settings: PrintSettings) -> LabelLayout:
    """The label in printer dots, at the settings' size and dpi. ValueError — a code does not fit its element."""
    dots_per_mm = settings.dpi / 25.4
    canvas = _Canvas(js_round(settings.width_mm * dots_per_mm), js_round(settings.height_mm * dots_per_mm), dots_per_mm)
    texts: list[str] = []
    overflow: list[str] = []
    skipped: list[str] = []
    qr_module_dots = qr_modules = bar_module_dots = None
    for element in template.elements:
        if element.kind == "field":
            _draw_text(canvas, element, content.value(element.field or ""), texts, overflow)
        elif element.kind == "text":
            _draw_text(canvas, element, content.substitute(element.text or ""), texts, overflow)
        elif element.kind == "qr":
            module, size = _draw_qr(canvas, element, content.code)
            if qr_module_dots is None:
                qr_module_dots, qr_modules = module, size
        elif element.kind == "code128":
            module = _draw_code128(canvas, element, content.code)
            if module is None:
                skipped.append(element.id)
            elif bar_module_dots is None:
                bar_module_dots = module
        else:
            _draw_rule(canvas, element)
    # Threshold in the middle: the label is black and white, the smoothed edges of letters become a dot or not.
    image = canvas.image.point(lambda value: 0 if value < 128 else 255).convert("1", dither=Image.Dither.NONE)
    return LabelLayout(
        image=image, width=canvas.width, height=canvas.height, qr_module_dots=qr_module_dots, qr_modules=qr_modules,
        bar_module_dots=bar_module_dots, texts=tuple(texts), overflow=tuple(overflow), skipped=tuple(skipped),
    )
