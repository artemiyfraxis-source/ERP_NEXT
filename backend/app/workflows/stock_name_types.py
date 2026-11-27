"""Shared shapes of the full stock name (owner 27.11.2026: «где папа где мама, совершенно разные наименования»).

`stock_name.build_stock_name` turns what a lot knows into OUR full name: series and variant, вилка (папа) / розетка (мама)
by the contacts, pins, execution, cable, the lug's section and size — so two different articles never share a name.
`complete=False` means a source is missing (`missing` says which); such a name is never written, the lot goes to review.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LotHints:
    """What the stock row knows about the item; the builder never reads quantities, places or documents."""

    article: str
    item_type: str = ""
    manufacturer: str = ""
    name: str = ""
    upd_name: str = ""
    description: str = ""


@dataclass(frozen=True)
class StockName:
    name: str
    complete: bool
    missing: tuple[str, ...] = ()
    source: str = ""
    attrs: Mapping[str, str] = field(default_factory=dict)
