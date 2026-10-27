"""Invoices uploaded in ERP procurement land in «Счета» (owner Витя, 26.10.2026: «нужен полноценный разборщик всех
данных, как у нас в roller_buyer»).

Until now the registry held only the Kaiten cards mirrored by `rb_import`; new invoices come in through procurement — the
buyer uploads the supplier's invoice to an order. Each uploaded file becomes a registry invoice of source «procurement»
(migration 0155), keyed by procurement's stored file id: the header and positions as the shared reader gives them
(`app.shared.invoice_reading`, Roller_Buyer's parser), the text for the document search, the PDF copied into
`purchasing_document_files` (modules don't share tables). The document chain (invoice ↔ UPD by number and sum) then works
for it as for a Kaiten card.

Status «to_pay» on arrival — the file is uploaded for payment; the payment itself lives on the procurement lines (no
payment orders here). The procurement side (which file, which order, when) is `app.workflows.procurement_invoice_registry`.
Re-sent, the same file updates its row only when the reading changed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .infrastructure import InvoiceFileRow, InvoiceRow, SupplierNameRow, SupplierRow
from .suppliers import supplier_key
from .upd_registry import _cut, _stored_file

SOURCE = "procurement"
STATUS = "to_pay"
BOARD = "Закупка ERP"


@dataclass(frozen=True)
class UploadedInvoiceDocument:
    """What procurement knows of one uploaded invoice file, with its reading, as plain values."""

    file_key: str  # procurement's stored file uuid
    file_name: str
    uploaded_at: datetime
    order_number: str  # «PO-012», '' — the line has no supplier order yet
    supplier: str  # the order's supplier as procurement names it
    number: str | None = None
    date: str | None = None  # ISO
    seller_name: str | None = None
    seller_inn: str | None = None
    total: int | None = None  # kopecks
    vat: int | None = None
    vat_free: bool = False
    lines: list[dict] = field(default_factory=list)  # [{seq, name, article, qty, unit, price, sum}]
    text: str = ""
    issues: tuple[str, ...] = ()
    parser: str = ""  # the reader's version


def _supplier(session: Session, inn: str | None, name: str) -> SupplierRow | None:
    """By the seller's INN; else the order's supplier by name — unless the directory gives it another INN."""
    if inn:
        found = sorted(session.scalars(select(SupplierRow).where(SupplierRow.inn == inn)),
                       key=lambda row: row.archived_at is not None)
        if found:
            return found[0]
    key = supplier_key(name) if name.strip() else ""
    if not key:
        return None
    named = session.scalar(select(SupplierRow).where(SupplierRow.name_key == key))
    if named is None:
        supplier_id = session.scalar(select(SupplierNameRow.supplier_id).where(SupplierNameRow.name_key == key))
        named = session.get(SupplierRow, supplier_id) if supplier_id is not None else None
    if named is not None and (not inn or not named.inn):
        return named
    return None


def _title(doc: UploadedInvoiceDocument) -> str:
    return f"Заказ {doc.order_number} · {doc.file_name}" if doc.order_number else doc.file_name


def _values(doc: UploadedInvoiceDocument, supplier_id: Any) -> dict[str, Any]:
    return {
        "title": _title(doc), "board_title": BOARD, "column_title": "",
        "number": _cut(doc.number, 128) or None, "doc_date": date.fromisoformat(doc.date) if doc.date else None,
        "seller_name": _cut(doc.seller_name, 512) or None, "seller_inn": _cut(doc.seller_inn, 32) or None,
        "supplier_id": supplier_id, "total": doc.total, "vat": doc.vat, "vat_free": bool(doc.vat_free),
        "parsed_file": _cut(f"{doc.file_key}#{doc.parser}", 64), "parse_issues": "; ".join(doc.issues),
        "text": doc.text, "lines": list(doc.lines),
    }


def record_uploaded_invoice(uow, doc: UploadedInvoiceDocument, content: bytes, actor: Actor, now: datetime) -> dict:
    """The invoice file into the registry. `{status: created | updated | unchanged, invoice_id}`; the caller commits."""
    session: Session = uow.session
    supplier = _supplier(session, doc.seller_inn, doc.supplier)
    values = _values(doc, supplier.id if supplier is not None else None)
    row = session.scalar(select(InvoiceRow).where(InvoiceRow.source == SOURCE, InvoiceRow.external_id == doc.file_key))
    if row is None:
        row = InvoiceRow(
            id=uuid4(), source=SOURCE, external_id=_cut(doc.file_key, 64), status=STATUS,
            card_created=doc.uploaded_at, card_updated=now, synced_at=now, payment_numbers="", **values,
        )
        session.add(row)
        status = "created"
    elif any(getattr(row, key) != value for key, value in values.items()):
        for key, value in values.items():
            setattr(row, key, value)
        row.card_updated = row.synced_at = now
        status = "updated"
    else:
        status = "unchanged"
    session.flush()
    has_file = session.scalar(select(InvoiceFileRow.id).where(InvoiceFileRow.invoice_id == row.id).limit(1))
    if has_file is None:
        stored = _stored_file(session, doc.file_name, content, now) if content else None
        session.add(InvoiceFileRow(
            id=uuid4(), invoice_id=row.id, external_id=_cut(doc.file_key, 64), name=_cut(doc.file_name, 1024),
            kind="invoice", size=len(content), seq=0, file_id=stored.id if stored is not None else None,
        ))
        session.flush()
    if status != "unchanged":
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=row.id, event_type=f"purchasing.invoice_upload_{status}", actor=actor,
            occurred_at=now, details={"file": doc.file_key, "name": doc.file_name, "order": doc.order_number},
        ))
    return {"status": status, "invoice_id": str(row.id)}
