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
    if kind == "instrument":
        return LabelTemplate.model_validate({"elements": _instrument(width, height)})
    build = _wide if width >= 90 else _narrow
    return LabelTemplate.model_validate({"elements": build(kind, width, height, code_type)})


def _instrument(width: float, height: float) -> list[dict]:
    """Logo over the QR on the left; number, serial, connectors, frequency range and date on the right; across the bottom a
    monospace table of round VSWR / loss limits per classic band (до 1 / 3 / 6 / 12 / 18 ГГц …).

    The QR includes its quiet zone and fits an HTTPS passport link at two dots/module at 203 dpi.
    """
    large = width >= 90
    compact = height <= 30
    if large:
        return _instrument_wide(width, height)
    return _instrument_narrow(width, height)


def _instrument_narrow(width: float, height: float) -> list[dict]:
    """58 × 30 / 58 × 40 — the GOST-style identification card squeezed into the shop's roll (owner 18.11): logo over the
    QR on the left; number, kind, model, connectors, maker, serial and frequency range on the right; the measured limits
    table with titles across the bottom; measurement date and next control in the last line. 300 dpi keeps 1 mm type."""
    tall = height >= 40
    k = 1.25 if tall else 1.0
    side = 18.0 if tall else 14.0
    logo_h = round(side * 82 / 292 + 0.05, 1)
    elements = [{"id": "logo", "kind": "logo", "x_mm": MARGIN, "y_mm": MARGIN, "w_mm": side, "h_mm": logo_h, "align": "left"}]
    qr_top = round(MARGIN + logo_h + 0.5, 1)
    elements.append(_code("qr", "qr", MARGIN, qr_top, side, side))
    x = MARGIN + side + 1.0
    w = round(width - MARGIN - x, 1)
    rows, bottom = _stack(x, MARGIN, w, [
        (0, {"id": "inventory_number", "font_mm": round(3.2 * k, 1), "field": "inventory_number", "bold": True}),
        (0.2, {"id": "type_line", "font_mm": round(1.0 * k, 1), "field": "type_line", "bold": True, "fit": "ellipsis"}),
        (0.1, {"id": "model_line", "font_mm": round(1.8 * k, 1), "field": "model_line", "bold": True}),
        (0.1, {"id": "connector_line", "font_mm": round(1.4 * k, 1), "field": "connector_line", "bold": True}),
        (0.1, {"id": "manufacturer_line", "font_mm": round(1.1 * k, 1), "field": "manufacturer_line", "fit": "ellipsis"}),
        (0.1, {"id": "serial_full", "font_mm": round(1.3 * k, 1), "field": "serial_full", "bold": True}),
        (0.15, {"id": "range_line", "font_mm": round(1.2 * k, 1), "field": "range_line", "bold": True}),
    ])
    elements += rows
    left = 2.0  # clear of the printer's unprintable left edge
    table_top = round(max(qr_top + side, bottom) + 0.3, 1)
    font_t = round(1.0 * k, 1)
    side_w = round(15.0 * k, 1)  # dates on the right of the table rows
    table_w = round(width - left - MARGIN - side_w - 0.5, 1)
    table, _ = _stack(left, table_top, table_w, [
        (0, {"id": "table_bands_long", "font_mm": font_t, "field": "table_bands_long", "mono": True, "fit": "ellipsis"}),
        (0.1, {"id": "table_vswr_long", "font_mm": font_t, "field": "table_vswr_long", "mono": True, "fit": "ellipsis"}),
        (0.1, {"id": "table_loss_long", "font_mm": font_t, "field": "table_loss_long", "mono": True, "fit": "ellipsis"}),
    ])
    elements += table
    dates = []
    for row, field in zip(table, ("date_short", "next_short", "footer_line")):
        element = _text(field, round(width - MARGIN - side_w, 1), row["y_mm"], side_w, round(1.0 * k, 1), field=field)
        element["align"] = "right"
        dates.append(element)
    elements += dates
    return elements


def _instrument_unused(width: float, height: float) -> list[dict]:
    large, compact = width >= 90, height <= 30
    qr_side = 26.0 if large else (14.0 if compact else 18.0)
    logo_h = round(qr_side * 82 / 292 + 0.05, 1)  # the wordmark's proportions (292 × 82)
    elements = [{"id": "logo", "kind": "logo", "x_mm": MARGIN, "y_mm": MARGIN, "w_mm": qr_side, "h_mm": logo_h,
                 "align": "left"}]
    qr_top = round(MARGIN + logo_h + 0.6, 1)
    elements.append(_code("qr", "qr", MARGIN, qr_top, qr_side, qr_side))
    x = MARGIN + qr_side + 1.0
    rows, bottom = _stack(x, MARGIN, width - MARGIN - x, [
        # Owner 18.11: maker, name, serial — in that order under the inventory number.
        (0, {"id": "inventory_number", "font_mm": 5 if large else 3.5, "field": "inventory_number", "bold": True}),
        (0.1, {"id": "manufacturer", "font_mm": 1.8 if large else 1.3, "field": "manufacturer", "fit": "ellipsis"}),
        (0.1, {"id": "connector_line", "font_mm": 2.2 if large else 1.6, "field": "connector_line", "lines": 1 if compact else 2, "bold": True}),
        (0.1, {"id": "serial_number", "font_mm": 2 if large else 1.5, "field": "serial_number"}),
        (0.2, {"id": "range_line", "font_mm": 2.2 if large else 1.5, "field": "range_line", "bold": True}),
    ])
    elements += rows
    table_font = 2.0 if large else (1.1 if compact else 1.4)
    table_top = max(qr_top + qr_side, bottom) + 0.4
    left = 2.0  # clear of the printer's unprintable left edge
    side_w = 30.0 if large else 18.0  # date and verification/impedance notes on the right of the table rows
    table_w = round(width - left - MARGIN - side_w - 0.5, 1)
    table, _ = _stack(left, table_top, table_w, [
        (0, {"id": "table_bands", "font_mm": table_font, "field": "table_bands", "mono": True, "fit": "ellipsis"}),
        (0.1, {"id": "table_vswr", "font_mm": table_font, "field": "table_vswr", "mono": True, "fit": "ellipsis"}),
        (0.1, {"id": "table_loss", "font_mm": table_font, "field": "table_loss", "mono": True, "fit": "ellipsis"}),
    ])
    side_x = round(width - MARGIN - side_w, 1)
    side, _ = _stack(side_x, table_top, side_w, [
        (0, {"id": "measurement_date", "font_mm": 1.6 if large else 1.1, "field": "measurement_date"}),
        (0.1, {"id": "footer_line", "font_mm": 1.4 if large else 1.0, "field": "footer_line", "lines": 2}),
    ])
    for element in side:
        element["align"] = "right"
    elements += side
    elements += table
    return elements


def _instrument_wide(width: float, height: float) -> list[dict]:
    """100 × 50: a full identification card (owner 18.11, after the GOST-style draft): «Учётный №» + number and the
    wordmark; a rule; the kind, model, connectors, maker and serial with the QR on the right; the measured limits table
    with full row titles between rules; measurement date and next control at the bottom."""
    x0, right = 3.0, width - 3.0
    logo_w = 27.0
    elements = [
        _text("caption_number", x0, 2.4, 40, 1.3, text="УЧЁТНЫЙ №"),
        _text("inventory_number", x0, 5.0, 60, 5.0, field="inventory_number", bold=True),
        {"id": "logo", "kind": "logo", "x_mm": round(right - logo_w, 1), "y_mm": 2.6, "w_mm": logo_w,
         "h_mm": round(logo_w * 82 / 292 + 0.05, 1), "align": "right"},
        {"id": "rule_top", "kind": "line", "x_mm": x0, "y_mm": 14.0, "w_mm": round(right - x0, 1), "h_mm": 0},
    ]
    qr_side = 19.0
    text_w = round(right - qr_side - 2 - x0, 1)
    rows, bottom = _stack(x0, 15.2, text_w, [
        (0, {"id": "type_line", "font_mm": 1.5, "field": "type_line", "bold": True, "fit": "ellipsis"}),
        (0.2, {"id": "model_line", "font_mm": 2.9, "field": "model_line", "bold": True}),
        (0.2, {"id": "connector_line", "font_mm": 2.0, "field": "connector_line", "bold": True}),
        (0.3, {"id": "manufacturer_line", "font_mm": 1.6, "field": "manufacturer_line", "fit": "ellipsis"}),
        (0.2, {"id": "serial_full", "font_mm": 1.8, "field": "serial_full", "bold": True}),
    ])
    elements += rows
    elements.append(_code("qr", "qr", round(right - qr_side, 1), 15.2, qr_side, qr_side))
    top = round(max(bottom, 15.2 + qr_side) + 0.5, 1)
    elements.append(_text("range_line", x0, top, round(right - x0, 1), 1.5, field="range_line", bold=True))
    table_top = round(top + _height(1.5) + 0.2, 1)
    table, table_bottom = _stack(x0, table_top, round(right - x0, 1), [
        (0, {"id": "table_bands_long", "font_mm": 1.45, "field": "table_bands_long", "mono": True, "fit": "ellipsis"}),
        (0.4, {"id": "table_vswr_long", "font_mm": 1.45, "field": "table_vswr_long", "mono": True, "fit": "ellipsis"}),
        (0.1, {"id": "table_loss_long", "font_mm": 1.45, "field": "table_loss_long", "mono": True, "fit": "ellipsis"}),
    ])
    elements += table
    rule_mid = round(table[0]["y_mm"] + table[0]["h_mm"] + 0.15, 2)
    elements.append({"id": "rule_table", "kind": "line", "x_mm": x0, "y_mm": rule_mid, "w_mm": round(right - x0, 1), "h_mm": 0})
    footer_y = round(table_bottom + 0.4, 1)
    elements.append(_text("date_full", x0, footer_y, 45, 1.4, field="date_full"))
    footer = _text("footer_line", 50, footer_y, round(right - 50, 1), 1.3, field="footer_line")
    footer["align"] = "right"
    elements.append(footer)
    return elements
