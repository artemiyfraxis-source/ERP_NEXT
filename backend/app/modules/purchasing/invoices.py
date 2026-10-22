"""«Счета»: the invoice registry mirrored from Kaiten (space «Закупки»), read-only.

One card = one invoice with its PDF and our payment orders (ported from Roller_Buyer kaiten.ts / routes/invoices.ts,
no network here — the importer fills the tables). The header was read from the PDF's text layer; the payments come
from the payment orders' file names, so they are summarised on read the same way the sync did. Each invoice carries
its chain state from the same computation as «Цепочка документов»."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .doc_chain import Chain, ChainInvoice, World, load_invoice_files
from .doc_refs import PaymentOrder
from .infrastructure import DocumentFileRow, InvoiceFileRow, InvoiceRow, SupplierRow

PAGE_SIZE = 100
INVOICE_STATUSES = ("to_pay", "paid", "return", "other")


class InvoiceNotFound(LookupError):
    pass


def invoice_status(board_title: str, column_title: str) -> str:
    """Where the card stands in Kaiten: the board of returns, a column «Оплачено», «В оплату» / «Оплатить»."""
    if re.search(r"возврат", board_title or "", re.IGNORECASE):
        return "return"
    if re.search(r"оплачен", column_title or "", re.IGNORECASE):
        return "paid"
    if re.search(r"в оплату|оплатить", column_title or "", re.IGNORECASE):
        return "to_pay"
    return "other"


@dataclass(frozen=True)
class PaymentSummary:
    numbers: str  # «880, 881»
    date: str | None  # the latest payment
    sum: int | None  # kopecks; None without payments


def payment_summary(payments: tuple[PaymentOrder, ...] | list[PaymentOrder]) -> PaymentSummary:
    return PaymentSummary(
        numbers=", ".join(p.number for p in payments),
        date=max((p.date for p in payments), default=None),
        sum=sum(p.sum for p in payments) if payments else None,
    )


def payment_matches_total(total: int | None, payment: int | None) -> bool | None:
    """Kaiten writes the payment sum into the order's file name in whole roubles («на_сумму_26330» for 26 330,40):
    equal to the rouble is equal. None when either side is unknown."""
    if total is None or payment is None:
        return None
    return abs(total - payment) < 100


def _chain_brief(chain: Chain | None) -> dict | None:
    if chain is None:
        return None
    return {
        "state": chain.state, "problem": chain.problem, "upds": len(chain.upds), "closed_gross": chain.closed_gross,
        "checks": [c.as_json() for c in chain.checks],
    }


def _file_json(row: InvoiceFileRow) -> dict:
    return {"id": str(row.id), "name": row.name, "kind": row.kind, "size": row.size, "downloaded": row.file_id is not None}


@dataclass(frozen=True)
class InvoiceQuery:
    q: str = ""
    status: str | None = None
    supplier_id: UUID | None = None
    date_from: str | None = None
    date_to: str | None = None
    closing: str | None = None  # none — no closing documents yet
    page: int = 1


def list_invoices(session: Session, world: World, query: InvoiceQuery) -> dict:
    rows = {
        row.id: row for row in session.execute(select(
            InvoiceRow.id, InvoiceRow.column_title, InvoiceRow.card_created, InvoiceRow.vat, InvoiceRow.vat_free,
            InvoiceRow.parse_issues, InvoiceRow.lines,
        ))
    }
    files = load_invoice_files(session)
    words = query.q.lower().split()

    def day_of(inv: ChainInvoice) -> str:
        row = rows.get(inv.id)
        return inv.date or (row.card_created.date().isoformat() if row is not None and row.card_created else "")

    def keep(inv: ChainInvoice, with_status: bool) -> bool:
        if with_status and query.status and inv.status != query.status:
            return False
        if query.supplier_id is not None and inv.supplier_id != query.supplier_id:
            return False
        day = day_of(inv)
        if query.date_from and day and day < query.date_from:
            return False
        if query.date_to and day and day > query.date_to:
            return False
        if query.closing == "none":
            chain = world.chain_of_invoice.get(inv.id)
            if chain is not None and chain.upds:
                return False
        if words:
            hay = " ".join(part for part in (
                inv.title, inv.number, inv.seller_name, inv.seller_inn, inv.supplier_name, *(p.number for p in inv.payments),
            ) if part).lower()
            if not all(word in hay for word in words):
                return False
        return True

    unfiltered = [inv for inv in world.invoices.values() if keep(inv, with_status=False)]
    counts = dict.fromkeys(INVOICE_STATUSES, 0)
    for inv in unfiltered:
        counts[inv.status] = counts.get(inv.status, 0) + 1
    chosen = [inv for inv in unfiltered if not query.status or inv.status == query.status]
    chosen.sort(key=day_of, reverse=True)
    page = chosen[(query.page - 1) * PAGE_SIZE:query.page * PAGE_SIZE]

    def item(inv: ChainInvoice) -> dict:
        row = rows.get(inv.id)
        paid = payment_summary(inv.payments)
        return {
            **inv.as_json(),
            "column_title": row.column_title if row is not None else "",
            "card_created": row.card_created.isoformat() if row is not None and row.card_created else None,
            "vat": row.vat if row is not None else None, "vat_free": bool(row.vat_free) if row is not None else False,
            "parse_issues": row.parse_issues if row is not None else "",
            "line_count": len(row.lines or []) if row is not None else 0,
            "payment_numbers": paid.numbers, "payment_date": paid.date, "payment_sum": paid.sum,
            "payment_matches": payment_matches_total(inv.total, paid.sum),
            "files": [_file_json(f) for f in files.get(inv.id, [])],
            "chain": _chain_brief(world.chain_of_invoice.get(inv.id)),
        }

    return {
        "items": [item(inv) for inv in page], "total": len(chosen), "page": query.page, "page_size": PAGE_SIZE,
        "counts": counts, "sum": sum(inv.total or 0 for inv in chosen),
    }


def invoice_card(session: Session, world: World, invoice_id: UUID) -> tuple[dict, Chain | None]:
    """Header, positions, payment orders and files of one invoice; the chain is returned for the HTTP layer to attach
    receiving to."""
    row = session.get(InvoiceRow, invoice_id)
    inv = world.invoices.get(invoice_id)
    if row is None or inv is None:
        raise InvoiceNotFound("Счёт не найден")
    files = load_invoice_files(session, [invoice_id]).get(invoice_id, [])
    paid = payment_summary(inv.payments)
    return {
        **inv.as_json(),
        "board_title": row.board_title, "column_title": row.column_title,
        "card_created": row.card_created.isoformat() if row.card_created else None,
        "vat": row.vat, "vat_free": row.vat_free, "parse_issues": row.parse_issues, "lines": row.lines or [],
        "payment_numbers": paid.numbers, "payment_date": paid.date, "payment_sum": paid.sum,
        "payment_matches": payment_matches_total(inv.total, paid.sum),
        "files": [_file_json(f) for f in files],
    }, world.chain_of_invoice.get(invoice_id)


def invoice_file(session: Session, invoice_id: UUID, file_row_id: UUID) -> tuple[bytes, str]:
    row = session.scalar(select(InvoiceFileRow).where(InvoiceFileRow.id == file_row_id, InvoiceFileRow.invoice_id == invoice_id))
    if row is None:
        raise InvoiceNotFound("Файл не найден")
    if row.file_id is None:
        raise InvoiceNotFound("Файл не скачан из Kaiten")
    stored = session.get(DocumentFileRow, row.file_id)
    if stored is None:
        raise InvoiceNotFound("Файл не найден")
    return stored.content, row.name


def supplier_inn(session: Session, supplier_id: UUID | None) -> str | None:
    if supplier_id is None:
        return None
    return session.scalar(select(SupplierRow.inn).where(SupplierRow.id == supplier_id)) or None


def iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
