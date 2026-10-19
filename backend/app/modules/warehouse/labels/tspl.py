"""A TSPL2 job — the language of the TSC MB341 (Roller_Buyer `labels/tspl.ts` + `packForTspl`).

The layout goes as one `BITMAP` command: the printer's own fonts have no Cyrillic. Commands are one-byte ASCII and the
raster is raw bytes, so the job is built as bytes, not a string. For the same raster the bytes equal Roller_Buyer's."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image

from .layout import js_round


@dataclass(frozen=True)
class TsplOptions:
    width_mm: float = 58
    height_mm: float = 30
    gap_mm: float = 2  # gap between labels; the printer's built-in calibration shows it
    density: int = 12  # head heat 0–15
    speed: int = 2  # inches per second
    copies: int = 1
    # Print offset in dots: a printer that prints off centre is corrected here, not in the layout.
    offset_x_dots: float = 0
    offset_y_dots: float = 0


def js_number(value: float) -> str:
    """A number as a JavaScript template literal writes it: 58 → «58», 2.5 → «2.5»."""
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


def pack_for_tspl(raster: Image.Image) -> tuple[int, bytes]:
    """The `BITMAP` data: a bit per dot, the high bit leftmost. In TSPL a zero prints and a one leaves the tape clean —
    Pillow's mode "1" packs white as one, so the bits come out right. The row is padded to whole bytes with ones
    (clean tape), as Roller_Buyer fills its buffer with 0xFF."""
    image = raster if raster.mode == "1" else raster.convert("1", dither=Image.Dither.NONE)
    width_bytes = math.ceil(image.width / 8)
    if width_bytes * 8 != image.width:
        padded = Image.new("1", (width_bytes * 8, image.height), 1)
        padded.paste(image, (0, 0))
        image = padded
    return width_bytes, image.tobytes()


def build_tspl_job(raster: Image.Image, options: TsplOptions) -> bytes:
    width_bytes, data = pack_for_tspl(raster)
    header = "\r\n".join((
        f"SIZE {js_number(options.width_mm)} mm,{js_number(options.height_mm)} mm",
        f"GAP {js_number(options.gap_mm)} mm,0 mm",
        # Print from the tape's edge: the layout is already drawn in dots, the offset is only a correction.
        f"REFERENCE {js_round(options.offset_x_dots)},{js_round(options.offset_y_dots)}",
        "DIRECTION 1",
        f"DENSITY {options.density}",
        f"SPEED {options.speed}",
        "CLS",
        f"BITMAP 0,0,{width_bytes},{raster.height},0,",
    ))
    footer = f"\r\nPRINT {options.copies},1\r\n"
    return header.encode("ascii") + data + footer.encode("ascii")
