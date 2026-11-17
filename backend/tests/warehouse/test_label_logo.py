from app.modules.warehouse.labels.defaults import default_template
from app.modules.warehouse.labels.render import render
from app.modules.warehouse.labels.settings import PrintSettings
from app.modules.warehouse.labels.template import FORMATS, instrument_content, parse_template


def content():
    return instrument_content("СИ.034", "Переходник", "ADP-ss-26-034", None, model="S-322", accessory=True,
                              in_house_serial=True, connector_line="SMA розетка – SMA розетка",
                              parameters_line="Применять до 12 ГГц", loss_line="КСВН ≤ 1,25 · потери ≤ 0,5 дБ",
                              measurement_date="Замер 16.11.26", card_url="https://erp.example.test/p/x")


def test_every_instrument_format_carries_the_wordmark_and_fits():
    for label_format, (width, height) in FORMATS.items():
        template = default_template("instrument", label_format, "qr")
        logos = [element for element in template.elements if element.kind == "logo"]
        assert len(logos) == 1
        assert logos[0].x_mm + logos[0].w_mm <= width and logos[0].y_mm + logos[0].h_mm <= height
        layout = render(template, content(), PrintSettings(width_mm=width, height_mm=height))
        assert layout.overflow == ()


def test_logo_prints_black_dots_in_its_box_and_nothing_elsewhere_in_it():
    template = default_template("instrument", "58x30", "qr")
    logo = next(element for element in template.elements if element.kind == "logo")
    layout = render(template, content(), PrintSettings(width_mm=58, height_mm=30))
    dots = layout.image.width / 58
    box = (int(logo.x_mm * dots), int(logo.y_mm * dots), int((logo.x_mm + logo.w_mm) * dots), int((logo.y_mm + logo.h_mm) * dots))
    area = layout.image.crop(box).convert("L")
    black = sum(1 for value in area.getdata() if value == 0)
    assert black > 0.25 * area.width * area.height  # a dark wordmark, not an empty box


def test_designer_json_with_a_logo_element_is_valid():
    raw = {"elements": [{"id": "logo", "kind": "logo", "x_mm": 40, "y_mm": 1, "w_mm": 14, "h_mm": 4}]}
    assert parse_template(raw, "instrument", "58x30").elements[0].kind == "logo"
