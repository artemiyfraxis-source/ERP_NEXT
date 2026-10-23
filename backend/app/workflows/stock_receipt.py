"""«Приход на склад» (owner 23.10.2026): what the warehouse reads from receiving, procurement and purchasing while the
storekeeper types a delivery. Read-only — no module's state changes here.

* `expected_in_receiving` — the double-count guard: the article is already on its way through «Приёмка» (a line
  «В пути» / «Ожидание» of the same supplier or of the order / invoice number the delivery names), so accepting it
  by hand too would count it twice;
* `purchasing_item` — the purchasing nomenclature card of the article (name, manufacturer, type, unit);
* `procurement_line` — the procurement line of this supplier's invoice with this article;
* `upd_document` — a closing document (УПД) purchasing already holds under the number the scan carries;
* `known_articles` — the articles the ERP already writes (stock lots, purchasing nomenclature, reference DB), which an
  article read off a scanned УПД snaps to.

A stand without one of the modules wired (factory None) simply gets nothing from it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import distinct, or_, select

from app.modules.procurement.infrastructure import ProcurementLineRow as PL
from app.modules.purchasing.doc_refs import norm_doc_number
from app.modules.purchasing.infrastructure import ItemRow, UpdDocumentRow, UpdLineRow
from app.modules.purchasing.upd_sellers import seller_name_key
from app.modules.receiving.domain import ReceivingStatus
from app.modules.receiving.infrastructure import ReceivingLineRow as RC
from app.modules.references.domain import TechOperationTab
from app.modules.warehouse.infrastructure import StockLotRow
from app.workflows.article_key import code_key

OPEN_RECEIVING = (ReceivingStatus.IN_TRANSIT.value, ReceivingStatus.WAITING.value)
NEAR_DAYS = 3


@dataclass(frozen=True)
class ExpectedArrival:
    order_number: str
    supplier: str


def _agree(a: str, b: str) -> bool:
    return bool(a and b and (a == b or a in b or b in a))


def expected_in_receiving(
    receiving_factory: Callable | None,
    procurement_factory: Callable | None,
    *,
    supplier: str,
    invoice_number: str,
    article: str,
) -> ExpectedArrival | None:
    """The first open receiving line of the article whose order / invoice number is the delivery's invoice number, or
    whose supplier is the delivery's supplier. Nothing to compare (no number, no supplier) — None."""
    key = code_key(article)
    number = norm_doc_number(invoice_number)
    seller = seller_name_key(supplier) or str(supplier or "").strip().casefold()
    if receiving_factory is None or not key or not (number or seller):
        return None
    with receiving_factory() as uow:
        rows = [row for row in uow.session.execute(
            select(RC.procurement_line_id, RC.order_number, RC.supplier, RC.article, RC.legacy_id)
            .where(RC.status.in_(OPEN_RECEIVING), RC.article != "")
            .order_by(RC.created_at, RC.id)
        ) if code_key(row.article) == key]
    if not rows:
        return None
    invoices: dict = {}
    if number and procurement_factory is not None:
        with procurement_factory() as uow:
            invoices = dict(uow.session.execute(
                select(PL.id, PL.invoice_number).where(PL.id.in_({row.procurement_line_id for row in rows}))
            ).all())
    for row in rows:
        numbers = {norm_doc_number(row.order_number or ""), norm_doc_number(invoices.get(row.procurement_line_id) or "")}
        line_seller = seller_name_key(row.supplier) or str(row.supplier or "").strip().casefold()
        if (number and number in numbers) or _agree(line_seller, seller):
            return ExpectedArrival(order_number=row.order_number or row.legacy_id, supplier=row.supplier or "")
    return None


def purchasing_item(purchasing_factory: Callable | None, article: str) -> dict | None:
    """The live nomenclature card with this part number."""
    key = code_key(article)
    if purchasing_factory is None or not key:
        return None
    with purchasing_factory() as uow:
        row = uow.session.scalar(
            select(ItemRow).where(ItemRow.part_key == key, ItemRow.archived_at.is_(None)).limit(1)
        )
        if row is None:
            return None
        return {"name": row.name or "", "manufacturer": row.manufacturer or "", "item_type": row.item_type or "",
                "unit": row.unit or ""}


def procurement_line(
    procurement_factory: Callable | None, *, supplier: str, invoice_number: str, article: str,
) -> dict | None:
    """The procurement line of the delivery's invoice (and supplier, when typed) with this article; its legacy id names
    it in the source."""
    key, number = code_key(article), norm_doc_number(invoice_number)
    if procurement_factory is None or not key or not number:
        return None
    seller = seller_name_key(supplier)
    with procurement_factory() as uow:
        rows = uow.session.execute(
            select(PL.legacy_id, PL.design_article, PL.invoice_number, PL.supplier, PL.calculated_name,
                   PL.invoice_item_name, PL.item_type, PL.calculated_unit, PL.actual_unit)
            .where(PL.invoice_number.is_not(None), PL.design_article.is_not(None))
            .order_by(PL.created_at.desc(), PL.id)
        )
        for row in rows:
            if norm_doc_number(row.invoice_number) != number or code_key(row.design_article) != key:
                continue
            if seller and seller_name_key(row.supplier) and not _agree(seller_name_key(row.supplier), seller):
                continue
            return {"legacy_id": row.legacy_id, "name": row.calculated_name or row.invoice_item_name or "",
                    "item_type": row.item_type or "", "unit": row.actual_unit or row.calculated_unit or ""}
    return None


def _quantity(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return text


def upd_document(
    purchasing_factory: Callable | None, *, number: str, day: date | None, inn: str, seller: str,
) -> dict | None:
    """The УПД purchasing holds under this number — dated within ±3 days, or of the same seller (ИНН or name). Its
    header (the buyer's corrections win) and lines (corrected ones when there are any)."""
    wanted = norm_doc_number(number)
    if purchasing_factory is None or not wanted or not (day or inn or seller_name_key(seller)):
        return None
    with purchasing_factory() as uow:
        session = uow.session
        filters = []
        if day is not None:
            filters.append(UpdDocumentRow.doc_date.between(
                date.fromordinal(day.toordinal() - NEAR_DAYS), date.fromordinal(day.toordinal() + NEAR_DAYS)))
        if inn:
            filters.append(UpdDocumentRow.seller_inn == inn)
        if seller_name_key(seller):
            filters.append(UpdDocumentRow.number != "")
        candidates = session.scalars(
            select(UpdDocumentRow).where(UpdDocumentRow.twin_of.is_(None), or_(*filters))
            .order_by(UpdDocumentRow.created_at.desc())
        )
        for row in candidates:
            header = {**{"number": row.number, "date": row.doc_date, "seller_name": row.seller_name,
                         "seller_inn": row.seller_inn}, **{
                key: value for key, value in (row.manual_header or {}).items()
                if key in {"number", "date", "seller_name", "seller_inn"} and value}}
            if norm_doc_number(str(header["number"] or "")) != wanted:
                continue
            doc_day = header["date"]
            if isinstance(doc_day, str):
                try:
                    doc_day = date.fromisoformat(doc_day[:10])
                except ValueError:
                    doc_day = None
            near = day is not None and doc_day is not None and abs((doc_day - day).days) <= NEAR_DAYS
            same_seller = (inn and header["seller_inn"] == inn) or _agree(
                seller_name_key(header["seller_name"]), seller_name_key(seller))
            if not (near or same_seller):
                continue
            lines = list(session.scalars(
                select(UpdLineRow).where(UpdLineRow.document_id == row.id).order_by(UpdLineRow.seq)
            ))
            manual = [line for line in lines if line.source == "manual"]
            return {
                "supplier": str(header["seller_name"] or ""),
                "inn": str(header["seller_inn"] or ""),
                "upd_number": str(header["number"] or ""),
                "upd_date": doc_day,
                "basis": row.basis or "",
                "lines": [
                    {"article": line.article or "", "name": line.name or "", "quantity": _quantity(line.quantity),
                     "unit": line.unit or ""}
                    for line in (manual or lines) if not line.is_service
                ],
            }
    return None


# Reference DB tabs whose records are articles of what the shop buys.
_ARTICLE_TABS = (TechOperationTab.MATERIAL, TechOperationTab.TERMINAL, TechOperationTab.COAX, TechOperationTab.WIRE)


def known_articles(
    warehouse_factory: Callable | None, purchasing_factory: Callable | None, references_factory: Callable | None,
) -> list[tuple[str, str]]:
    """(article, where from) of every article the ERP already writes, in the order a scanned one snaps to them: stock
    lots (the shop's own spelling), the live purchasing nomenclature, the reference DB records."""
    known: list[tuple[str, str]] = []
    if warehouse_factory is not None:
        with warehouse_factory() as uow:
            known += [(article, "партии склада") for article in uow.session.scalars(
                select(distinct(StockLotRow.article)).where(StockLotRow.article != "")) if article]
    if purchasing_factory is not None:
        with purchasing_factory() as uow:
            known += [(article, "номенклатуры закупок") for article in uow.session.scalars(
                select(ItemRow.part_number).where(ItemRow.archived_at.is_(None), ItemRow.part_number.is_not(None)))
                if article]
    if references_factory is not None:
        with references_factory() as uow:
            known += [(record.display_text, "справочника") for tab in _ARTICLE_TABS
                      for record in uow.tech_operations.list(tab=tab, query="", limit=10_000)]
    return known
