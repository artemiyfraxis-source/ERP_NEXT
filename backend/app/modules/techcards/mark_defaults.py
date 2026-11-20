"""Последний выбор маркировки проводов в проекте (владелец 20.11, «последний выбор» буквально): генератор подставляет его
в новую КБ того же проекта — чем метила последняя собранная КБ («нет», трубка, лента, лазер), ширину ленты и Ø трубки
маркиратора по Ø провода.

Источник — сохранённые рецепты генератора (`techcard_projects.inputs`, конфиг как пришёл): поле `wireMarks` конфига
(`inputs.wireMarks`; `inputs.config.wireMarks` — тот же рецепт в обёртке). Рецепт «Сложной сборки» без поля — маркировка
выключена («none»: генератор пишет поле, только когда она включена); рецепт другой вкладки маркировки проводов не знает и
не считается. «Последняя» — по последней сборке рецепта (события `techcards.project_auto_built` / `project_rebuilt`
журнала: `updated_at` карты двигают и выдача, и правки); карта без такого события — по `updated_at`.
`kind` — самой новой карты; `tapeMm` и `tubeByDia` — самой новой карты, где они были. Ничего — `{kind: null, tapeMm:
null, tubeByDia: {}}`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.shared.persistence import AuditEventRow

from .project_cards import ProjectTechcard

KINDS = frozenset({"none", "tube", "tape", "laser"})
COMPLEX_TAB = "complex"
# The events that save a recipe into `inputs` (application.auto_build_project_techcard / rebuild_project_techcard).
BUILD_EVENTS = ("techcards.project_auto_built", "techcards.project_rebuilt")


def _mm(value: object) -> float | None:
    """«3,2» / "3.2" / 3.2 → 3.2; не число, бесконечность, NaN или ≤ 0 — None."""
    try:
        number = float(Decimal(str(value).strip().replace(",", ".")))
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _dia_key(value: object) -> str | None:
    number = _mm(value)
    return None if number is None else format(Decimal(str(number)).normalize(), "f")


def _wire_marks(inputs: object) -> Mapping[str, Any] | None:
    """The recipe's wire-mark settings: its `wireMarks`, {} — a «Сложная сборка» recipe without them (marks off),
    None — a recipe that knows no wire marks."""
    if not isinstance(inputs, Mapping):
        return None
    for holder in (inputs, inputs.get("config")):
        if not isinstance(holder, Mapping):
            continue
        marks = holder.get("wireMarks")
        if isinstance(marks, Mapping):
            return marks
        if holder.get("tab") == COMPLEX_TAB:
            return {}
    return None


def _kind(marks: Mapping[str, Any]) -> str:
    if not marks.get("enabled"):
        return "none"
    kind = str(marks.get("kind") or "tube").strip()  # нет поля — трубка, как до 20.11 (WireMarkCfg.kind)
    return kind if kind in KINDS else "none"


def _tube_by_dia(marks: Mapping[str, Any]) -> dict[str, float]:
    raw = marks.get("tubeByDia")
    found: dict[str, float] = {}
    for dia, tube in (raw.items() if isinstance(raw, Mapping) else ()):
        key, size = _dia_key(dia), _mm(tube)
        if key is not None and size is not None:
            found[key] = size
    return found


def defaults_of(marks: Mapping[str, Any]) -> dict[str, Any]:
    """`{kind, tapeMm, tubeByDia: {"<Ø провода>": Ø трубки}}` одного рецепта."""
    return {"kind": _kind(marks), "tapeMm": _mm(marks.get("tapeMm")), "tubeByDia": _tube_by_dia(marks)}


def last_builds(session: Session, card_ids: Iterable[UUID]) -> dict[UUID, datetime]:
    """When each card's recipe was last built (the build events of the audit journal)."""
    ids = list(card_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(AuditEventRow.aggregate_id, func.max(AuditEventRow.occurred_at))
        .where(AuditEventRow.event_type.in_(BUILD_EVENTS), AuditEventRow.aggregate_id.in_(ids))
        .group_by(AuditEventRow.aggregate_id)
    )
    return {card_id: built for card_id, built in rows if built is not None}


def project_mark_defaults(cards: Iterable[ProjectTechcard], built: Mapping[UUID, datetime] | None = None) -> dict[str, Any]:
    """The last choice of the project: `kind` of the newest recipe that knows wire marks, `tapeMm` / `tubeByDia` of the
    newest one that had them."""
    built = built or {}
    recipes = [(built.get(card.id) or card.updated_at, marks) for card in cards
               if (marks := _wire_marks(card.inputs)) is not None]
    recipes.sort(key=lambda item: item[0], reverse=True)
    result: dict[str, Any] = {"kind": None, "tapeMm": None, "tubeByDia": {}}
    for _when, marks in recipes:
        found = defaults_of(marks)
        if result["kind"] is None:
            result["kind"] = found["kind"]
        if result["tapeMm"] is None:
            result["tapeMm"] = found["tapeMm"]
        if not result["tubeByDia"]:
            result["tubeByDia"] = found["tubeByDia"]
    return result
