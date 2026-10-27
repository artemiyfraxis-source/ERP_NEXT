"""«Счета»: the invoice registry mirrored from Kaiten (space «Закупки»), read-only, and the invoices uploaded to orders in
ERP procurement (source «procurement», `invoice_uploads`).

One card = one invoice with its PDF and our payment orders (ported from Roller_Buyer kaiten.ts / routes/invoices.ts,
no network here — the importer fills the tables). The header was read from the PDF's text layer; the payments come
from the payment orders' file names, so they are summarised on read the same way the sync did. Each invoice carries
its chain state from the same computation as «Цепочка документов»."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .doc_chain import VOID_STATUSES, Chain, ChainInvoice, World, load_invoice_files
from .doc_refs import PaymentOrder, norm_doc_number
from .infrastructure import DocumentFileRow, InvoiceFileRow, InvoiceRow, SupplierRow

PAGE_SIZE = 100
# cancelled / replaced — only an invoice uploaded in procurement (its lines cancelled, a newer invoice in its place).
INVOICE_STATUSES = ("to_pay", "paid", "return", "other", "cancelled", "replaced")


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


def procurement_payment(source: str, payments: PaymentSummary, payment_date: date | None,
                        payment_sum: int | None) -> PaymentSummary:
    """An invoice uploaded in procurement has no payment orders of its own: its payment is the procurement lines' one
    (`invoice_uploads.apply_procurement_state`) — the day and sum written on the row."""
    if source != "procurement" or payments.date is not None or payment_date is None:
        return payments
    return PaymentSummary(numbers=payments.numbers, date=payment_date.isoformat(), sum=payment_sum)


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
            InvoiceRow.id, InvoiceRow.source, InvoiceRow.column_title, InvoiceRow.card_created, InvoiceRow.vat,
            InvoiceRow.vat_free, InvoiceRow.parse_issues, InvoiceRow.lines, InvoiceRow.payment_date, InvoiceRow.payment_sum,
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
        if row is not None:
            paid = procurement_payment(row.source, paid, row.payment_date, row.payment_sum)
        return {
            **inv.as_json(),
            "source": row.source if row is not None else "kaiten",
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
        # a cancelled or replaced upload is no money owed: out of the sum unless asked for by its status
        "counts": counts, "sum": sum(inv.total or 0 for inv in chosen if query.status or inv.status not in VOID_STATUSES),
    }


def invoice_card(session: Session, world: World, invoice_id: UUID) -> tuple[dict, Chain | None]:
    """Header, positions, payment orders and files of one invoice; the chain is returned for the HTTP layer to attach
    receiving to."""
    row = session.get(InvoiceRow, invoice_id)
    inv = world.invoices.get(invoice_id)
    if row is None or inv is None:
        raise InvoiceNotFound("Счёт не найден")
    files = load_invoice_files(session, [invoice_id]).get(invoice_id, [])
    paid = procurement_payment(row.source, payment_summary(inv.payments), row.payment_date, row.payment_sum)
    return {
        **inv.as_json(),
        "source": row.source, "board_title": row.board_title, "column_title": row.column_title,
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


def invoice_pdf(session: Session, invoice_id: UUID, number: str | None = None) -> tuple[bytes, str] | None:
    """The invoice's own PDF downloaded into the registry: the file Kaiten marks as the invoice, else a PDF named with
    `number` (the Roller_Buyer import picks it the same way); None while none is downloaded."""
    files = [row for row in session.scalars(
        select(InvoiceFileRow).where(InvoiceFileRow.invoice_id == invoice_id).order_by(InvoiceFileRow.seq))
        if row.file_id is not None and row.name.lower().endswith(".pdf")]
    key = norm_doc_number(number or "")
    row = (next((item for item in files if item.kind == "invoice"), None)
           or next((item for item in files if key and key in norm_doc_number(item.name)), None))
    stored = session.get(DocumentFileRow, row.file_id) if row is not None else None
    return (bytes(stored.content), row.name) if stored is not None else None


def invoice_title(row: InvoiceRow) -> str:
    """«Счёт Kaiten № … от …» (an invoice uploaded in procurement: «Счёт № … от …») — how an order shows the registry's
    invoice it stands on."""
    if row.source != "kaiten":
        return "Счёт" + (f" № {row.number}" if row.number else "") + (f" от {row.doc_date:%d.%m.%Y}" if row.doc_date else "")
    number = f" № {row.number}" if row.number else f" (карточка {row.external_id})"
    return f"Счёт Kaiten{number}" + (f" от {row.doc_date:%d.%m.%Y}" if row.doc_date else "")


def missing_file_page(row: InvoiceRow) -> str:
    """What the invoice link of an order opens while the registry has no PDF of the invoice yet."""
    title = f"{invoice_title(row)} (файл не приложен)"
    facts = [
        ("Карточка Kaiten", f"{row.external_id} — {row.title}"),
        ("Продавец", " ".join(item for item in (row.seller_name or "", f"ИНН {row.seller_inn}" if row.seller_inn else "") if item)),
        ("Сумма", f"{Decimal(row.total) / 100:,.2f} ₽".replace(",", " ").replace(".", ",") if row.total is not None else ""),
        ("Оплата", f"{row.payment_date:%d.%m.%Y}" if row.payment_date else ""),
    ]
    rows = "".join(f"<tr><th>{escape(name)}</th><td>{escape(value)}</td></tr>" for name, value in facts if value)
    return (
        f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{escape(title)}</title>'
        '<style>body{font:14px/1.5 system-ui,sans-serif;margin:24px;color:#202124}th{text-align:left;padding-right:16px;'
        'font-weight:500;color:#5f6368}</style></head>'
        f"<body><h1 style=\"font-size:18px\">{escape(title)}</h1><table>{rows}</table>"
        "<p>Счёт есть в реестре «Счета», но его PDF ещё не скачан. Файл приложится к заказу при переносе "
        "из Roller_Buyer с папкой файлов.</p></body></html>"
    )


def supplier_inn(session: Session, supplier_id: UUID | None) -> str | None:
    if supplier_id is None:
        return None
    return session.scalar(select(SupplierRow.inn).where(SupplierRow.id == supplier_id)) or None


def iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
