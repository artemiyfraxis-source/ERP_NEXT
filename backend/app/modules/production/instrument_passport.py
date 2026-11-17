"""Authenticated on-demand instrument passport; chart scales never follow the measured extrema."""

from datetime import datetime
import math
from pathlib import Path

from fpdf import FPDF

from .instrument_characterization import Characterization, connector_line, impedance_warning, loss_db, number, upper_two
from .rf_interfaces import RfDetails
from .instrument_public import PublicMeasurement, PublicPassport, conclusion, control_line, public_fields, public_measurement_fields
from .traceability import MeasurementInstrument
from .vna_files import VSWR_CEILING

VSWR_SCALE = (1.0, 2.0, 0.1)
LOSS_SCALE = (-3.0, 0.0, 0.3)
FREQUENCY_LIMIT_GHZ = 18.0
FONTS = Path(__file__).resolve().parents[2] / "assets" / "fonts"


class _Passport(FPDF):
    generated: datetime
    card_url: str

    def footer(self):
        self.set_y(-20)
        self.set_font("Plex", size=7)
        self.set_text_color(60)
        self.multi_cell(0, 3.5, f"Fraxis ERP · {self.generated:%d.%m.%Y %H:%M} · Методика: — · Стр. {self.page_no()}",
                        new_x="LMARGIN", new_y="NEXT")
        if self.card_url:
            self.multi_cell(0, 3, self.card_url, link=self.card_url, new_x="LMARGIN", new_y="NEXT")


def _line(pdf, text: str, *, bold=False, size=9):
    pdf.set_font("Plex", "B" if bold else "", size)
    pdf.set_text_color(0)
    pdf.multi_cell(0, 4.8, text, new_x="LMARGIN", new_y="NEXT")


def band_rows(measurement: Characterization | PublicMeasurement):
    start, stop = measurement.band
    edges = [start] + [edge for edge in (2, 6, 11, 18) if start < edge < stop] + [stop]
    for low, high in zip(edges, edges[1:]):
        values = []
        for name in ("S11", "S22", "S21"):
            points = [v for f, v in measurement.points(name) if low * 1e9 <= f <= high * 1e9]
            if not points:
                values.append("—")
            else:
                maximum = max(loss_db(v) for v in points) if name == "S21" else max(points)
                values.append(f"≥ {number(VSWR_CEILING)}" if name != "S21" and maximum >= VSWR_CEILING
                              else upper_two(maximum) if math.isfinite(maximum) else "∞")
        yield number(low) + "–" + number(high), *values


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


def _chart(pdf, measurement: Characterization | PublicMeasurement, reflection: bool):
    if pdf.get_y() + 67 > pdf.h - 23:
        pdf.add_page()
    title = "КСВН · сторона 1 (синяя), сторона 2 (красная)" if reflection else "Передача S21, дБ"
    _line(pdf, title, bold=True)
    left, top, width, height = 23, pdf.get_y() + 2, 172, 43
    minimum, maximum, step = VSWR_SCALE if reflection else LOSS_SCALE
    stop = min(FREQUENCY_LIMIT_GHZ, measurement.band[1])
    stop = stop if stop > 0 else FREQUENCY_LIMIT_GHZ
    pdf.set_font("Plex", size=7)
    pdf.set_line_width(0.1)
    for index in range(11):
        y = top + index * height / 10
        value = maximum - index * step
        pdf.set_draw_color(205)
        pdf.line(left, y, left + width, y)
        pdf.set_text_color(40)
        pdf.text(13, y + 1, number(round(value, 1)))
    for index in range(7):
        x = left + index * width / 6
        pdf.set_draw_color(205)
        pdf.line(x, top, x, top + height)
        pdf.text(x - 2, top + height + 4, number(round(stop * index / 6, 2)))
    pdf.text(left + width - 5, top + height + 8, "ГГц")
    outside = False
    names = ("S11", "S22") if reflection else ("S21",)
    for name, color in zip(names, ((25, 80, 190), (190, 45, 35))):
        raw = measurement.points(name)
        points = raw if reflection else tuple((f, 20 * math.log10(v) if v > 0 else -math.inf) for f, v in raw)
        outside |= any(v < minimum or v > maximum for _, v in points)
        pdf.set_draw_color(*color)
        pdf.set_line_width(0.35)
        last = None
        for frequency, value in _envelope(points, stop):
            x = left + frequency / (stop * 1e9) * width
            y = top + (maximum - min(maximum, max(minimum, value))) / (maximum - minimum) * height
            if last is not None:
                pdf.line(*last, x, y)
            last = x, y
    pdf.set_y(top + height + 10)
    if outside:
        _line(pdf, "Значения вне шкалы показаны на её границе; максимумы — в таблице.", size=7)


def _new_pdf(generated: datetime, card_url: str) -> _Passport:
    pdf = _Passport(format="A4")
    pdf.generated, pdf.card_url = generated, card_url
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(True, margin=24)
    pdf.add_font("Plex", fname=str(FONTS / "DejaVuSans.ttf"))
    pdf.add_font("Plex", "B", fname=str(FONTS / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    return pdf


def draw_measurement(pdf: FPDF, measurement: Characterization | PublicMeasurement):
    rows = [("Диапазон, ГГц", "КСВН 1, макс.", "КСВН 2, макс.", "Потери, дБ, макс."),
            *band_rows(measurement)]
    row_height = 6
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
    _chart(pdf, measurement, True)
    _chart(pdf, measurement, False)


def public_passport_pdf(passport: PublicPassport, *, generated: datetime, public_url: str = "") -> bytes:
    """The renderer accepts only the public projection, never an instrument or attachment."""
    pdf = _new_pdf(generated, public_url)
    _line(pdf, f"Паспорт {passport.inventory_number}", bold=True, size=17)
    _line(pdf, passport.name, bold=True, size=11)
    for label, value in public_fields(passport):
        _line(pdf, f"{label}: {value}")
    if passport.impedance_warning:
        _line(pdf, passport.impedance_warning, bold=True)
    if passport.measurement:
        pdf.ln(3)
        _line(pdf, "Заключение", bold=True, size=12)
        verdict, warning = conclusion(passport.measurement)
        _line(pdf, verdict, bold=True, size=11)
        if warning:
            _line(pdf, warning, bold=True)
        _line(pdf, control_line(passport), size=8)
        if passport.control_overdue:
            _line(pdf, "Требуется повторный замер", bold=True)
        if public_url:
            pdf.set_font("Plex", size=9)
            pdf.set_text_color(0, 100, 90)
            pdf.cell(0, 7, "Скачать .s2p", link=public_url + "/measurement.s2p", new_x="LMARGIN", new_y="NEXT")
    if passport.measurement is None:
        pdf.ln(6)
        _line(pdf, "Замер не проведён", bold=True, size=14)
    else:
        pdf.ln(2)
        _line(pdf, "Результаты замера", bold=True, size=12)
        for label, value in public_measurement_fields(passport.measurement):
            _line(pdf, f"{label}: {value}")
        draw_measurement(pdf, passport.measurement)
    if passport.history:
        _line(pdf, "История контроля", bold=True, size=12)
        for previous in passport.history:
            band = previous.band.band_line if previous.band else "Не годен для 50 Ом тракта"
            _line(pdf, f"{previous.measured_on:%d.%m.%Y} · {band}", size=8)
    return bytes(pdf.output())


def passport_pdf(item: MeasurementInstrument, rf: RfDetails, measurement: Characterization | None,
                 *, generated: datetime, card_url: str = "") -> bytes:
    pdf = _new_pdf(generated, card_url)
    _line(pdf, f"Паспорт {item.inventory_number}", bold=True, size=17)
    _line(pdf, item.name, bold=True, size=11)
    for label, value in (("Номер", item.serial_number), ("Модель / артикул", item.doc_designation),
                         ("Изготовитель", item.manufacturer), ("Хранение", item.storage_location)):
        _line(pdf, f"{label}: {value or '—'}")
    _line(pdf, "Разъёмы: " + connector_line(item.name, item.serial_number, rf))
    for index, port in enumerate(rf.interfaces, 1):
        _line(pdf, f"Сторона {index}: {port.series}; корпус: {port.body}; контакт: {port.center}; " +
              (f"{number(port.impedance_ohm)} Ом" if port.impedance_ohm is not None else "сопротивление не указано"))
    if not rf.interfaces:
        _line(pdf, "Сопротивление: не указано")
    if warning := impedance_warning(rf):
        _line(pdf, warning, bold=True)
    low_limit = f"{number(rf.f_min_ghz)}–" if rf.f_min_ghz is not None else "до "
    limits = [f"сторона {index}: {low_limit}{number(port.f_max_ghz)} ГГц ({port.f_source or 'источник не указан'})"
              for index, port in enumerate(rf.interfaces, 1) if port.f_max_ghz is not None]
    if limits:
        _line(pdf, "Пределы по карточке: " + "; ".join(limits), size=8)
    elif rf.f_min_ghz is not None:
        _line(pdf, f"Пределы по карточке: от {number(rf.f_min_ghz)} ГГц; верхняя частота не указана", size=8)
    if rf.source:
        _line(pdf, "Источник ВЧ-данных: " + rf.source, size=8)
    if measurement is None:
        pdf.ln(6)
        _line(pdf, "Замер не проведён", bold=True, size=14)
    else:
        pdf.ln(2)
        _line(pdf, "Результаты замера", bold=True, size=12)
        _line(pdf, f"Дата: {measurement.measured_on:%d.%m.%Y}; оператор: {measurement.operator or '—'}")
        _line(pdf, f"Файл: {measurement.file_name}; опорное сопротивление: 50 Ом", size=8)
        # Preserve recorded instrument, calibration, conditions and de-embedding verbatim: no invented metadata.
        if measurement.comments:
            _line(pdf, "Условия и оборудование (заголовок файла):", bold=True, size=8)
            for comment in measurement.comments:
                _line(pdf, comment, size=7)
        else:
            _line(pdf, "ВАЦ / калибровка / условия / цепь: в заголовке не указаны", size=8)
        draw_measurement(pdf, measurement)
    return bytes(pdf.output())
