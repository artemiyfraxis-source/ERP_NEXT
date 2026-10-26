"""КСВН-норма коаксиальной сборки по её комплектующим (владелец 22.10.2026: норма приёмки — из разъёмов и кабеля,
никогда не общая «≤1,3»).

Сборка меряется со стороны А (S11), сторона Б нагружена согласованной нагрузкой 50 Ом. Худший случай — отражения
разъёмов складываются в фазе:

    Γ = (КСВ − 1) / (КСВ + 1)                                  по каждому разъёму, КСВ — поле «КСВ» БД.КОАКС
    Γmax = Γ_A + Γ_B · 10^(−2 · A(f) · L / 20)                 отражение Б доходит до порта через кабель дважды
    КСВmax = (1 + Γmax) / (1 − Γmax)

A(f) — затухание кабеля, дБ/м (карточка кабеля «Коаксиальные кабели», точки затухания, лог-лог интерполяция), L — длина,
м. Полоса — от 1 МГц до меньшей из «Частота до, ГГц» разъёмов и кабеля (у кабеля — его ячейка «Частота до, ГГц» в
«Коаксиальных кабелях», пустая — рабочая частота карточки; то же значение показывает таблица, владелец 25.10.2026). Норма берётся в начале полосы,
где затухание меньше всего и отражение Б почти не гасится, — строгая; S21 = A(f_max) · L — справочно. Нет КСВ у любого
разъёма — норма не определена, с причиной, без подстановки значения."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Any, Iterable, Mapping, Sequence

from .cable_structure import CoaxAttenuation, CoaxCard, card_for
from .domain import CABLE_MAX_GHZ_FIELD, cable_key

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


def cable_max_ghz(wire_rows: Iterable[Mapping[str, Any]], card: CoaxCard | None, name: str) -> float | None:
    """«Частота до, ГГц» of the cable as «Коаксиальные кабели» shows it (owner 25.10.2026: one frequency field, as at the
    connectors): the БД.ПРОВ cell of the cable's row (by its name or its card's), else the rated maximum operating
    frequency of its exact card; never the cut-off."""
    keys = {cable_key(name)} | ({card.name_key} if card is not None else set())
    keys.discard("")
    for row in wire_rows:
        if cable_key(row.get("Марка")) in keys:
            value = parse_max_ghz(row.get(CABLE_MAX_GHZ_FIELD))
            if value is not None and value > 0:
                return value
    if card is not None and card.max_operating_ghz is not None and card.max_operating_ghz > 0:
        return float(card.max_operating_ghz)
    return None


def cable_standard(wire_rows: Iterable[Mapping[str, Any]], name: str) -> str:
    """«Стандарт» of the cable's БД.ПРОВ row (owner 25.10.2026: the passport prints «RG-316 (MIL-DTL-17/113)»); '' — no
    row or the cell is empty (the passport prints «—», never a guess)."""
    key = cable_key(name)
    if not key:
        return ""
    return next((str(row.get("Стандарт") or "").strip() for row in wire_rows
                 if cable_key(row.get("Марка")) == key and str(row.get("Стандарт") or "").strip()), "")


def vswr_norm(
    ends: Sequence[tuple[str, str]], rows: Iterable[Mapping[str, Any]], card: CoaxCard | None, cable_name: str,
    length_mm: object, wire_rows: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The norm of an assembly with connectors `ends` [("A", article), ("B", article)] on the cable `card` (None —
    no card: no loss, no frequency limit) of `length_mm`. Returns the ETK contract `coaxVswrNorm`:
    {status, reason, vswrMax, bandMhz: [from, to|None], s21Db, lengthM, cable: {name, lossDbM}, ends: [{side, article,
    vswrText, vswr, maxGhz}]}. `wire_rows` — БД.ПРОВ rows: the cable's «Частота до, ГГц» (`cable_max_ghz`)."""
    rows = list(rows)
    found = []
    for side, article in ends:
        text, vswr, max_ghz = connector_cells(rows, article)
        found.append({"side": side, "article": article, "vswrText": text, "vswr": vswr, "maxGhz": max_ghz})
    curve = attenuation_curve(card.attenuation) if card is not None else ()
    limits = [end["maxGhz"] for end in found if end["maxGhz"] is not None]
    cable_ghz = cable_max_ghz(wire_rows, card, cable_name)
    if cable_ghz is not None:
        limits.append(cable_ghz)
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
        norm["reason"] = "верхняя граница полосы не определена — нет «Частота до» у разъёмов и кабеля"
    return norm


# --- Норма заказчика (владелец 23.10.2026: «норма заказчика — на этапе ТК; я главный на производстве по технологии») ----
#
# Технолог задаёт в генераторе (КОАКС, под «Контроль КСВН») норму из КД заказчика: КСВН ≤ x в полосе от … до МГц и
# основание (ТУ / КД / заказ) — `build_config.coax.vswrCustomer` {vswrMax, fromMhz, toMhz, basis}. Задана — она и есть
# норма приёмки (`source: "customer"`), вердикт ОТК — по точкам её полосы; норма по комплектующим остаётся справочно
# (`components`). Не задана — норма по комплектующим, как раньше.

SOURCE_CUSTOMER = "customer"
COMPONENT_KEYS = ("status", "reason", "vswrMax", "bandMhz", "s21Db", "ends")


class CustomerNormError(ValueError):
    """The technologist's customer norm cannot be read; the message is for the screen."""


def _strict_number(value: object) -> float | None:
    text = str(value if value is not None else "").strip().replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        raise CustomerNormError(f"Норма заказчика: «{value}» — не число") from None
    if not math.isfinite(number):
        raise CustomerNormError(f"Норма заказчика: «{value}» — не число")
    return number


def customer_norm(raw: object) -> dict[str, Any] | None:
    """`build_config.coax.vswrCustomer` read and checked: {vswrMax, fromMhz, toMhz, basis}. Not set or no КСВН — None
    (the norm by components). КСВН < 1, no band or «от» ≥ «до», no basis — CustomerNormError with the Russian reason."""
    if not isinstance(raw, Mapping):
        return None
    vswr_max = _strict_number(raw.get("vswrMax"))
    if vswr_max is None:
        return None
    if vswr_max < 1:
        raise CustomerNormError("Норма заказчика: КСВН не может быть меньше 1")
    low, high = _strict_number(raw.get("fromMhz")), _strict_number(raw.get("toMhz"))
    if low is None or high is None:
        raise CustomerNormError("Норма заказчика: укажите полосу — от и до, МГц")
    if low <= 0 or low >= high:
        raise CustomerNormError("Норма заказчика: начало полосы должно быть больше 0 и меньше конца")
    basis = " ".join(str(raw.get("basis") or "").split())
    if not basis:
        raise CustomerNormError("Норма заказчика: укажите основание — ТУ, КД или заказ")
    return {"vswrMax": round(vswr_max, 3), "fromMhz": round(low, 3), "toMhz": round(high, 3), "basis": basis[:300]}


def components_of(norm: Mapping[str, Any]) -> dict[str, Any]:
    """The norm by components inside a norm: `components` of a customer norm, else the norm itself."""
    if norm.get("source") == SOURCE_CUSTOMER and isinstance(norm.get("components"), Mapping):
        return dict(norm["components"])
    return {key: norm.get(key) for key in COMPONENT_KEYS if key in norm}


def customer_of(norm: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The customer requirement a norm carries ({vswrMax, fromMhz, toMhz, basis}); a norm by components — None."""
    if not isinstance(norm, Mapping) or norm.get("source") != SOURCE_CUSTOMER:
        return None
    customer = norm.get("customer")
    return dict(customer) if isinstance(customer, Mapping) else None


def with_customer_norm(components: Mapping[str, Any], customer: Mapping[str, Any]) -> dict[str, Any]:
    """The acceptance norm when the technologist set the customer's: status "ok", КСВН and band — the customer's,
    `basis` — its text, `components` — the norm by components for reference (also when it is not defined); the
    assembly facts (cable, length, ends) stay where the passport reads them."""
    base = components_of(components)
    norm = {key: deepcopy(value) for key, value in components.items() if key not in ("source", "basis", "customer", "components")}
    norm.update(
        status=STATUS_OK, reason="", vswrMax=customer["vswrMax"], bandMhz=[customer["fromMhz"], customer["toMhz"]],
        source=SOURCE_CUSTOMER, basis=customer["basis"], customer=dict(customer), components=deepcopy(base),
    )
    return norm
