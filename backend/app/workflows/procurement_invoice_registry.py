"""Procurement ↔ purchasing for supplier invoices (owner Витя, 26.10.2026: «нужен полноценный разборщик всех данных, как
у нас в roller_buyer»).

* `PurchasingSupplierInns` — procurement's `SupplierInns` port over purchasing's supplier directory: the INN of the
  order's supplier, the supplier owning an INN — for the checks shown when an invoice is uploaded.
* `push_after_upload` — every invoice file a buyer uploads to an order («Файл счёта», «Вставить счёт») goes to
  purchasing's «Счета» (`purchasing.invoice_uploads`, source «procurement»), so the document chain matches it with its
  UPDs. Run after the answer; never fails the upload — the file is stored in procurement whatever the registry says.

Modules never import each other; this workflow is the only place the two meet for invoices, the same shape as
`receipt_upd_registry` for the warehouse's UPDs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.modules.purchasing.infrastructure import SupplierNameRow, SupplierRow
from app.modules.purchasing.invoice_uploads import UploadedInvoiceDocument, record_uploaded_invoice
from app.modules.purchasing.suppliers import supplier_key
from app.shared.identity import Actor
from app.shared.invoice_reading import PARSER_VERSION, InvoiceReading, line_json, read_invoice
from app.shared.pdf_text import PdfLimitExceeded

log = logging.getLogger(__name__)
TOO_HEAVY = "файл слишком тяжёлый для разбора (превышен лимит обработки)"


class PurchasingSupplierInns:
    """`app.modules.procurement.invoice_checks.SupplierInns` over purchasing's directory (read-only)."""

    def __init__(self, purchasing_factory: Callable):
        self.purchasing_factory = purchasing_factory

    def _by_name(self, session, name: str) -> SupplierRow | None:
        key = supplier_key(name) if name.strip() else ""
        if not key:
            return None
        row = session.scalar(select(SupplierRow).where(SupplierRow.name_key == key))
        if row is None:
            supplier_id = session.scalar(select(SupplierNameRow.supplier_id).where(SupplierNameRow.name_key == key))
            row = session.get(SupplierRow, supplier_id) if supplier_id is not None else None
        return row

    def inn_of(self, supplier: str) -> str | None:
        with self.purchasing_factory() as uow:
            row = self._by_name(uow.session, supplier)
            return row.inn if row is not None and row.inn else None

    def supplier_of_inn(self, inn: str) -> str | None:
        with self.purchasing_factory() as uow:
            rows = sorted(uow.session.scalars(select(SupplierRow).where(SupplierRow.inn == inn)),
                          key=lambda row: row.archived_at is not None)
            return rows[0].name if rows else None


def uploaded_document(
    file_id: UUID, file_name: str, uploaded_at: datetime, order_number: str, supplier: str, reading: InvoiceReading,
) -> UploadedInvoiceDocument:
    h = reading.header
    return UploadedInvoiceDocument(
        file_key=str(file_id), file_name=file_name, uploaded_at=uploaded_at, order_number=order_number,
        supplier=supplier, number=h.number, date=h.date, seller_name=h.seller_name, seller_inn=h.seller_inn,
        total=h.total, vat=h.vat, vat_free=h.vat_free, lines=[line_json(line) for line in reading.lines],
        text=reading.text, issues=reading.issues, parser=PARSER_VERSION,
    )


def push_uploaded_invoice(
    purchasing_factory: Callable, file_id: UUID, file_name: str, uploaded_at: datetime, content: bytes,
    reading: InvoiceReading | None, order_number: str, supplier: str, actor: Actor, now: datetime,
) -> dict[str, Any]:
    """One uploaded invoice file into «Счета»; `reading` None — read here (the «Вставить счёт» path reads prices with
    its own reader)."""
    if reading is None:
        try:
            reading = read_invoice(content)
        except PdfLimitExceeded:
            reading = InvoiceReading(issues=(TOO_HEAVY,))
    doc = uploaded_document(file_id, file_name, uploaded_at, order_number, supplier, reading)
    with purchasing_factory() as uow:
        result = record_uploaded_invoice(uow, doc, content, actor, now)
        uow.commit()
    return {"file": str(file_id), **result}


def push_after_upload(
    purchasing_factory: Callable | None, file_id: UUID, file_name: str, uploaded_at: datetime, content: bytes,
    reading: InvoiceReading | None, order_number: str, supplier: str, actor: Actor, now: datetime,
) -> dict[str, Any] | None:
    """The hook of the invoice upload: never raises — the file is stored in procurement whatever the registry says."""
    if purchasing_factory is None:
        return None
    try:
        return push_uploaded_invoice(
            purchasing_factory, file_id, file_name, uploaded_at, content, reading, order_number, supplier, actor, now,
        )
    except Exception:  # noqa: BLE001 — the registry is purchasing's business; the upload is done
        log.exception("счёт %s не попал в реестр «Счета»", file_id)
        return None
