"""Instrument content, raster output, designer templates and the new queue kind."""

import io
from datetime import date, datetime

import pytest
from PIL import Image
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.label_jobs import LabelJobRow, add_job
from app.modules.warehouse.labels.defaults import default_template
from app.modules.warehouse.labels.render import render
from app.modules.warehouse.labels.settings import PrintSettings
from app.modules.warehouse.labels.template import FORMATS, check_template, instrument_content
from app.modules.warehouse.labels_application import layout_in, sample_content, tspl_job
from app.shared.clock import MOSCOW
from tests.warehouse.test_api import warehouse_api  # noqa: F401


def content(**values):
    return instrument_content("СИ.034", "SMA розетка – SMA розетка", **values)


def test_serials_and_verification_have_domain_display_rules():
    data = content(serial_number="ADP-ss-26-034", in_house_serial=True, verification_due=date(2027, 11, 17), model="S-322")
    assert data.code == "СИ.034"
    assert data.value("inventory_number") == "СИ.034"
    assert data.value("name") == "SMA розетка – SMA розетка"
    assert data.value("model") == "S-322"
    assert data.value("serial_number") == "№ ADP-ss-26-034"
    assert data.value("verification_due") == "Поверка до 17.11.2027"
    assert content(serial_number="N8B139").value("serial_number") == "Зав. № N8B139"
    assert content().value("serial_number") == ""
    assert content().value("verification_due") == ""
    assert content(accessory=True, verification_due=date(2027, 11, 17)).value("verification_due") == ""


@pytest.mark.parametrize("label_format", FORMATS)
@pytest.mark.parametrize("dpi", [203, 300])
@pytest.mark.parametrize("code_type", ["qr", "both", "code128"])
def test_default_templates_always_render_cyrillic_inventory_qr_without_overflow(label_format, dpi, code_type):
    width, height = FORMATS[label_format]
    settings = PrintSettings(width_mm=width, height_mm=height, dpi=dpi, code_type=code_type)
    template = check_template(default_template("instrument", label_format, code_type), "instrument", label_format)
    layout = render(template, sample_content("instrument"), settings)
    assert "СИ.034" in layout.texts
    assert ("Заводской № S-322.1" if width >= 90 else "Зав. № S-322.1") in layout.texts
    assert "Поверка до 17.11.2027" in layout.texts
    assert layout.qr_modules and layout.qr_module_dots >= 2
    assert not layout.overflow and not layout.skipped
    assert layout.bar_module_dots is None
    # Hairline rules lie in the empty space between grid rows; every other box stands alone.
    boxes = [(e.x_mm, e.y_mm, e.x_mm + e.w_mm, e.y_mm + e.h_mm) for e in template.elements if e.kind != "line"]
    for index, a in enumerate(boxes):
        for b in boxes[index + 1:]:
            assert a[2] <= b[0] + 1e-9 or b[2] <= a[0] + 1e-9 or a[3] <= b[1] + 1e-9 or b[3] <= a[1] + 1e-9
    job = tspl_job(layout, settings, 1)
    assert job.startswith(f"SIZE {width:g} mm,{height:g} mm\r\n".encode())
    assert b"BITMAP " in job and job.endswith(b"PRINT 1,1\r\n")
    assert layout.image.getbbox() and layout.image.mode == "1"


def test_long_names_are_ellipsized_inside_their_boxes():
    settings = PrintSettings(width_mm=58, height_mm=30)
    data = instrument_content("СИ.034", "Очень длинное наименование разъёма " * 20,
                              serial_number="Очень-длинный-заводской-номер-" * 20)
    layout = render(default_template("instrument", "58x30", "qr"), data, settings)
    assert any(text.endswith("…") for text in layout.texts)
    assert layout.qr_modules
    assert not layout.overflow


SI009 = dict(serial_number="N8B140", model="ADP1A-35F-35F", accessory=True, manufacturer="Планар",
             type_line="Переход коаксиальный", model_line="ADP1A-35F-35F",
             connector_line="3.5mm розетка – 3.5mm розетка", measured_on=date(2026, 11, 16),
             next_control=date(2027, 11, 16),
             bands=(("1", "1,05", "0,05"), ("3", "1,05", "0,05"), ("6", "1,10", "0,10"), ("12", "1,10", "0,10"),
                    ("18", "1,15", "0,15")))


def _label(dpi=300, **values):
    data = instrument_content("СИ.009", "Переход коаксиальный ADP1A-35F-35F", **(SI009 | values))
    template = default_template("instrument", "58x30", "qr")
    return template, render(template, data, PrintSettings(width_mm=58, height_mm=30, dpi=dpi))


def _box(element, dpi=300):
    scale = dpi / 25.4
    return [round(v * scale) for v in (element.x_mm, element.y_mm, element.x_mm + element.w_mm, element.y_mm + element.h_mm)]


@pytest.mark.parametrize("dpi", [203, 300])
def test_58x30_label_is_pure_black_and_white_with_solid_type(dpi):
    template, layout = _label(dpi)
    assert layout.image.mode == "1"
    assert set(layout.image.convert("L").getdata()) <= {0, 255}  # no grey: a dot prints or not
    assert not layout.overflow
    texts = [e for e in template.elements if e.kind in ("field", "text")]
    # Thermal print (owner 18.11): one SemiBold sans face, capitals ≥ 1.6 mm, no thin monospace.
    assert all(e.bold and not e.mono and e.font_mm >= 1.6 for e in texts)
    # Strokes: almost no black run is a single dot wide.
    image = layout.image.convert("L")
    runs, single = 0, 0
    for y in range(image.height):
        row, length = [image.getpixel((x, y)) for x in range(image.width)] + [255], 0
        for value in row:
            if value == 0:
                length += 1
            elif length:
                runs, single, length = runs + 1, single + (length == 1), 0
    assert single / runs < 0.03


def test_58x30_band_grid_columns_line_up_under_the_frequencies():
    template, layout = _label()
    by_id = {e.id: e for e in template.elements}
    for n in range(1, 6):
        band, vswr, loss = by_id[f"table_band_{n}"], by_id[f"table_vswr_{n}"], by_id[f"table_loss_{n}"]
        assert band.align == vswr.align == loss.align == "right"
        assert band.x_mm == vswr.x_mm == loss.x_mm and band.w_mm == vswr.w_mm == loss.w_mm
    # The printed numbers end on the same column of dots as the frequency above them.
    image = layout.image.convert("L")

    def ink_right(element):
        x0, y0, x1, y1 = _box(element)
        columns = [x for x in range(x0, x1) if any(image.getpixel((x, y)) == 0 for y in range(y0 + 4, y1 - 2))]
        return max(columns)

    for n in range(1, 6):
        edges = [ink_right(by_id[f"{prefix}_{n}"]) for prefix in ("table_band", "table_vswr", "table_loss")]
        assert max(edges) - min(edges) <= 2, (n, edges)
    for value in ("Частота до, ГГц", "КСВН ≤", "Потери, дБ ≤", "18", "1,15", "0,15",
                  "Изм. 16.11.2026 · Контроль 16.11.2027"):
        assert value in layout.texts


def test_58x30_fewer_bands_pack_to_the_right_and_more_keep_the_full_range():
    four = instrument_content("СИ.034", "SMA", bands=(("1", "1,05", "0,05"), ("3", "1,10", "0,10"),
                                                      ("6", "1,15", "0,10"), ("12", "1,25", "0,50")))
    assert [four.value(f"table_band_{n}") for n in range(1, 6)] == ["", "1", "3", "6", "12"]
    seven = instrument_content("СИ.034", "SMA", bands=tuple((str(f), "1,05", "0,05") for f in (1, 3, 6, 12, 18, 26.5, 40)))
    assert [seven.value(f"table_band_{n}") for n in range(1, 6)] == ["1", "3", "6", "12", "40"]
    empty = instrument_content("СИ.034", "SMA", accessory=True)
    assert empty.value("table_band_head") == "" and empty.value("table_vswr_1") == ""
    assert empty.value("dates_line") == "Не измерен"


def test_58x30_maker_and_resource_rows_only_when_known_and_never_leave_a_hole():
    _template, layout = _label(manufacturer="")
    assert not any(text.startswith("Изготовитель") for text in layout.texts)
    data = instrument_content("СИ.034", "SMA", serial_number="S-322.1")
    assert [data.value(f"info_{n}") for n in (1, 2, 3)] == ["Зав. № S-322.1", "", ""]
    _template, layout = _label(mating_resource=3000, temp_min_c=-60, temp_max_c=85)
    assert "Изготовитель: Планар" in layout.texts and "Ресурс 3000 сочл. · −60…+85 °C" in layout.texts
    counted = instrument_content("СИ.009", "x", mating_resource=3000, mating_count=120, temp_min_c=-60, temp_max_c=85)
    assert counted.value("resource_line") == "Ресурс: осталось 2880 из 3000 · −60…+85 °C"
    assert instrument_content("СИ.009", "x").value("resource_line") == ""
    # All four known: the maker gives way, the verification stays.
    full = instrument_content("СИ.009", "x", "N8B140", date(2027, 11, 17), manufacturer="Планар", mating_resource=3000)
    assert [full.value(f"info_{n}") for n in (1, 2, 3)] == ["Зав. № N8B140", "Ресурс 3000 сочл.", "Поверка до 17.11.2027"]


def test_designer_supports_saved_instrument_template_and_queue_kind(warehouse_api):  # noqa: F811
    client, _lot = warehouse_api
    response = client.get("/api/stock/labels/templates/instrument")
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["kind"] == "instrument" and "inventory_number" in [item["key"] for item in view["fields"]]
    template = view["formats"]["58x30"]["template"]
    response = client.put("/api/stock/labels/templates/instrument/58x30", json={"template": template, "version": None})
    assert response.status_code == 200, response.text
    png = client.post("/api/stock/labels/templates/instrument/58x30/preview",
                      json={"template": template})
    assert png.status_code == 200, png.text
    assert Image.open(io.BytesIO(png.content)).size == (685, 354)
    with app.state.warehouse_uow_factory() as uow:
        layout = layout_in(uow.session, content(), PrintSettings(width_mm=58, height_mm=30))
        job_id = add_job(uow.session, tspl=tspl_job(layout, PrintSettings(width_mm=58, height_mm=30), 1), copies=1,
                         kind="instrument", lot_id=None, created_by="ОТК", now=datetime(2026, 11, 17, tzinfo=MOSCOW))
        uow.commit()
    with app.state.warehouse_uow_factory() as uow:
        row = uow.session.scalar(select(LabelJobRow).where(LabelJobRow.id == job_id))
        assert row.kind == "instrument" and row.lot_id is None
