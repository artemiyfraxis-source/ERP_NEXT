"""PDFs of the registry read by its own parser (`upd_parse`): the upload, the queue, the parser's answer written.

A document read from a PDF keeps origin «google» — the registry's word for «a paper document read by a model» (doc
chain, twins and the screens treat it so), whether it came from Roller_Buyer's Google parser through `rb_import` or is
uploaded and parsed here (key «pdf-<sha1 of the file>»). No migration: the parser's verdict lives in `status` as
before, with two waiting values — «В очереди» and «Разбирается».

A parse is claimed by a conditional update «В очереди» → «Разбирается», so of several API processes exactly one
reads a document (a model call is paid). The parser's columns and its lines (source «google») are replaced; the
buyer's word — manual header and lines, «Проверено», a chosen supplier or project — is never touched, and the new
`parser_updated` tells the card that the parse changed after the buyer's edit."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update

from app.shared.identity import Actor

from . import upd_parse
from .infrastructure import DocumentFileRow, UpdDocumentRow, UpdLineRow
from .upd_registry import UpdError, _audit, _cut, _line_row, _stored_file, get_document, relink_documents

ORIGIN = "google"
PREFIX = "pdf-"
FOLDER = "Загрузка в ERP"
MAX_BYTES = 20 * 1024 * 1024
MAX_FILES = 20
# A claim older than this is a parse whose process died (restart, deploy): «Разобрать заново» may take it again.
STALE_CLAIM = timedelta(minutes=15)
_TAG_CODES = {label: code for code, label in upd_parse.TAG_LABELS.items() if label}


def pdf_document_id(content: bytes) -> str:
    return f"{PREFIX}{hashlib.sha1(content).hexdigest()}"


def tag_code(row: UpdDocumentRow) -> str:
    """The file's tag as the parser takes it: the stored label («М-15», «ТН + СФ»), else read off the name."""
    return _TAG_CODES.get(row.tag or "", "") or upd_parse.file_tag(row.file_name)


def add_pdfs(uow, uploads: list[tuple[str, bytes]], actor: Actor, now: datetime) -> dict[str, Any]:
    """PDFs into the registry, each «В очереди». The same file twice (by content) is kept once: a PDF the registry
    already holds — uploaded here or imported from Roller_Buyer — is reported, not added. Returns `{files: [{name,
    status: created | unchanged | error, document_id?, message?}], queued: [document ids]}`."""
    session = uow.session
    files: list[dict[str, Any]] = []
    queued: list[str] = []
    for name, content in uploads[:MAX_FILES]:
        if not content.startswith(b"%PDF"):
            files.append({"name": name, "status": "error", "message": "это не PDF"})
            continue
        if len(content) > MAX_BYTES:
            files.append({"name": name, "status": "error", "message": "файл больше 20 МБ"})
            continue
        md5 = hashlib.md5(content).hexdigest()
        same = session.scalar(select(UpdDocumentRow).where(UpdDocumentRow.md5 == md5, UpdDocumentRow.origin == ORIGIN))
        if same is not None:
            files.append({"name": name, "status": "unchanged", "document_id": same.id,
                          "message": f"уже есть в реестре: «{same.file_name}»"})
            continue
        key = pdf_document_id(content)
        if session.get(UpdDocumentRow, key) is not None:
            files.append({"name": name, "status": "unchanged", "document_id": key, "message": "уже есть в реестре"})
            continue
        stored = _stored_file(session, name, content, now)
        tag = upd_parse.file_tag(name)
        session.add(UpdDocumentRow(
            id=key, origin=ORIGIN, file_name=_cut(name, 1024), folder=FOLDER, md5=md5, file_id=stored.id,
            status=upd_parse.QUEUED, kind="", tag=upd_parse.TAG_LABELS[tag], issues="", model="",
            file_amount=upd_parse.kopecks(upd_parse.file_amount(name)), line_count=0, drive_url="",
            parser_updated="", synced_at=now, created_at=now,
        ))
        session.flush()
        _audit(uow, key, "pdf_uploaded", actor, now, file=name)
        files.append({"name": name, "status": "created", "document_id": key})
        queued.append(key)
    for name, _ in uploads[MAX_FILES:]:
        files.append({"name": name, "status": "error", "message": f"за раз — не больше {MAX_FILES} файлов"})
    return {"files": files, "queued": queued}


def queue_parse(uow, document_id: str, actor: Actor, now: datetime) -> UpdDocumentRow:
    """«Разобрать заново»: the document waits for the parser again. Its current reading stays until the new one
    is written (a failed parse leaves it and says so)."""
    row = get_document(uow.session, document_id)
    if row.origin != ORIGIN:
        raise UpdError("Разобрать заново можно только PDF — ЭДО и скан склада читаются из своих источников", 409)
    if row.file_id is None:
        raise UpdError("У документа нет файла PDF — разбирать нечего", 409)
    if row.status == upd_parse.PARSING and row.synced_at and _since(row.synced_at, now) < STALE_CLAIM:
        raise UpdError("Документ уже разбирается", 409)
    row.status = upd_parse.QUEUED
    row.synced_at = now
    _audit(uow, document_id, "parse_queued", actor, now)
    return row


def _since(moment: datetime, now: datetime) -> timedelta:
    """SQLite hands timestamps back without a zone: compare like with like."""
    if moment.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    elif moment.tzinfo is not None and now.tzinfo is None:
        moment = moment.replace(tzinfo=None)
    return now - moment


def queued_ids(session, limit: int | None = None) -> list[str]:
    query = (select(UpdDocumentRow.id).where(UpdDocumentRow.origin == ORIGIN, UpdDocumentRow.status == upd_parse.QUEUED)
             .order_by(UpdDocumentRow.created_at, UpdDocumentRow.id))
    return list(session.scalars(query.limit(limit) if limit else query))


@dataclass(frozen=True)
class Claimed:
    document_id: str
    file_name: str
    tag: str  # 'M15' | 'TN' | ''
    content: bytes


def claim(uow, document_id: str, now: datetime) -> Claimed | None:
    """«В очереди» → «Разбирается» for this caller only; None — not queued (another process took it, or it was
    never queued). The caller commits."""
    session = uow.session
    taken = session.execute(
        update(UpdDocumentRow)
        .where(UpdDocumentRow.id == document_id, UpdDocumentRow.origin == ORIGIN,
               UpdDocumentRow.status == upd_parse.QUEUED)
        .values(status=upd_parse.PARSING, synced_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if taken != 1:
        return None
    row = session.get(UpdDocumentRow, document_id)
    session.refresh(row)
    content = session.scalar(select(DocumentFileRow.content).where(DocumentFileRow.id == row.file_id))
    if not content:
        row.status, row.issues = upd_parse.ERROR, "файл PDF не найден в базе"
        return None
    return Claimed(document_id, row.file_name, tag_code(row), content)


def store_parse(uow, document_id: str, doc: upd_parse.ParsedDoc, model: str, actor: Actor, now: datetime) -> str | None:
    """The parser's answer in place of the previous one; returns the verdict (None — the document is gone)."""
    session = uow.session
    row = session.get(UpdDocumentRow, document_id)
    if row is None or row.origin != ORIGIN:
        return None
    for name, value in upd_parse.header_values(doc).items():
        setattr(row, name, value)
    row.model = _cut(model, 64)
    row.parser_updated = _cut(f"erp|{model}|{now.isoformat()}", 128)
    row.synced_at = now
    session.flush()
    session.execute(delete(UpdLineRow).where(UpdLineRow.document_id == document_id, UpdLineRow.source == "google"))
    for line in doc.lines:
        session.add(_line_row(document_id, "google", **upd_parse.line_values(line)))
    session.flush()
    relink_documents(session, now)
    _audit(uow, document_id, "pdf_parsed", actor, now, status=doc.status, model=model, lines=len(doc.lines),
           issues=len(doc.issues))
    return doc.status


def store_failure(uow, document_id: str, message: str, now: datetime) -> None:
    """A parse that did not happen: «Ошибка» with the reason; the previous reading (header, lines) stays."""
    row = uow.session.get(UpdDocumentRow, document_id)
    if row is None or row.origin != ORIGIN:
        return
    had_reading = row.line_count > 0 or bool(row.number)
    row.status = upd_parse.ERROR
    row.issues = _cut(f"Разбор не удался: {message}" + ("; оставлен прежний разбор" if had_reading else ""), 2000)
    row.synced_at = now
