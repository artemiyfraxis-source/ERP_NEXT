from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageFont
import pytest

from app.modules.warehouse.marking_render import DPI, FONT, Geometry, _lines, dots, halves, presets, render


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


def _zone_crops(png, geometry):
    image = Image.open(BytesIO(png))
    crops = []
    for zone in geometry.zones:
        crop = image.crop((dots(zone.x), dots(zone.y), dots(zone.x + zone.width), dots(zone.y + zone.height))).convert("L")
        crops.append(crop.crop(ImageChops.invert(crop).getbbox()))
    return crops


def test_flag_halves_carry_their_own_text_with_one_font_size():
    geometry = Geometry.model_validate(presets()[-1][2])
    assert halves("X1 | КБ1-001", 2) == ["X1", "КБ1-001"] and halves("X1", 2) == ["X1", "X1"]
    left, right = _zone_crops(render("X1 | X1", geometry)[0], geometry)
    assert ImageChops.difference(left, right).getbbox() is None
    split = _zone_crops(render("X1 | X2", geometry)[0], geometry)
    same = _zone_crops(render("X1", geometry)[0], geometry)
    assert ImageChops.difference(split[0], same[0]).getbbox() is None
    assert ImageChops.difference(*split).getbbox() is not None
    # A long right half shrinks both halves: the flag reads as one label.
    long_left, long_right = _zone_crops(render("X1 | КБ1-001 ЛЕВЫЙ ВЫВОД", geometry)[0], geometry)
    assert long_left.size[1] <= same[0].size[1]


@pytest.mark.parametrize("text,zones", [("А | Б | В", 2), ("А |", 2), ("| Б", 2), ("А | Б", 1)])
def test_bad_halves_are_rejected(text, zones):
    with pytest.raises(ValueError):
        halves(text, zones)


def test_single_zone_profile_refuses_different_halves():
    with pytest.raises(ValueError, match="двумя текстовыми зонами"):
        render("А | Б", Geometry.model_validate(presets()[0][2]))


def test_left_half_follows_the_zone_position_not_the_list_order():
    raw = presets()[-1][2]
    geometry = Geometry.model_validate(raw)
    reversed_zones = Geometry.model_validate({**raw, "zones": list(reversed(raw["zones"]))})
    assert render("X1 | КБ1-001", reversed_zones)[0] == render("X1 | КБ1-001", geometry)[0]


def _render_before_halves(text, geometry, *, calibration=False):
    """The renderer as it was before flag halves (origin/main ed34f2a3): one text in every zone. Kept verbatim as
    the in-process reference, so the comparison holds on any Pillow/FreeType build."""
    image = Image.new("1", (dots(geometry.width_mm), dots(geometry.height_mm)), 1)
    draw = ImageDraw.Draw(image)
    bounds = [(dots(z.x), dots(z.y), dots(z.x + z.width), dots(z.y + z.height)) for z in geometry.zones]
    width = min(b[2] - b[0] for b in bounds) - 2 * abs(dots(geometry.offset_x_mm))
    height = min(b[3] - b[1] for b in bounds) - 2 * abs(dots(geometry.offset_y_mm))
    fitted = None
    for size in range(dots(geometry.max_font_mm) * 2, 0, -1):
        font = ImageFont.truetype(str(FONT), size=size)
        cap = draw.textbbox((0, 0), "БH", font=font)
        if cap[3] - cap[1] > dots(geometry.max_font_mm):
            continue
        if cap[3] - cap[1] < dots(geometry.min_font_mm):
            break
        lines = _lines(draw, text, font, width)
        if lines is None or len(lines) > geometry.max_lines:
            continue
        boxes = [draw.textbbox((0, 0), line or " ", font=font) for line in lines]
        gap = max(2, round(size * 0.15))
        total = sum(b[3] - b[1] for b in boxes) + gap * (len(boxes) - 1)
        if total <= height and all(b[2] - b[0] <= width for b in boxes):
            fitted = font, lines, boxes, gap, total
            break
    font, lines, boxes, gap, total = fitted
    for left, top, right, bottom in bounds:
        y = top + (bottom - top - total) // 2 + dots(geometry.offset_y_mm)
        for line, box in zip(lines, boxes):
            w = box[2] - box[0]
            x = (left + (right - left - w) // 2 if geometry.alignment == "center" else
                 left + abs(dots(geometry.offset_x_mm)) if geometry.alignment == "left" else
                 right - w - abs(dots(geometry.offset_x_mm))) + dots(geometry.offset_x_mm)
            draw.text((x - box[0], y - box[1]), line, font=font, fill=0)
            y += box[3] - box[1] + gap
        if calibration:
            draw.line([(left, min(top + 8, bottom - 1)), (left, top), (min(left + 16, right - 1), top)], fill=0, width=1)
    rotation = (geometry.rotation + (90 if geometry.feed_direction == "width" else 0)) % 360
    if rotation:
        image = image.rotate(-rotation, expand=True)
    output = BytesIO()
    image.save(output, format="PNG", dpi=(DPI, DPI))
    return output.getvalue()


@pytest.mark.parametrize("preset", range(len(presets())))
@pytest.mark.parametrize("text,calibration,change", [("КБ1\nЛЕВО\n123", False, {}), ("АБВ 123", True, {}),
    ("X1", False, {"alignment": "left", "offset_x_mm": 0.5}), ("Жгут 12", False, {"rotation": 90, "offset_y_mm": -0.4})])
def test_text_without_divider_renders_byte_identical_to_before(preset, text, calibration, change):
    geometry = Geometry.model_validate({**presets()[preset][2], **change})
    assert render(text, geometry, calibration=calibration)[0] == _render_before_halves(text, geometry, calibration=calibration)


def test_same_text_sits_identically_inside_both_flag_zones():
    geometry = Geometry.model_validate(presets()[-1][2])
    image = Image.open(BytesIO(render("КБ1-001\nX1", geometry)[0])).convert("L")
    boxes = [ImageChops.invert(image.crop((dots(z.x), dots(z.y), dots(z.x + z.width), dots(z.y + z.height)))).getbbox()
             for z in geometry.zones]
    assert boxes[0] is not None and boxes[0] == boxes[1]
