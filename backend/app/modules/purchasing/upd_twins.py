"""The same document in the UPD registry twice (ported from Roller_Buyer upd-twins.ts computeTwins).

A PDF read by the Google parser and the EDO XML of it, or an XML the seller sent again. Recomputed whenever documents
arrive or the buyer edits a header (upd_registry.relink_documents), so `twin_of` always follows the documents' own data.
The one in force: the copy goods were received by, otherwise the EDO XML (exact), otherwise the later formed XML, then
the better read scan; the other copies point at it. Pure: facts in, copy → main out."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any

from .upd_checks import norm_doc_number


@dataclass(frozen=True)
class TwinFacts:
    id: str
    origin: str  # google | edo
    status: str  # the parser's verdict: «Разобран», «Требует проверки», «Дубликат»…
    kind: str
    number: str  # the buyer's number when given, else the parser's
    date: str | None  # ISO
    seller_inn: str  # as printed (the buyer's version wins)
    total_gross: int | None
    parser_updated: str
    checked: bool = False  # verified or corrected by the buyer
    seller_org_inn: str = ""  # the seller's INN with OCR slips corrected (upd_sellers); "" — as printed


def twin_facts(row: Any, seller_org_inn: str = "") -> TwinFacts:
    """From a purchasing_upd_documents row, taking the buyer's header over the parser's field by field the way
    Roller_Buyer did (an emptied number or date falls back to the parser's)."""
    manual = row.manual_header or {}
    parsed_date = row.doc_date.isoformat() if row.doc_date else None
    gross = manual.get("total_gross")
    return TwinFacts(
        id=row.id, origin=row.origin, status=row.status, kind=row.kind or "",
        number=manual.get("number") or row.number or "",
        date=manual.get("date") if manual.get("date") is not None else parsed_date,
        seller_inn=manual.get("seller_inn") or row.seller_inn or "",
        total_gross=gross if gross is not None else row.total_gross,
        parser_updated=row.parser_updated or "",
        checked=bool(row.verified_at or row.manual_saved_at),
        seller_org_inn=seller_org_inn,
    )


def _kind_class(kind: str) -> str | None:
    """Corrections only twin corrections, М-15 only М-15; every other closing document is one class."""
    if kind == "Прочее":
        return None
    if kind == "Корректировочный СФ":
        return "correction"
    if kind == "М-15":
        return "m15"
    return "closing"


def _seller(row: TwinFacts) -> str:
    return row.seller_org_inn or row.seller_inn


def _key(row: TwinFacts) -> str | None:
    inn = _seller(row)
    number = norm_doc_number(row.number)
    cls = _kind_class(row.kind)
    if not inn or not number or not row.date or not cls or row.status == "Дубликат":
        return None
    return f"{inn}|{number}|{row.date}|{cls}"


def _differ(a: int | None, b: int | None) -> bool:
    return a is not None and b is not None and abs(a - b) > max(100, abs(b) * 0.01)


def _quality(row: TwinFacts) -> int:
    """How well a copy was read: checked or corrected by the buyer, read cleanly, doubtful."""
    return 2 if row.checked else 1 if row.status == "Разобран" else 0


def _cmp(a: str, b: str) -> int:
    return (a > b) - (a < b)


def _rank(received: set[str]):
    """Best first: received by, EDO over PDF, later formed, the better read scan, then a stable order."""

    def compare(a: TwinFacts, b: TwinFacts) -> int:
        return (
            int(b.id in received) - int(a.id in received)
            or int(b.origin == "edo") - int(a.origin == "edo")
            or (_cmp(b.parser_updated, a.parser_updated) if a.origin == "edo" and b.origin == "edo" else 0)
            or _quality(b) - _quality(a)
            or _cmp(a.id, b.id)
        )

    return cmp_to_key(compare)


def compute_twins(rows: Iterable[TwinFacts], received: set[str] | None = None) -> dict[str, str]:
    """document id → the document it twins (absent = in force). `received` — documents goods were received by."""
    received = received or set()
    rows = list(rows)
    groups: dict[str, list[TwinFacts]] = {}
    for row in rows:
        key = _key(row)
        if key:
            groups.setdefault(key, []).append(row)
    twins: dict[str, str] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=_rank(received))
        main = ordered[0]
        for row in ordered[1:]:
            # Goods were received by both: two real documents, whatever their numbers say.
            if row.id in received:
                continue
            # Two XMLs are exact: a different total means a different document under the same number.
            if row.origin == "edo" and main.origin == "edo" and row.total_gross != main.total_gross:
                continue
            # An act and a delivery note may share a number and a day («26002614»: 813 ₽ of services, 19 279 ₽ of
            # goods): a different kind with a different total is another document.
            if row.kind != main.kind and _differ(row.total_gross, main.total_gross):
                continue
            twins[row.id] = main.id
    # A separate PDF of the invoice-factura beside the delivery note or act that carries it: same seller, day and total
    # under another number («TP-1284» and «ТР-1277») — one delivery, counted once.
    in_force = [row for row in rows if row.id not in twins and row.status != "Дубликат"]
    for invoice in in_force:
        if invoice.kind != "Счёт-фактура" or invoice.id in received:
            continue
        if not _seller(invoice) or not invoice.date or invoice.total_gross is None:
            continue
        partner = next((
            other for other in in_force
            if other is not invoice and other.kind != "Счёт-фактура" and _kind_class(other.kind) == "closing"
            and other.id not in twins and _seller(other) == _seller(invoice) and other.date == invoice.date
            and other.total_gross == invoice.total_gross
        ), None)
        if partner is not None:
            twins[invoice.id] = partner.id
    return twins
