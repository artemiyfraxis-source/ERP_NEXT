from dataclasses import replace
from datetime import date
from io import BytesIO

from PIL import Image
import pytest

from app.modules.production.instrument_characterization import characterization, loss_db, working_band
from app.modules.production.instrument_public import public_passport
from app.modules.production.rf_interfaces import RfDetails
from app.modules.production.vna_files import VnaTrace
from app.modules.warehouse.labels.defaults import default_template
from app.modules.warehouse.labels.layout import label_png
from app.modules.warehouse.labels.render import render
from app.modules.warehouse.labels.settings import PrintSettings
from app.modules.warehouse.labels.template import FORMATS, instrument_content
from tests.production.test_instrument_passport import instrument, measured_file, rf


def summary(left=(1.1, 1.2, 1.3, 1.1), right=(1.1, 1.1, 1.1, 1.1)):
    frequencies = (0.01e9, 11.8e9, 12e9, 18e9)
    traces = tuple(VnaTrace(name, tuple(zip(frequencies, values))) for name, values in (
        ("S11", left), ("S22", right), ("S21", (.99, .98, .1, .01)), ("S12", (.99,) * 4)))
    return replace(characterization(measured_file()), traces=traces)


def test_resonance_stops_at_last_passing_point_and_excludes_out_of_band_loss():
    band = working_band(summary())
    assert (band.start_ghz, band.stop_ghz) == (.01, 11.8)
    assert band.max_loss_in_band == pytest.approx(loss_db(.98))
    assert band.band_line == "Рабочая полоса 0,01–11,8 ГГц"
    assert band.limits_line == "КСВН ≤ 1,2 · потери ≤ 0,18 дБ"


def test_flat_band_and_exact_threshold_include_last_point():
    band = working_band(summary(left=(1.2,) * 4, right=(1.2,) * 4))
    assert band.stop_ghz == 18
    assert band.max_loss_in_band == 40


@pytest.mark.parametrize("side", ["left", "right"])
def test_failing_first_point_has_no_working_band(side):
    assert working_band(summary(**{side: (1.21, 1.1, 1.1, 1.1)})) is None


def test_one_side_failure_cannot_be_hidden_by_good_other_side():
    assert working_band(summary(left=(1.1,) * 4, right=(1.1, 1.21, 1.1, 1.1))).stop_ghz == .01
    assert working_band(summary(), limit=1.1).limits_line.startswith("КСВН ≤ 1,1")


def test_next_control_is_calendar_year_and_leap_day_is_clamped():
    measured = replace(summary(), measured_on=date(2024, 2, 29))
    current = public_passport(instrument(), rf(), measured, today=date(2025, 2, 28))
    assert current.next_control == date(2025, 2, 28) and not current.control_overdue
    assert public_passport(instrument(), rf(), measured, today=date(2025, 3, 1)).control_overdue


def test_serial_connectors_can_supply_russian_title_without_rf_record():
    projected = public_passport(replace(instrument(), name="Переходник"), RfDetails(), None)
    assert projected.name == "Переходник SMA розетка – SMA розетка"


def test_unrepresentable_next_control_date_does_not_break_public_passport():
    from app.modules.production.instrument_public import control_line
    projected = public_passport(instrument(), rf(), replace(summary(), measured_on=date.max))
    assert projected.next_control is None
    assert control_line(projected) == "Замер: 31.12.9999"


@pytest.mark.parametrize("label_format", FORMATS)
@pytest.mark.parametrize("dpi", [203, 300])
def test_working_band_labels_fit_with_left_margin(label_format, dpi):
    band = working_band(summary())
    data = instrument_content("СИ.034", "Переходник", "ADP-ss-26-034", accessory=True,
        connector_line="SMA розетка – SMA розетка", parameters_line=band.band_line, loss_line=band.limits_line,
        measurement_date="Замер 16.11.26", impedance_warning="75 Ом — не для 50 Ом тракта")
    width, height = FORMATS[label_format]
    template = default_template("instrument", label_format, "qr")
    assert next(e for e in template.elements if e.id == "parameters_line").x_mm >= 2
    layout = render(template, data, PrintSettings(width_mm=width, height_mm=height, dpi=dpi))
    assert not layout.overflow
    assert "Рабочая полоса 0,01–11,8 ГГц" in layout.texts
    assert "КСВН ≤ 1,2 · потери ≤ 0,18 дБ" in layout.texts
    assert Image.open(BytesIO(label_png(layout))).size == (round(width * dpi / 25.4), round(height * dpi / 25.4))
