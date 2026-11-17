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
    assert "№ ADP-ss-26-034" in layout.texts
    assert "Поверка до 17.11.2027" in layout.texts
    assert layout.qr_modules and layout.qr_module_dots >= 2
    assert not layout.overflow and not layout.skipped
    assert layout.bar_module_dots is None
    boxes = [(e.x_mm, e.y_mm, e.x_mm + e.w_mm, e.y_mm + e.h_mm) for e in template.elements]
    for index, a in enumerate(boxes):
        for b in boxes[index + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
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
