"""The UPDs of an ERP supplier order, for its card . Computed on read.

A UPD belongs to the order when
* its basis («Основание: Счёт № 77639 от 26.10.2025») names one of the order's invoice numbers, or
* receiving typed its number on a delivery row of the order (the storekeeper's УПД, `ReceivingRow.document_number`),
and the seller does not contradict the order's supplier (a UPD found by receiving's number of four digits and more
is kept even then: the storekeeper held it). Numbers compare under the document-number key (doc_refs.norm_doc_number);
a twin (the same document twice) is left
out for the one in force. Nothing is stored."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from .doc_refs import loose_doc_number, norm_doc_number, parse_basis_refs
from .infrastructure import UpdDocumentRow
from .upd_sellers import effective_header, seller_name_key


def _agree(a: str, b: str) -> bool | None:
    if not a or not b:
        return None
    return a == b or a in b or b in a


def for_order(session: Session, order: dict, receiving_rows) -> list[dict]:
    invoice_numbers = {norm_doc_number(item.get("number") or "") for item in order.get("invoices", [])}
    invoice_numbers.discard("")
    upd_numbers = {norm_doc_number(row.document_number) for row in receiving_rows if row.document_number}
    upd_numbers.discard("")
    if not invoice_numbers and not upd_numbers:
        return []
    supplier = seller_name_key(order.get("supplier"))
    columns = (
        UpdDocumentRow.id, UpdDocumentRow.number, UpdDocumentRow.doc_date, UpdDocumentRow.seller_name,
        UpdDocumentRow.seller_inn, UpdDocumentRow.seller_kpp, UpdDocumentRow.buyer_inn, UpdDocumentRow.total_net,
        UpdDocumentRow.total_vat, UpdDocumentRow.total_gross, UpdDocumentRow.basis, UpdDocumentRow.manual_header,
        UpdDocumentRow.twin_of, UpdDocumentRow.kind, UpdDocumentRow.status,
    )
    found: list[dict] = []
    for row in session.scalars(select(UpdDocumentRow).options(load_only(*columns)).where(UpdDocumentRow.twin_of.is_(None))):
        header = effective_header(row)
        number = norm_doc_number(header["number"] or "")
        by_receiving = bool(number) and number in upd_numbers
        by_basis = False
        if invoice_numbers and row.basis:
            by_basis = any(norm_doc_number(ref.number) in invoice_numbers for ref in parse_basis_refs(row.basis))
        if not by_receiving and not by_basis:
            continue
        agree = _agree(seller_name_key(header["seller_name"]), supplier)
        if agree is False and (not by_receiving or len(loose_doc_number(header["number"] or "")) <= 3):
            continue
        found.append({
            "id": row.id, "number": header["number"], "date": header["date"], "seller": header["seller_name"],
            "total_gross": header["total_gross"], "kind": row.kind, "status": row.status,
            "by": "receiving" if by_receiving else "basis", "seller_agrees": agree,
        })
    found.sort(key=lambda item: (item["date"] or "", item["number"] or ""))
    return found
