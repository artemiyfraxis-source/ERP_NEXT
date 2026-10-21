"""«Поиск по документам» (ported from Roller_Buyer domain/doc-search.ts and doc-search.ts).

A position, a number, a seller or an amount → the UPDs and Kaiten invoices that have it. Matching folds case, «ё»,
Latin look-alikes of Cyrillic letters and separators (spaces, dashes, dots, slashes): «RK50-3» finds «РК 50-3». Every
word of the query must be found.

What is searched, in this order per document (the first that matches makes the hit):
UPDs (the version in force — the buyer's lines once saved, else the parser's; duplicates and «not a purchase» left
out): positions → header (number, seller, INN, supplier, file name) → total; Kaiten invoices: positions read from the
PDF table → header (number, seller, INN, supplier) → total → the PDF text (words around the hit) → the card title.
The header and the total are ERP's addition: Roller_Buyer searched positions only. Newest first."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from .doc_chain import invoice_pdf, load_invoice_files, upd_status
from .infrastructure import InvoiceRow, SupplierRow, UpdDocumentRow, UpdLineRow
from .upd_sellers import effective_header

LIMIT = 200

_LOOKALIKES = {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м", "t": "т", "h": "н", "b": "в"}
_SEPARATORS = re.compile(r"[\s\-‐‑–—.,/\\_()«»\"'№#:;]")
_FOLD = str.maketrans({"ё": "е", **_LOOKALIKES})


def _fold(ch: str) -> str:
    low = ch.lower()
    if len(low) != 1:
        return ch
    return low.translate(_FOLD)


def compact(text: str) -> tuple[str, list[int]]:
    """The compact form and, for each of its characters, where it stood in the original text."""
    out: list[str] = []
    at: list[int] = []
    for i, ch in enumerate(text):
        if _SEPARATORS.match(ch):
            continue
        out.append(_fold(ch))
        at.append(i)
    return "".join(out), at


def compact_string(text: str) -> str:
    """The compact form alone — the fast path for matching thousands of names and texts."""
    return _SEPARATORS.sub("", (text or "").lower()).translate(_FOLD)


def query_words(q: str) -> list[str]:
    """The query's words in compact form; words shorter than 2 characters say nothing and are dropped."""
    words = (compact(word)[0] for word in (q or "").split())
    return [word for word in words if len(word) >= 2]


def matches_all(haystack: str, words: list[str]) -> bool:
    if not words:
        return False
    h = compact_string(haystack)
    return all(word in h for word in words)


_SPACES = re.compile(r"\s+")


def snippet(text: str, words: list[str], around: int = 70) -> str | None:
    """«…около найденного…»: the original text around the first word's first hit."""
    s, at = compact(text or "")
    i = s.find(words[0]) if words else -1
    if i < 0:
        return None
    start = max(0, at[i] - around)
    end = min(len(text), at[i + len(words[0]) - 1] + 1 + around)
    piece = _SPACES.sub(" ", text[start:end]).strip()
    return f"{'…' if start > 0 else ''}{piece}{'…' if end < len(text) else ''}"


_AMOUNT = re.compile(r"(\d+)(?:[.,](\d{1,2}))?")
_AMOUNT_NOISE = re.compile(r"\s|₽|руб\.?|р\.", re.IGNORECASE)


@dataclass(frozen=True)
class Amount:
    kop: int
    exact: bool  # kopecks were typed: equal to the kopeck; else equal to the rouble

    def fits(self, total: int | None) -> bool:
        if total is None:
            return False
        return total == self.kop if self.exact else abs(total - self.kop) < 100


def query_amount(q: str) -> Amount | None:
    """«4 815,00», «4815.5», «12 000 ₽» → the amount to compare totals with; None when the query is not an amount."""
    m = _AMOUNT.fullmatch(_AMOUNT_NOISE.sub("", q or ""))
    if not m:
        return None
    rubles = int(m.group(1))
    if m.group(2) is None:
        return Amount(rubles * 100, False)
    return Amount(rubles * 100 + int(m.group(2).ljust(2, "0")), True)


def _vat_rate(text: str) -> Decimal | None:
    """«22», «22%», «без НДС» → percent (0 for no VAT); None when unknown."""
    if re.search(r"без\s*ндс", text or "", re.IGNORECASE):
        return Decimal(0)
    raw = (text or "").replace("%", "").replace(",", ".").strip()
    if not raw:
        return None
    try:
        n = Decimal(raw)
    except ArithmeticError:
        return None
    return (n * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP) if 0 < n < 1 else n


def unit_gross(quantity: Decimal | None, gross: int | None, price: Decimal | None, vat_rate: str) -> int | None:
    """Kopecks per unit with VAT of a UPD line: the line's gross over its quantity, else price × (100 + rate)."""
    if gross is not None and quantity is not None and quantity > 0:
        return int((Decimal(gross) / quantity).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    rate = _vat_rate(vat_rate)
    if price is not None and rate is not None:
        return int((price * (100 + rate)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return None


def _qty(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value)


def search_documents(session: Session, q: str, kind: str = "all") -> dict:
    words = query_words(q)
    amount = query_amount(q)
    if not words and amount is None:
        return {"hits": [], "found": 0}
    supplier_names = dict(session.execute(select(SupplierRow.id, SupplierRow.name)).all())
    hits: list[dict] = []

    if kind != "invoice":
        docs = {
            d.id: d for d in session.scalars(select(UpdDocumentRow)) if upd_status(d) not in ("duplicate", "not_purchase")
        }
        found_lines: dict[str, list[dict]] = {}
        line_columns = (
            UpdLineRow.document_id, UpdLineRow.source, UpdLineRow.seq, UpdLineRow.name, UpdLineRow.article,
            UpdLineRow.quantity, UpdLineRow.unit, UpdLineRow.price, UpdLineRow.gross, UpdLineRow.vat_rate,
        )
        if words:
            for line in session.execute(select(*line_columns)):
                doc = docs.get(line.document_id)
                if doc is None or line.source != ("manual" if doc.manual_saved_at else "google"):
                    continue
                if not matches_all(f"{line.name} {line.article}", words):
                    continue
                found_lines.setdefault(line.document_id, []).append({
                    "seq": line.seq, "name": line.name, "article": line.article, "qty": _qty(line.quantity), "unit": line.unit,
                    "price": unit_gross(line.quantity, line.gross, line.price, line.vat_rate), "sum": line.gross,
                })
        for doc in docs.values():
            header = effective_header(doc)
            supplier = supplier_names.get(doc.supplier_id)
            lines = sorted(found_lines.get(doc.id, []), key=lambda line: line["seq"])
            matched = "lines" if lines else None
            head = (header["number"], header["seller_name"], header["seller_inn"], supplier, doc.file_name)
            if matched is None and words and matches_all(" ".join(str(part) for part in head if part), words):
                matched = "header"
            if matched is None and amount is not None and amount.fits(header["total_gross"]):
                matched = "amount"
            if matched is None:
                continue
            hits.append({
                "kind": "upd", "id": doc.id, "doc_kind": doc.kind or "УПД", "number": header["number"] or None,
                "date": header["date"], "seller_name": supplier or header["seller_name"] or None,
                "seller_inn": header["seller_inn"] or None, "origin": doc.origin, "total": header["total_gross"],
                "file_id": str(doc.file_id) if doc.file_id else None, "title": None, "lines": lines, "snippet": None,
                "matched": matched,
            })

    if kind != "upd":
        invoices = list(session.scalars(select(InvoiceRow).options(load_only(
            InvoiceRow.id, InvoiceRow.title, InvoiceRow.number, InvoiceRow.doc_date, InvoiceRow.card_created,
            InvoiceRow.seller_name, InvoiceRow.seller_inn, InvoiceRow.supplier_id, InvoiceRow.total, InvoiceRow.lines,
            InvoiceRow.text,
        ))))
        files = load_invoice_files(session)
        for inv in invoices:
            supplier = supplier_names.get(inv.supplier_id)
            lines = [
                line for line in (inv.lines or [])
                if words and matches_all(f"{line.get('name', '')} {line.get('article', '')}", words)
            ]
            matched, text = ("lines", None) if lines else (None, None)
            if matched is None and words and matches_all(
                " ".join(str(part) for part in (inv.number, inv.seller_name, inv.seller_inn, supplier) if part), words,
            ):
                matched = "header"
            if matched is None and amount is not None and amount.fits(inv.total):
                matched = "amount"
            # Positions not read (or read wrong): the text of the PDF, then the card title typed in Kaiten.
            if matched is None and words and matches_all(inv.text or "", words):
                matched, text = "text", snippet(inv.text or "", words)
            if matched is None and words and matches_all(inv.title, words):
                matched, text = "title", inv.title
            if matched is None:
                continue
            pdf = invoice_pdf(files.get(inv.id, []))
            day = inv.doc_date or (inv.card_created.date() if inv.card_created else None)
            hits.append({
                "kind": "invoice", "id": str(inv.id), "doc_kind": "Счёт", "number": inv.number,
                "date": day.isoformat() if isinstance(day, date) else None, "seller_name": supplier or inv.seller_name,
                "seller_inn": inv.seller_inn, "origin": None, "total": inv.total, "file_id": str(pdf.id) if pdf else None,
                "title": inv.title,
                "lines": [{
                    "seq": line.get("seq"), "name": line.get("name", ""), "article": line.get("article", ""),
                    "qty": _qty(line.get("qty")), "unit": line.get("unit", ""), "price": line.get("price"), "sum": line.get("sum"),
                } for line in lines],
                "snippet": text, "matched": matched,
            })

    hits.sort(key=lambda hit: hit["date"] or "", reverse=True)
    return {"hits": hits[:LIMIT], "found": len(hits)}
