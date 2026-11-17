from io import BytesIO

from PIL import Image, ImageChops
import pytest

from app.modules.warehouse.marking_render import Geometry, dots, presets, render


@pytest.mark.parametrize("inventory,name,raw", presets())
def test_presets_are_one_bit_and_keep_forbidden_media_white(inventory, name, raw):
    geometry = Geometry.model_validate(raw)
    png, dimensions = render("АБВ\n123", geometry)
    image = Image.open(BytesIO(png))
    assert image.mode == "1"
    assert image.size == (dots(raw["width_mm"]), dots(raw["height_mm"]))
    assert dimensions["dpi"] == 300
    assert image.getextrema() == (0, 255)
    for zone in geometry.blank_zones:
        assert image.crop((dots(zone.x), dots(zone.y), dots(zone.x + zone.width), dots(zone.y + zone.height))).getextrema() == (255, 255)


def test_flag_halves_have_identical_normal_glyphs_and_rotation_preserves_pixels():
    geometry = Geometry.model_validate(presets()[-1][2])
    png, _ = render("КБ1\nЛЕВО\n123", geometry)
    image = Image.open(BytesIO(png))
    crops = []
    for zone in geometry.zones:
        crop = image.crop((dots(zone.x), dots(zone.y), dots(zone.x + zone.width), dots(zone.y + zone.height))).convert("L")
        crops.append(crop.crop(ImageChops.invert(crop).getbbox()))
    assert ImageChops.difference(*crops).getbbox() is None
    rotated, dimensions = render("КБ1\nЛЕВО\n123", geometry.model_copy(update={"rotation": 90}))
    assert dimensions["width_mm"] == 20 and dimensions["height_mm"] == 92.5
    assert Image.open(BytesIO(rotated)).tobytes() == image.rotate(-90, expand=True).tobytes()


@pytest.mark.parametrize("text", ["X" * 200, "А\nБ\nВ\nГ", "\x00abc", " "])
def test_overflow_and_invalid_text_are_rejected_without_truncation(text):
    with pytest.raises(ValueError):
        render(text, Geometry.model_validate(presets()[0][2]))


@pytest.mark.parametrize("change", [{"width_mm": float("nan")}, {"rotation": 45},
    {"zones": [{"x": 0, "y": 14, "width": 10, "height": 3}]}, {"min_font_mm": 1},
    {"zones": [{"x": 0, "y": 0, "width": 11, "height": 5}]}])
def test_invalid_geometry_is_rejected(change):
    with pytest.raises(ValueError):
        Geometry.model_validate({**presets()[0][2], **change})


def test_calibration_fiducial_stays_inside_a_narrow_zone():
    geometry = Geometry(width_mm=10, height_mm=10, zones=[dict(x=1, y=1, width=1, height=4)],
                        blank_zones=[dict(x=2, y=0, width=7, height=10)])
    png, _ = render("I", geometry, calibration=True)
    assert Image.open(BytesIO(png)).crop((dots(2), 0, dots(9), dots(10))).getextrema() == (255, 255)


def test_width_feed_rotates_into_the_driver_feed_axis():
    geometry = Geometry.model_validate(presets()[-1][2])
    plain, _ = render("ABC", geometry)
    turned, dimensions = render("ABC", geometry.model_copy(update={"feed_direction": "width"}))
    assert (dimensions["width_mm"], dimensions["height_mm"]) == (20, 92.5)
    assert Image.open(BytesIO(turned)).tobytes() == Image.open(BytesIO(plain)).rotate(-90, expand=True).tobytes()
