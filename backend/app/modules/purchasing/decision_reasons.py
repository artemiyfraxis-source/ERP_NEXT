"""Roller_Buyer's fixed reason lists of a decision (packages/shared/src/decisions.ts), pure.

A reason is a code from the list; «Другое» (other) needs the buyer's own words. The decision keeps both: the code (for
the statistics) and the text the buyer reads — the label, or the words for «Другое»."""

from __future__ import annotations

GATE_OVERRIDE_REASONS: dict[str, str] = {
    "urgent": "Срочно",
    "single_source": "Единственный производитель или поставщик",
    "repeat_fresh_quote": "Повторная закупка по свежему КП",
    "other": "Другое",
}
NOT_CHEAPEST_REASONS: dict[str, str] = {
    "lead_time": "Срок",
    "availability": "Наличие",
    "moq": "Минимальная партия",
    "quality": "Качество или происхождение",
    "other": "Другое",
}


class ReasonError(ValueError):
    pass


def reason_text(code: str | None, note: str | None, labels: dict[str, str], what: str) -> str | None:
    """The text stored with the code: the label, or the buyer's words for «Другое». None — no code."""
    if code is None or code == "":
        return None
    if code not in labels:
        raise ReasonError(f"{what}: неизвестная причина")
    if code == "other":
        words = " ".join((note or "").split())
        if not words:
            raise ReasonError(f"{what}: «Другое» — опишите причину")
        return words[:2000]
    return labels[code]
