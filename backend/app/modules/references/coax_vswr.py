"""КСВН-норма коаксиальной сборки по её комплектующим (владелец 22.10.2026: норма приёмки — из разъёмов и кабеля,
никогда не общая «≤1,3»).

Сборка меряется со стороны А (S11), сторона Б нагружена согласованной нагрузкой 50 Ом. Худший случай — отражения
разъёмов складываются в фазе:

    Γ = (КСВ − 1) / (КСВ + 1)                                  по каждому разъёму, КСВ — поле «КСВ» БД.КОАКС
    Γmax = Γ_A + Γ_B · 10^(−2 · A(f) · L / 20)                 отражение Б доходит до порта через кабель дважды
    КСВmax = (1 + Γmax) / (1 − Γmax)

A(f) — затухание кабеля, дБ/м (карточка кабеля «Коаксиальные кабели», точки затухания, лог-лог интерполяция), L — длина,
м. Полоса — от 1 МГц до меньшей из «Частота до, ГГц» разъёмов и рабочей частоты кабеля. Норма берётся в начале полосы,
где затухание меньше всего и отражение Б почти не гасится, — строгая; S21 = A(f_max) · L — справочно. Нет КСВ у любого
разъёма — норма не определена, с причиной, без подстановки значения."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping, Sequence

from .cable_structure import CoaxAttenuation, CoaxCard, card_for
from .domain import cable_key

VSWR_COLUMN = "КСВ"
MAX_GHZ_COLUMN = "Частота до, ГГц"
BAND_FROM_MHZ = 1.0
STATUS_OK, STATUS_UNDEFINED = "ok", "undefined"

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def parse_number(value: object) -> float | None:
    """The first number of a free-text cell: «≤1,43» → 1.43, «<2 (до 6 ГГц)» → 2, «1.35» → 1.35; none — None."""
    found = _NUMBER.search(str(value or ""))
    return float(found.group().replace(",", ".")) if found else None


def parse_vswr(value: object) -> float | None:
    """КСВ of a connector from the БД.КОАКС cell; a value below 1 is not a VSWR — None."""
    number = parse_number(value)
    return number if number is not None and number >= 1 else None


def parse_max_ghz(value: object) -> float | None:
    number = parse_number(value)
    return number if number is not None and number > 0 else None


def reflection(vswr: float) -> float:
    return (vswr - 1) / (vswr + 1)


def vswr_of(gamma: float) -> float:
    return (1 + gamma) / (1 - gamma)


def attenuation_curve(points: Iterable[CoaxAttenuation]) -> tuple[tuple[float, float], ...]:
    """(МГц, дБ/м) by frequency; several points of one frequency (nominal / typical / max, two sources) — the smallest:
    less loss gives the stricter norm."""
    curve: dict[float, float] = {}
    for point in points:
        freq, loss = float(point.freq_mhz), float(point.loss_db_m)
        if freq > 0 and loss > 0:
            curve[freq] = min(loss, curve.get(freq, loss))
    return tuple(sorted(curve.items()))


def loss_db_m(curve: Sequence[tuple[float, float]], freq_mhz: float) -> float | None:
    """Cable loss at a frequency, dB/m: log-log between the two neighbouring points; beyond the points — the slope of
    the two outer ones, √f (skin effect) when there is one point. No points — None."""
    if not curve or freq_mhz <= 0:
        return None
    if len(curve) == 1:
        freq, loss = curve[0]
        return loss * math.sqrt(freq_mhz / freq)
    for (f1, a1), (f2, a2) in zip(curve, curve[1:]):
        if freq_mhz <= f2:
            break  # below the first point — the first pair, above the last — the last pair
    slope = math.log(a2 / a1) / math.log(f2 / f1)
    return a1 * (freq_mhz / f1) ** slope


def _folded(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def connector_cells(rows: Iterable[Mapping[str, Any]], article: str) -> tuple[str, float | None, float | None]:
    """(КСВ text, КСВ, max GHz) of a connector from its БД.КОАКС rows (every row of the article: КСВ and frequency are
    the connector's, not of the pair). Rows that disagree — the worst: the highest КСВ, the lowest frequency."""
    key = _folded(article)
    mine = [fields for fields in rows if key and _folded(fields.get("coaxArticle") or fields.get("Артикул")) == key]
    texts = [str(fields.get(VSWR_COLUMN) or "").strip() for fields in mine]
    readable = [(parse_vswr(text), text) for text in texts if parse_vswr(text) is not None]
    if readable:
        vswr, text = max(readable, key=lambda item: item[0])
    else:
        vswr, text = None, next((text for text in texts if text), "")
    freqs = [value for value in (parse_max_ghz(fields.get(MAX_GHZ_COLUMN)) for fields in mine) if value is not None]
    return text, vswr, min(freqs) if freqs else None


def cable_card(cards: Iterable[CoaxCard], name: str) -> CoaxCard | None:
    """The card of the assembly's cable: by its name or one of its aliases; a family card («RG-58» for «RG-58 A/U») is
    only a suggestion — its loss would loosen the norm, so none."""
    card, exact = card_for(cable_key(name), {card.name_key: card for card in cards})
    return card if exact else None


def vswr_norm(
    ends: Sequence[tuple[str, str]], rows: Iterable[Mapping[str, Any]], card: CoaxCard | None, cable_name: str,
    length_mm: object,
) -> dict[str, Any]:
    """The norm of an assembly with connectors `ends` [("A", article), ("B", article)] on the cable `card` (None —
    no card: no loss, no frequency limit) of `length_mm`. Returns the ETK contract `coaxVswrNorm`:
    {status, reason, vswrMax, bandMhz: [from, to|None], s21Db, lengthM, cable: {name, lossDbM}, ends: [{side, article,
    vswrText, vswr, maxGhz}]}."""
    rows = list(rows)
    found = []
    for side, article in ends:
        text, vswr, max_ghz = connector_cells(rows, article)
        found.append({"side": side, "article": article, "vswrText": text, "vswr": vswr, "maxGhz": max_ghz})
    curve = attenuation_curve(card.attenuation) if card is not None else ()
    limits = [end["maxGhz"] for end in found if end["maxGhz"] is not None]
    if card is not None and card.max_operating_ghz is not None and card.max_operating_ghz > 0:
        limits.append(float(card.max_operating_ghz))
    band_to = min(limits) * 1000 if limits else None
    length = parse_number(length_mm)
    length_m = round(length / 1000, 4) if length else None
    loss_top = loss_db_m(curve, band_to) if band_to is not None else None
    s21 = round(loss_top * length_m, 2) if loss_top is not None and length_m else None
    norm: dict[str, Any] = {
        "status": STATUS_UNDEFINED, "reason": "", "vswrMax": None, "bandMhz": [BAND_FROM_MHZ, band_to],
        "s21Db": s21, "lengthM": length_m,
        "cable": {"name": card.name if card is not None else cable_name,
                  "lossDbM": round(loss_top, 4) if loss_top is not None else None},
        "ends": found,
    }
    missing = [end for end in found if end["vswr"] is None]
    if missing:
        without = [end["article"] for end in missing if not end["vswrText"]]
        unreadable = [f"{end['article']} («{end['vswrText']}»)" for end in missing if end["vswrText"]]
        reasons = ([f"у {', '.join(without)} нет КСВ в БД.КОАКС"] if without else []) + (
            [f"КСВ не читается: {', '.join(unreadable)}"] if unreadable else [])
        norm["reason"] = "; ".join(reasons)
        return norm
    loss_low = loss_db_m(curve, BAND_FROM_MHZ) if length_m else None
    through = 10 ** (-2 * loss_low * length_m / 20) if loss_low is not None and length_m else 1.0
    gammas = [reflection(end["vswr"]) for end in found]
    gamma = gammas[0] + sum(gammas[1:]) * through
    if gamma >= 1:
        norm["reason"] = "отражения разъёмов в сумме ≥ 1 — норма по комплектующим не определена"
        return norm
    norm.update(status=STATUS_OK, vswrMax=round(vswr_of(gamma), 2))
    if band_to is None:
        norm["reason"] = "верхняя граница полосы не определена — нет «Частота до» у разъёмов и рабочей частоты кабеля"
    return norm
