"""Invoices uploaded in ERP procurement land in «Счета» (owner Витя, 26.10.2026: «нужен полноценный разборщик всех
данных, как у нас в roller_buyer»).

Until now the registry held only the Kaiten cards mirrored by `rb_import`; new invoices come in through procurement — the
buyer uploads the supplier's invoice to an order. An uploaded file becomes a registry invoice of source «procurement»
(migration 0156): the header and positions as the shared reader gives them (`app.shared.invoice_reading`, Roller_Buyer's
parser), the text for the document search, the PDF copied into `purchasing_document_files` (modules don't share tables).
The document chain (invoice ↔ UPD by number and sum) then works for it as for a Kaiten card.

One invoice, one row (owner's session 27.10.2026): every uploaded file is remembered in `purchasing_invoice_uploads`
(procurement's file uuid → the registry invoice). A file of an invoice the registry already has — the same seller (INN,
else the seller's name key), the same № (`norm_doc_number`) and a date within ± 3 days: a Kaiten card, or an earlier
upload of the same invoice — is attached to that invoice instead of making a second row; on a procurement row the newest
file's reading becomes the header and its PDF the invoice file (the older ones stay as «other»). A Kaiten card keeps
Kaiten's header and files; the upload is only added to its files.

Status: «to_pay» on arrival; from then on it follows the procurement lines carrying the invoice's files
(`apply_procurement_state`, driven by `app.workflows.procurement_invoice_registry`): paid with them, «cancelled» when they
are all cancelled, «replaced» when a newer invoice took the file's place (a wrong file uploaded and replaced). A Kaiten
card is only ever moved to «paid» by procurement (CHECK `ck_purchasing_invoices_void_source`). Re-sent, the same file
updates its row only when the reading changed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .doc_refs import norm_doc_number
from .infrastructure import InvoiceFileRow, InvoiceRow, InvoiceUploadRow, SupplierNameRow, SupplierRow
from .suppliers import supplier_key
from .upd_registry import _cut, _stored_file
from .upd_sellers import seller_name_key

SOURCE = "procurement"
STATUS = "to_pay"
BOARD = "Закупка ERP"
# The same invoice uploaded again or found in Kaiten: sellers re-issue an invoice a day or two later under its №.
TWIN_DAYS = 3
PROCUREMENT_STATUSES = ("to_pay", "paid", "cancelled", "replaced")
# A Kaiten card procurement may move to «paid»: «В оплату» or an unknown column, never a return.
_KAITEN_PAYABLE = ("to_pay", "other")


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
    line_id: UUID | None = None  # the procurement line the file was uploaded for; None — dropped on the order


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


def _doc_day(doc: UploadedInvoiceDocument) -> date | None:
    try:
        return date.fromisoformat(doc.date) if doc.date else None
    except ValueError:
        return None


def _values(doc: UploadedInvoiceDocument, supplier_id: Any) -> dict[str, Any]:
    return {
        "title": _title(doc), "board_title": BOARD, "column_title": "",
        "number": _cut(doc.number, 128) or None, "doc_date": _doc_day(doc),
        "seller_name": _cut(doc.seller_name, 512) or None, "seller_inn": _cut(doc.seller_inn, 32) or None,
        "supplier_id": supplier_id, "total": doc.total, "vat": doc.vat, "vat_free": bool(doc.vat_free),
        "parsed_file": _cut(f"{doc.file_key}#{doc.parser}", 64), "parse_issues": "; ".join(doc.issues),
        "text": doc.text, "lines": list(doc.lines),
    }


def same_seller(row_inn: str | None, row_name: str | None, inn: str | None, name: str | None) -> bool:
    """Both INNs read — the INNs decide; else the sellers' name keys (legal form and quotes dropped)."""
    if row_inn and inn:
        return row_inn.strip() == inn.strip()
    a, b = seller_name_key(row_name), seller_name_key(name)
    return bool(a and b and a == b)


def find_twin(session: Session, doc: UploadedInvoiceDocument) -> InvoiceRow | None:
    """The registry's invoice this file is a copy of: same seller, same №, date within ± TWIN_DAYS. Without a read №
    and date nothing is a twin (a scan is its own row until the buyer replaces it). The nearest date wins, a Kaiten card
    before an upload, then the oldest."""
    key = norm_doc_number(doc.number or "")
    day = _doc_day(doc)
    if not key or day is None:
        return None
    candidates = [
        row for row in session.scalars(select(InvoiceRow).where(
            InvoiceRow.doc_date.between(day - timedelta(days=TWIN_DAYS), day + timedelta(days=TWIN_DAYS)),
            InvoiceRow.number.is_not(None),
        ))
        if norm_doc_number(row.number or "") == key and same_seller(row.seller_inn, row.seller_name, doc.seller_inn, doc.seller_name)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: (abs((row.doc_date - day).days), row.source != "kaiten", row.card_created))


def _newest_upload(session: Session, invoice_id: UUID) -> InvoiceUploadRow | None:
    return session.scalar(
        select(InvoiceUploadRow).where(InvoiceUploadRow.invoice_id == invoice_id)
        .order_by(InvoiceUploadRow.uploaded_at.desc(), InvoiceUploadRow.recorded_at.desc()).limit(1)
    )


def _attach_file(session: Session, row: InvoiceRow, doc: UploadedInvoiceDocument, content: bytes, now: datetime) -> None:
    """The uploaded PDF among the invoice's files, once. On a procurement row the newest upload is «the invoice» and the
    older uploads become «other»; on a Kaiten card it is «the invoice» only while the card has no downloaded one."""
    key = _cut(doc.file_key, 64)
    files = list(session.scalars(select(InvoiceFileRow).where(InvoiceFileRow.invoice_id == row.id)))
    if any(item.external_id == key for item in files):
        return
    uploaded = set(session.scalars(select(InvoiceUploadRow.file_key).where(InvoiceUploadRow.invoice_id == row.id)))
    if row.source == SOURCE:
        for item in files:
            if item.kind == "invoice" and item.external_id in uploaded:
                item.kind = "other"
        kind = "invoice"
    else:
        kind = "other" if any(item.kind == "invoice" and item.file_id is not None for item in files) else "invoice"
    stored = _stored_file(session, doc.file_name, content, now) if content else None
    session.add(InvoiceFileRow(
        id=uuid4(), invoice_id=row.id, external_id=key, name=_cut(doc.file_name, 1024), kind=kind,
        size=len(content), seq=max((item.seq for item in files), default=-1) + 1,
        file_id=stored.id if stored is not None else None,
    ))


def _link(session: Session, row: InvoiceRow, doc: UploadedInvoiceDocument, created: bool, now: datetime) -> None:
    session.add(InvoiceUploadRow(
        id=uuid4(), invoice_id=row.id, file_key=_cut(doc.file_key, 64), line_id=doc.line_id,
        order_number=_cut(doc.order_number, 32), file_name=_cut(doc.file_name, 1024), uploaded_at=doc.uploaded_at,
        recorded_at=now, created_row=created,
    ))


def record_uploaded_invoice(uow, doc: UploadedInvoiceDocument, content: bytes, actor: Actor, now: datetime) -> dict:
    """The invoice file into the registry: `{status: created | attached | updated | unchanged, invoice_id, source}`.
    `attached` — the file joined an invoice the registry already had (its Kaiten card, an earlier upload of it). The
    caller commits."""
    session: Session = uow.session
    supplier = _supplier(session, doc.seller_inn, doc.supplier)
    values = _values(doc, supplier.id if supplier is not None else None)
    link = session.scalar(select(InvoiceUploadRow).where(InvoiceUploadRow.file_key == _cut(doc.file_key, 64)))
    row = session.get(InvoiceRow, link.invoice_id) if link is not None else None
    if row is None:  # a file of before 0156's upload links: Витя's first rows were keyed by the file alone
        row = session.scalar(select(InvoiceRow).where(InvoiceRow.source == SOURCE, InvoiceRow.external_id == doc.file_key))
        if row is not None:
            _link(session, row, doc, True, now)
    if row is None:
        twin = find_twin(session, doc)
        if twin is not None:
            _link(session, twin, doc, False, now)
            session.flush()
            if twin.source == SOURCE:
                for key, value in values.items():
                    setattr(twin, key, value)
                twin.card_updated = twin.synced_at = now
            _attach_file(session, twin, doc, content, now)
            session.flush()
            uow.audit.add(AuditEvent(
                id=uuid4(), aggregate_id=twin.id, event_type="purchasing.invoice_upload_attached", actor=actor,
                occurred_at=now, details={"file": doc.file_key, "name": doc.file_name, "order": doc.order_number,
                                          "source": twin.source},
            ))
            return {"status": "attached", "invoice_id": str(twin.id), "source": twin.source}
        row = InvoiceRow(
            id=uuid4(), source=SOURCE, external_id=_cut(doc.file_key, 64), status=STATUS,
            card_created=doc.uploaded_at, card_updated=now, synced_at=now, payment_numbers="", **values,
        )
        session.add(row)
        session.flush()
        _link(session, row, doc, True, now)
        status = "created"
    else:
        session.flush()
        newest = _newest_upload(session, row.id)
        latest = newest is None or newest.file_key == _cut(doc.file_key, 64)
        if row.source == SOURCE and latest and any(getattr(row, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(row, key, value)
            row.card_updated = row.synced_at = now
            status = "updated"
        else:
            status = "unchanged"
    session.flush()
    _attach_file(session, row, doc, content, now)
    session.flush()
    if status != "unchanged":
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=row.id, event_type=f"purchasing.invoice_upload_{status}", actor=actor,
            occurred_at=now, details={"file": doc.file_key, "name": doc.file_name, "order": doc.order_number},
        ))
    return {"status": status, "invoice_id": str(row.id), "source": row.source}


# ---- status from procurement ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceUploadFact:
    """One uploaded file of a registry invoice, as the status workflow needs it."""

    invoice_id: UUID
    source: str  # the invoice's source: kaiten | procurement
    file_key: str
    line_id: UUID | None
    order_number: str
    uploaded_at: datetime


def upload_facts(session: Session, invoice_ids: list[UUID] | None = None) -> list[InvoiceUploadFact]:
    """Every uploaded file (of these invoices) with its invoice's source."""
    query = (
        select(InvoiceUploadRow.invoice_id, InvoiceRow.source, InvoiceUploadRow.file_key, InvoiceUploadRow.line_id,
               InvoiceUploadRow.order_number, InvoiceUploadRow.uploaded_at)
        .join(InvoiceRow, InvoiceRow.id == InvoiceUploadRow.invoice_id)
        .order_by(InvoiceUploadRow.invoice_id, InvoiceUploadRow.uploaded_at)
    )
    if invoice_ids is not None:
        if not invoice_ids:
            return []
        query = query.where(InvoiceUploadRow.invoice_id.in_(invoice_ids))
    return [InvoiceUploadFact(*row) for row in session.execute(query)]


def invoices_of(session: Session, *, file_keys: set[str] = frozenset(), line_ids: set[UUID] = frozenset(),
                order_numbers: set[str] = frozenset()) -> list[UUID]:
    """The invoices whose uploads are these files, or were uploaded for these lines or orders."""
    found: set[UUID] = set()
    for column, values in ((InvoiceUploadRow.file_key, file_keys), (InvoiceUploadRow.line_id, line_ids),
                           (InvoiceUploadRow.order_number, order_numbers)):
        wanted = [value for value in values if value]
        for start in range(0, len(wanted), 500):
            found |= set(session.scalars(select(InvoiceUploadRow.invoice_id).where(column.in_(wanted[start:start + 500]))))
    return sorted(found)


@dataclass(frozen=True)
class ProcurementInvoiceState:
    """Where the procurement lines carrying the invoice stand: to_pay | paid | cancelled | replaced; for «paid» the
    latest payment day and the paid sum in kopecks (None when a line has no sum)."""

    status: str
    payment_date: date | None = None
    payment_sum: int | None = None


def apply_procurement_state(uow, invoice_id: UUID, state: ProcurementInvoiceState, actor: Actor, now: datetime) -> str | None:
    """The procurement state onto the registry invoice; the new status when something changed, else None. A
    procurement row takes the state as it is (the payment fields only while paid); a Kaiten card only goes «paid», its
    payment fields filled only where Kaiten left them empty. The caller commits."""
    if state.status not in PROCUREMENT_STATUSES:
        raise ValueError(f"unknown procurement invoice state {state.status!r}")
    session: Session = uow.session
    row = session.get(InvoiceRow, invoice_id)
    if row is None:
        return None
    before = (row.status, row.payment_date, row.payment_sum)
    if row.source == SOURCE:
        paid = state.status == "paid"
        row.status = state.status
        row.payment_date = state.payment_date if paid else None
        row.payment_sum = state.payment_sum if paid else None
    elif state.status == "paid" and row.status in _KAITEN_PAYABLE:
        row.status = "paid"
        row.payment_date = row.payment_date or state.payment_date
        row.payment_sum = row.payment_sum if row.payment_sum is not None else state.payment_sum
    after = (row.status, row.payment_date, row.payment_sum)
    if after == before:
        return None
    row.synced_at = now
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=row.id, event_type="purchasing.invoice_status_from_procurement", actor=actor,
        occurred_at=now, details={
            "from": before[0], "to": row.status, "source": row.source,
            "payment_date": row.payment_date.isoformat() if row.payment_date else None, "payment_sum": row.payment_sum,
        },
    ))
    session.flush()
    return row.status
