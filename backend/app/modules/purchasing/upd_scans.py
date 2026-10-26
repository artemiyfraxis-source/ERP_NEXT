"""«Скан склада» in the «Реестр УПД» (owner 26.10.2026, relaying Витя: «сваливать всё в реестр, что будем принимать»).

A paper УПД the warehouse took in through «Поставка» becomes a registry document of origin «scan»: one per stored scan
file (id «wh-<file uuid>»), the PDF copied into `purchasing_document_files` (modules don't share tables), the header as
the storekeeper saved it on the delivery, the lines as the model read them off the scan. Its parser verdict is always
«Требует проверки» — the buyer checks it like any doubtful parse. The buyer's word works as for the other origins: his
header and lines (source «manual») and «проверено» are never touched here; a later change of the delivery moves
`parser_updated`, so the card says the document changed after his edit. The warehouse side (which delivery, which
file, when) is `app.workflows.receipt_upd_registry`."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select

from app.shared.identity import Actor

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


def document_id(file_key: str) -> str:
    return f"{PREFIX}{file_key}"


def version(doc: ScanDocument) -> str:
    """The «parser version» of a scan document: a digest of everything it is written from, so the same delivery and
    reading give the same value (nothing to write) and any change of them a new one."""
    data = json.dumps(asdict(doc), ensure_ascii=False, sort_keys=True, default=str)
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


def _values(doc: ScanDocument, inn: str, md5: str, file_id, now: datetime) -> dict[str, Any]:
    issues = [CHECK_NOTE] + ([doc.warning] if doc.warning else [])
    return {
        "file_name": _cut(doc.file_name, 1024), "folder": _cut(doc.folder, 255), "md5": md5, "file_id": file_id,
        "status": STATUS, "kind": KIND, "tag": "", "issues": "; ".join(issues),
        "model": _cut(f"Склад: {doc.engine}" if doc.engine else "Склад", 64), "number": _cut(doc.number, 128),
        "doc_date": doc.day, "seller_name": _cut(doc.seller_name, 512), "seller_inn": _cut(inn, 32),
        "seller_kpp": "", "buyer_inn": "", "basis": doc.basis, "payment_doc": "", "total_net": None,
        "total_vat": None, "total_gross": None, "file_amount": None, "line_count": len(doc.lines), "dup_of": None,
        "drive_url": "", "parser_updated": version(doc), "synced_at": now,
    }


def _write_lines(session, document_id_: str, doc: ScanDocument) -> None:
    """The reading's lines in the parser's place (source «google», as an EDO XML's); the buyer's stay."""
    session.execute(delete(UpdLineRow).where(UpdLineRow.document_id == document_id_, UpdLineRow.source == "google"))
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
    values = _values(doc, _inn(session, doc), hashlib.md5(data).hexdigest(), stored.id, now)
    if previous is None:
        session.add(UpdDocumentRow(id=key, origin=ORIGIN, created_at=now, **values))
    else:
        for name, value in values.items():
            setattr(previous, name, value)
    session.flush()
    _write_lines(session, key, doc)
    session.flush()
    relink_documents(session, now)
    row = session.get(UpdDocumentRow, key)
    status = "twin" if row.twin_of else "updated" if previous is not None else "created"
    _audit(uow, key, "scan_imported", actor, now, status=status, number=doc.number, lines=len(doc.lines),
           file=doc.file_key)
    return {"document_id": key, "status": status}
