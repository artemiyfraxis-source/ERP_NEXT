"""«Реестр УПД»: the closing documents of the purchasing hybrid, ported from Roller_Buyer
upd-docs.ts, upd-sync.ts (linkUpdSuppliers), upd-twins.ts (markTwins) and edo-import.ts.

A document is the parser's reading (Google for a PDF, the exact XML for EDO) plus the buyer's word: a corrected header
and lines (manual_header, lines with source «manual» — they win on read), «проверено», the project, a supplier chosen by
hand. Everything else is computed: the status, the seller's corrected INN, whose document it is, the supplier by INN and
the twins. `relink_documents` recomputes the stored part of that (supplier_id, twin_of) and runs after every import,
upload and header edit. Writes take the unit of work (audit events are part of the transaction)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from . import suppliers as directory
from .edo_xml import EdoDoc, Unsupported, UploadRejected, edo_document_id, parse_edo_xml, unpack_edo_upload
from .infrastructure import DocumentFileRow, SupplierRow, UpdDocumentRow, UpdLineRow
from .items import alias_key, part_key
from .suppliers import inn_valid
from .upd_checks import (
    STATUSES,
    UNKNOWN_BUYER,
    BuyerVerdict,
    LineFacts,
    classify_buyers,
    doc_issues,
    line_issues,
    registry_status,
)
from .upd_sellers import (
    OWN_INNS,
    SellerVerdict,
    effective_header,
    near_inn,
    resolve_sellers,
    seller_facts,
    seller_inn_of,
)
from .upd_twins import compute_twins, twin_facts

PAGE_SIZE = 100
# The buyer's choice of the supplier lives in the audit trail (no column for it in 0108): the latest event per document
# wins; an empty supplier_id hands the document back to the link by INN.
SUPPLIER_EVENT = "purchasing.upd_supplier_set"
_NO_SELLER = SellerVerdict("", "")


class UpdError(ValueError):
    """A refusal with an HTTP status: 404 not found, 409 conflict, 422 invalid data."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def aggregate_id(document_id: str) -> UUID:
    """Audit events need a UUID; a document's id is the Drive file id or «edo-…»."""
    return uuid5(NAMESPACE_URL, f"erp:purchasing_upd_documents/{document_id}")


def _audit(uow, document_id: str, event: str, actor: Actor, now: datetime, **details: Any) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=aggregate_id(document_id), event_type=f"purchasing.upd_{event}", actor=actor,
        occurred_at=now, details={"document_id": document_id, **{key: str(value) for key, value in details.items()}},
    ))


def _cut(value: Any, size: int) -> str:
    return str(value or "")[:size]


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(Decimal(value).normalize(), "f")


def _split(text: str | None) -> list[str]:
    return [part for part in str(text or "").split("; ") if part]


def get_document(session: Session, document_id: str) -> UpdDocumentRow:
    row = session.get(UpdDocumentRow, document_id)
    if row is None:
        raise UpdError("УПД не найден", 404)
    return row


# ---- computed facts over the whole registry ---------------------------------------------------------------------

def seller_verdicts(docs: list[UpdDocumentRow], known: Iterable[SupplierRow]) -> dict[str, SellerVerdict]:
    """Seller verdicts need the whole registry and the supplier directory: a misread INN joins the real one."""
    verdicts = resolve_sellers([seller_facts(row) for row in docs], [(row.name, row.inn) for row in known], OWN_INNS)
    return {row.id: verdict for row, verdict in zip(docs, verdicts)}


def buyer_verdicts(docs: list[UpdDocumentRow]) -> dict[str, BuyerVerdict]:
    """Buyer verdicts need the whole registry too: a misread INN is matched against the INNs it really has."""
    headers = [effective_header(row) for row in docs]
    verdicts = classify_buyers((header["buyer_inn"] or "", header["seller_inn"] or "") for header in headers)
    return {row.id: verdict for row, verdict in zip(docs, verdicts)}


def manual_suppliers(session: Session) -> dict[str, UUID]:
    """document id → the supplier the buyer chose by hand (documents handed back to the INN are absent)."""
    return {
        document_id: supplier_id for document_id, supplier_id in session.execute(
            select(UpdDocumentRow.id, UpdDocumentRow.supplier_id).where(
                UpdDocumentRow.supplier_manual_at.is_not(None), UpdDocumentRow.supplier_id.is_not(None),
            )
        )
    }


def _received(session: Session) -> set[str]:
    """Documents goods were received by. ERP's receiving keeps the UPD number in its own lines and has no link to this
    registry yet, so no copy is held in force by a receipt."""
    return set()


def relink_documents(session: Session, now: datetime) -> dict[str, int]:
    """Supplier by the seller's corrected INN (never a «supplier» carrying our own INN — it came from a scan with the
    parties swapped; the buyer's choice by hand stays), then twin_of for all. `now` — the moment of the run, for the
    callers' contract; links and twins are facts of the data, nothing is timestamped. Flushes, never commits."""
    docs =list(session.scalars(select(UpdDocumentRow).order_by(UpdDocumentRow.id)))
    known = list(session.scalars(select(SupplierRow).order_by(SupplierRow.created_at, SupplierRow.id)))
    sellers = seller_verdicts(docs, known)
    by_inn: dict[str, UUID] = {}
    # Active suppliers first: an archived duplicate must not catch the documents of the live card.
    for supplier in sorted(known, key=lambda row: row.archived_at is not None):
        if supplier.inn and supplier.inn not in OWN_INNS:
            by_inn.setdefault(supplier.inn, supplier.id)
    allowed = {supplier.id for supplier in known if supplier.inn not in OWN_INNS}
    chosen = manual_suppliers(session)
    linked = 0
    for row in docs:
        choice = chosen.get(row.id)
        supplier_id = choice if choice in allowed else by_inn.get(seller_inn_of(row, sellers.get(row.id)))
        if supplier_id != row.supplier_id:
            row.supplier_id = supplier_id
            linked += 1
    # Grouped by the seller's real INN: a misread digit must not hide a twin.
    twins = compute_twins([twin_facts(row, sellers[row.id].inn) for row in docs], _received(session))
    changed = 0
    for row in docs:
        target = twins.get(row.id)
        if target != row.twin_of:
            row.twin_of = target
            changed += 1
    session.flush()
    return {"documents": len(docs), "suppliers_changed": linked, "twins": len(twins), "twins_changed": changed}


# ---- reading ----------------------------------------------------------------------------------------------------

def _line_facts(line: UpdLineRow) -> LineFacts:
    return LineFacts(line.quantity, line.price, line.net, line.vat_rate or "", line.vat, line.gross)


def line_json(line: UpdLineRow) -> dict[str, Any]:
    own = line.source == "manual"
    return {
        "seq": line.seq, "name": line.name, "article": line.article, "unit": line.unit,
        "quantity": _dec(line.quantity), "price": _dec(line.price), "net": line.net, "vat_rate": line.vat_rate,
        "vat": line.vat, "gross": line.gross, "is_service": line.is_service,
        "product_type_code": line.product_type_code, "unit_code": line.unit_code, "excise": line.excise,
        "country_code": line.country_code, "country": line.country, "declaration": line.declaration,
        # The buyer's lines are re-checked here; the parser's carry the parser's verdict.
        "issues": line_issues(_line_facts(line)) if own else _split(line.issues),
        "fixes": [] if own else _split(line.fixes),
    }


def parser_header(row: UpdDocumentRow) -> dict[str, Any]:
    return {
        "number": row.number, "date": _iso(row.doc_date), "seller_name": row.seller_name, "seller_inn": row.seller_inn,
        "seller_kpp": row.seller_kpp, "buyer_inn": row.buyer_inn, "total_net": row.total_net, "total_vat": row.total_vat,
        "total_gross": row.total_gross,
    }


def _changed_in_parser(row: UpdDocumentRow) -> bool:
    """The parser read the document again after the buyer's edit or verification."""
    return (row.verified_at is not None and row.verified_parser_updated != row.parser_updated) or (
        row.manual_saved_at is not None and row.manual_based_on != row.parser_updated
    )


@dataclass
class Registry:
    """Everything a card needs that is computed over the whole registry, read once per request."""

    docs: list[UpdDocumentRow]
    sellers: dict[str, SellerVerdict]
    buyers: dict[str, BuyerVerdict]
    supplier_names: dict[UUID, str]
    chosen: dict[str, UUID]
    manual_lines: dict[str, list[UpdLineRow]]
    copies: dict[str, list[UpdDocumentRow]]

    def card(self, row: UpdDocumentRow) -> dict[str, Any]:
        header = effective_header(row)
        manual = row.manual_saved_at is not None
        own_lines = self.manual_lines.get(row.id, [])
        issues = doc_issues(row.kind, header, [_line_facts(line) for line in own_lines]) if manual else _split(row.issues)
        seller = self.sellers.get(row.id, _NO_SELLER)
        buyer = self.buyers.get(row.id, UNKNOWN_BUYER)
        return {
            "id": row.id, "file_name": row.file_name, "kind": row.kind, "origin": row.origin,
            "status": registry_status(row.status, row.verified_at, row.twin_of), "parser_status": row.status,
            "number": header["number"] or "", "date": header["date"], "seller_name": header["seller_name"] or "",
            "seller_inn": header["seller_inn"] or "", "seller_org_inn": seller.inn, "seller_note": seller.note,
            "supplier_id": str(row.supplier_id) if row.supplier_id else None,
            "supplier_name": self.supplier_names.get(row.supplier_id) if row.supplier_id else None,
            "supplier_manual": self.chosen.get(row.id) is not None and self.chosen.get(row.id) == row.supplier_id,
            "total_gross": header["total_gross"], "line_count": len(own_lines) if manual else row.line_count,
            "issues": issues, "has_file": row.file_id is not None, "project_reference": row.project_reference,
            "manual": manual, "verified": row.verified_at is not None, "changed_in_parser": _changed_in_parser(row),
            "twin_of": row.twin_of, "copies": len(self.copies.get(row.id, [])),
            "buyer_inn": header["buyer_inn"] or "", "buyer_kind": buyer.kind, "buyer_org_inn": buyer.inn,
            "buyer_note": buyer.note,
        }


def load_registry(session: Session) -> Registry:
    docs = list(session.scalars(select(UpdDocumentRow).order_by(UpdDocumentRow.id)))
    known = list(session.scalars(select(SupplierRow)))
    manual_ids = [row.id for row in docs if row.manual_saved_at is not None]
    manual_lines: dict[str, list[UpdLineRow]] = {}
    if manual_ids:
        for line in session.scalars(
            select(UpdLineRow).where(UpdLineRow.source == "manual", UpdLineRow.document_id.in_(manual_ids))
            .order_by(UpdLineRow.seq)
        ):
            manual_lines.setdefault(line.document_id, []).append(line)
    copies: dict[str, list[UpdDocumentRow]] = {}
    for row in docs:
        if row.twin_of:
            copies.setdefault(row.twin_of, []).append(row)
    return Registry(
        docs=docs, sellers=seller_verdicts(docs, known), buyers=buyer_verdicts(docs),
        supplier_names={row.id: row.name for row in known}, chosen=manual_suppliers(session),
        manual_lines=manual_lines, copies=copies,
    )


def _fold(text: Any) -> str:
    return str(text or "").casefold().replace("ё", "е")


def buyer_key(card: dict[str, Any]) -> str:
    """«ours», «unknown» or the INN of the other organisation the document is for."""
    return (card["buyer_org_inn"] or "unknown") if card["buyer_kind"] == "other" else card["buyer_kind"]


def _needs_review(card: dict[str, Any]) -> bool:
    return card["status"] == "needs_review" or (card["changed_in_parser"] and card["status"] != "duplicate")


@dataclass(frozen=True)
class ListFilter:
    q: str = ""
    status: str | None = None
    origin: str | None = None
    supplier_id: UUID | None = None
    no_supplier: bool = False
    date_from: date | None = None
    date_to: date | None = None
    buyer: str | None = None  # ours | other | unknown | an INN
    review: bool = False  # «требует проверки»: the parser's doubt, or the parser changed it after the buyer's word
    duplicates: bool = False  # the copies, shown apart; hidden otherwise
    page: int = 1


def list_documents(session: Session, wanted: ListFilter) -> dict[str, Any]:
    registry = load_registry(session)
    cards = [registry.card(row) for row in registry.docs]
    in_force = [card for card in cards if card["status"] != "duplicate"]
    origins = {origin: sum(1 for card in in_force if card["origin"] == origin) for origin in ("edo", "google")}
    buyers: dict[str, int] = {}
    for card in in_force:
        if not wanted.origin or card["origin"] == wanted.origin:
            buyers[buyer_key(card)] = buyers.get(buyer_key(card), 0) + 1
    buyer_list = sorted(
        buyers.items(), key=lambda item: (item[0] != "ours", item[0] == "unknown", -item[1], item[0]),
    )
    # The supplier filter offers the suppliers the documents in force are linked to (the directory may be off).
    linked: dict[str, list] = {}
    for card in in_force:
        if card["supplier_id"]:
            linked.setdefault(card["supplier_id"], [card["supplier_name"] or "", 0])[1] += 1
    supplier_list = sorted(
        ({"id": key, "name": name, "count": count} for key, (name, count) in linked.items()),
        key=lambda item: _fold(item["name"]),
    )

    needle = _fold(wanted.q.strip())
    supplier = str(wanted.supplier_id) if wanted.supplier_id else None

    def kept(card: dict[str, Any]) -> bool:
        if wanted.origin and card["origin"] != wanted.origin:
            return False
        if wanted.buyer:
            if wanted.buyer in ("ours", "other", "unknown"):
                if card["buyer_kind"] != wanted.buyer:
                    return False
            elif card["buyer_kind"] != "other" or card["buyer_org_inn"] != wanted.buyer:
                return False
        if supplier and card["supplier_id"] != supplier:
            return False
        if wanted.no_supplier and card["supplier_id"] is not None:
            return False
        if wanted.date_from and (not card["date"] or card["date"] < wanted.date_from.isoformat()):
            return False
        if wanted.date_to and (not card["date"] or card["date"] > wanted.date_to.isoformat()):
            return False
        if needle:
            hay = (card["file_name"], card["seller_name"], card["number"], card["seller_inn"], card["seller_org_inn"],
                   card["supplier_name"] or "")
            if not any(needle in _fold(value) for value in hay):
                return False
        return True

    base = [card for card in cards if kept(card)]
    counts = {status: sum(1 for card in base if card["status"] == status) for status in STATUSES}
    review_count = sum(1 for card in base if _needs_review(card))
    if wanted.status:
        shown = [card for card in base if card["status"] == wanted.status]
    elif wanted.duplicates:
        shown = [card for card in base if card["status"] == "duplicate"]
    else:
        shown = [card for card in base if card["status"] != "duplicate"]
    if wanted.review:
        shown = [card for card in shown if _needs_review(card)]
    shown.sort(key=lambda card: _fold(card["file_name"]))
    shown.sort(key=lambda card: card["date"] or "", reverse=True)
    page = max(1, wanted.page)
    return {
        "items": shown[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], "total": len(shown), "page": page,
        "page_size": PAGE_SIZE, "counts": counts, "review": review_count, "origins": origins,
        "buyers": [{"key": key, "count": count} for key, count in buyer_list], "suppliers": supplier_list,
        "no_supplier": sum(1 for card in in_force if card["supplier_id"] is None),
        "sum_gross": sum(card["total_gross"] or 0 for card in shown if card["status"] != "duplicate"),
    }


def _brief(card: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "origin", "file_name", "kind", "number", "date", "status", "total_gross", "seller_name")
    return {key: card[key] for key in keys}


def document_detail(session: Session, document_id: str) -> dict[str, Any]:
    row = get_document(session, document_id)
    registry = load_registry(session)
    by_id = {doc.id: doc for doc in registry.docs}
    lines = list(session.scalars(
        select(UpdLineRow).where(UpdLineRow.document_id == row.id).order_by(UpdLineRow.seq)
    ))
    manual = row.manual_saved_at is not None
    parser_lines = [line_json(line) for line in lines if line.source == "google"]
    file_info = None
    if row.file_id is not None:
        stored = session.get(DocumentFileRow, row.file_id)
        if stored is not None:
            name = stored.file_name or row.file_name
            kind = "xml" if name.lower().endswith(".xml") else "pdf" if name.lower().endswith(".pdf") else "file"
            file_info = {"name": name, "size": stored.size, "kind": kind}
    main = by_id.get(row.twin_of) if row.twin_of else None
    return {
        **registry.card(row),
        "folder": row.folder, "tag": row.tag, "model": row.model, "basis": row.basis, "payment_doc": row.payment_doc,
        "file_amount": row.file_amount, "dup_of": row.dup_of, "drive_url": row.drive_url,
        "parser_updated": row.parser_updated, "synced_at": _iso(row.synced_at), "verified_at": _iso(row.verified_at),
        "manual_saved_at": _iso(row.manual_saved_at),
        "header": effective_header(row), "parser_header": parser_header(row),
        "lines": [line_json(line) for line in lines if line.source == "manual"] if manual else parser_lines,
        "parser_lines": parser_lines,
        "twins": {
            "main": _brief(registry.card(main)) if main is not None else None,
            "copies": [_brief(registry.card(copy)) for copy in registry.copies.get(row.id, [])],
        },
        "file": file_info,
    }


def document_file(session: Session, document_id: str) -> tuple[bytes, str]:
    row = get_document(session, document_id)
    stored = session.get(DocumentFileRow, row.file_id) if row.file_id is not None else None
    if stored is None:
        raise UpdError("Файла документа нет в базе", 404)
    return stored.content, stored.file_name or row.file_name


# ---- the buyer's word -------------------------------------------------------------------------------------------

_INN = re.compile(r"^(?:[0-9]{10}|[0-9]{12})?$")
_KPP = re.compile(r"^(?:[0-9]{9})?$")


def _text(value: Any, size: int, label: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > size:
        raise UpdError(f"«{label}» — не длиннее {size} символов")
    return text


def _kopecks(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise UpdError(f"«{label}»: сумма — целое число копеек")
    return value


def _decimal(value: Any, label: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise UpdError(f"«{label}»: не число") from None
    if not number.is_finite() or number < 0:
        raise UpdError(f"«{label}»: число не меньше нуля")
    return number


def clean_header(raw: dict[str, Any]) -> dict[str, Any]:
    """The buyer's header as stored in manual_header: every field present, INNs and КПП digits only."""
    day = raw.get("date") or None
    if day is not None:
        try:
            day = date.fromisoformat(str(day)).isoformat()
        except ValueError:
            raise UpdError("Дата документа — ГГГГ-ММ-ДД") from None
    header = {
        "number": _text(raw.get("number"), 100, "№ документа"),
        "date": day,
        "seller_name": _text(raw.get("seller_name"), 300, "Продавец"),
        "seller_inn": _text(raw.get("seller_inn"), 12, "ИНН продавца"),
        "seller_kpp": _text(raw.get("seller_kpp"), 9, "КПП продавца"),
        "buyer_inn": _text(raw.get("buyer_inn"), 12, "ИНН покупателя"),
        "total_net": _kopecks(raw.get("total_net"), "Итого без НДС"),
        "total_vat": _kopecks(raw.get("total_vat"), "НДС"),
        "total_gross": _kopecks(raw.get("total_gross"), "Итого с НДС"),
    }
    for field, label in (("seller_inn", "ИНН продавца"), ("buyer_inn", "ИНН покупателя")):
        if not _INN.match(header[field]):
            raise UpdError(f"{label} — 10 или 12 цифр")
    if not _KPP.match(header["seller_kpp"]):
        raise UpdError("КПП — 9 цифр")
    return header


def clean_lines(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    seqs: set[int] = set()
    for index, item in enumerate(raw, start=1):
        seq = item.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise UpdError(f"Строка {index}: № п/п — целое число больше нуля")
        if seq in seqs:
            raise UpdError(f"№ п/п {seq} повторяется")
        seqs.add(seq)
        where = f"Строка {seq}"
        lines.append({
            "seq": seq,
            "name": _text(item.get("name"), 500, f"{where}: наименование"),
            "article": _text(item.get("article"), 100, f"{where}: артикул"),
            "unit": _text(item.get("unit"), 30, f"{where}: ед."),
            "quantity": _decimal(item.get("quantity"), f"{where}: кол-во"),
            "price": _decimal(item.get("price"), f"{where}: цена"),
            "net": _kopecks(item.get("net"), f"{where}: без НДС"),
            "vat_rate": _text(item.get("vat_rate"), 10, f"{where}: ставка"),
            "vat": _kopecks(item.get("vat"), f"{where}: НДС"),
            "gross": _kopecks(item.get("gross"), f"{where}: с НДС"),
            "is_service": bool(item.get("is_service")),
        })
    return lines


def _line_row(document_id: str, source: str, **values: Any) -> UpdLineRow:
    """article_key / name_key are derived on every write, for lookups by item."""
    article = _cut(values.pop("article", ""), 255)
    name = str(values.pop("name", "") or "")
    key = part_key(article)
    return UpdLineRow(
        id=uuid4(), document_id=document_id, source=source, name=name, article=article,
        article_key=key[:255] if key else None, name_key=_cut(alias_key(name), 512), **values,
    )


def save_manual(
    uow, document_id: str, header: dict[str, Any], lines: list[dict[str, Any]], expected_parser_updated: str,
    expected_saved_at: str | None, actor: Actor, now: datetime,
) -> UpdDocumentRow:
    """The buyer's own version of the document; the parser's data stays as it was. Refused when the document changed
    since the card was opened (the parser read it again, or someone saved another version)."""
    session = uow.session
    row = get_document(session, document_id)
    if row.parser_updated != expected_parser_updated:
        raise UpdError("Документ переразобран, пока вы его правили — откройте карточку заново", 409)
    if _iso(row.manual_saved_at) != (expected_saved_at or None):
        raise UpdError("Документ уже исправили — откройте карточку заново", 409)
    header = clean_header(header)
    lines = clean_lines(lines)
    session.execute(delete(UpdLineRow).where(UpdLineRow.document_id == row.id, UpdLineRow.source == "manual"))
    for line in lines:
        session.add(_line_row(row.id, "manual", **line))
    row.manual_header = header
    row.manual_saved_at, row.manual_saved_by, row.manual_based_on = now, actor.id, row.parser_updated
    session.flush()
    # A corrected INN, number or date may make (or unmake) a supplier link and a twin.
    relink_documents(session, now)
    _audit(uow, row.id, "manual_saved", actor, now, number=header["number"], lines=len(lines),
           total_gross=header["total_gross"] if header["total_gross"] is not None else "")
    return row


def drop_manual(uow, document_id: str, actor: Actor, now: datetime) -> UpdDocumentRow:
    session = uow.session
    row = get_document(session, document_id)
    if row.manual_saved_at is None and row.manual_header is None:
        return row
    session.execute(delete(UpdLineRow).where(UpdLineRow.document_id == row.id, UpdLineRow.source == "manual"))
    row.manual_header = None
    row.manual_saved_at = row.manual_saved_by = row.manual_based_on = None
    session.flush()
    relink_documents(session, now)
    _audit(uow, row.id, "manual_dropped", actor, now)
    return row


def set_verified(uow, document_id: str, verified: bool, actor: Actor, now: datetime) -> UpdDocumentRow:
    row = get_document(uow.session, document_id)
    if verified and row.status == "В очереди":
        raise UpdError("Документ ещё не разобран — проверять нечего")
    if verified == (row.verified_at is not None) and (not verified or row.verified_parser_updated == row.parser_updated):
        return row
    if verified:
        row.verified_at, row.verified_by, row.verified_parser_updated = now, actor.id, row.parser_updated
    else:
        row.verified_at = row.verified_by = row.verified_parser_updated = None
    uow.session.flush()
    # A checked copy outranks a doubtful one among twins.
    relink_documents(uow.session, now)
    _audit(uow, row.id, "verified" if verified else "unverified", actor, now)
    return row


def set_supplier(uow, document_id: str, supplier_id: UUID | None, actor: Actor, now: datetime) -> UpdDocumentRow:
    """The buyer's choice of the supplier, kept by every relink; None hands the document back to the link by INN."""
    session = uow.session
    row = get_document(session, document_id)
    name = ""
    if supplier_id is not None:
        supplier = session.get(SupplierRow, supplier_id)
        if supplier is None:
            raise UpdError("Поставщик не найден", 404)
        if supplier.inn in OWN_INNS:
            raise UpdError("У этого поставщика ИНН нашей организации — документ не может быть от неё")
        name = supplier.name
    if supplier_id is None:
        row.supplier_manual_at, row.supplier_manual_by = None, None
    else:
        row.supplier_id, row.supplier_manual_at, row.supplier_manual_by = supplier_id, now, actor.id
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=aggregate_id(row.id), event_type=SUPPLIER_EVENT, actor=actor, occurred_at=now,
        details={"document_id": row.id, "supplier_id": str(supplier_id or ""), "supplier": name},
    ))
    session.flush()
    relink_documents(session, now)
    return row


def set_project(uow, document_id: str, reference: str | None, actor: Actor, now: datetime) -> UpdDocumentRow:
    row = get_document(uow.session, document_id)
    text = _text(reference, 255, "Проект") or None
    if text != row.project_reference:
        row.project_reference = text
        uow.session.flush()
        _audit(uow, row.id, "project_set", actor, now, project=text or "")
    return row


# ---- EDO upload -------------------------------------------------------------------------------------------------

def _stored_file(session: Session, name: str, content: bytes, now: datetime) -> DocumentFileRow:
    """One row per distinct content: the same XML uploaded twice is kept once."""
    digest = hashlib.sha256(content).hexdigest()
    found = session.scalar(select(DocumentFileRow).where(DocumentFileRow.sha256 == digest))
    if found is not None:
        return found
    row = DocumentFileRow(id=uuid4(), sha256=digest, file_name=_cut(name, 1024), size=len(content), content=content,
                          created_at=now)
    session.add(row)
    session.flush()
    return row


def _edo_values(doc: EdoDoc, name: str, md5: str, file_id: UUID, now: datetime) -> dict[str, Any]:
    header = doc.header
    return {
        "file_name": _cut(name, 1024), "folder": "edo", "md5": md5, "file_id": file_id,
        "status": "Требует проверки" if doc.issues else "Разобран", "kind": doc.kind, "tag": "",
        "issues": "; ".join(doc.issues), "model": _cut(f"ЭДО XML {doc.version}", 64), "number": _cut(header.number, 128),
        "doc_date": date.fromisoformat(header.date) if header.date else None,
        "seller_name": _cut(header.seller_name, 512), "seller_inn": _cut(header.seller_inn, 32),
        "seller_kpp": _cut(header.seller_kpp, 32), "buyer_inn": _cut(header.buyer_inn, 32), "basis": header.basis,
        "payment_doc": header.payment_doc, "total_net": header.total_net, "total_vat": header.total_vat,
        "total_gross": header.total_gross, "file_amount": None, "line_count": len(doc.lines), "dup_of": None,
        "drive_url": "",
        # The «version» a verification or the buyer's edit is compared against: the content itself.
        "parser_updated": f"{doc.formed_at}|{md5}" if doc.formed_at else md5, "synced_at": now,
    }


def _write_edo_lines(session: Session, document_id: str, doc: EdoDoc) -> None:
    """The document's own lines; the buyer's version (source «manual») and verification stay."""
    session.execute(delete(UpdLineRow).where(UpdLineRow.document_id == document_id, UpdLineRow.source == "google"))
    used: set[int] = set()
    for line in doc.lines:
        # A broken XML may repeat НомСтр; the table keeps one line per number, so a repeat takes the next free one.
        seq = line.seq if line.seq not in used else max(used) + 1
        used.add(seq)
        session.add(_line_row(
            document_id, "google", seq=seq, name=line.name, article=line.article,
            product_type_code=_cut(line.product_type_code, 64), unit_code=_cut(line.unit_code, 16),
            unit=_cut(line.unit, 64), quantity=line.quantity, price=line.price, net=line.net,
            excise=_cut(line.excise, 64), vat_rate=_cut(line.vat_rate, 16), vat=line.vat, gross=line.gross,
            country_code=_cut(line.country_code, 16), country=_cut(line.country, 128),
            declaration=_cut(line.declaration, 255), is_service=line.is_service, issues="", fixes="",
        ))


def import_edo(uow, uploads: list[tuple[str, bytes]], actor: Actor, now: datetime) -> dict[str, Any]:
    """EDO documents (ФНС XML from Diadoc and other operators) into the registry. The XML is exact, so its data takes
    the place the Google reading has for a PDF: the same columns and lines, origin «edo». Re-importing a file changes
    nothing; the same document twice (a PDF and its XML, or an XML resent) ends up as a twin. The caller rolls the
    session back for a dry run — the report then says what an apply would do, twins included."""
    session = uow.session
    files: list[dict[str, Any]] = []
    written: list[tuple[int, str]] = []
    seen: set[str] = set()
    for upload_name, content in uploads:
        try:
            unpacked = unpack_edo_upload(upload_name, content)
        except UploadRejected as error:
            files.append({"name": upload_name, "status": "error", "message": str(error)})
            continue
        for xml_file in unpacked:
            parsed = parse_edo_xml(xml_file.xml, xml_file.name)
            if isinstance(parsed, Unsupported):
                files.append({"name": xml_file.name, "status": "skipped", "message": parsed.reason})
                continue
            document_id = edo_document_id(parsed.id_file)
            base = {"name": xml_file.name, "document_id": document_id, "number": parsed.header.number,
                    "seller_name": parsed.header.seller_name}
            # The same XML twice in one upload (two archives overlap): once is enough.
            if document_id in seen:
                files.append({**base, "status": "unchanged", "message": "уже есть в этой загрузке"})
                continue
            seen.add(document_id)
            md5 = hashlib.md5(xml_file.content).hexdigest()
            previous = session.get(UpdDocumentRow, document_id)
            if previous is not None and previous.origin != "edo":
                files.append({**base, "status": "error", "message": "ключ документа совпал с файлом из Google — не принят"})
                continue
            if previous is not None and previous.md5 == md5:
                files.append({**base, "status": "unchanged"})
                continue
            stored = _stored_file(session, xml_file.name, xml_file.content, now)
            values = _edo_values(parsed, xml_file.name, md5, stored.id, now)
            if previous is None:
                session.add(UpdDocumentRow(id=document_id, origin="edo", created_at=now, **values))
            else:
                for key, value in values.items():
                    setattr(previous, key, value)
            session.flush()
            _write_edo_lines(session, document_id, parsed)
            files.append({**base, "status": "updated" if previous is not None else "created"})
            written.append((len(files) - 1, document_id))
    if written:
        session.flush()
        relink_documents(session, now)
        for index, document_id in written:
            row = session.get(UpdDocumentRow, document_id)
            main = session.get(UpdDocumentRow, row.twin_of) if row is not None and row.twin_of else None
            if main is not None:
                message = ("этот документ уже загружен из ЭДО (повторная отправка)" if main.origin == "edo"
                           else f"уже есть как PDF «{main.file_name}»")
                files[index] = {**files[index], "status": "twin", "message": message}
        statuses = [item["status"] for item in files]
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=uuid4(), event_type="purchasing.upd_edo_imported", actor=actor, occurred_at=now,
            details={status: str(statuses.count(status)) for status in ("created", "updated", "twin")},
        ))
    return {"files": files}


# ---- sellers without a supplier ---------------------------------------------------------------------------------

def sellers_without_supplier(session: Session) -> dict[str, Any]:
    """Sellers of documents in force (no copies, no М-15 tolling, nothing waiting or not a purchase) that are not in
    the supplier directory, by the seller's real INN: a misread INN counts under the real one."""
    registry = load_registry(session)
    known = {inn for inn in session.scalars(select(SupplierRow.inn)) if inn}
    groups: dict[str, dict[str, Any]] = {}
    for row in registry.docs:
        card = registry.card(row)
        if card["status"] in ("duplicate", "not_purchase", "waiting") or card["kind"] == "М-15":
            continue
        inn = card["seller_org_inn"] or card["seller_inn"]
        key = inn or f"name:{_fold(card['seller_name'])}"
        group = groups.setdefault(key, {
            "inn": inn, "name": card["seller_name"], "documents": 0, "total_gross": 0, "last_date": None,
            "needs_review": 0, "linked": False,
        })
        group["documents"] += 1
        group["total_gross"] += card["total_gross"] or 0
        if card["date"] and (not group["last_date"] or card["date"] > group["last_date"]):
            group["last_date"] = card["date"]
            group["name"] = card["seller_name"] or group["name"]
        if card["status"] == "needs_review":
            group["needs_review"] += 1
        group["linked"] = group["linked"] or card["supplier_id"] is not None
    sellers: list[dict[str, Any]] = []
    skipped = 0
    for group in groups.values():
        linked = group.pop("linked")
        if not group["inn"] or not inn_valid(group["inn"]):
            skipped += 1
            continue
        if linked or group["inn"] in known or near_inn(group["inn"], OWN_INNS):
            continue  # linked (by INN or by hand), already in the directory, or our own company
        sellers.append(group)
    sellers.sort(key=lambda group: (-group["total_gross"], group["inn"]))
    return {"sellers": sellers, "skipped_invalid_inn": skipped}


def import_sellers(uow, inns: list[str] | None, actor: Actor, now: datetime) -> dict[str, Any]:
    """Suppliers for the sellers above (all, or only `inns`), then the documents are linked to them. The caller rolls
    back for a dry run."""
    session = uow.session
    wanted = set(inns) if inns is not None else None
    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for seller in sellers_without_supplier(session)["sellers"]:
        if wanted is not None and seller["inn"] not in wanted:
            continue
        name = " ".join(str(seller["name"] or "").split())[:255] or f"ИНН {seller['inn']}"
        row = None
        reason = ""
        for candidate in (name, f"{name[:236]} (ИНН {seller['inn']})"):
            try:
                row = directory.create(session, {"name": candidate, "inn": seller["inn"], "categories": []}, actor.id, now)
                break
            except directory.SupplierError as error:
                reason = str(error)
        if row is None:
            skipped.append({"inn": seller["inn"], "name": name, "reason": reason})
            continue
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=row.id, event_type="purchasing.supplier_created", actor=actor, occurred_at=now,
            details={"name": row.name, "inn": row.inn or "", "source": "УПД"},
        ))
        created.append({"inn": seller["inn"], "name": row.name, "supplier_id": str(row.id)})
    links = relink_documents(session, now) if created else None
    return {"created": created, "skipped": skipped, "suppliers_changed": links["suppliers_changed"] if links else 0}
