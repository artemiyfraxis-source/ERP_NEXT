"""«Пул запроса КП» and the supplier suggestion by purchase statistics (as Roller_Buyer RequestsPage + RfqCreatePanel).

The pool is the buyer's lines that can go into a request (`rfq.candidate_views`), grouped by the nomenclature category,
each with its item match and price history. Procurement facts (paid lines, prices) come only through the
`PurchaseStats` port (app/workflows/purchasing_rfq_stats.py); the directory, key suppliers and the rotation are
read here. Results are plain JSON-ready dicts (Decimals as strings)."""

from __future__ import annotations

import re

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.shared.identity import Actor

from app.shared import stage_options

from . import settings as purchasing_settings
from . import upd_prices
from .infrastructure import RfqSupplierRow, SupplierNameRow, SupplierRow
from .items import guess_category, normalize_unit
from .suppliers import is_stock_source
from .rfq import (
    LineView,
    ProcurementLines,
    Refused,
    UnitOfWork,
    _line_result,
    _live_lines,
    _own,
    _rfq,
    _session,
    candidate_views,
    category_code,
    num,
    stored_reason,
)
from .suppliers import supplier_key

GROUPS = (
    ("cable", "Кабель и провод"), ("connector", "Разъёмы, клеммы, наконечники"), ("ekb", "ЭКБ"),
    ("consumables", "Маркировка и расходники"), ("other", "Прочее"),
)
# A pool group only (nomenclature categories stay four): what would otherwise crowd «Прочее».
_CONSUMABLES = re.compile(r"маркиров|маркер|бирк|^тут\b|\bтут\s|термоусад|трубк|кембрик|стяжк|хомут|клей|герметик"
                          r"|лента|изолент|флюс|припой|спирт|перчатк|салфетк", re.IGNORECASE)

GROUP_LABEL = dict(GROUPS)

# ── the statistics port ─────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PastPrice:
    price: Decimal  # with VAT, roubles per unit
    on: date
    supplier: str | None
    unit: str | None


@dataclass(frozen=True)
class ItemMatch:
    id: UUID
    name: str
    category: str
    matched_by: str  # "link" | "part_key" | "alias"


@dataclass(frozen=True)
class LineInsight:
    category: str
    item: ItemMatch | None = None
    purchases: int = 0  # paid priced lines of the item
    last: PastPrice | None = None
    cheapest: PastPrice | None = None
    cheapest_same_unit: Decimal | None = None  # the lowest past price in the line's own unit


@dataclass(frozen=True)
class SupplierStat:
    text: str  # as procurement lines write it (the commonest spelling of one supplier key)
    lines: int
    last: date | None
    categories: tuple[str, ...] = ()
    items: frozenset[UUID] = frozenset()  # nomenclature items among these paid lines


@dataclass(frozen=True)
class Analysis:
    lines: dict[UUID, LineInsight]
    by_items: tuple[SupplierStat, ...] = ()  # paid lines of the same items as the given lines
    by_categories: tuple[SupplierStat, ...] = ()  # paid lines of the given lines' categories


class PurchaseStats(Protocol):
    def analyse(self, session: Session, views: Sequence[LineView], suppliers: bool = True) -> Analysis:
        """Insights per line; with `suppliers` also who supplied the same items / categories (the suggestion)."""
        ...


# ── the supplier directory, read once ───────────────────────────────────────────────────────────────────────────


class Directory:
    """Supplier texts → directory suppliers by `supplier_key` (the name, then the learned names), in two queries."""

    def __init__(self, session: Session):
        self.rows = list(session.scalars(select(SupplierRow)))
        self.by_key = {row.name_key: row for row in self.rows}
        by_id = {row.id: row for row in self.rows}
        self.learned = {key: by_id[supplier_id] for key, supplier_id in session.execute(
            select(SupplierNameRow.name_key, SupplierNameRow.supplier_id)) if supplier_id in by_id}

    def find(self, text: str | None) -> SupplierRow | None:
        """The supplier, archived or not (the caller decides); None — not in the directory."""
        key = supplier_key(text or "")
        if not key:
            return None
        return self.by_key.get(key) or self.learned.get(key)

    def active(self) -> list[SupplierRow]:
        return [row for row in self.rows if row.archived_at is None]


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


# ── the pool ────────────────────────────────────────────────────────────────────────────────────────────────────


def _price(value: PastPrice | None) -> dict | None:
    if value is None:
        return None
    return {"price": num(value.price), "date": value.on.isoformat(), "supplier": value.supplier, "unit": value.unit}


def cheaper_before_pct(budget: Decimal | None, cheapest_same_unit: Decimal | None) -> Decimal | None:
    """How much cheaper it was bought before than the budget, in percent (only when it was, in the same unit)."""
    if budget is None or budget <= 0 or cheapest_same_unit is None or cheapest_same_unit >= budget:
        return None
    return ((budget - cheapest_same_unit) / budget * 100).quantize(Decimal("0.1"))


def _extra_prices(session: Session, views: Sequence[LineView], analysis: Analysis) -> dict | None:
    """With references.price_book on: the lowest known UPD / reference price of each line's item in the line's unit."""
    if not stage_options.enabled("references.price_book"):
        return None
    wanted = []
    for view in views:
        item = analysis.lines[view.id].item if view.id in analysis.lines else None
        if item is not None and view.budget_unit_gross is not None:
            wanted.append((item.id, normalize_unit(view.unit)))
    vat = Decimal(purchasing_settings.load(session)["default_vat_rate"])
    return upd_prices.cheapest_known(session, wanted, vat)


def _cheaper(view: LineView, insight: LineInsight, extra: dict | None) -> dict:
    """«Раньше дешевле»: from procurement history; with references.price_book also from UPDs and reference prices
    (the lowest wins, its source is named)."""
    if extra is None:
        return {"cheaper_before_pct": num(cheaper_before_pct(view.budget_unit_gross, insight.cheapest_same_unit))}
    best, source = insight.cheapest_same_unit, "procurement" if insight.cheapest_same_unit is not None else None
    found = extra.get((insight.item.id, normalize_unit(view.unit))) if insight.item is not None else None
    if found is not None and (best is None or found["price"] < best):
        best, source = found["price"], found["source"]
    pct = cheaper_before_pct(view.budget_unit_gross, best)
    return {
        "cheaper_before_pct": num(pct),
        "cheaper_source": source if pct is not None else None,
        "cheaper_source_label": ({"procurement": "по закупкам", **upd_prices.SOURCE_LABELS}[source]
                                 if pct is not None and source else None),
    }


def _insight(analysis: Analysis, view: LineView) -> LineInsight:
    return analysis.lines.get(view.id) or LineInsight(category=guess_category(view.name, view.item_type))


def pool(uow: UnitOfWork, lines: ProcurementLines, stats: PurchaseStats, actor: Actor) -> dict:
    """The buyer's lines for a new request by category: each group with its count and the suppliers its lines were
    written with («изначально»), each line with its nomenclature match, price history and «раньше дешевле»."""
    with uow:
        session = _session(uow)
        views = candidate_views(session, lines, actor)
        analysis = stats.analyse(session, views, suppliers=False)
        directory = Directory(session)
        extra = _extra_prices(session, views, analysis)
        groups: dict[str, dict] = {}
        for view in sorted(views, key=lambda v: (v.name.casefold(), v.legacy_id)):
            insight = _insight(analysis, view)
            code = insight.category if insight.category in ("cable", "connector", "ekb") else (
                "consumables" if _CONSUMABLES.search(view.name or "") else "other")
            group = groups.setdefault(code, {"lines": [], "suppliers": {}, "from_stock": 0})
            text = _clean(view.supplier)
            if is_stock_source(text):  # «Склад» in the supplier column: from our stock, nobody to ask
                group["from_stock"] += 1
                text = ""
            known = directory.find(text) if text else None
            shown = known.name if known is not None else text  # the directory's spelling, archived or not
            supplier_id = str(known.id) if known is not None and known.archived_at is None else None
            if shown:
                slot = group["suppliers"].setdefault(supplier_key(shown), {"name": shown, "supplier_id": supplier_id, "lines": 0})
                slot["lines"] += 1
            item = insight.item
            group["lines"].append({
                **_line_result(view),
                "project": view.project,
                "category": code,
                "supplier_name": shown or None,
                "supplier_id": supplier_id,
                "item": {"id": str(item.id), "name": item.name, "category": item.category, "matched_by": item.matched_by}
                if item else None,
                "history": {
                    "purchases": insight.purchases, "last": _price(insight.last), "min": _price(insight.cheapest),
                } if insight.purchases else None,
                **_cheaper(view, insight, extra),
            })
        return {
            "total": len(views),
            "groups": [
                {
                    "category": code, "label": label, "count": len(groups[code]["lines"]),
                    "from_stock": groups[code]["from_stock"],
                    "suppliers": sorted(groups[code]["suppliers"].values(),
                                        key=lambda s: (-s["lines"], s["name"].casefold())),
                    "lines": groups[code]["lines"],
                }
                for code, label in GROUPS if code in groups
            ],
        }


# ── the suggestion ──────────────────────────────────────────────────────────────────────────────────────────────

REASON_RANK = {"items": 0, "category_history": 1, "original": 2, "key": 3, "rotation": 4, "category": 5}
REASON_LABEL = {
    "items": "поставлял эти позиции",
    "category_history": "поставлял такие же",
    "original": "в строке заявки",
    "key": "ключевой по категории",
    "rotation": "давно не спрашивали",
    "category": "по категории в справочнике",
}
CATEGORY_HISTORY_SHOWN = 10  # the busiest suppliers of the categories; the rest are found by name
CATEGORY_HISTORY_PRESELECTED = 3  # решено агентом: a whole category's history would mail half the directory


@dataclass
class _Entry:
    row: SupplierRow | None
    name: str
    reasons: dict[str, dict] = field(default_factory=dict)
    preselect: bool = False
    items: set[UUID] = field(default_factory=set)  # the lines' items it delivered
    history: set[str] = field(default_factory=set)  # the lines' categories it delivered
    originals: set[UUID] = field(default_factory=set)  # the lines written with it

    def add(self, code: str, *, lines: int | None = None, last: date | None = None, label: str | None = None,
            preselect: bool = False) -> None:
        if code not in self.reasons:
            self.reasons[code] = {
                "code": code, "label": label or REASON_LABEL[code], "lines": lines,
                "last_date": last.isoformat() if last else None,
            }
        self.preselect = self.preselect or preselect

    def sort_key(self) -> tuple:
        best = min(self.reasons.values(), key=lambda reason: REASON_RANK[reason["code"]])
        return (REASON_RANK[best["code"]], -(best["lines"] or 0), _neg_day(best["last_date"]), self.name.casefold())


def _neg_day(value: str | None) -> int:
    return -date.fromisoformat(value).toordinal() if value else 0


def _category_label(codes: Iterable[str]) -> str:
    names = [GROUP_LABEL[code].lower() if code != "ekb" else "ЭКБ" for code, _ in GROUPS if code in set(codes)]
    return f"{REASON_LABEL['category_history']} ({', '.join(names)})" if names else REASON_LABEL["category_history"]


def suggest(
    uow: UnitOfWork, lines: ProcurementLines, stats: PurchaseStats, actor: Actor, *,
    line_ids: Sequence[UUID] = (), rfq_id: UUID | None = None,
) -> dict:
    """Whom to ask for these lines (before the request exists) or for a request's lines, each supplier once with all
    its reasons, ranked: delivered these items > delivered the same category > written in the line > key supplier of
    the category > not asked for longest (rotation, unticked) > the rest of the category in the directory (unticked).
    Supplier texts not in the directory come with `supplier_id: null` — the client quick-adds them by name."""
    with uow:
        session = _session(uow)
        in_rfq: set[UUID] = set()
        if rfq_id is not None:
            rfq = _own(_rfq(session, rfq_id), actor)
            ids = [line.procurement_line_id for line in _live_lines(session, rfq.id)]
            in_rfq = set(session.scalars(select(RfqSupplierRow.supplier_id).where(RfqSupplierRow.rfq_id == rfq.id)))
        else:
            ids = list(dict.fromkeys(line_ids))
            if not ids:
                raise Refused("Выберите строки для запроса")
        found_views = lines.get_many(ids)
        views = [found_views[line_id] for line_id in ids if line_id in found_views]
        analysis = stats.analyse(session, views)
        categories = {_insight(analysis, view).category for view in views}
        directory = Directory(session)
        rotation_size = int(purchasing_settings.load(session)["rotation_non_key"])
        last_asked = dict(session.execute(
            select(RfqSupplierRow.supplier_id, func.max(RfqSupplierRow.sent_at))
            .where(RfqSupplierRow.sent_at.is_not(None)).group_by(RfqSupplierRow.supplier_id)
        ).all())

        entries: dict[tuple[str, object], _Entry] = {}

        def key_of(text: str) -> tuple[tuple[str, object], SupplierRow | None] | None:
            row = directory.find(text)
            if row is not None and row.archived_at is not None:
                return None  # archived suppliers are never suggested
            return (("id", row.id) if row is not None else ("text", supplier_key(text))), row

        def entry(text: str) -> _Entry | None:
            found = key_of(text)
            if found is None:
                return None
            key, row = found
            if key not in entries:
                entries[key] = _Entry(row, row.name if row is not None else _clean(text))
            return entries[key]

        def entry_of(row: SupplierRow) -> _Entry:
            return entries.setdefault(("id", row.id), _Entry(row, row.name))

        for stat in analysis.by_items:
            found = entry(stat.text)
            if found is not None:
                found.add("items", lines=stat.lines, last=stat.last, preselect=True)
                found.items.update(stat.items)
        # Every supplier's delivered categories (not only the shown ones): what relates it to each line.
        history: dict[tuple[str, object], set[str]] = {}
        for stat in analysis.by_categories:
            known = key_of(stat.text)
            if known is not None:
                history.setdefault(known[0], set()).update(stat.categories)
        rank = 0
        for stat in sorted(analysis.by_categories, key=lambda s: (-s.lines, -(s.last.toordinal() if s.last else 0))):
            found = entry(stat.text)
            if found is None:
                continue
            found.add("category_history", lines=stat.lines, last=stat.last, label=_category_label(stat.categories),
                      preselect=rank < CATEGORY_HISTORY_PRESELECTED)
            rank += 1
            if rank == CATEGORY_HISTORY_SHOWN:
                break
        originals: dict[str, list] = {}
        for view in views:
            text = _clean(view.supplier)
            if text and not is_stock_source(text):
                slot = originals.setdefault(supplier_key(text), [text, 0, []])
                slot[1] += 1
                slot[2].append(view.id)
        for text, count, line_ids_written in originals.values():
            found = entry(text)
            if found is not None:
                found.add("original", lines=count, preselect=True)
                found.originals.update(line_ids_written)

        relevant = sorted(
            (row for row in directory.active()
             if any(category_code(value) in categories for value in (row.categories or []))),
            key=lambda row: row.name.casefold(),
        )
        for row in relevant:
            if row.is_key:
                entry_of(row).add("key", preselect=True)
        non_key = sorted(
            (row for row in relevant if not row.is_key and ("id", row.id) not in entries),
            key=lambda row: (last_asked.get(row.id) is not None, last_asked.get(row.id) or datetime.min, row.name.casefold()),
        )
        for index, row in enumerate(non_key):
            asked = last_asked.get(row.id)
            entry_of(row).add("rotation" if index < rotation_size else "category", last=asked.date() if asked else None)

        for key, item in entries.items():
            item.history = history.get(key, set())
        line_insights = [(view.id, _insight(analysis, view)) for view in views]
        ordered = sorted(entries.values(), key=_Entry.sort_key)
        return {
            "categories": [code for code, _ in GROUPS if code in categories],
            "lines": len(views),
            "suggestions": [_suggestion(item, in_rfq, last_asked, line_insights) for item in ordered],
        }


def _line_reasons(item: _Entry, lines: Sequence[tuple[UUID, LineInsight]]) -> list[dict]:
    """Which of the chosen lines the supplier relates to and why (ranked codes): it delivered the line's item, it
    delivered the line's category, the line is written with it, its directory categories hold the line's category."""
    row = item.row
    directory = {category_code(value) for value in (row.categories or [])} if row is not None else set()
    directory_code = "key" if row is not None and row.is_key else "rotation" if "rotation" in item.reasons else "category"
    out = []
    for line_id, insight in lines:
        codes = []
        if insight.item is not None and insight.item.id in item.items:
            codes.append("items")
        if insight.category in item.history:
            codes.append("category_history")
        if line_id in item.originals:
            codes.append("original")
        if insight.category in directory:
            codes.append(directory_code)
        if codes:
            out.append({"line_id": str(line_id), "reasons": codes})
    return out


def _suggestion(item: _Entry, in_rfq: set[UUID], last_asked: dict[UUID, datetime],
                lines: Sequence[tuple[UUID, LineInsight]]) -> dict:
    reasons = sorted(item.reasons.values(), key=lambda reason: REASON_RANK[reason["code"]])
    row = item.row
    taken = row is not None and row.id in in_rfq
    asked = last_asked.get(row.id) if row is not None else None
    return {
        "supplier_id": str(row.id) if row is not None else None,
        "name": item.name,
        "is_key": bool(row and row.is_key),
        "categories": list(row.categories or []) if row is not None else [],
        "reasons": reasons,
        "reason": reasons[0]["code"],
        "rfq_reason": stored_reason(reasons[0]["code"]),
        "preselect": item.preselect and not taken,
        "in_rfq": taken,
        "last_asked_at": asked.isoformat() if asked else None,
        # The lines it relates to (procurement line ids) — ticked by default; none would mean every line.
        "lines": _line_reasons(item, lines),
    }
