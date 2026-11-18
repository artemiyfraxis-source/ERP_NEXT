"""Godex raster geometry. The same lossless bitmap is previewed and queued."""

from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, model_validator

DPI = 300
FONT = Path(__file__).parent / "labels/fonts/IBMPlexSans-SemiBold.ttf"


def dots(mm: float) -> int:
    return round(mm * DPI / 25.4)


class Rect(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, le=150)
    y: float = Field(ge=0, le=150)
    width: float = Field(gt=0, le=150)
    height: float = Field(gt=0, le=150)

    def overlaps(self, other):
        return (self.x < other.x + other.width and other.x < self.x + self.width
                and self.y < other.y + other.height and other.y < self.y + self.height)


class Geometry(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    width_mm: float = Field(ge=5, le=150)
    height_mm: float = Field(ge=5, le=150)
    rotation: Literal[0, 90, 180, 270] = 0
    feed_direction: Literal["height", "width"] = "height"
    zones: list[Rect] = Field(min_length=1, max_length=2)
    blank_zones: list[Rect] = Field(default_factory=list, max_length=4)
    offset_x_mm: float = Field(default=0, ge=-5, le=5)
    offset_y_mm: float = Field(default=0, ge=-5, le=5)
    min_font_mm: float = Field(default=1.6, ge=1.6, le=10)
    max_font_mm: float = Field(default=5, ge=1.6, le=15)
    max_lines: int = Field(default=3, ge=1, le=3, strict=True)
    alignment: Literal["center", "left", "right"] = "center"

    @model_validator(mode="after")
    def valid_geometry(self):
        if self.min_font_mm > self.max_font_mm:
            raise ValueError("Минимальный шрифт больше максимального")
        for zone in [*self.zones, *self.blank_zones]:
            if zone.x + zone.width > self.width_mm or zone.y + zone.height > self.height_mm:
                raise ValueError("Зона выходит за размер этикетки")
        for i, zone in enumerate(self.zones):
            if any(zone.overlaps(other) for other in [*self.zones[i + 1:], *self.blank_zones]):
                raise ValueError("Текстовые и пустые зоны пересекаются")
        if len(self.zones) == 2 and (self.zones[0].width != self.zones[1].width
                                    or self.zones[0].height != self.zones[1].height):
            raise ValueError("Половины флажка должны иметь одинаковые текстовые зоны")
        return self


def presets():
    result = []
    for width, height in ((10, 60), (20, 50), (30, 60), (40, 60)):
        result.append(("ОБ.102", f"{width}×{height}", Geometry(width_mm=width, height_mm=height,
            zones=[Rect(x=1, y=1, width=width - 2, height=13)],
            blank_zones=[Rect(x=0, y=15, width=width, height=height - 15)]).model_dump()))
    result.append(("ОБ.101", "92,5×20", Geometry(width_mm=92.5, height_mm=20,
        zones=[Rect(x=1, y=1, width=38.25, height=18), Rect(x=53.25, y=1, width=38.25, height=18)],
        blank_zones=[Rect(x=40.25, y=0, width=12, height=20)]).model_dump()))
    return result


def _lines(draw, text, font, width):
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = line + (" " if line else "") + word
            if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
                line = candidate
            elif line:
                lines.append(line)
                line = word
            else:
                return None
        lines.append(line)
    return lines


HALF = "|"


def halves(text: str, zones: int) -> list[str]:
    """Text of every text zone. «ЛЕВО | ПРАВО» — different text on the two halves of a flag (owner, 18.11);
    without «|» — the same text on every zone, as before."""
    if HALF not in text:
        return [text] * zones
    parts = [part.strip() for part in text.split(HALF)]
    if len(parts) != 2:
        raise ValueError(f"Половины флажка разделяет один знак «|»: «{text}»")
    if zones != 2:
        raise ValueError(f"Разный текст на половинах печатается только на флажке с двумя текстовыми зонами: «{text}»")
    if not all(parts):
        raise ValueError(f"Заполните обе половины флажка: «{text}»")
    return parts


def _layout(draw, text, font, size, geometry, width, height):
    lines = _lines(draw, text, font, width)
    if lines is None or len(lines) > geometry.max_lines:
        return None
    boxes = [draw.textbbox((0, 0), line or " ", font=font) for line in lines]
    gap = max(2, round(size * 0.15))
    total = sum(b[3] - b[1] for b in boxes) + gap * (len(boxes) - 1)
    if total > height or any(b[2] - b[0] > width for b in boxes):
        return None
    return lines, boxes, gap, total


def render(text: str, geometry: Geometry, *, calibration: bool = False) -> tuple[bytes, dict]:
    if not text.strip() or len(text) > 1000 or any(ord(c) < 32 and c != "\n" for c in text):
        raise ValueError("Нужна непустая надпись до 1000 символов без управляющих знаков")
    # The left half is the zone nearer the label's left edge, whatever order the zones are listed in.
    by_x = sorted(range(len(geometry.zones)), key=lambda index: geometry.zones[index].x)
    parts = halves(text, len(geometry.zones))
    texts = [parts[by_x.index(index)] for index in range(len(geometry.zones))]
    image = Image.new("1", (dots(geometry.width_mm), dots(geometry.height_mm)), 1)
    draw = ImageDraw.Draw(image)
    bounds = [(dots(z.x), dots(z.y), dots(z.x + z.width), dots(z.y + z.height)) for z in geometry.zones]
    width = min(b[2] - b[0] for b in bounds) - 2 * abs(dots(geometry.offset_x_mm))
    height = min(b[3] - b[1] for b in bounds) - 2 * abs(dots(geometry.offset_y_mm))
    fitted = None
    # min_font_mm measures visible capital height, not the font's em square. Both halves share one size.
    for size in range(dots(geometry.max_font_mm) * 2, 0, -1):
        font = ImageFont.truetype(str(FONT), size=size)
        cap = draw.textbbox((0, 0), "БH", font=font)
        if cap[3] - cap[1] > dots(geometry.max_font_mm):
            continue
        if cap[3] - cap[1] < dots(geometry.min_font_mm):
            break
        layouts = [_layout(draw, part, font, size, geometry, width, height) for part in texts]
        if all(layouts):
            fitted = font, layouts
            break
    if fitted is None:
        raise ValueError(f"Надпись не помещается: «{text}»")
    font, layouts = fitted
    for (left, top, right, bottom), (lines, boxes, gap, total) in zip(bounds, layouts):
        y = top + (bottom - top - total) // 2 + dots(geometry.offset_y_mm)
        for line, box in zip(lines, boxes):
            w = box[2] - box[0]
            x = (left + (right - left - w) // 2 if geometry.alignment == "center" else
                 left + abs(dots(geometry.offset_x_mm)) if geometry.alignment == "left" else
                 right - w - abs(dots(geometry.offset_x_mm))) + dots(geometry.offset_x_mm)
            if x < left or x + w > right or y < top or y + box[3] - box[1] > bottom:
                raise ValueError(f"Смещение выводит надпись за текстовую зону: «{text}»")
            draw.text((x - box[0], y - box[1]), line, font=font, fill=0)
            y += box[3] - box[1] + gap
        if calibration:
            # Asymmetric fiducial confined to the text zone; blank media remains blank.
            draw.line([(left, min(top + 8, bottom - 1)), (left, top), (min(left + 16, right - 1), top)], fill=0, width=1)
    width_mm, height_mm = geometry.width_mm, geometry.height_mm
    # Driver Y is the feed axis. A stock whose X dimension feeds needs one extra quarter turn.
    rotation = (geometry.rotation + (90 if geometry.feed_direction == "width" else 0)) % 360
    if rotation:
        image = image.rotate(-rotation, expand=True)
    if rotation in (90, 270):
        width_mm, height_mm = height_mm, width_mm
    output = BytesIO()
    image.save(output, format="PNG", dpi=(DPI, DPI))
    return output.getvalue(), {"width_mm": width_mm, "height_mm": height_mm,
                              "orientation": "landscape" if width_mm > height_mm else "portrait", "dpi": DPI}
