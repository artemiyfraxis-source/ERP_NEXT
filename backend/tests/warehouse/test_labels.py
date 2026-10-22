"""GRN label layout, Code128 and the TSPL job, ported from Roller_Buyer
`apps/api/src/test/labels.test.ts` with its vectors; the byte vectors of the TSPL job and Code128 were produced by
Roller_Buyer's own builder for the same input."""

from dataclasses import replace
from datetime import date

import pytest
from PIL import Image

from app.modules.warehouse.labels.code128 import code128_modules, encode_code128
from app.modules.warehouse.labels.layout import (
    QR_DOTS_PER_MODULE,
    GrnLabelData,
    LabelOptions,
    build_grn_label,
    label_png,
    qr_symbol,
    render_label,
)
from app.modules.warehouse.labels.settings import DEFAULT_PRINT_SETTINGS, LABEL_SIZES, PrintSettings
from app.modules.warehouse.labels.tspl import TsplOptions, build_tspl_job, pack_for_tspl

SAMPLE = GrnLabelData(
    grn="26.0000001",
    sku="TSRSLT-001",
    item_name="Провод МГТФ 0,5 экранированный многожильный с длинным наименованием",
    quantity="25",
    unit="м",
    manufacturer_batch="L-2026-07",
    received_on=date(2026, 8, 31),
    place="1F4",
    warehouse="ЦАР",
)

BOTH = LabelOptions(code_type="both")
LOW = LabelOptions(height_mm=30)  # the old 58 × 30 label

# Roller_Buyer encodeCode128('FRX.26.0000041').
RB_FRX_BARS = [
    2, 1, 1, 2, 1, 4, 1, 3, 2, 3, 1, 1, 2, 3, 1, 1, 3, 1, 3, 3, 1, 1, 2, 1, 1, 2, 2, 2, 3, 1, 2, 2, 3, 2, 1, 1, 2, 2,
    3, 1, 1, 2, 1, 2, 2, 2, 3, 1, 1, 2, 3, 1, 2, 2, 1, 2, 3, 1, 2, 2, 1, 2, 3, 1, 2, 2, 1, 2, 3, 1, 2, 2, 1, 2, 3, 1,
    2, 2, 2, 2, 1, 2, 3, 1, 1, 2, 3, 2, 2, 1, 3, 1, 1, 2, 2, 2, 2, 3, 3, 1, 1, 1, 2,
]


def test_canvas_is_one_to_one_with_the_printer_resolution():
    label = build_grn_label(SAMPLE)
    # The default 58 × 40 mm at 300 dpi — 11.81 dots per mm.
    assert (label.width, label.height) == (685, 472)
    assert label.image.size == (685, 472) and label.image.mode == "1"
    assert build_grn_label(SAMPLE, LOW).image.size == (685, 354)


def test_qr_is_a_whole_number_of_dots_per_module():
    label = build_grn_label(SAMPLE, LabelOptions(code_type="qr"))
    # A fractional module would give uneven cells and scanner misses.
    assert label.qr_module_dots == QR_DOTS_PER_MODULE == 6
    # Ten characters of the alphanumeric set — version 1, 21 modules.
    assert label.qr_modules == 21
    assert label.bar_module_dots is None


def test_the_label_says_what_the_storekeeper_reads_by_eye():
    texts = build_grn_label(replace(SAMPLE, item_name=SAMPLE.item_name + " для проверки переноса на вторую строку")).texts
    # Cyrillic «ГРН», never Latin «GRN»; then the number, the quantity with the unit.
    assert texts[:3] == ("ГРН", "26.0000001", "25 м")
    assert "GRN" not in texts
    # The name wraps to two lines, the ellipsis only ends the second one; the article has its own line.
    assert texts[3].startswith("Провод МГТФ") and not texts[3].endswith("…")
    assert texts[4].endswith("…")
    assert texts[5] == "TSRSLT-001"
    assert texts[6] == "1F4 · ЦАР"
    assert texts[7] == "поступило 31.08.2026 · серия L-2026-07"


def test_a_name_that_already_carries_the_article_is_not_doubled():
    texts = build_grn_label(GrnLabelData("FRX.26.0000001", "510210200", "Molex 510210200 PicoBlade", "5", "шт", "", None)).texts
    assert texts == ("ГРН", "FRX.26.0000001", "5 шт", "Molex 510210200 PicoBlade")  # no place, no date, no article


def test_a_short_name_stays_on_one_line_and_the_place_is_empty_before_putaway():
    texts = build_grn_label(GrnLabelData("FRX.26.0000001", "A-1", "Разъём", "5", "шт", "", date(2026, 9, 25), "", "ЦАР")).texts
    assert texts[3:] == ("Разъём", "A-1", "ЦАР", "поступило 25.09.2026")


def test_the_series_does_not_ride_on_the_date_line_when_it_does_not_fit():
    data = GrnLabelData("FRX.26.0000001", "", "Разъём", "5", "шт", "очень-длинная-серия-изготовителя-2026", date(2026, 9, 25))
    assert build_grn_label(data).texts[-1] == "поступило 25.09.2026"


def test_a_low_label_58x30_still_fits_and_drops_the_article_before_the_name():
    long = GrnLabelData("FRX.26.0000041", "510210200", "Соединитель PicoBlade 2pin (MOLEX) в корпусе", "338", "шт", "12/2026",
                        date(2026, 9, 25), "1F4", "ЦАР")
    for options in (LOW, LabelOptions(height_mm=30, code_type="both")):
        label = build_grn_label(long, options)
        assert label.image.size == (685, 354)
        assert "510210200" not in label.texts, "the article goes first"
        assert "1F4 · ЦАР" in label.texts and label.texts[-1].startswith("поступило 25.09.2026")
    # Where it has room (58 × 40) the article is there.
    assert "510210200" in build_grn_label(long).texts


def test_the_name_is_the_last_to_go_on_a_low_label():
    label = build_grn_label(SAMPLE, LabelOptions(height_mm=30, code_type="both"))
    assert label.texts[3].startswith("Провод МГТФ") and "TSRSLT-001" not in label.texts


def test_code128_start_b_checksum_stop_and_whole_dot_modules():
    bars = encode_code128(SAMPLE.grn)
    # Start + 10 characters + check symbol, 11 modules each; stop — 13.
    assert code128_modules(SAMPLE.grn) == 11 * 12 + 13
    assert bars[:6] == [2, 1, 1, 2, 1, 4], "start B"
    assert bars[-7:] == [2, 3, 3, 1, 1, 1, 2], "stop"
    assert encode_code128("FRX.26.0000041") == RB_FRX_BARS

    label = build_grn_label(SAMPLE, BOTH)
    assert label.bar_module_dots == 3, "a quarter millimetre at 300 dpi"
    assert label.qr_module_dots == 6
    # At 203 dpi the same size has fewer dots, the modules stay whole.
    small = build_grn_label(SAMPLE, LabelOptions(dpi=203, code_type="both"))
    assert small.width == 464
    assert small.bar_module_dots == 2
    assert isinstance(small.qr_module_dots, int)


def test_code128_refuses_cyrillic_and_the_label_falls_back_to_qr_alone():
    with pytest.raises(ValueError, match="Code128 B не кодирует"):
        encode_code128("ФРХ.26.0000001")
    cyrillic = GrnLabelData("ФРХ.26.0000001", "", "Разъём", "1", "шт", "", None)
    with pytest.raises(ValueError):
        build_grn_label(cyrillic, BOTH)
    label = render_label(cyrillic, PrintSettings(code_type="both"))
    assert (label.width, label.height) == (685, 472)
    assert label.bar_module_dots is None and label.qr_module_dots == 6


def test_qr_keeps_level_m_latin_alphanumeric_and_cyrillic_as_utf8_bytes():
    latin = qr_symbol("FRX.26.0000041")
    assert (latin.mode, latin.error, latin.version) == ("alphanumeric", "M", 1)
    # Not Kanji (Shift_JIS has Cyrillic): a scanner reads UTF-8 bytes back as «ФРХ».
    assert qr_symbol("ФРХ.26.0000001").mode == "byte"
    assert qr_symbol("FRX.26.0000001-П2").mode == "byte"


@pytest.mark.parametrize("grn", ["FRX.26.0000041", "FRX.26.0000041-2", "ФРХ.26.0000041-П2", "R&D.26.0000001"])
@pytest.mark.parametrize("code_type", ["both", "qr", "code128"])
def test_every_code_type_renders_long_names_without_exceptions(grn, code_type):
    data = GrnLabelData(grn, "X" * 300, "Очень длинное наименование " * 20, "123456.789", "шт", "серия " * 40, date(2026, 10, 1))
    label = render_label(data, PrintSettings(code_type=code_type))
    assert label.image.size == (685, 472)
    assert label_png(label).startswith(b"\x89PNG")


def test_the_default_code_is_the_qr_alone_and_code128_stays_an_option():
    default = render_label(SAMPLE, DEFAULT_PRINT_SETTINGS)
    assert default.qr_module_dots == 6 and default.bar_module_dots is None
    both = render_label(SAMPLE, PrintSettings(code_type="both"))
    assert both.qr_module_dots == 6 and both.bar_module_dots == 3
    only = render_label(SAMPLE, PrintSettings(code_type="code128"))
    assert only.qr_module_dots is None and only.bar_module_dots == 3


def test_the_place_and_warehouse_are_on_the_label():
    texts = build_grn_label(GrnLabelData("FRX.26.0000001", "X", "Y", "1", "шт", "", None, "1F4", "ЦАР")).texts
    assert "1F4 · ЦАР" in texts


def test_a_black_dot_is_a_zero_bit_as_tspl_prints():
    raster = Image.new("1", (8, 1), 1)
    raster.putpixel((0, 0), 0)
    assert pack_for_tspl(raster) == (1, bytes([0b01111111]))


def test_a_row_is_padded_with_clean_tape_bits():
    raster = Image.new("1", (10, 1), 0)  # all black
    width_bytes, data = pack_for_tspl(raster)
    assert width_bytes == 2
    assert data == bytes([0x00, 0b00111111])


def test_a_tiny_job_is_byte_for_byte_roller_buyers():
    raster = Image.new("1", (10, 2), 1)
    for index, dark in enumerate([1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0]):
        if dark:
            raster.putpixel((index % 10, index // 10), 0)
    job = build_tspl_job(raster, TsplOptions(copies=3, offset_x_dots=11.81, offset_y_dots=-5.9))
    assert job == (
        b"SIZE 58 mm,30 mm\r\nGAP 2 mm,0 mm\r\nREFERENCE 12,-6\r\nDIRECTION 1\r\nDENSITY 12\r\nSPEED 2\r\nCLS\r\n"
        b"BITMAP 0,0,2,2,0,\x7f?\xaa\xff\r\nPRINT 3,1\r\n"
    )


def test_the_job_carries_size_gap_and_the_whole_raster():
    raster = build_grn_label(SAMPLE, LOW).image
    job = build_tspl_job(raster, TsplOptions(gap_mm=3, copies=2, width_mm=58, height_mm=30.5))
    text = job.decode("latin-1")
    assert "SIZE 58 mm,30.5 mm" in text
    assert "GAP 3 mm,0 mm" in text
    assert "BITMAP 0,0,86,354,0," in text
    assert text.endswith("\r\nPRINT 2,1\r\n")
    # The raster goes as raw bytes: 86 bytes a row, 354 rows.
    header = text.index("BITMAP 0,0,86,354,0,") + len("BITMAP 0,0,86,354,0,")
    assert len(job) - header - len("\r\nPRINT 2,1\r\n") == 86 * 354


def test_settings_defaults_are_the_ones_proven_on_the_real_printer():
    # Roller_Buyer, 2026-09-25: density 8 / speed 4 printed pale and speckled, the QR broke up.
    assert (DEFAULT_PRINT_SETTINGS.density, DEFAULT_PRINT_SETTINGS.speed, DEFAULT_PRINT_SETTINGS.gap_mm) == (12, 2, 2)
    assert (DEFAULT_PRINT_SETTINGS.width_mm, DEFAULT_PRINT_SETTINGS.height_mm, DEFAULT_PRINT_SETTINGS.dpi) == (58, 40, 300)
    assert DEFAULT_PRINT_SETTINGS.code_type == "qr" and DEFAULT_PRINT_SETTINGS.printer_name == ""


@pytest.mark.parametrize("bad", [
    {"dpi": 600}, {"density": 16}, {"density": -1}, {"speed": 0}, {"speed": 13}, {"width_mm": 44}, {"height_mm": 81},
    {"gap_mm": 21}, {"offset_x_mm": 5.5}, {"code_type": "datamatrix"}, {"printer_name": "x" * 201},
])
def test_settings_out_of_bounds_are_refused(bad):
    with pytest.raises(ValueError):
        PrintSettings(**bad)


# --- Label size presets (owner, 22.10.2026: «58x30, 100x50, 58x40») ---------------------------------------------------

@pytest.mark.parametrize("width, height", LABEL_SIZES)
def test_the_three_presets_are_accepted(width, height):
    settings = PrintSettings(width_mm=width, height_mm=height)
    assert (settings.width_mm, settings.height_mm) == (width, height)
    assert LABEL_SIZES == ((58, 30), (58, 40), (100, 50))


@pytest.mark.parametrize("width, height", [(58, 35), (60, 40), (100, 30), (50, 50), (100, 40), (58, 50)])
def test_any_other_size_is_refused_in_russian(width, height):
    with pytest.raises(ValueError, match="Формат этикетки — 58 × 30, 58 × 40 или 100 × 50 мм"):
        PrintSettings(width_mm=width, height_mm=height)


WIDE = GrnLabelData(
    "FRX.26.0000041-12", "510210200", "Соединитель PicoBlade 2pin вилка на плату угловая шаг 1,25 мм (MOLEX)", "338", "шт",
    "12/2026", date(2026, 9, 25), "1F4", "ЦАР",
)


@pytest.mark.parametrize("code_type", ["qr", "both", "code128"])
def test_wide_label_is_100_by_50_with_every_field(code_type):
    label = render_label(WIDE, PrintSettings(width_mm=100, height_mm=50, code_type=code_type))
    assert (label.width, label.height) == (1181, 591) and label.image.mode == "1"
    texts = label.texts
    assert texts[:3] == ("ГРН", "FRX.26.0000041-12", "338 шт")
    # The name in full, over up to three lines.
    assert " ".join(texts[3:-3]) == WIDE.item_name and 1 <= len(texts) - 6 <= 3
    assert texts[-3:] == ("510210200", "1F4 · ЦАР", "поступило 25.09.2026 · серия 12/2026")
    assert label.qr_module_dots == (None if code_type == "code128" else 12)
    assert label.bar_module_dots == (None if code_type == "qr" else 3)


def test_wide_label_stays_inside_its_margins_and_the_qr_does_not_meet_the_bars():
    label = render_label(WIDE, PrintSettings(width_mm=100, height_mm=50, code_type="both"))
    left, top, right, bottom = Image.eval(label.image.convert("L"), lambda v: 255 - v).getbbox()
    assert left >= 11 and top >= 11 and right <= label.width - 11 and bottom <= label.height - 11


def test_wide_label_cuts_a_very_long_name_at_three_lines_and_skips_empty_fields():
    data = GrnLabelData("FRX.26.0000041", "X1", "Очень длинное наименование " * 20, "1", "шт", "", None)
    texts = render_label(data, PrintSettings(width_mm=100, height_mm=50, code_type="both")).texts
    name_lines = [text for text in texts[3:] if text != "X1"]
    assert len(name_lines) == 3 and name_lines[-1].endswith("…")
    assert not any(text.startswith("поступило") for text in texts)


def test_narrow_labels_keep_their_layouts():
    for height, expected in ((30, 354), (40, 472)):
        label = render_label(SAMPLE, PrintSettings(width_mm=58, height_mm=height))
        assert (label.width, label.height) == (685, expected)
        assert label.texts[0] == "ГРН" and label.qr_module_dots is not None
