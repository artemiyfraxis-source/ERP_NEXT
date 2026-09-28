"""МАССА 1 ШТ, Г: the mass of one piece, entered as the weight of 1, 10 or 100 pieces.

Port of SUPPLYMAP Stock05.gs massPerUnit_ / massFieldsFrom_ (123-161) and Schema.gs icrNum_. The owner's
scales read 0.001 g, so small parts are weighed by the hundred and divided; what is stored is always the
mass of one piece, rounded to 5 decimals. Pure: shared by Receiving (incoming control) and Warehouse
(manual entry), which both take the three inputs.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

MASS_SAMPLES = (1, 10, 100)
MASS_DECIMALS = 5
# Numeric(18, 5) column: 13 integer digits.
MASS_LIMIT = Decimal(10) ** 13

_LEADING_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def parse_number(value: object) -> float:
    """icrNum_: drop all whitespace, the first comma is the decimal point, then JS parseFloat — the leading
    number is taken and the rest ignored («12г» → 12). Nothing numeric → NaN."""
    text = re.sub(r"\s", "", "" if value is None else str(value)).replace(",", ".", 1)
    match = _LEADING_NUMBER.match(text)
    return float(match.group(0)) if match else math.nan


def _js_round(value: float) -> float:
    # JS Math.round: nearest integer, a tie goes up (towards +inf), unlike Python's banker's round().
    floor = math.floor(value)
    return floor + 1 if value - floor >= 0.5 else floor


def mass_per_unit(mass_1: object = None, mass_10: object = None, mass_100: object = None) -> Decimal | None:
    """massPerUnit_: the largest filled sample wins (the same scale division is split over more pieces).
    A field that is empty, not a number, zero or negative is skipped; nothing usable → None (the stored
    mass is left as it was). A result rounding to 0 is returned as 0, as SUPPLYMAP does."""
    for sample, raw in reversed(tuple(zip(MASS_SAMPLES, (mass_1, mass_10, mass_100)))):
        value = parse_number(raw)
        # SUPPLYMAP would also take «Infinity»; a stored mass must be a finite number.
        if math.isnan(value) or math.isinf(value) or value <= 0:
            continue
        scale = 10 ** MASS_DECIMALS
        rounded = _js_round(value / sample * scale) / scale
        mass = Decimal(repr(rounded)).quantize(Decimal(1).scaleb(-MASS_DECIMALS))
        if mass >= MASS_LIMIT:
            raise ValueError("Масса 1 шт слишком большая")
        return mass
    return None


def mass_fields_from(mass: object) -> dict[str, str]:
    """massFieldsFrom_: which of the three fields to prefill from a stored mass — the one it was most likely
    weighed with (below 0.01 g a hundred, below 0.1 g ten, otherwise one), rounded to 3 decimals."""
    value = parse_number(mass)
    if math.isnan(value) or math.isinf(value) or value <= 0:
        return {"mass_1": "", "mass_10": "", "mass_100": ""}

    def shown(number: float) -> str:
        return format(Decimal(repr(_js_round(number * 1000) / 1000)).normalize(), "f")

    if value < 0.01:
        return {"mass_1": "", "mass_10": "", "mass_100": shown(value * 100)}
    if value < 0.1:
        return {"mass_1": "", "mass_10": shown(value * 10), "mass_100": ""}
    return {"mass_1": shown(value), "mass_10": "", "mass_100": ""}
