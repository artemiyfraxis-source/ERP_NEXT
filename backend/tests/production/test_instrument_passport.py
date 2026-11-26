from dataclasses import replace
from datetime import datetime
from io import BytesIO
from uuid import uuid4

from PIL import Image
from pypdf import PdfReader
from pydantic import SecretStr
import pytest

from app.modules.production.instrument_characterization import application, characterization, connector_line, impedance_warning, upper_two
from app.modules.production.instrument_norms import norm_for
from app.modules.production.instrument_passport import band_rows, chart_scale, passport_pdf
from app.modules.production.rf_interfaces import RfDetails, RfInterface
from app.modules.production.traceability import InstrumentFile, InstrumentFileKind, InstrumentKind, InstrumentStatus, MeasurementInstrument
from app.modules.warehouse.labels.defaults import default_template
from app.modules.warehouse.labels.layout import label_png
from app.modules.warehouse.labels.render import render
from app.modules.warehouse.labels.settings import PrintSettings
from app.modules.warehouse.labels.template import FORMATS, instrument_content
from app.workflows.instrument_labels import band_cells
from app.shared.public_url import PublicSettings, instrument_card_url, public_passport_url


def measured_file(*, stop=18, resistance=50, name="Замер ВАЦ 16.11.26.s2p", header="! S2VNA Planar S50180 №123 2026-11-16T12:30:00\n! Калибровка: АКМ №456\n! Температура ВАЦ: 27 C; АКМ: 25 C\n! Цепь: de-embedding выполнен\n"):
    content = (header + f"# GHz S MA R {resistance}\n" +
               "0.01 0.01 0 0.99 0 0.99 0 0.02 0\n" +
               f"{stop} 0.02 0 0.98 0 0.98 0 0.025 0\n").encode()
    return InstrumentFile(uuid4(), uuid4(), InstrumentFileKind.OTHER, name, len(content), datetime(2026, 11, 17), uuid4(), "Иван", content)


def instrument():
    return MeasurementInstrument(id=uuid4(), version=1, inventory_number="СИ.034", name="Переходник SMA female–SMA female S-322",
        instrument_type="Переходник", verification_number=None, verified_at=None, verification_due=None,
        status=InstrumentStatus.ACTIVE, kind=InstrumentKind.ACCESSORY, serial_number="ADP-ss-26-034", manufacturer="Изготовитель", doc_designation="S-322")


def rf(impedance=50):
    # The norm is assigned per item (owner 18.11); these fixtures pin MIL-PRF-55339/55 explicitly for their numbers.
    return RfDetails(interfaces=(RfInterface("SMA", "розетка", "гнездо", impedance, 18),) * 2, role="adapter",
                     norm_id="mil-55339-55" if impedance == 50 else "")


def test_characterization_rounds_both_sides_up_and_uses_full_stored_band():
    result = characterization(measured_file())
    assert result.parameters_line == "КСВН ≤ 1,06 · 0,01–18 ГГц"
    assert result.loss_line == "Потери ≤ 0,18 дБ"
    assert result.measurement_date == "Замер 16.11.26"
    assert result.operator == "Иван"
    assert upper_two(1.050001) == "1,06"
    assert upper_two(1.05) == "1,05"


@pytest.mark.parametrize("name", ["Замер ВАЦ, СИ.009.s2p", "Замер ВАЦ СИ.009.s2p", "Замер ВАЦ_СИ.009.S2P", "Замер ВАЦ-СИ.009.s2p"])
def test_characterization_accepts_vna_measurement_file_names(name):
    assert characterization(measured_file(name=name)) is not None


@pytest.mark.parametrize("file", [
    measured_file(resistance=75), measured_file(name="datasheet.s2p"), measured_file(name="Замер ВАЦ.s1p"),
    replace(measured_file(), content=b"# GHz S MA R 50\n1 .1 0\n2 .1 0"),
    replace(measured_file(), content=b"broken"),
])
def test_only_valid_named_two_port_50_ohm_files_are_measurements(file):
    assert characterization(file) is None


def test_touchstone_default_reference_and_date_fallbacks():
    file = measured_file(header="", name="Замер ВАЦ 15.11.26.s2p")
    assert characterization(file).measurement_date == "Замер 15.11.26"
    file = replace(file, file_name="Замер ВАЦ адаптер.s2p", content=file.content.replace(b" R 50", b""))
    assert characterization(file).measurement_date == "Замер 17.11.26"


def test_capture_date_takes_precedence_over_earlier_calibration_date():
    file = measured_file(header="! Operator=private; calibration=2026-11-16T23:55:00\n! Raw=private; captured=2026-11-17T00:05:00+03:00; deembedding=T-matrix\n")
    assert characterization(file).measurement_date == "Замер 17.11.26"


@pytest.mark.parametrize("captured", ["2026-10-31T22:30:00+00:00", "2026-10-31T22:30:00Z",
                                       "2026-11-01T01:30:00+03:00", "2026-11-01T01:30:00"])
def test_capture_date_uses_moscow_day_and_preserves_naive_local_time(captured):
    file = measured_file(header=f"! calibration=2026-10-30T12:00:00; captured={captured}\n")
    assert characterization(file).measurement_date == "Замер 01.11.26"


def test_capture_date_outside_moscow_datetime_range_keeps_legacy_date_fallback():
    file = measured_file(header="! captured=9999-12-31T23:30:00+00:00\n")
    assert characterization(file).measured_on.isoformat() == "9999-12-31"


def test_explicit_offline_synthetic_marker_is_never_a_measurement():
    assert characterization(measured_file(header="! SYNTHETIC OFFLINE DATA - NOT A MEASUREMENT\n")) is None


def test_connector_sources_short_names_and_non_50_ohm_warning():
    assert connector_line("ignored", "", rf()) == "SMA розетка – SMA розетка"
    reverse = RfDetails(interfaces=(RfInterface("RP-SMA", "розетка", "штырь"), RfInterface("SMA", "розетка", "гнездо")))
    assert connector_line("", "", reverse) == "RP-SMA вилка – SMA розетка"
    assert connector_line("", "ADP-Ns-26-034", RfDetails()) == "N вилка – SMA розетка"
    assert connector_line("", "ADP-Ps-26-034", RfDetails()) == "RP-SMA вилка – SMA розетка"
    assert connector_line("", "ADP-ps-26-034", RfDetails()) == "RP-SMA розетка – SMA розетка"
    assert connector_line("Прямой ВЧ-переходник S-322", "", RfDetails()) == "S-322"
    assert impedance_warning(rf(75)) == "75 Ом — не для 50 Ом тракта"
    assert impedance_warning(rf()) == ""


@pytest.mark.parametrize("name", ["RP-SMA вилка – SMA розетка", "RP-SMA розетка – SMA вилка"])
def test_russian_reverse_polarity_connector_name_roundtrip(name):
    assert connector_line(name, "", RfDetails()) == name


def test_sma_reversed_contact_is_labelled_rp_sma():
    details = RfDetails(interfaces=(RfInterface("SMA", "вилка", "гнездо"), RfInterface("SMA", "розетка", "штырь")))
    assert connector_line("", "", details) == "RP-SMA розетка – RP-SMA вилка"


def test_passport_header_is_one_value_per_line_without_sources():
    details = replace(rf(), f_min_ghz=2, source="https://maker.example/datasheet.pdf",
                      interfaces=tuple(replace(p, f_source="https://maker.example/a.pdf") for p in rf().interfaces))
    pdf = passport_pdf(replace(instrument(), storage_location="Шкаф 1"), details, None, generated=datetime(2026, 11, 17))
    lines = [line.strip() for page in PdfReader(BytesIO(pdf)).pages for line in page.extract_text().splitlines()]
    for expected in ("Заводской №: ADP-ss-26-034", "Обозначение: S-322", "Изготовитель: Изготовитель",
                     "Разъёмы: SMA розетка – SMA розетка", "Сопротивление: 50 Ом", "Диапазон: 2–18 ГГц",
                     "Рабочие температуры: −65…+165 °C", "Ресурс сочленений: 500"):
        assert expected in lines
    text = " ".join(lines)
    for gone in ("maker.example", "Хранение", "Сторона 1", "Пределы по карточке", "Источник", "Номер:", "Модель / артикул"):
        assert gone not in text


@pytest.mark.parametrize("stop,impedance,measured", [(18, 50, True), (11, 50, True), (18, 75, True), (18, 50, False)])
def test_pdf_is_readable_with_serial_measured_unmeasured_limited_and_75_ohm(stop, impedance, measured):
    item = instrument()
    data = characterization(measured_file(stop=stop)) if measured else None
    pdf = passport_pdf(item, rf(impedance), data, generated=datetime(2026, 11, 17), card_url=instrument_card_url(item.inventory_number, "https://erp.example.test"))
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert item.serial_number in text and "Паспорт СИ.034" in text
    assert "Методика: СТО ФРАК-002-2026" in text and "erp.example.test" in text
    assert ("Результаты замера" in text) == measured
    assert ("Замер не проведён" in text) != measured
    if measured:
        assert "Замер 16.11.2026, Иван, ВАЦ Планар S50180, калибровка АКМ" in " ".join(text.split())
        for gone in ("de-embedding", "27 C", "опорное", "Условия", "ILAC", "Погрешность", "относятся только"):
            assert gone not in text
        rows = list(band_rows(data))
        assert [row[0] for row in rows] == ["0,01–1", "0,01–3", "0,01–6"] + (["0,01–12", "0,01–18"] if stop == 18 else ["0,01–11"])
        assert "Заключение" in text and "контроль характеристик" not in text
        flat = " ".join(text.split())
        assert ("Норма: MIL-PRF-55339/55 — КСВН ≤ 1,05 + 0,005·F в полосе 0,5–27 ГГц" in flat) == (impedance == 50)
        assert ("Норма не назначена" in flat) == (impedance == 75)
    if impedance == 75:
        assert "не для 50 Ом тракта" in text


def test_chart_scales_follow_the_data_and_the_norm():
    measured = characterization(measured_file())   # VSWR 1,02–1,04, |S21| 0,98–0,99
    norm = norm_for("", "", rf())                    # /55: 1,05 + 0,005·F from 0,5 GHz; loss 0,03·√F
    vswr = chart_scale(measured, True, norm)
    assert vswr.minimum == 1.0 and 1.14 <= vswr.maximum <= 1.2 and len(vswr.ticks) <= 11
    assert vswr.limit[0][0] == 0.5 and vswr.limit_label == "норма ≤ 1,05 + 0,005·F"
    assert vswr.band_edges == (1.0, 3.0, 6.0, 12.0)
    loss = chart_scale(measured, False, norm)
    assert loss.maximum == 0 and -0.3 <= loss.minimum <= -0.15 and loss.limit[-1][1] < 0
    bare = chart_scale(measured, True)
    assert bare.limit == () and bare.maximum <= 1.06


@pytest.mark.parametrize("label_format", FORMATS)
@pytest.mark.parametrize("impedance", [50, 75])
def test_measured_labels_use_real_link_and_show_parameters_at_203_dpi(label_format, impedance):
    item, measured = instrument(), characterization(measured_file(stop=11))
    usage = application(measured)  # no norm: all measured bands on the grid (the norm-based range: test_instrument_application)
    data = instrument_content(item.inventory_number, item.name, item.serial_number, model=item.doc_designation,
        accessory=True, in_house_serial=True, connector_line=connector_line(item.name, item.serial_number, rf()),
        range_line=usage.range_line, table=usage.table_lines(),
        table_long=usage.table_lines(long=True), measured_on=measured.measured_on,
        bands=band_cells(usage),
        measurement_date=measured.measurement_date, impedance_warning=impedance_warning(rf(impedance)),
        card_url=public_passport_url(item.id, PublicSettings(public_base_url="https://erp.example.test",
            passport_secret=SecretStr("test-only-passport-key-do-not-deploy-123456789"))))
    width, height = FORMATS[label_format]
    settings = PrintSettings(width_mm=width, height_mm=height, dpi=203)
    layout = render(default_template("instrument", label_format, "qr"), data, settings)
    assert not layout.overflow and layout.qr_module_dots >= 2
    joined = " ".join(layout.texts)
    # The 58 mm card states the range by its band grid («Частота до, ГГц»), the 100 × 50 one also in words.
    for value in (item.inventory_number, "ADP-ss-26-034", "КСВН ≤", "Потери, дБ ≤", "16.11.2026",
                  "Норма не назначена" if width >= 90
                  else "Частота до, ГГц"):
        assert value in joined
    if impedance == 75:
        assert "75 Ом — не для 50 Ом тракта" in joined
    assert Image.open(BytesIO(label_png(layout))).size == (round(width * 203 / 25.4), round(height * 203 / 25.4))


def test_two_line_connector_keeps_each_gender_with_its_series():
    data = instrument_content("СИ.009", "3.5mm розетка – 3.5mm розетка", accessory=True)
    layout = render(default_template("instrument", "58x30", "qr"), data, PrintSettings(width_mm=58, height_mm=30, dpi=203))
    # One line on 58×30 (shrunk if needed), never cut inside a connector name.
    assert "3.5mm розетка – 3.5mm розетка" in layout.texts


def test_saturated_vswr_is_a_lower_bound_not_a_false_upper_bound():
    file = measured_file()
    result = characterization(replace(file, content=file.content.replace(b"0.025 0", b"1 0")))
    assert "КСВН ≥ 999" in result.parameters_line
    assert list(band_rows(result))[-1][2] == "≥ 999"


def test_connector_overflow_never_ellipsizes_inside_a_word():
    name = "необыкновенно-длинное-наименование-разъёма " * 30
    data = instrument_content("СИ.034", name, accessory=True, connector_line=name)
    layout = render(default_template("instrument", "58x30", "qr"), data, PrintSettings(width_mm=58, height_mm=30, dpi=203))
    for line in layout.texts:
        if line.endswith("…"):
            assert line == "…" or line[:-1].split()[-1] in name.split()


@pytest.mark.parametrize("bad", ["http://erp.example.test", "https://user:pass@erp.example.test", "https://erp.example.test/?x=1", "https://erp.example.test/#x", "https://erp.example.test/path", "https://", "https://erp.example.test:bad"])
def test_public_base_url_validation(bad):
    with pytest.raises(ValueError):
        PublicSettings(public_base_url=bad)


def test_qr_url_configuration_and_inventory_fallback(monkeypatch):
    monkeypatch.delenv("ERP_PUBLIC_BASE_URL", raising=False)
    assert instrument_card_url("СИ.034") == ""
    assert instrument_content("СИ.034", "Переходник").code == "СИ.034"
    monkeypatch.setenv("ERP_PUBLIC_BASE_URL", "https://erp.example.test/")
    assert instrument_card_url("СИ.034") == "https://erp.example.test/?section=quality&tab=instruments&si=%D0%A1%D0%98.034"
