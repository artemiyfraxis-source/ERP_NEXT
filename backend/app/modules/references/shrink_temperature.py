"""Typed recovery temperature over the heat-shrink catalog's sheet-header payload.

Equal bounds mean one stated value; an absent bound remains open. Operating
temperature is deliberately never used as a fallback. The optional characteristic
preserves passports that state both a nominal value and an optimal range.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Mapping
from urllib.parse import urlsplit

LOWER = "Температура усадки от, °C"
UPPER = "Температура усадки до, °C"
KIND = "Вид температуры усадки"
CHARACTERISTIC = "Температура усадки по характеристикам, °C"
SOURCE_URL = "Источник температуры усадки"
DOCUMENT = "Документ температуры усадки"
PAGE = "Страница температуры усадки"
QUOTE = "Цитата температуры усадки"
COLUMNS = (LOWER, UPPER, KIND, CHARACTERISTIC, SOURCE_URL, DOCUMENT, PAGE, QUOTE)
KINDS = ("рекомендуемая", "диапазон", "минимальная", "оптимальная")


@dataclass(frozen=True)
class ShrinkTemperature:
    lower_c: float | None = None
    upper_c: float | None = None
    kind: str = ""
    characteristic_c: float | None = None
    source_url: str = ""
    document: str = ""
    page: str = ""
    quote: str = ""


def _number(fields: Mapping[str, str], key: str) -> float | None:
    text = str(fields.get(key) or "").strip().replace(",", ".").replace("−", "-")
    if not text:
        return None
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        raise ValueError(f"«{key}»: укажите число в °C")
    value = float(text)
    if not math.isfinite(value) or value < -273.15:
        raise ValueError(f"«{key}»: недопустимая температура")
    return value


def read(fields: Mapping[str, str]) -> ShrinkTemperature:
    """Validate the complete candidate payload before any write; keep source text intact."""
    result = ShrinkTemperature(
        lower_c=_number(fields, LOWER), upper_c=_number(fields, UPPER),
        kind=str(fields.get(KIND) or "").strip(), characteristic_c=_number(fields, CHARACTERISTIC),
        source_url=str(fields.get(SOURCE_URL) or "").strip(),
        document=str(fields.get(DOCUMENT) or "").strip(), page=str(fields.get(PAGE) or "").strip(),
        quote=str(fields.get(QUOTE) or "").strip(),
    )
    low, high = result.lower_c, result.upper_c
    has_value = low is not None or high is not None
    if result.kind and result.kind not in KINDS:
        raise ValueError("Вид температуры усадки: выберите рекомендуемую, диапазон, минимальную или оптимальную")
    if not has_value and (result.kind or result.characteristic_c is not None):
        raise ValueError("Укажите хотя бы одну границу температуры усадки")
    if low is not None and high is not None and low > high:
        raise ValueError("Температура усадки от не может быть выше температуры до")
    if result.kind == "минимальная" and (low is None or high is not None):
        raise ValueError("Минимальная температура усадки задаётся только нижней границей")
    if result.source_url:
        try:
            url = urlsplit(result.source_url)
            valid = url.scheme in ("http", "https") and bool(url.hostname) and not url.username and not url.password
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Источник температуры усадки: нужна ссылка http:// или https://")
    return result


def read_stored(fields: Mapping[str, str]) -> dict:
    """Keep legacy payloads readable, with the original cells available for repair."""
    try:
        return asdict(read(fields))
    except ValueError as error:
        return {**asdict(ShrinkTemperature()), "needs_review": True,
                "validation_error": str(error),
                "raw_fields": {key: fields[key] for key in COLUMNS if key in fields}}
