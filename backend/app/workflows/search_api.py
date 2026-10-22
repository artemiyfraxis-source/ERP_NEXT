"""Global search (Ctrl+K in the portal): one request, a few groups of at most 8 results.

Words in any order, case / ё / Latin-Cyrillic look-alikes folded (the warehouse word search's `fold`). Every group is
narrowed in SQL first — LIKE on the most selective word, only the columns shown — and then checked by words in Python,
so a few thousand rows cost a few milliseconds. A group is searched only when the principal may view its section
(the same keys the routes check).

A result tells the frontend where to go: `section` (a portal section id) and `params` (URL query the page reads)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing.deps import purchasing_uow_factory
from app.modules.purchasing.infrastructure import (
    InternalProjectRow, ItemAliasRow, ItemRow, RfqRow, SupplierRow,
)
from app.modules.warehouse.infrastructure import StockLotRow
from app.modules.warehouse.word_search import _FOLD, fold, matches_words
from app.shared import sqlite_casefold  # noqa: F401 — erp_casefold() on SQLite connections
from app.shared.http_auth import Principal, authenticated_principal, section_allowed

router = APIRouter(prefix="/api/search", tags=["search"])

PER_GROUP = 8
MAX_QUERY = 120
# A GRN: letters, year, serial, optional package — «PPM.26.0000001», «FRX.26.0000003-П2».
GRN_LIKE = re.compile(r"^[a-zа-яё]{2,6}\.\d{2}\.\d{3,}(?:[-\s]\S{1,6})?$", re.IGNORECASE)

_AMBIGUOUS = {chr(code) for code in _FOLD} | set(_FOLD.values()) | {"ё", "е"}

G_LINES, G_PROJECTS, G_ITEMS = "Строки закупки", "Проекты", "Номенклатура"
G_SUPPLIERS, G_STOCK, G_RFQ = "Поставщики", "Склад", "Запросы КП"


def _result(group: str, title: str, subtitle: str, section: str, **params: str) -> dict[str, Any]:
    return {"group": group, "title": title, "subtitle": subtitle, "section": section, "params": params}


def _prefilter(query: str) -> list[str]:
    """LIKE patterns for the most selective word. Look-alikes and ё/е become `_` (any one character), a Cyrillic
    the pattern is casefolded and compared case-insensitively by `_narrow`. [] — no prefilter."""
    # A word with a dash may span two columns (GRN «…0000003-П2» = grn + package): any dash-free part is enough.
    raw = [part for word in query.strip().casefold().split() for part in word.split("-") if part]
    if not raw:
        return []

    def solid(word: str) -> int:
        return sum(1 for char in word if char not in _AMBIGUOUS)

    best = max(raw, key=lambda word: (solid(word), len(word)))
    if solid(best) == 0:
        return []
    masked = "".join(
        "_" if char in _AMBIGUOUS else ("\\" + char if char in "%_\\" else char) for char in best
    )
    return [f"%{masked}%"]


def _narrow(columns: Iterable[Any], patterns: list[str], session: Session):
    """PostgreSQL ILIKE folds Unicode case; SQLite compares the Unicode-casefolded text (sqlite_casefold)."""
    if not patterns:
        return None
    if session.get_bind().dialect.name == "sqlite":
        return or_(*[func.erp_casefold(column).like(pattern, escape="\\") for column in columns for pattern in patterns])
    return or_(*[column.ilike(pattern, escape="\\") for column in columns for pattern in patterns])


def _scan(session: Session, columns: list[Any], patterns: list[str], match_columns: Iterable[Any],
          *, order: Any = None, base: Any = None) -> Iterator[Any]:
    statement = select(*columns)
    if base is not None:
        statement = statement.where(base)
    narrow = _narrow(match_columns, patterns, session)
    if narrow is not None:
        statement = statement.where(narrow)
    if order is not None:
        statement = statement.order_by(order)
    yield from session.execute(statement)


def _text(*parts: object) -> str:
    return fold(" ".join(str(part) for part in parts if part))


def _hit(words: list[str], text: str) -> bool:
    return all(word in text for word in words)


def _dot(*parts: object) -> str:
    return " · ".join(str(part) for part in parts if part)


def _lines(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = ProcurementLineRow
    columns = [row.legacy_id, row.calculated_name, row.design_article, row.order_number, row.supplier,
               row.project_reference, row.designation, row.invoice_item_name]
    found: list[dict] = []
    for legacy, name, article, order, supplier, project, designation, invoice in _scan(
        session, columns, patterns, columns, order=row.created_at.desc(),
    ):
        if _hit(words, _text(legacy, name, article, order, supplier, project, designation, invoice)):
            found.append(_result(
                G_LINES, _dot(legacy, name),
                _dot(project or designation, article, f"№ {order}" if order else "", supplier),
                "procurement", tab="sheet", q=legacy,
            ))
            if len(found) >= PER_GROUP:
                break
    return found


def _projects(session: Session, request: Request, words: list[str], patterns: list[str]) -> list[dict]:
    found: list[dict] = []
    registry = getattr(request.app.state, "project_registry", None)
    numbers = (registry.open_projects() if registry is not None else None) or []
    for number in numbers:
        if number.startswith("ВП-"):
            continue  # internal projects come from their own table, with names
        if _hit(words, _text(number)):
            found.append(_result(G_PROJECTS, number, "Проект", "projects", project=number))
            if len(found) >= PER_GROUP:
                return found
    row = InternalProjectRow
    columns = [row.number, row.name, row.purpose, row.closed_at]
    for number, name, purpose, closed in _scan(session, columns, patterns, [row.number, row.name],
                                               order=row.number):
        if _hit(words, _text(number, name)):
            found.append(_result(
                G_PROJECTS, _dot(number, name), _dot("Внутренний проект", purpose, "закрыт" if closed else ""),
                "projects", tab="internal",
            ))
            if len(found) >= PER_GROUP:
                break
    return found[:PER_GROUP]


def _items(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row, alias = ItemRow, ItemAliasRow
    found: dict[Any, dict] = {}
    for item_id, name, part, category in _scan(
        session, [row.id, row.name, row.part_number, row.category], patterns, [row.name, row.part_number],
        order=row.name, base=row.archived_at.is_(None),
    ):
        if _hit(words, _text(name, part)):
            found[item_id] = _result(G_ITEMS, name, _dot(part, category), "procurement", tab="items", q=name)
            if len(found) >= PER_GROUP:
                return list(found.values())
    statement = select(row.id, row.name, row.part_number, alias.alias_text).join(
        alias, alias.item_id == row.id).where(row.archived_at.is_(None))
    narrow = _narrow([alias.alias_text], patterns, session)
    if narrow is not None:
        statement = statement.where(narrow)
    for item_id, name, part, alias_text in session.execute(statement):
        if item_id not in found and _hit(words, _text(name, part, alias_text)):
            found[item_id] = _result(G_ITEMS, name, _dot(part, f"у поставщика: {alias_text}"), "procurement",
                                     tab="items", q=name)
            if len(found) >= PER_GROUP:
                break
    return list(found.values())


def _suppliers(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = SupplierRow
    found: list[dict] = []
    for name, inn, key in _scan(session, [row.name, row.inn, row.is_key], patterns, [row.name, row.inn],
                                order=row.name, base=row.archived_at.is_(None)):
        if _hit(words, _text(name, inn)):
            found.append(_result(G_SUPPLIERS, name, _dot(f"ИНН {inn}" if inn else "", "ключевой" if key else ""),
                                 "procurement", tab="directory", q=name))
            if len(found) >= PER_GROUP:
                break
    return found


def _stock(session: Session, query: str, patterns: list[str]) -> list[dict]:
    row = StockLotRow
    columns = [row.grn, row.package, row.article, row.name, row.storage_location, row.series, row.purpose,
               row.item_type, row.inbound, row.written_off, row.reserved, row.unit]
    exact = fold(query).replace(" ", "")
    ranked: list[tuple[int, dict]] = []
    for lot in _scan(session, columns, patterns,
                     [row.grn, row.article, row.name, row.storage_location, row.series], order=row.grn):
        if not matches_words(lot, query):
            continue
        grn = f"{lot.grn}-{lot.package}" if lot.package else lot.grn
        free = lot.inbound - lot.written_off - lot.reserved
        rank = 0 if exact in {fold(grn), fold(lot.grn)} else 1
        ranked.append((rank, _result(
            G_STOCK, grn, _dot(lot.article, lot.name, lot.storage_location, f"{free:g} {lot.unit}"),
            "stock", q=grn,
        )))
    ranked.sort(key=lambda pair: pair[0])
    return [result for _rank, result in ranked[:PER_GROUP]]


def _rfqs(session: Session, words: list[str], patterns: list[str]) -> list[dict]:
    row = RfqRow
    found: list[dict] = []
    for number, title, closed in _scan(session, [row.number, row.title, row.closed_at], patterns,
                                       [row.number, row.title], order=row.number.desc()):
        if _hit(words, _text(number, title)):
            found.append(_result(G_RFQ, _dot(number, title), "закрыт" if closed else "открыт", "procurement",
                                 tab="rfq"))
            if len(found) >= PER_GROUP:
                break
    return found


def _may(principal: Principal, *sections: str) -> bool:
    return section_allowed(principal, sections, "view")


@router.get("")
def search(
    request: Request,
    principal: Annotated[Principal, Depends(authenticated_principal)],
    factory: Annotated[Callable, Depends(purchasing_uow_factory)],
    q: Annotated[str, Query(max_length=MAX_QUERY)] = "",
) -> dict:
    started = time.perf_counter()
    words = fold(q).split()
    if not words:
        return {"query": q, "results": [], "ms": 0}
    patterns = _prefilter(q)
    procurement = _may(principal, "procurement")
    groups: list[tuple[str, Callable[[Session], list[dict]]]] = []
    if procurement:
        groups.append((G_LINES, lambda s: _lines(s, words, patterns)))
    if _may(principal, "projects"):
        groups.append((G_PROJECTS, lambda s: _projects(s, request, words, patterns)))
    if procurement:
        groups.append((G_ITEMS, lambda s: _items(s, words, patterns)))
    if procurement:
        groups.append((G_SUPPLIERS, lambda s: _suppliers(s, words, patterns)))
    if _may(principal, "warehouse"):
        groups.append((G_STOCK, lambda s: _stock(s, q, patterns)))
    if procurement:
        groups.append((G_RFQ, lambda s: _rfqs(s, words, patterns)))
    # A scanned / typed GRN: the lot first.
    if GRN_LIKE.match(q.strip()):
        groups.sort(key=lambda pair: pair[0] != G_STOCK)
    results: list[dict] = []
    if groups:
        with factory() as uow:
            for _name, run in groups:
                results.extend(run(uow.session))
    return {"query": q, "results": results, "ms": round((time.perf_counter() - started) * 1000)}
