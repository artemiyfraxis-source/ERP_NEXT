"""«Скан склада» in the «Реестр УПД» (owner 26.10.2026, relaying Витя: «сваливать всё в реестр, что будем принимать»).

A paper УПД the warehouse took in through «Поставка» becomes a registry document of origin «scan»: one per stored scan
file (id «wh-<file uuid>»), the PDF copied into `purchasing_document_files` (modules don't share tables), the header as
the storekeeper saved it on the delivery. Since 26.10.2026 the scan is read by the ERP's one document reader
(`app.shared.doc_reader`, Витя: «остальные данные парсятся в реестр упд (Деньги, привязка к остальным документам (Счет,
платёжка))»): the same reading that gave the delivery its rows gives the registry the kind, prices, net / VAT / gross
per line, the totals, the basis and the payments, the reader's remarks and its verdict — checked against the header
the storekeeper saved (his № / date / seller win over the reading). A scan read without the model (tesseract, a
reading kept before) is written as before: the lines without money, «Требует проверки».

The buyer's word works as for the other origins: his header and lines (source «manual») and «проверено» are never
touched here; a later change of the delivery or the reading moves `parser_updated`, so the card says the document
changed after his edit. The warehouse side (which delivery, which file, when) is `app.workflows.receipt_upd_registry`."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select

from app.shared.doc_reader import closing
from app.shared.identity import Actor

from . import upd_parse
from .infrastructure import SupplierRow, UpdDocumentRow, UpdLineRow
from .upd_registry import _audit, _cut, _line_row, _stored_file, relink_documents
from .upd_sellers import seller_name_key

ORIGIN = "scan"
PREFIX = "wh-"
STATUS = "Требует проверки"
KIND = "УПД"
CHECK_NOTE = "Скан со склада: шапка — как сохранил кладовщик, строки — распознавание модели; сверьте с файлом"


@dataclass(frozen=True)
class ScanLine:
    name: str
    article: str
    unit: str
    quantity: Decimal | None
    number: str = ""  # «№ п/п» as read; the line takes it as its seq when it is a free whole number


@dataclass(frozen=True)
class ScanDocument:
    """What the warehouse knows of one stored scan, as plain values."""

    file_key: str  # the warehouse file's uuid
    file_name: str
    folder: str  # where it came from, e.g. «Склад ЦАР»
    number: str
    day: date | None
    seller_name: str
    seller_inn: str  # '' — unknown
    basis: str
    engine: str = ""  # who read the lines: «текст PDF», the model, «tesseract»
    warning: str = ""  # how the reading fell back
    lines: tuple[ScanLine, ...] = field(default_factory=tuple)
    payment_doc: str = ""  # the ПРД as an ЭДО document names it: «№ 916 от 19.10.2026»
    # The document reader's answer per page, as the warehouse keeps it with the file (None — a page no model read);
    # () — the scan was read without the model: the lines above, no money.
    pages: tuple[dict | None, ...] = ()


def document_id(file_key: str) -> str:
    return f"{PREFIX}{file_key}"


def version(doc: ScanDocument) -> str:
    """The «parser version» of a scan document: a digest of everything it is written from, so the same delivery and
    reading give the same value (nothing to write) and any change of them a new one. A scan read without the model
    (no `pages`) digests as before the pages were kept (27.10.2026): its document is not marked «changed after the
    buyer's check» by the merge alone."""
    fields = asdict(doc)
    if not fields["pages"]:
        fields.pop("pages")
    data = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
    return f"wh|{hashlib.sha1(data.encode('utf-8')).hexdigest()}"


def _inn(session, doc: ScanDocument) -> str:
    """The seller's INN as read; when none, the INN of the one directory supplier with the seller's name."""
    inn = "".join(doc.seller_inn.split())
    if inn:
        return inn
    key = seller_name_key(doc.seller_name)
    if not key:
        return ""
    found = {row.inn for row in session.scalars(select(SupplierRow).where(SupplierRow.inn.is_not(None)))
             if row.inn and seller_name_key(row.name) == key}
    return found.pop() if len(found) == 1 else ""


def parsed_doc(doc: ScanDocument, inn: str) -> closing.ParsedDoc | None:
    """The reader's pages merged and checked under the header the storekeeper saved (№, date, seller and its ИНН, the
    basis and the payments as the delivery has them); the seller's КПП only when the ИНН is the one read off the scan.
    None — no page was read by the model."""
    if not any(isinstance(page, dict) for page in doc.pages):
        return None
    read_inn = "".join(ch for ch in str(closing.merge_pages(doc.pages).get("sellerInn") or "") if ch.isdigit())
    header: dict[str, Any] = {
        "number": doc.number, "date": doc.day.isoformat() if doc.day else None, "sellerName": doc.seller_name,
        "sellerInn": inn, "basis": doc.basis, "paymentDoc": doc.payment_doc,
    }
    if inn != read_inn:
        header["sellerKpp"] = ""
    return closing.closing_doc(doc.pages, header=header)


def _values(doc: ScanDocument, parsed: closing.ParsedDoc | None, inn: str, md5: str, file_id,
            now: datetime) -> dict[str, Any]:
    common = {
        "file_name": _cut(doc.file_name, 1024), "folder": _cut(doc.folder, 255), "md5": md5, "file_id": file_id,
        "tag": "", "model": _cut(f"Склад: {doc.engine}" if doc.engine else "Склад", 64), "number": _cut(doc.number, 128),
        "doc_date": doc.day, "seller_name": _cut(doc.seller_name, 512), "seller_inn": _cut(inn, 32),
        "basis": doc.basis, "payment_doc": doc.payment_doc, "file_amount": None, "dup_of": None, "drive_url": "",
        "parser_updated": version(doc), "synced_at": now,
    }
    if parsed is None:
        issues = [CHECK_NOTE] + ([doc.warning] if doc.warning else [])
        return {**common, "status": STATUS, "kind": KIND, "issues": "; ".join(issues), "seller_kpp": "",
                "buyer_inn": "", "total_net": None, "total_vat": None, "total_gross": None,
                "line_count": len(doc.lines)}
    # The reader's own remarks: a page the model did not read is named there («их строк в разборе нет») — the
    # warehouse's fallback note about it would only repeat it.
    values = upd_parse.header_values(parsed)
    return {**common, "status": values["status"], "kind": values["kind"], "issues": values["issues"],
            "seller_kpp": values["seller_kpp"], "buyer_inn": values["buyer_inn"], "total_net": values["total_net"],
            "total_vat": values["total_vat"], "total_gross": values["total_gross"], "line_count": len(parsed.lines)}


def _write_lines(session, document_id_: str, doc: ScanDocument, parsed: closing.ParsedDoc | None) -> None:
    """The reading's lines in the parser's place (source «google», as an EDO XML's); the buyer's stay. Read by the
    model — with their money, as a registry PDF's; without it — name, code, unit and quantity as the warehouse read
    them."""
    session.execute(delete(UpdLineRow).where(UpdLineRow.document_id == document_id_, UpdLineRow.source == "google"))
    if parsed is not None:
        for line in parsed.lines:
            session.add(_line_row(document_id_, "google", **upd_parse.line_values(line)))
        return
    used: set[int] = set()
    for line in doc.lines:
        seq = int(line.number) if line.number.isdigit() and int(line.number) > 0 else 0
        if not seq or seq in used:
            seq = max(used, default=0) + 1
        used.add(seq)
        session.add(_line_row(
            document_id_, "google", seq=seq, name=line.name, article=line.article, unit=_cut(line.unit, 64),
            quantity=line.quantity, issues="", fixes="",
        ))


def import_scan(uow, doc: ScanDocument, content: Callable[[], bytes], actor: Actor, now: datetime) -> dict[str, Any]:
    """The scan document created or brought up to date. `content` — the PDF's bytes, read only when something is
    written. Returns `{document_id, status}`: created | updated | unchanged | twin (written, and it is a copy of a
    document the registry already holds — an ЭДО XML or a Google PDF of the same УПД) | error (`message`)."""
    session = uow.session
    key = document_id(doc.file_key)
    previous = session.get(UpdDocumentRow, key)
    if previous is not None and previous.origin != ORIGIN:
        return {"document_id": key, "status": "error", "message": "ключ документа занят документом другого источника"}
    if previous is not None and previous.parser_updated == version(doc):
        return {"document_id": key, "status": "unchanged"}
    data = content()
    if not data:
        return {"document_id": key, "status": "error", "message": "файл скана пустой"}
    stored = _stored_file(session, doc.file_name, data, now)
    inn = _inn(session, doc)
    parsed = parsed_doc(doc, inn)
    values = _values(doc, parsed, inn, hashlib.md5(data).hexdigest(), stored.id, now)
    if previous is None:
        session.add(UpdDocumentRow(id=key, origin=ORIGIN, created_at=now, **values))
    else:
        for name, value in values.items():
            setattr(previous, name, value)
    session.flush()
    _write_lines(session, key, doc, parsed)
    session.flush()
    relink_documents(session, now)
    row = session.get(UpdDocumentRow, key)
    status = "twin" if row.twin_of else "updated" if previous is not None else "created"
    _audit(uow, key, "scan_imported", actor, now, status=status, number=doc.number,
           lines=len(parsed.lines) if parsed is not None else len(doc.lines), file=doc.file_key,
           verdict=values["status"])
    return {"document_id": key, "status": status}
