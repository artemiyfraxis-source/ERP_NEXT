"""Code128 for scanners that read only one-dimensional codes (Roller_Buyer `labels/code128.ts`).

Set B: a ГРН is letters, digits, dots and a dash, and set C knows no dot. The result is the widths of bars and spaces in
modules, starting with a bar; the layout lays them out in printer dots, a whole number of dots per module."""

from __future__ import annotations

# Symbols 0–105: widths «bar, space, bar…» in modules.
PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232",
)
STOP = "2331112"
START_B = 104

# The light field on each side: at least ten modules, or a scanner does not find the start.
QUIET_MODULES = 10


def encode_code128(text: str) -> list[int]:
    """Bar and space widths of `text` in set B. A character outside ASCII 32–126 (a Cyrillic ГРН) → ValueError."""
    values: list[int] = []
    for char in text:
        code = ord(char)
        if code < 32 or code > 126:
            raise ValueError(f"Code128 B не кодирует символ «{char}»")
        values.append(code - 32)
    # Check symbol: the start plus the position-weighted sum, modulo 103.
    checksum = (START_B + sum(value * (index + 1) for index, value in enumerate(values))) % 103
    symbols = [START_B, *values, checksum]
    return [int(width) for width in "".join(PATTERNS[value] for value in symbols) + STOP]


def code128_modules(text: str) -> int:
    """Width of the code in modules without the light fields."""
    return sum(encode_code128(text))
