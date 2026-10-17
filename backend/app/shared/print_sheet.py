"""Shared shape for a printable tabular sheet.

No PDF/Doc rendering in this stack yet (Google exit horizon — cheap, temporary
mechanisms only). A module builds a `PrintSheet` server-side (so the column
layout and business text — e.g. "— ждём поставки —" — live in one place next
to the domain rules, not duplicated per screen) and the frontend renders it
in a browser print window via `frontend/src/print.ts`. Keep this contract
minimal and stable: several modules print through it (fulfillment, warehouse).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrintSheet:
    title: str
    subtitle: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    sign: str
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "note": self.note,
            "sign": self.sign,
        }
