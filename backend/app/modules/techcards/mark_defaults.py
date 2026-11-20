"""Последний выбор печатной маркировки проводов в проекте (владелец 20.11): генератор подставляет его в новую КБ того же
проекта — чем метить (трубка / лента), ширину ленты и Ø трубки маркиратора по Ø провода.

Источник — сохранённые рецепты генератора (`techcard_projects.inputs`, конфиг как пришёл): поле `wireMarks` конфига
(`inputs.wireMarks`; `inputs.config.wireMarks` — тот же рецепт в обёртке). Берётся самая новая (по `updated_at`) карта
проекта, где выбрана трубка или лента; лазер ОБ.019 и выключенная маркировка — не печать, такие карты пропускаются.
Ничего не сохранено — `{kind: null, tapeMm: null, tubeByDia: {}}`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .project_cards import ProjectTechcard

PRINTED_KINDS = frozenset({"tube", "tape"})
EMPTY: dict[str, Any] = {"kind": None, "tapeMm": None, "tubeByDia": {}}


def _mm(value: object) -> float | None:
    """«3,2» / "3.2" / 3.2 → 3.2; не число или ≤ 0 — None."""
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    return float(number)


def _dia_key(value: object) -> str | None:
    number = _mm(value)
    return None if number is None else format(Decimal(str(number)).normalize(), "f")


def _wire_marks(inputs: object) -> Mapping[str, Any] | None:
    if not isinstance(inputs, Mapping):
        return None
    for holder in (inputs, inputs.get("config")):
        marks = holder.get("wireMarks") if isinstance(holder, Mapping) else None
        if isinstance(marks, Mapping):
            return marks
    return None


def _kind(marks: Mapping[str, Any]) -> str | None:
    if not marks.get("enabled"):
        return None
    kind = str(marks.get("kind") or "tube").strip()  # нет поля — трубка, как до 20.11 (WireMarkCfg.kind)
    return kind if kind in PRINTED_KINDS else None


def defaults_of(marks: Mapping[str, Any]) -> dict[str, Any]:
    """`{kind, tapeMm, tubeByDia: {"<Ø провода>": Ø трубки}}` одного рецепта."""
    tube_by_dia: dict[str, float] = {}
    raw = marks.get("tubeByDia")
    for dia, tube in (raw.items() if isinstance(raw, Mapping) else ()):
        key, size = _dia_key(dia), _mm(tube)
        if key is not None and size is not None:
            tube_by_dia[key] = size
    return {"kind": _kind(marks), "tapeMm": _mm(marks.get("tapeMm")), "tubeByDia": tube_by_dia}


def project_mark_defaults(cards: Iterable[ProjectTechcard]) -> dict[str, Any]:
    """Выбор самой новой карты проекта с печатной маркировкой проводов; нет такой — пустой ответ."""
    for card in sorted(cards, key=lambda item: item.updated_at, reverse=True):
        marks = _wire_marks(card.inputs)
        if marks is not None and _kind(marks) is not None:
            return defaults_of(marks)
    return {**EMPTY, "tubeByDia": {}}
