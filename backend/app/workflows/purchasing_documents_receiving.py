"""Документы закупки ↔ приёмка: the receiving lines a closing document (УПД) came with. Read-only.

The chain «счёт → оплата → УПД» continues into ERP receiving: a receiving line carries the shipping document's number,
date and the supplier as typed. A line belongs to a document when the numbers agree under the document-number key
(doc_refs.norm_doc_number: case, separators, Latin look-alikes, the letter О for zero) and

* the dates are equal, or
* they are at most NEAR_DAYS apart (or one of them is missing) and the supplier agrees with the seller.

The supplier is a tiebreak: when several documents claim one line, the ones with the exact date, then the agreeing
supplier, keep it. A short number (up to three digits — «УПД № 15» recurs at every seller) never goes to a document of
another seller. Supplier text agrees with the seller name when their seller_name_key are equal or one contains the
other («ООО Ромашка» and «ООО "Ромашка Электро"»). Archived revisions («Архив ВК») are not receipts."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.modules.purchasing.doc_refs import loose_doc_number, norm_doc_number
from app.modules.purchasing.upd_sellers import seller_name_key
from app.modules.receiving.infrastructure import ReceivingLineRow as RC

NEAR_DAYS = 3
_ARCHIVE = "Архив ВК"


@dataclass(frozen=True)
class DocumentKey:
    key: str  # the caller's id of the document (a UPD id)
    number: str
    date: date | None
    seller_name: str


@dataclass(frozen=True)
class ReceivingFacts:
    id: UUID
    procurement_line_id: UUID
    document_number: str
    document_date: date | None
    document_name: str
    supplier: str
    name: str
    article: str
    unit: str
    accepted_quantity: Decimal | None
    fact_quantity: Decimal | None
    grn: str | None
    package: str
    status: str

    def as_json(self) -> dict:
        return {
            "id": str(self.id), "procurement_line_id": str(self.procurement_line_id),
            "document_number": self.document_number,
            "document_date": self.document_date.isoformat() if self.document_date else None,
            "document_name": self.document_name, "supplier": self.supplier, "name": self.name, "article": self.article,
            "unit": self.unit, "accepted_quantity": _qty(self.accepted_quantity), "fact_quantity": _qty(self.fact_quantity),
            "grn": self.grn, "package": self.package, "status": self.status,
        }


def _qty(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _sellers_agree(a: str, b: str) -> bool | None:
    """True / False when both names say something; None when one of them is unknown."""
    if not a or not b:
        return None
    return a == b or a in b or b in a


def receiving_by_documents(
    receiving_factory: Callable | None, documents: Iterable[DocumentKey],
) -> dict[str, list[ReceivingFacts]]:
    """Document key → its receiving lines (by GRN). A stand without receiving gives {}."""
    by_number: dict[str, list[tuple[DocumentKey, str]]] = {}
    for doc in documents:
        number = norm_doc_number(doc.number)
        if number and any(ch.isdigit() for ch in number):
            by_number.setdefault(number, []).append((doc, seller_name_key(doc.seller_name)))
    if receiving_factory is None or not by_number:
        return {}
    columns = (
        RC.id, RC.procurement_line_id, RC.document_number, RC.document_date, RC.document_name, RC.supplier, RC.name,
        RC.article, RC.unit, RC.accepted_quantity, RC.fact_quantity, RC.grn, RC.package, RC.status,
    )
    out: dict[str, list[ReceivingFacts]] = {}
    uow = receiving_factory()
    with uow:
        rows = uow.session.execute(select(*columns).where(RC.document_number != "", RC.status != _ARCHIVE))
        for row in rows:
            number = norm_doc_number(row.document_number)
            candidates = by_number.get(number)
            if not candidates:
                continue
            supplier = seller_name_key(row.supplier)
            short = len(loose_doc_number(row.document_number)) <= 3
            ranked: list[tuple[tuple[int, int], DocumentKey]] = []
            for doc, seller in candidates:
                agree = _sellers_agree(supplier, seller)
                if row.document_date is not None and doc.date is not None:
                    gap = abs((row.document_date - doc.date).days)
                    exact = gap == 0
                    if not exact and gap > NEAR_DAYS:
                        continue
                else:
                    exact = False
                if not exact and agree is not True:
                    continue
                if agree is False and short:
                    continue
                ranked.append(((0 if exact else 1, 0 if agree else 1 if agree is None else 2), doc))
            if not ranked:
                continue
            best = min(rank for rank, _ in ranked)
            facts = ReceivingFacts(
                id=row.id, procurement_line_id=row.procurement_line_id, document_number=row.document_number,
                document_date=row.document_date, document_name=row.document_name or "", supplier=row.supplier or "",
                name=row.name or "", article=row.article or "", unit=row.unit or "", accepted_quantity=row.accepted_quantity,
                fact_quantity=row.fact_quantity, grn=row.grn, package=row.package or "", status=row.status,
            )
            for rank, doc in ranked:
                if rank == best:
                    out.setdefault(doc.key, []).append(facts)
    for lines in out.values():
        lines.sort(key=lambda line: (line.grn or "", line.package, line.name))
    return out


def summary(lines: list[ReceivingFacts]) -> dict:
    """«Приёмка: N строк, ГРН …, статус» of one document."""
    statuses: dict[str, int] = {}
    for line in lines:
        statuses[line.status] = statuses.get(line.status, 0) + 1
    grns = list(dict.fromkeys(line.grn for line in lines if line.grn))
    return {
        "lines": len(lines), "grns": grns,
        "statuses": [{"status": status, "count": count} for status, count in statuses.items()],
    }
