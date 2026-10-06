"""Length rules of the harness model: cut length with signed end allowances, cable sheath strip, coax strip steps,
terminal L+/L−, covering length.

The rules are TECHMAP-GRAPHER's (© Василий, SaveOurSouls, commit e63befb, used with the author's permission),
re-expressed here and checked against his docs and examples (docs/acceptance-examples.md EX-01,
docs/m3-03-wire-cut-length.md, docs/data-contracts.md). Each function names the original it follows; where the
original misbehaves the difference is stated in the docstring and pinned by a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable, Mapping, Sequence

from .model import Cable, Contact, Covering, Dimension, StripLayer, Topology, Wire

MAX_LENGTH_MM = Decimal(1_000_000_000)
DEFAULT_ROUNDING_STEP_MM = Decimal(1)
# docs/data-contracts.md («L1…Ln: 0, пусто, «-» означают отсутствие слоя»)
NO_VALUE = ("", "-", "—", "–")

COVERING_KINDS = {
    "heat-shrink": "Термоусадка", "nylon": "Нейлонка", "braid": "Оплётка",
    "metal-braid": "Металлическая плетёнка", "tape": "Обмотка", "band": "Нитевый бандаж",
}


class HarnessRuleError(ValueError):
    """A value the rules refuse (Russian message)."""


def to_decimal(value: object) -> Decimal | None:
    """Number or numeric text («1,5» too); None for empty, «-» and anything non-numeric."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value)) if value == value and value not in (float("inf"), float("-inf")) else None
    text = str(value).strip().replace(",", ".")
    if text in NO_VALUE:
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def micrometres(value: Decimal, name: str) -> int:
    """Lengths are exact to 0.001 mm (toExactMicrometres, src/Techmap.Client/src/editor/model.ts)."""
    scaled = value * 1000
    if scaled != scaled.to_integral_value():
        raise HarnessRuleError(f"{name}: точность не более 0,001 мм")
    return int(scaled)


def from_micrometres(value: int) -> Decimal:
    return (Decimal(value) / 1000).normalize() if value else Decimal(0)


@dataclass(frozen=True)
class CutLength:
    length_mm: Decimal | None
    correction_a_mm: Decimal
    correction_b_mm: Decimal
    rounding_step_mm: Decimal
    unrounded_mm: Decimal | None
    cut_mm: Decimal | None

    @property
    def is_complete(self) -> bool:
        return self.cut_mm is not None


def cut_length(
    length_mm: Decimal | None,
    correction_a_mm: Decimal = Decimal(0),
    correction_b_mm: Decimal = Decimal(0),
    rounding_step_mm: Decimal = DEFAULT_ROUNDING_STEP_MM,
) -> CutLength:
    """Blank length = drawing length + allowance A + allowance B (signed), rounded UP to the step once, at the end.
    Follows calculateWireCutLength (src/Techmap.Client/src/editor/model.ts) = CutLengthCalculator.Calculate
    (src/Techmap.Domain/CutLengthCalculator.cs). EX-01: 1000 + (10−2) + (20+3) = 1031. Unknown length → no cut."""
    if length_mm is not None and (length_mm < 0 or length_mm > MAX_LENGTH_MM):
        raise HarnessRuleError("Длина должна быть неотрицательным числом в миллиметрах")
    for value, name in ((correction_a_mm, "Поправка конца А"), (correction_b_mm, "Поправка конца Б")):
        if abs(value) > MAX_LENGTH_MM:
            raise HarnessRuleError(f"{name} задана неверно")
    if rounding_step_mm <= 0 or rounding_step_mm > MAX_LENGTH_MM:
        raise HarnessRuleError("Шаг округления длины резки должен быть положительным")
    if length_mm is None:
        return CutLength(None, correction_a_mm, correction_b_mm, rounding_step_mm, None, None)
    total = (
        micrometres(length_mm, "Длина") + micrometres(correction_a_mm, "Поправка конца А")
        + micrometres(correction_b_mm, "Поправка конца Б")
    )
    if total < 0:
        raise HarnessRuleError("Длина с поправками получилась отрицательной")
    step = micrometres(rounding_step_mm, "Шаг округления")
    remainder = total % step
    rounded = total if remainder == 0 else total + step - remainder
    return CutLength(length_mm, correction_a_mm, correction_b_mm, rounding_step_mm,
                     from_micrometres(total), from_micrometres(rounded))


def blank_cut(item: Wire | Cable) -> CutLength:
    return cut_length(item.length_mm, item.correction_a_mm, item.correction_b_mm, item.rounding_step_mm)


@dataclass(frozen=True)
class SheathStrip:
    a_mm: Decimal | None            # sheath removed at end A, as drawn
    b_mm: Decimal | None
    cut_a_mm: Decimal | None        # …with the end allowance (the allowance is extra core outside the sheath)
    cut_b_mm: Decimal | None


def sheath_strip(cable: Cable) -> SheathStrip | None:
    """Follows calculateCableSheathStrip (src/Techmap.Client/src/editor/model.ts): a non-zero strip grows by the
    end allowance, 0 stays 0 (no strip at that end); both strips together may not exceed the cable."""
    if not cable.has_sheath_strip:
        return None
    corrected: dict[str, int | None] = {}
    for side, value, correction in (("A", cable.sheath_a_mm, cable.correction_a_mm),
                                    ("B", cable.sheath_b_mm, cable.correction_b_mm)):
        if value is None:
            corrected[side] = None
            continue
        if value < 0 or value > MAX_LENGTH_MM:
            raise HarnessRuleError("Снятие оболочки должно быть неотрицательным")
        um = 0 if value == 0 else micrometres(value, "Снятие оболочки") + micrometres(correction, "Поправка")
        if um < 0:
            raise HarnessRuleError("Снятие оболочки с поправкой получилось отрицательным")
        corrected[side] = um
    raw_sum = sum(micrometres(v, "Снятие оболочки") for v in (cable.sheath_a_mm, cable.sheath_b_mm) if v is not None)
    if cable.length_mm is not None and raw_sum > micrometres(cable.length_mm, "Длина кабеля"):
        raise HarnessRuleError("Снятие оболочки длиннее кабеля")
    unrounded = blank_cut(cable).unrounded_mm
    if unrounded is not None and sum(v or 0 for v in corrected.values()) > micrometres(unrounded, "Длина кабеля"):
        raise HarnessRuleError("Снятие оболочки с поправками длиннее заготовки")
    return SheathStrip(
        cable.sheath_a_mm, cable.sheath_b_mm,
        None if corrected["A"] is None else from_micrometres(corrected["A"]),
        None if corrected["B"] is None else from_micrometres(corrected["B"]),
    )


def normalize_strip_layers(raw: Iterable[Mapping[str, object]]) -> tuple[tuple[StripLayer, ...], list[str]]:
    """Coax strip layers L1…Ln, cumulative from the cut end, from the centre conductor outwards.
    Follows normalizeWireStripProfileLayers (src/Techmap.Client/src/editor/model.ts), except: an L of 0, empty or
    «-» is «no layer» and is skipped, as docs/data-contracts.md defines it — the original refuses the whole profile
    there. An out-of-order layer is kept with a warning instead of refusing the profile."""
    layers: list[StripLayer] = []
    warnings: list[str] = []
    previous_index, previous_length, previous_diameter = 0, Decimal(0), Decimal(0)
    for position, item in enumerate(raw, start=1):
        index_value = to_decimal(item.get("index"))
        index = int(index_value) if index_value is not None and index_value == index_value.to_integral_value() else position
        raw_length = item.get("stripLengthMm")
        length = to_decimal(raw_length)
        if raw_length is None or (isinstance(raw_length, str) and raw_length.strip() in NO_VALUE) or length == 0:
            continue
        if length is None or length < 0:
            warnings.append(f"L{index} «{raw_length}» — не длина, слой пропущен")
            continue
        try:
            micrometres(length, f"L{index}")
        except HarnessRuleError as error:
            warnings.append(str(error))
            continue
        diameter = to_decimal(item.get("diameterMm"))
        if index <= previous_index or length <= previous_length or (diameter is not None and diameter <= previous_diameter):
            warnings.append(f"L{index}: слои должны идти по возрастанию номера, длины и диаметра")
        layers.append(StripLayer(index, length, diameter))
        previous_index, previous_length = index, length
        previous_diameter = diameter if diameter is not None else previous_diameter
    return tuple(layers), warnings


def strip_steps(layers: Sequence[StripLayer]) -> list[tuple[StripLayer, Decimal]]:
    """Each layer with the length it adds over the previous one (calculateWireStripSteps, model.ts)."""
    result, previous = [], Decimal(0)
    for layer in layers:
        result.append((layer, layer.length_mm - previous))
        previous = layer.length_mm
    return result


def terminal_label(key: str) -> str:
    """A complete БД.ТЕР key «n:maker|n:reel|n:piece|n:series» shows the reel article, else the piece one; any other
    text stays as is (terminalArticleLabel, src/Techmap.Client/src/editor/terminal-article-label.ts)."""
    parts: list[str] = []
    offset = 0
    while offset < len(key):
        colon = key.find(":", offset)
        if colon < 0 or not re.fullmatch(r"\d+", key[offset:colon]):
            return key
        end = colon + 1 + int(key[offset:colon])
        if end > len(key):
            return key
        parts.append(key[colon + 1:end])
        if end == len(key):
            return (parts[1] or parts[2] or key) if len(parts) == 4 else key
        if key[end] != "|":
            return key
        offset = end + 1
    return key


def terminal_name(contact: Contact) -> str:
    snapshot = contact.terminal
    return (snapshot.article if snapshot and snapshot.article else "") or terminal_label(contact.terminal_article)


def signed_l_minus(value: Decimal | None) -> Decimal | None:
    """L− always shortens the blank whatever sign the catalog cell has (terminal-details.ts)."""
    return None if value is None else -abs(value)


def covering_kind(kind: str, name: str) -> str:
    """Explicit kind, else guessed from the name (coveringKind, src/Techmap.Client/src/editor/physical-coverings.ts)."""
    if kind:
        return kind
    lowered = name.lower()
    for marker, guessed in (("термо", "heat-shrink"), ("нейлон", "nylon"), ("метал", "metal-braid"),
                            ("бандаж", "band"), ("обмот", "tape"), ("лент", "tape")):
        if marker in lowered:
            return guessed
    return "braid"


def covering_length(covering: Covering, dimensions: Sequence[Dimension]) -> Decimal | None:
    """A manual length wins; an automatic one is the sum of drawing dimensions bound exactly to the span anchors —
    drawing pixels never become millimetres (coveringMeasuredLength, physical-coverings.ts). Differs from the
    original: a covering with no spans has no length (None), not 0."""
    if covering.length_mode == "manual" or (covering.length_mode == "" and covering.length_mm is not None):
        return covering.length_mm
    if not covering.spans:
        return None
    total = 0
    for span in covering.spans:
        if span.start_anchor is None or span.stop_anchor is None:
            return None
        own = [item for item in dimensions if item.segment_id == span.segment_id and not item.auxiliary]
        exact = next((item for item in own if item.start == span.start_anchor and item.stop == span.stop_anchor), None)
        if exact is not None:
            if exact.length_mm is None:
                return None
            total += round(exact.length_mm * 1000)
            continue
        cursor = span.start_anchor
        for item in sorted((d for d in own if d.start >= span.start_anchor and d.stop <= span.stop_anchor),
                           key=lambda d: d.start):
            if item.start != cursor or item.length_mm is None:
                return None
            total += round(item.length_mm * 1000)
            cursor = item.stop
        if cursor != span.stop_anchor:
            return None
    return from_micrometres(total)


def covering_segments(topology: Topology, covering: Covering) -> list[str]:
    """Physical segments under a covering; a span over a common pipe covers the pipe's member segments."""
    result: list[str] = []
    for span in covering.spans:
        for segment in topology.pipe_segments.get(span.segment_id, (span.segment_id,)):
            if segment not in result:
                result.append(segment)
    return result


def covering_wires(topology: Topology, covering: Covering) -> list[str]:
    segments = set(covering_segments(topology, covering))
    return [wire_id for wire_id, route in topology.wire_segments.items() if segments.intersection(route)]
