"""The built-in templates: what prints while nobody has saved a template of a kind × format — today's layouts redrawn as
templates (the hard-coded layout before 23.10.2026), so nothing changes for the warehouse until someone edits.

58 mm wide: the QR top left with «ГРН», the number and the quantity beside it; below, full width, the name (two lines
on 58 × 40, one on 58 × 30), the article (58 × 40), «место · склад» and the received date with the series. With
Code128 the bars run along the bottom; a low 58 × 30 label with bars takes everything 15 % smaller. 100 × 50: a large QR
on the left, the text stacked on the right, the name over up to three lines. The type of code follows the settings'
«Код на этикетке» — only for these built-in templates; a saved template carries its own codes.

A lot label (23.10.2026) has no quantity: the ARTICLE stands big and bold beside the QR under the ГРН (the storekeeper
searches by it); full width below it the name, the manufacturer, «место · склад» and the date with the series. The low
58 × 30 puts the manufacturer on the place line («JST · 1F4 · ЦАР»), the taller formats give it a line of its own.

Intake (an act of «Приёмка с производства») prints as today: «проект КБ» where a lot has its article, no series."""

from __future__ import annotations

import math

from .template import FORMATS, LabelTemplate

MARGIN = 1.0
CAP_TO_PITCH = 1.676  # a line of text takes 1.17 em, and capitals are 0.698 em (render.py)
SLACK_PER_LINE = 0.25  # whole-dot rounding of a line at 203 dpi


def _round_up(value: float) -> float:
    return round(math.ceil(value * 10 - 1e-6) / 10, 1)


def _height(font_mm: float, lines: int = 1) -> float:
    """A text box that holds `lines` lines of capitals `font_mm` high at both 300 and 203 dpi."""
    return _round_up(lines * (CAP_TO_PITCH * font_mm + SLACK_PER_LINE) + 0.05)


def _text(id_: str, x: float, y: float, w: float, font_mm: float, *, field: str | None = None, text: str | None = None,
          bold: bool = False, mono: bool = False, lines: int = 1, fit: str = "shrink") -> dict:
    element = {
        "id": id_, "kind": "field" if field else "text", "x_mm": round(x, 1), "y_mm": _round_up(y), "w_mm": round(w, 1),
        "h_mm": _height(round(font_mm, 1), lines), "font_mm": round(font_mm, 1), "bold": bold, "align": "left",
        "max_lines": lines, "fit": fit, "mono": mono,
    }
    element.update({"field": field} if field else {"text": text})
    return element


def _stack(x: float, y: float, w: float, rows: list[tuple[float, dict]]) -> tuple[list[dict], float]:
    """Rows top-down: (gap before, the row's settings) → the elements and the bottom of the last one."""
    elements = []
    for gap, row in rows:
        y += gap
        element = _text(row.pop("id"), x, y, w, row.pop("font_mm"), **row)
        elements.append(element)
        y = element["y_mm"] + element["h_mm"]
    return elements, y


def _code(id_: str, kind: str, x: float, y: float, w: float, h: float) -> dict:
    return {"id": id_, "kind": kind, "x_mm": x, "y_mm": y, "w_mm": w, "h_mm": h, "align": "left" if kind == "qr" else "center"}


def _lower_fields(kind: str) -> tuple[str, str]:
    """(the line under the name of the taller formats, the date line) of each kind."""
    return ("manufacturer", "received_series") if kind == "lot" else ("project_item", "received")


def _right_row(kind: str, narrow: bool) -> dict:
    """The row under the ГРН beside the QR: a lot's article (what the storekeeper searches by), an intake's quantity."""
    if kind == "lot":
        return {"id": "article", "font_mm": 3.0 if narrow else 4.0, "field": "article", "bold": True}
    return {"id": "quantity", "font_mm": 2.7 if narrow else 3.8, "field": "quantity", "bold": True}


def _narrow(kind: str, width: float, height: float, code_type: str) -> list[dict]:
    with_qr, with_bars = code_type != "code128", code_type != "qr"
    scale = 0.85 if height < 40 and with_bars else 1.0  # 58 × 30 with bars: today's second shrink step
    qr_side = 12.3 if scale < 1 else 14.8  # 5 / 6 dots a module at 300 dpi for a 21-module QR (a 14-character ГРН)
    elements: list[dict] = []
    if with_qr:
        elements.append(_code("qr", "qr", MARGIN, MARGIN, qr_side, qr_side))
    x = round(MARGIN + qr_side + 0.1, 2) if with_qr else MARGIN
    gap = 0.5 * scale
    top, bottom = _stack(x, 1.2 if scale == 1 else 1.0, width - MARGIN - x, [
        (0, {"id": "caption", "font_mm": 1.4 * scale, "text": "ГРН", "bold": True}),
        (gap, {"id": "grn", "font_mm": 3.2 * scale, "field": "grn", "mono": True, "bold": True}),
        (gap, _right_row(kind, True) | {"font_mm": _right_row(kind, True)["font_mm"] * scale}),
    ])
    elements += top
    if with_qr:
        bottom = max(bottom, MARGIN + qr_side)
    lot = kind == "lot"
    second, date_line = _lower_fields(kind)
    place = "place_warehouse" if height >= 40 or not lot else "manufacturer_place"
    rows: list[tuple[float, dict]] = [(0.6 * scale, {
        "id": "name", "font_mm": 1.8 * scale, "field": "name", "bold": True, "lines": 2 if height >= 40 else 1,
        "fit": "ellipsis",
    })]
    if height >= 40:
        extra = "manufacturer" if lot else second
        rows.append((0.4, {"id": extra, "font_mm": 1.5, "field": extra, "fit": "ellipsis"}))
    rows += [
        (0.4, {"id": place, "font_mm": 1.9 * scale, "field": place, "bold": True, "fit": "ellipsis"}),
        (0.4, {"id": date_line, "font_mm": round(1.5 * scale, 1), "field": date_line, "fit": "ellipsis"}),
    ]
    lower, _ = _stack(MARGIN, bottom, width - 2 * MARGIN, rows)
    elements += lower
    if with_bars:
        bar_height = round(5 * scale, 1)
        elements.append(_code("code128", "code128", MARGIN, round(height - MARGIN - bar_height, 2), width - 2 * MARGIN, bar_height))
    return elements


def _wide(kind: str, width: float, height: float, code_type: str) -> list[dict]:
    with_qr, with_bars = code_type != "code128", code_type != "qr"
    qr_side = 29.5  # 12 dots a module at 300 dpi
    bars_top = height - MARGIN - 5
    limit = bars_top - 0.5 if with_bars else height - MARGIN
    elements: list[dict] = []
    if with_qr:
        top = MARGIN + math.floor((limit - MARGIN - qr_side) / 2 * 10) / 10
        elements.append(_code("qr", "qr", MARGIN, round(top, 2), qr_side, qr_side))
    x = round(MARGIN + qr_side + 0.1, 2) if with_qr else MARGIN
    second, date_line = _lower_fields(kind)
    rows: list[tuple[float, dict]] = [
        (0, {"id": "caption", "font_mm": 1.8, "text": "ГРН"}),
        (0.2, {"id": "grn", "font_mm": 4.5, "field": "grn", "mono": True, "bold": True}),
        (0.5, _right_row(kind, False)),
        (0.5, {"id": "name", "font_mm": 2.1, "field": "name", "bold": True, "lines": 3, "fit": "ellipsis"}),
        (0.5, {"id": "manufacturer" if kind == "lot" else second, "font_mm": 1.8,
               "field": "manufacturer" if kind == "lot" else second, "fit": "ellipsis"}),
        (0.2, {"id": "place_warehouse", "font_mm": 1.8, "field": "place_warehouse", "fit": "ellipsis"}),
        (0.2, {"id": date_line, "font_mm": 1.8, "field": date_line, "fit": "ellipsis"}),
    ]
    used = sum(gap + _height(row["font_mm"], row.get("lines", 1)) for gap, row in rows)
    start = MARGIN + math.floor(max(0.0, limit - MARGIN - used) / 2 * 10) / 10
    stacked, _ = _stack(x, start, width - MARGIN - x, rows)
    elements += stacked
    if with_bars:
        elements.append(_code("code128", "code128", MARGIN, bars_top, width - 2 * MARGIN, 5.0))
    return elements


def default_template(kind: str, label_format: str, code_type: str) -> LabelTemplate:
    """The built-in template of a kind × format for the settings' type of code (qr | code128 | both)."""
    width, height = FORMATS[label_format]
    build = _wide if width >= 90 else _narrow
    return LabelTemplate.model_validate({"elements": build(kind, width, height, code_type)})
