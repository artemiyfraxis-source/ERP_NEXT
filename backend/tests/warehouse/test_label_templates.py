"""The label designer (owner 23.10.2026): the template model and its Russian refusals, the built-in templates of every
kind × format, the one renderer, the saved templates over HTTP (optimistic version, 409) and every print drawing the
saved template of the settings' format."""

import io
import re
from datetime import date

import pytest
from PIL import Image
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.label_jobs import LabelJobRow
from app.modules.warehouse.labels.defaults import default_template
from app.modules.warehouse.labels.layout import GrnLabelData
from app.modules.warehouse.labels.render import render
from app.modules.warehouse.labels.settings import PrintSettings
from app.modules.warehouse.labels.template import (
    FIELDS, FORMATS, LabelTemplate, TemplateError, check_template, intake_content, lot_content, parse_template,
)
from app.modules.warehouse.labels_application import SAMPLE, SAMPLE_INTAKE
from tests.view_access import as_viewer
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture

LOT_KEYS = ["grn", "quantity", "name", "article", "place", "warehouse", "place_warehouse", "manufacturer_place",
            "received", "series", "received_series", "manufacturer", "purpose"]
INTAKE_KEYS = ["grn", "quantity", "name", "project", "item_index", "project_item", "place", "warehouse",
               "place_warehouse", "received"]


def element(**values) -> dict:
    return {"id": "e1", "kind": "field", "field": "grn", "x_mm": 1, "y_mm": 1, "w_mm": 40, "h_mm": 8, **values}


def tpl(*elements: dict) -> dict:
    return {"elements": list(elements)}


def settings_for(label_format: str, **values) -> PrintSettings:
    width, height = FORMATS[label_format]
    return PrintSettings(width_mm=width, height_mm=height, **values)


# --- The model ---------------------------------------------------------------------------------------------------


def test_the_field_registry_is_the_contract():
    assert list(FIELDS["lot"]) == LOT_KEYS
    assert list(FIELDS["intake"]) == INTAKE_KEYS
    assert FIELDS["lot"]["grn"] == "ГРН" and FIELDS["intake"]["grn"] == "ГРН ГП"


def test_a_template_takes_its_defaults():
    parsed = parse_template(tpl(element()), "lot", "58x40").elements[0]
    assert (parsed.font_mm, parsed.bold, parsed.align, parsed.max_lines, parsed.fit, parsed.mono) == (
        3.0, False, "left", 1, "shrink", False)


@pytest.mark.parametrize("raw, message", [
    (tpl(element(colour="red")), "элемент 1 «e1», «colour»: неизвестное поле"),
    ({"elements": [element()], "name": "x"}, "«name»: неизвестное поле"),
    (tpl(element(font_mm=0.5)), "«высота букв»: не меньше 1"),
    (tpl(element(font_mm=25)), "«высота букв»: не больше 20"),
    (tpl(element(max_lines=5)), "«строк»: не больше 4"),
    (tpl(element(align="justify")), "«выравнивание»: недопустимое значение (допустимо: left, center, right)"),
    (tpl(element(kind="barcode")), "«тип»: недопустимое значение"),
    (tpl(element(x_mm=-1)), "«X»: не меньше 0"),
    (tpl(element(field=None)), "не выбрано поле"),
    (tpl(element(kind="text", text="  ")), "пустой текст"),
    (tpl(element(kind="box", h_mm=0)), "ширина и высота должны быть больше нуля"),
    (tpl(element(kind="line", w_mm=0, h_mm=0)), "у линии нулевая длина"),
    (tpl(element(), element()), "id «e1» повторяется"),
    ({"elements": []}, "нет ни одного элемента"),
    ([], "ожидается объект"),
    (tpl(element(x_mm=20, w_mm=40)), "выходит за край этикетки 58 × 40 мм"),
    (tpl(element(y_mm=35, h_mm=6)), "выходит за край"),
    (tpl(element(field="colour")), "нет поля «colour»"),
    (tpl(element(kind="text", text="Партия {grn} для {project}")), "неизвестное поле {project}"),
])
def test_a_bad_template_is_refused_in_russian(raw, message):
    with pytest.raises(TemplateError) as error:
        parse_template(raw, "lot", "58x40")
    assert message in str(error.value)
    assert not re.search(r"[A-Za-z]{4,} (?:inputs|field|should|valid)", str(error.value)), "no pydantic English"


def test_fields_and_bounds_depend_on_the_kind_and_format():
    raw = tpl(element(field="project_item", x_mm=50, w_mm=48))
    assert parse_template(raw, "intake", "100x50")
    with pytest.raises(TemplateError, match="нет поля «project_item»"):
        parse_template(raw, "lot", "100x50")
    with pytest.raises(TemplateError, match="выходит за край этикетки 58 × 30 мм"):
        parse_template(raw, "intake", "58x30")
    # A box right to the edge is on the label.
    assert parse_template(tpl(element(x_mm=18, w_mm=40, y_mm=32, h_mm=8)), "lot", "58x40")


# --- The built-in templates --------------------------------------------------------------------------------------


@pytest.mark.parametrize("dpi", [300, 203])
@pytest.mark.parametrize("code_type", ["qr", "both", "code128"])
@pytest.mark.parametrize("label_format", list(FORMATS))
@pytest.mark.parametrize("kind", ["lot", "intake"])
def test_every_built_in_template_renders_inside_its_label(kind, label_format, code_type, dpi):
    template = default_template(kind, label_format, code_type)
    check_template(template, kind, label_format)  # every field exists, every box on the label
    content = lot_content(SAMPLE) if kind == "lot" else SAMPLE_INTAKE
    if kind == "intake" and code_type != "qr":
        content = intake_content(GrnLabelData("GP.26.0000012", "", "Жгут", "3", "шт", "", None), "533К.12", "КБ1")
    label = render(template, content, settings_for(label_format, code_type=code_type, dpi=dpi))
    width, height = FORMATS[label_format]
    assert label.image.size == (round(width * dpi / 25.4), round(height * dpi / 25.4))
    assert not label.overflow, "every text fits its box at its own size"
    assert not label.skipped
    left, top, right, bottom = Image.eval(label.image.convert("L"), lambda v: 255 - v).getbbox()
    margin = round(dpi / 25.4) - 1
    assert left >= margin and top >= margin and right <= label.width - margin and bottom <= label.height - margin
    # No two elements overlap: the designer gets the built-in one as a clean start.
    boxes = [(e.x_mm, e.y_mm, e.x_mm + e.w_mm, e.y_mm + e.h_mm) for e in template.elements]
    for index, a in enumerate(boxes):
        for b in boxes[index + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)


def test_the_intake_label_shows_project_and_kb_where_a_lot_has_its_article():
    texts = render(default_template("intake", "58x40", "qr"), SAMPLE_INTAKE, settings_for("58x40")).texts
    assert texts == ("ГРН", "ГП.26.0000012", "350 шт", "Жгут CCW-0,2 питания блока", "533К.12 КБ1", "А-1 · ГП",
                     "поступило 22.10.2026")


def test_the_built_in_template_follows_the_code_type_of_the_settings():
    kinds = lambda code_type: {e.kind for e in default_template("lot", "58x40", code_type).elements}  # noqa: E731
    assert {"qr"} <= kinds("qr") and "code128" not in kinds("qr")
    assert {"qr", "code128"} <= kinds("both")
    assert "qr" not in kinds("code128") and "code128" in kinds("code128")


# --- The renderer ------------------------------------------------------------------------------------------------


def _ink(label) -> tuple[int, int, int, int] | None:
    return Image.eval(label.image.convert("L"), lambda v: 255 - v).getbbox()


def test_shrink_fits_a_long_value_and_ellipsis_cuts_it():
    long = lot_content(GrnLabelData("FRX.26.0000041-12345", "", "Разъём", "1", "шт", "", None))
    base = {"x_mm": 1, "y_mm": 1, "w_mm": 30, "h_mm": 6, "font_mm": 3.2, "mono": True}
    shrunk = render(LabelTemplate.model_validate(tpl(element(**base))), long, settings_for("58x40"))
    assert shrunk.texts == ("FRX.26.0000041-12345",) and not shrunk.overflow
    cut = render(LabelTemplate.model_validate(tpl(element(**base, fit="ellipsis"))), long, settings_for("58x40"))
    assert cut.texts[0].endswith("…") and len(cut.texts) == 1


def test_text_never_leaves_its_box():
    template = LabelTemplate.model_validate(tpl(element(
        field="name", x_mm=10, y_mm=10, w_mm=20, h_mm=3, font_mm=5, max_lines=4)))
    data = lot_content(GrnLabelData("G", "", "Очень длинное наименование " * 10, "1", "шт", "", None))
    label = render(template, data, settings_for("58x40"))
    dots = 300 / 25.4
    left, top, right, bottom = _ink(label)
    assert left >= round(10 * dots) and top >= round(10 * dots)
    assert right <= round(30 * dots) and bottom <= round(13 * dots)
    assert label.overflow == ("e1",), "too small even at half the size: cut and reported"


def test_text_placeholders_lines_boxes_and_alignment():
    template = LabelTemplate.model_validate(tpl(
        element(id="t", kind="text", text="Партия {grn} · {place_warehouse}", x_mm=1, y_mm=1, w_mm=56, h_mm=5, font_mm=2,
                align="right"),
        element(id="l", kind="line", x_mm=1, y_mm=7, w_mm=56, h_mm=0),
        element(id="b", kind="box", x_mm=1, y_mm=10, w_mm=20, h_mm=10),
    ))
    label = render(template, lot_content(SAMPLE), settings_for("58x40"))
    assert label.texts == ("Партия FRX.26.0000041 · 1F4 · ЦАР",)
    pixels = label.image.load()
    dots = 300 / 25.4
    assert pixels[round(30 * dots), round(7 * dots) + 1] == 0, "the line is drawn 0.3 mm thick"
    # The box: its left side is ink, inside it the tape stays clean.
    assert pixels[round(1 * dots) + 1, round(15 * dots)] == 0 and pixels[round(1 * dots) + 20, round(15 * dots)] == 255
    _left, _top, right, _bottom = _ink(render(LabelTemplate.model_validate(tpl(template.elements[0].model_dump())),
                                              lot_content(SAMPLE), settings_for("58x40")))
    assert right >= round(57 * dots) - 3, "aligned right"


def test_codes_take_whole_dots_and_refuse_a_box_too_small():
    qr = LabelTemplate.model_validate(tpl(element(kind="qr", field=None, x_mm=1, y_mm=1, w_mm=30, h_mm=20)))
    label = render(qr, lot_content(SAMPLE), settings_for("58x40"))
    assert label.qr_modules == 21 and label.qr_module_dots == round(20 * 300 / 25.4) // 29
    tiny = LabelTemplate.model_validate(tpl(element(kind="qr", field=None, w_mm=4, h_mm=4)))
    with pytest.raises(ValueError, match="QR-код не помещается"):
        render(tiny, lot_content(SAMPLE), settings_for("58x40"))
    narrow = LabelTemplate.model_validate(tpl(element(kind="code128", field=None, w_mm=20, h_mm=5)))
    with pytest.raises(ValueError, match="Штрихкод не помещается"):
        render(narrow, lot_content(SAMPLE), settings_for("58x40"))
    bars = LabelTemplate.model_validate(tpl(element(kind="code128", field=None, w_mm=56, h_mm=5)))
    assert render(bars, lot_content(SAMPLE), settings_for("58x40")).bar_module_dots == 3
    cyrillic = render(bars, SAMPLE_INTAKE, settings_for("58x40"))
    assert cyrillic.skipped == ("e1",) and cyrillic.bar_module_dots is None


# --- HTTP ----------------------------------------------------------------------------------------------------------


@pytest.fixture()
def labels(warehouse_api):  # noqa: F811
    return warehouse_api


def _jobs() -> list[LabelJobRow]:
    with app.state.warehouse_uow_factory() as uow:
        rows = list(uow.session.scalars(select(LabelJobRow).order_by(LabelJobRow.created_at)))
        uow.session.expunge_all()
    return rows


def _bitmap(tspl: bytes) -> Image.Image:
    """The raster of a TSPL job back as an image (0 bit — a dot)."""
    match = re.search(rb"BITMAP 0,0,(\d+),(\d+),0,", tspl)
    width_bytes, height = int(match.group(1)), int(match.group(2))
    data = tspl[match.end():match.end() + width_bytes * height]
    return Image.frombytes("1", (width_bytes * 8, height), data)


MINE = tpl(
    element(id="grn", x_mm=2, y_mm=2, w_mm=54, h_mm=10, font_mm=5, mono=True, bold=True, align="center"),
    element(id="qr", kind="qr", field=None, x_mm=2, y_mm=14, w_mm=24, h_mm=24),
    element(id="caption", kind="text", text="Место: {place_warehouse}", x_mm=28, y_mm=14, w_mm=28, h_mm=8, font_mm=2,
            max_lines=2),
    element(id="frame", kind="box", x_mm=0, y_mm=0, w_mm=58, h_mm=40),
)


def test_get_lists_fields_and_built_in_templates_of_every_format(labels):
    client, _ = labels
    body = client.get("/api/stock/labels/templates/lot").json()
    assert body["kind"] == "lot" and body["kind_label"] == "Партия склада"
    assert [field["key"] for field in body["fields"]] == LOT_KEYS
    assert {"key": "place_warehouse", "label": "Место · склад"} in body["fields"]
    assert set(body["formats"]) == {"58x30", "58x40", "100x50"}
    for label_format, entry in body["formats"].items():
        assert entry["is_default"] is True and entry["version"] is None and entry["updated_by"] is None
        assert (entry["width_mm"], entry["height_mm"]) == FORMATS[label_format]
        assert parse_template(entry["template"], "lot", label_format)
    assert (body["current_format"], body["code_type"]) == ("58x40", "qr")
    intake = client.get("/api/stock/labels/templates/intake").json()
    assert [field["key"] for field in intake["fields"]] == INTAKE_KEYS
    assert client.get("/api/stock/labels/templates/box").status_code == 404


def test_save_bumps_the_version_and_a_stale_save_is_409(labels):
    client, _ = labels
    url = "/api/stock/labels/templates/lot/58x40"
    first = client.put(url, json={"template": MINE, "version": None})
    assert first.status_code == 200, first.text
    saved = first.json()
    assert (saved["is_default"], saved["version"], saved["updated_by"]) == (False, 1, "Кладовщик")
    assert saved["template"]["elements"][0]["font_mm"] == 5 and saved["template"]["elements"][1]["max_lines"] == 1

    entry = client.get("/api/stock/labels/templates/lot").json()["formats"]["58x40"]
    assert (entry["is_default"], entry["version"], entry["template"]) == (False, 1, saved["template"])
    # Someone else edited the built-in one meanwhile: refused, not overwritten.
    stale = client.put(url, json={"template": MINE, "version": None})
    assert stale.status_code == 409 and "обновите" in stale.json()["detail"]
    assert client.put(url, json={"template": MINE, "version": 1}).json()["version"] == 2
    assert client.put(url, json={"template": MINE, "version": 1}).status_code == 409
    # The other formats and the other kind stay built-in.
    formats = client.get("/api/stock/labels/templates/lot").json()["formats"]
    assert formats["58x30"]["is_default"] and formats["100x50"]["is_default"]
    assert client.get("/api/stock/labels/templates/intake").json()["formats"]["58x40"]["is_default"]

    reset = client.delete(url)
    assert reset.status_code == 200 and (reset.json()["is_default"], reset.json()["version"]) == (True, None)
    assert client.get("/api/stock/labels/templates/lot").json()["formats"]["58x40"]["is_default"]
    assert client.put(url, json={"template": MINE, "version": 2}).status_code == 409, "reset since version 2"
    assert client.put(url, json={"template": MINE, "version": None}).json()["version"] == 1


def test_a_bad_template_or_kind_or_format_is_refused(labels):
    client, _ = labels
    bad = client.put("/api/stock/labels/templates/lot/58x40", json={"template": tpl(element(colour=1)), "version": None})
    assert bad.status_code == 422 and "неизвестное поле" in bad.json()["detail"]
    wide = client.put("/api/stock/labels/templates/lot/58x30", json={"template": MINE, "version": None})
    assert wide.status_code == 422 and "выходит за край этикетки 58 × 30 мм" in wide.json()["detail"]
    small_qr = tpl(element(kind="qr", field=None, w_mm=3, h_mm=3))
    refused = client.put("/api/stock/labels/templates/lot/58x40", json={"template": small_qr, "version": None})
    assert refused.status_code == 422 and "QR-код не помещается" in refused.json()["detail"]
    assert client.put("/api/stock/labels/templates/lot/60x40", json={"template": MINE}).status_code == 404
    assert client.put("/api/stock/labels/templates/box/58x40", json={"template": MINE}).status_code == 404
    assert client.get("/api/stock/labels/templates/lot").json()["formats"]["58x40"]["is_default"]


def test_preview_is_the_png_of_the_format_in_printer_dots(labels):
    client, lot = labels
    for label_format, size in (("58x40", (685, 472)), ("58x30", (685, 354)), ("100x50", (1181, 591))):
        template = client.get("/api/stock/labels/templates/lot").json()["formats"][label_format]["template"]
        png = client.post(f"/api/stock/labels/templates/lot/{label_format}/preview", json={"template": template})
        assert png.status_code == 200, png.text
        assert png.headers["content-type"] == "image/png" and png.headers["cache-control"] == "no-store"
        assert Image.open(io.BytesIO(png.content)).size == size
    real = client.post("/api/stock/labels/templates/lot/58x40/preview", json={"template": MINE, "sample": str(lot.id)})
    sample = client.post("/api/stock/labels/templates/lot/58x40/preview", json={"template": MINE, "sample": None})
    assert real.status_code == sample.status_code == 200 and real.content != sample.content
    intake = client.post("/api/stock/labels/templates/intake/58x40/preview", json={"template": MINE})
    assert intake.status_code == 200
    missing = client.post("/api/stock/labels/templates/lot/58x40/preview",
                          json={"template": MINE, "sample": "00000000-0000-0000-0000-000000000001"})
    assert missing.status_code == 404
    assert client.post("/api/stock/labels/templates/lot/58x40/preview", json={"template": MINE, "sample": "x"}).status_code == 404
    bad = client.post("/api/stock/labels/templates/lot/58x40/preview", json={"template": {"elements": "x"}})
    assert bad.status_code == 422 and "ожидается список" in bad.json()["detail"]
    assert client.get("/api/stock/labels/templates/lot").json()["formats"]["58x40"]["is_default"], "preview saves nothing"


def test_every_print_draws_the_saved_template_of_the_current_format(labels):
    client, lot = labels
    before = client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 1})
    assert before.status_code == 200
    # A template of another format does not touch the current 58 × 40.
    client.put("/api/stock/labels/templates/lot/58x30", json={"template": tpl(element(w_mm=50, h_mm=20)), "version": None})
    unchanged = client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 1})
    assert client.put("/api/stock/labels/templates/lot/58x40", json={"template": MINE, "version": None}).status_code == 200
    after = client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 1})
    sample = client.post("/api/stock/labels/test")
    assert after.status_code == sample.status_code == 200
    jobs = _jobs()
    assert len(jobs) == 4
    assert _bitmap(jobs[0].tspl).tobytes() == _bitmap(jobs[1].tspl).tobytes()
    assert _bitmap(jobs[2].tspl).tobytes() != _bitmap(jobs[0].tspl).tobytes()

    # The print is the preview, dot for dot.
    preview = Image.open(io.BytesIO(client.get(f"/api/stock/batches/{lot.id}/label.png").content)).convert("1")
    designed = Image.open(io.BytesIO(client.post(
        "/api/stock/labels/templates/lot/58x40/preview", json={"template": MINE, "sample": str(lot.id)}).content)).convert("1")
    printed = _bitmap(jobs[2].tspl).crop((0, 0, 685, 472))
    assert printed.tobytes() == preview.tobytes() == designed.tobytes()
    sample_png = Image.open(io.BytesIO(client.get("/api/stock/labels/sample.png").content)).convert("1")
    assert _bitmap(jobs[3].tspl).crop((0, 0, 685, 472)).tobytes() == sample_png.tobytes()

    # Switching the settings to 58 × 30 prints that format's saved template.
    settings = client.get("/api/stock/labels/settings").json()["settings"]
    client.put("/api/stock/labels/settings", json={**settings, "height_mm": 30})
    client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 1})
    low = _jobs()[-1].tspl
    assert low.startswith(b"SIZE 58 mm,30 mm")
    assert _bitmap(low).crop((0, 0, 685, 354)).tobytes() == Image.open(io.BytesIO(client.post(
        "/api/stock/labels/templates/lot/58x30/preview",
        json={"template": tpl(element(w_mm=50, h_mm=20)), "sample": str(lot.id)}).content)).convert("1").tobytes()


def test_a_viewer_reads_templates_but_cannot_design_them(labels):
    client, _ = labels
    as_viewer("management", "warehouse")
    assert client.get("/api/stock/labels/templates/lot").status_code == 200
    # The preview is the designer's tool: a POST, so it takes edit like every write of the warehouse.
    assert client.post("/api/stock/labels/templates/lot/58x40/preview", json={"template": MINE}).status_code == 403
    assert client.put("/api/stock/labels/templates/lot/58x40", json={"template": MINE}).status_code == 403
    assert client.delete("/api/stock/labels/templates/lot/58x40").status_code == 403


def test_a_saved_template_that_no_longer_parses_prints_the_built_in_one(labels):
    client, lot = labels
    from app.modules.warehouse.label_templates import LabelTemplateRow  # noqa: PLC0415

    with app.state.warehouse_uow_factory() as uow:
        uow.session.add(LabelTemplateRow(kind="lot", format="58x40", template={}, version=3,
                                         updated_at=date(2026, 10, 23), updated_by="seed"))
        uow.commit()
    entry = client.get("/api/stock/labels/templates/lot").json()["formats"]["58x40"]
    assert (entry["is_default"], entry["version"]) == (True, 3), "the version stays to save over it"
    assert client.post(f"/api/stock/batches/{lot.id}/label", json={"copies": 1}).status_code == 200
    assert client.put("/api/stock/labels/templates/lot/58x40", json={"template": MINE, "version": 3}).json()["version"] == 4


def test_the_designers_sample_lot_shows_the_article_and_the_manufacturer():
    values = lot_content(SAMPLE).values
    assert (values["article"], values["manufacturer"]) == ("XHP-4", "JST")
    assert values["manufacturer_place"] == "JST · 1F4 · ЦАР"
