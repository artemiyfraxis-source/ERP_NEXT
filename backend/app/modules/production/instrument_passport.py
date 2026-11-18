"""Authenticated on-demand instrument passport. Short by owner decision 18.11: one value per line, no sources or URLs
in the text (they stay in rf-norms.json and the card), charts scaled to the data and the norm."""

from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path

from fpdf import FPDF

from .instrument_characterization import STEPS_GHZ, Characterization, loss_db, number, step_stops, upper_two
from .instrument_norms import Norm, norms_data
from .instrument_serials import article_of
from .rf_interfaces import RfDetails
from .instrument_public import (PublicMeasurement, PublicPassport, conclusion, control_line, measurement_line, public_fields,
                                public_passport)
from .traceability import MeasurementInstrument
from .vna_files import VSWR_CEILING

# Hard bounds of the autoscale: beyond them a value is drawn on the edge and the note says so.
VSWR_CAP = 2.0
LOSS_CAP_DB = -3.0
VSWR_STEPS = (0.005, 0.01, 0.02, 0.05, 0.1, 0.2)
LOSS_STEPS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5)
FREQUENCY_LIMIT_GHZ = 18.0
FONTS = Path(__file__).resolve().parents[2] / "assets" / "fonts"
SIDE_COLORS = ((25, 80, 190), (190, 45, 35))
NORM_COLOR = (200, 30, 30)


def _decimal(value: float, step: float) -> str:
    digits = 3 if step < 0.01 else 2 if step < 0.1 else 1
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "−")


@dataclass(frozen=True)
class ChartScale:
    minimum: float
    maximum: float
    step: float
    stop_ghz: float
    limit: tuple[tuple[float, float], ...]   # the norm as (GHz, value) on the chart's axis; () without a norm
    limit_label: str
    label_above: bool = True   # the label sits on the side of the line away from the measured curve at the right end

    @property
    def ticks(self) -> tuple[float, ...]:
        count = round((self.maximum - self.minimum) / self.step)
        return tuple(self.minimum + index * self.step for index in range(count + 1))

    @property
    def band_edges(self) -> tuple[float, ...]:
        """Light verticals at «1 / 3 / 6 / 12 / 18 ГГц» inside the chart."""
        return tuple(edge for edge in STEPS_GHZ if edge < self.stop_ghz - 0.05)

    def label(self, value: float) -> str:
        return _decimal(value, self.step)


def _nice(span: float, steps: tuple[float, ...]) -> float:
    return next((step for step in steps if span / step <= 10 + 1e-9), steps[-1])


def _values(measurement, reflection: bool, stop: float) -> list[float]:
    names = ("S11", "S22") if reflection else ("S21",)
    values = [value if reflection else (20 * math.log10(value) if value > 0 else -math.inf)
              for name in names for frequency, value in measurement.points(name) if frequency <= stop * 1e9 + 0.5]
    return [value for value in values if math.isfinite(value)]


def _norm_trace(norm: Norm | None, reflection: bool, stop: float) -> tuple[tuple[tuple[float, float], ...], str]:
    if norm is None:
        return (), ""
    limit, segments = (norm.limit, norm.segments) if reflection else \
        ((norm.loss.limit, norm.loss.segments) if norm.loss and norm.loss.segments else (None, ()))
    if limit is None:
        return (), ""
    start = norm.from_ghz if reflection else norm.loss.from_ghz
    edges = sorted({stop * index / 200 for index in range(201)} | {s.to_ghz for s in segments if s.to_ghz < stop}
                   | ({start} if 0 < start < stop else set()))
    points = []
    for ghz in edges:
        value = limit(ghz)
        if value is not None:
            points.append((ghz, value if reflection else -value))
    if not points:
        return (), ""
    first = next(s for s in segments if points[0][0] <= s.to_ghz + 1e-9)
    label = f"норма ≤ {first.text}" if reflection else f"норма потерь ≤ {first.text} дБ"
    return tuple(points), label


def _above(measurement, reflection: bool, limit) -> bool:
    if not limit:
        return True
    ghz, value = limit[-1]
    names = ("S11", "S22") if reflection else ("S21",)
    near = [v for name in names for f, v in measurement.points(name) if f <= ghz * 1e9 + 0.5][-len(names):]
    if not reflection:
        near = [20 * math.log10(v) if v > 0 else -math.inf for v in near]
    return not near or max(near) < value


def chart_scale(measurement, reflection: bool, norm: Norm | None = None) -> ChartScale:
    """VSWR from 1,00 to ~1,1× the larger of the worst measured value and the norm; loss from 0 to ~1,2× the deeper of
    the worst measured loss and the norm. Nice steps, at most ten divisions."""
    stop = min(FREQUENCY_LIMIT_GHZ, measurement.band[1])
    stop = stop if stop > 0 else FREQUENCY_LIMIT_GHZ
    values = _values(measurement, reflection, stop)
    limit, label = _norm_trace(norm, reflection, stop)
    if reflection:
        worst = max([1.0, *values, *(value for _, value in limit)])
        span = min(VSWR_CAP, 1 + (worst - 1) * 1.1) - 1 or 0.05
        step = _nice(span, VSWR_STEPS)
        maximum = min(VSWR_CAP, 1 + math.ceil(span / step - 1e-9) * step)
        return ChartScale(1.0, round(maximum, 4), step, stop, limit, label, _above(measurement, reflection, limit))
    worst = min([0.0, *values, *(value for _, value in limit)])
    span = min(-LOSS_CAP_DB, -worst * 1.2) or 0.05
    step = _nice(span, LOSS_STEPS)
    minimum = max(LOSS_CAP_DB, -math.ceil(span / step - 1e-9) * step)
    return ChartScale(round(minimum, 4), 0.0, step, stop, limit, label, _above(measurement, reflection, limit))


class _Passport(FPDF):
    generated: datetime
    card_url: str

    def footer(self):
        self.set_y(-18)
        self.set_font("Plex", size=7)
        self.set_text_color(60)
        method = norms_data()["method"]["code"]
        self.multi_cell(0, 3.5, f"Fraxis ERP · {self.generated:%d.%m.%Y %H:%M} · Методика: {method} · Стр. {self.page_no()}",
                        new_x="LMARGIN", new_y="NEXT", align="L")
        if self.card_url:
            self.multi_cell(0, 3, self.card_url, link=self.card_url, new_x="LMARGIN", new_y="NEXT", align="L")


def _line(pdf, text: str, *, bold=False, size=9, height=4.8):
    pdf.set_font("Plex", "B" if bold else "", size)
    pdf.set_text_color(0)
    # Left-aligned: fpdf2 justifies multi_cell by default, which stretched long lines into wide gaps.
    pdf.multi_cell(0, height, text, new_x="LMARGIN", new_y="NEXT", align="L")


def band_rows(measurement: Characterization | PublicMeasurement):
    """The label's bands «до 1 / 3 / 6 / 12 / 18 ГГц», cumulative from the lowest measured frequency."""
    start, stop = measurement.band
    for high in (edge for edge in step_stops(stop) if edge > start):
        values = []
        for name in ("S11", "S22", "S21"):
            points = [v for f, v in measurement.points(name) if f <= high * 1e9 + 0.5]
            if not points:
                values.append("—")
            else:
                maximum = max(loss_db(v) for v in points) if name == "S21" else max(points)
                values.append(f"≥ {number(VSWR_CEILING)}" if name != "S21" and maximum >= VSWR_CEILING
                              else upper_two(maximum) if math.isfinite(maximum) else "∞")
        yield number(start) + "–" + number(high), *values


def _envelope(points, stop: float, limit=600):
    """Bound PDF size while retaining every visible bucket's min/max and their order."""
    buckets = {}
    for frequency, value in points:
        if frequency > stop * 1e9:
            continue
        key = min(limit - 1, int(frequency / (stop * 1e9) * limit))
        pair = buckets.setdefault(key, [(frequency, value), (frequency, value)])
        if value < pair[0][1]:
            pair[0] = frequency, value
        if value > pair[1][1]:
            pair[1] = frequency, value
    return [point for pair in buckets.values() for point in sorted(set(pair))]


def _chart(pdf, measurement: Characterization | PublicMeasurement, reflection: bool, norm: Norm | None):
    if pdf.get_y() + 50 > pdf.h - 22:
        pdf.add_page()
    title = "КСВН · сторона 1 (синяя), сторона 2 (красная)" if reflection else "Передача S21, дБ"
    _line(pdf, title, bold=True)
    scale = chart_scale(measurement, reflection, norm)
    left, top, width, height = 23, pdf.get_y() + 1.5, 172, 34
    minimum, maximum, stop = scale.minimum, scale.maximum, scale.stop_ghz

    def y_of(value):
        return top + (maximum - min(maximum, max(minimum, value))) / (maximum - minimum) * height

    def x_of(ghz):
        return left + ghz / stop * width

    pdf.set_font("Plex", size=7)
    pdf.set_line_width(0.1)
    pdf.set_draw_color(215)
    for value in scale.ticks:
        pdf.line(left, y_of(value), left + width, y_of(value))
        pdf.set_text_color(40)
        pdf.text(11, y_of(value) + 1, scale.label(value))
    pdf.set_draw_color(225)
    pdf.line(left, top, left, top + height)
    pdf.line(left + width, top, left + width, top + height)
    for edge in scale.band_edges:
        pdf.line(x_of(edge), top, x_of(edge), top + height)
    for edge in (0.0, *scale.band_edges, stop):
        text = number(round(edge, 2))
        pdf.text(x_of(edge) - pdf.get_string_width(text) / 2, top + height + 4, text)
    pdf.text(left + width - 5, top + height + 8, "ГГц")
    if scale.limit:
        pdf.set_draw_color(*NORM_COLOR)
        pdf.set_line_width(0.3)
        pdf.set_dash_pattern(dash=1.2, gap=0.9)
        # One polyline keeps the dash pattern continuous (separate short lines would each restart it).
        pdf.polyline([(x_of(f), y_of(v)) for f, v in scale.limit])
        pdf.set_dash_pattern()
        pdf.set_text_color(*NORM_COLOR)
        label_width = pdf.get_string_width(scale.limit_label) + 1
        ys = [y_of(v) for f, v in scale.limit if x_of(f) >= x_of(scale.limit[-1][0]) - label_width]
        pdf.text(x_of(scale.limit[-1][0]) - label_width, min(ys) - 1.2 if scale.label_above else max(ys) + 3,
                 scale.limit_label)
    outside = False
    names = ("S11", "S22") if reflection else ("S21",)
    for name, color in zip(names, SIDE_COLORS):
        raw = measurement.points(name)
        points = raw if reflection else tuple((f, 20 * math.log10(v) if v > 0 else -math.inf) for f, v in raw)
        outside |= any(v < minimum or v > maximum for f, v in points if f <= stop * 1e9 + 0.5)
        pdf.set_draw_color(*color)
        pdf.set_line_width(0.35)
        last = None
        for frequency, value in _envelope(points, stop):
            x, y = x_of(frequency / 1e9), y_of(value)
            if last is not None:
                pdf.line(*last, x, y)
            last = x, y
    pdf.set_text_color(0)
    pdf.set_y(top + height + 9)
    if outside:
        _line(pdf, "Значения вне шкалы показаны на её границе; максимумы — в таблице.", size=7)


def _new_pdf(generated: datetime, card_url: str) -> _Passport:
    pdf = _Passport(format="A4")
    pdf.generated, pdf.card_url = generated, card_url
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(True, margin=22)
    pdf.add_font("Plex", fname=str(FONTS / "DejaVuSans.ttf"))
    pdf.add_font("Plex", "B", fname=str(FONTS / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    return pdf


def draw_measurement(pdf: FPDF, measurement: Characterization | PublicMeasurement, norm: Norm | None = None):
    rows = [("Полоса, ГГц", "КСВН 1, макс.", "КСВН 2, макс.", "Потери, дБ, макс."),
            *band_rows(measurement)]
    row_height = 5.5
    if pdf.get_y() + len(rows) * row_height > pdf.h - pdf.b_margin:
        pdf.add_page()
    pdf.set_draw_color(180)
    pdf.set_line_width(0.15)
    for index, row in enumerate(rows):
        pdf.set_font("Plex", "B" if index == 0 else "", 8)
        pdf.set_text_color(0)
        for column, (width, value) in enumerate(zip((44, 44, 44, 54), row)):
            pdf.cell(width, row_height, value, border="B", align="L" if column == 0 else "R",
                     new_x="RIGHT", new_y="TOP")
        pdf.ln(row_height)
    pdf.ln(2)
    _chart(pdf, measurement, True, norm)
    _chart(pdf, measurement, False, norm)


def _draw_conclusion(pdf: FPDF, passport: PublicPassport):
    result = conclusion(passport)
    if result is None:
        return
    pdf.ln(3)
    _line(pdf, result.title, bold=True, size=12, height=6)
    _line(pdf, result.verdict, bold=True, size=11, height=5.5)
    if result.warning:
        _line(pdf, result.warning, bold=True)
    for label, value in result.lines:
        _line(pdf, f"{label}: {value}" if label else value)
    _line(pdf, control_line(passport))
    if passport.control_overdue:
        _line(pdf, "Требуется повторный замер", bold=True)
    pdf.ln(1)
    for text in result.closing:
        _line(pdf, text)


def _draw_results(pdf: FPDF, passport: PublicPassport, measurement, operator: str = ""):
    pdf.ln(3)
    _line(pdf, "Результаты замера", bold=True, size=12, height=6)
    _line(pdf, measurement_line(passport, operator))
    pdf.ln(1)
    draw_measurement(pdf, measurement, passport.norm_check.norm if passport.norm_check else None)


def public_passport_pdf(passport: PublicPassport, *, generated: datetime, public_url: str = "") -> bytes:
    """The renderer accepts only the public projection, never an instrument or attachment."""
    pdf = _new_pdf(generated, public_url)
    _line(pdf, f"Паспорт {passport.inventory_number}", bold=True, size=17, height=8)
    _line(pdf, passport.name, bold=True, size=11, height=6)
    for label, value in public_fields(passport):
        _line(pdf, f"{label}: {value}")
    if passport.impedance_warning:
        _line(pdf, passport.impedance_warning, bold=True)
    if passport.measurement:
        _draw_conclusion(pdf, passport)
        if public_url:
            pdf.set_font("Plex", size=9)
            pdf.set_text_color(0, 100, 90)
            pdf.cell(0, 7, "Скачать .s2p", link=public_url + "/measurement.s2p", new_x="LMARGIN", new_y="NEXT")
        _draw_results(pdf, passport, passport.measurement)
    else:
        pdf.ln(6)
        _line(pdf, "Замер не проведён", bold=True, size=14)
    if passport.history:
        _line(pdf, "История контроля", bold=True, size=12, height=6)
        for previous in passport.history:
            _line(pdf, f"{previous.measured_on:%d.%m.%Y} · {previous.summary}", size=8)
    return bytes(pdf.output())


def range_line(rf: RfDetails) -> str:
    """«0–32 ГГц» from the card: the lower edge and the lowest upper edge of the two ports."""
    tops = [port.f_max_ghz for port in rf.interfaces if port.f_max_ghz is not None]
    if not tops:
        return f"от {number(rf.f_min_ghz)} ГГц" if rf.f_min_ghz is not None else ""
    return f"{number(rf.f_min_ghz or 0)}–{number(min(tops))} ГГц"


def header_fields(item: MeasurementInstrument, rf: RfDetails, projection: PublicPassport) -> tuple[tuple[str, str], ...]:
    """One value per line: serial, designation, model, maker, connectors, impedance, range, temperatures, resource."""
    model = article_of(item) or ""
    rows = [("Заводской №", item.serial_number), ("Обозначение", item.doc_designation),
            ("Модель", model if model != item.doc_designation else ""), ("Изготовитель", projection.manufacturer),
            ("Разъёмы", projection.connectors), ("Сопротивление", projection.impedance), ("Диапазон", range_line(rf))]
    spec = projection.spec
    if spec and spec.temperature_line:
        rows.append(("Рабочие температуры", spec.temperature_line))
    if spec and spec.mating_resource:
        rows.append(("Ресурс сочленений", str(spec.mating_resource)))
    return tuple((label, value) for label, value in rows if value)


def passport_pdf(item: MeasurementInstrument, rf: RfDetails, measurement: Characterization | None,
                 *, generated: datetime, card_url: str = "", registry=(), repeats=()) -> bytes:
    pdf = _new_pdf(generated, card_url)
    projection = public_passport(item, rf, measurement, registry=registry, repeats=repeats)
    _line(pdf, f"Паспорт {item.inventory_number}", bold=True, size=17, height=8)
    _line(pdf, item.name, bold=True, size=11, height=6)
    for label, value in header_fields(item, rf, projection):
        _line(pdf, f"{label}: {value}")
    if projection.impedance_warning:
        _line(pdf, projection.impedance_warning, bold=True)
    if measurement is None:
        pdf.ln(6)
        _line(pdf, "Замер не проведён", bold=True, size=14)
    else:
        _draw_conclusion(pdf, projection)
        _draw_results(pdf, projection, measurement, measurement.operator)
    return bytes(pdf.output())
