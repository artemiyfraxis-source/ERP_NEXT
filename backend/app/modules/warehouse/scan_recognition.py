"""«Отсканировать УПД»: the answer of a stored scan, made in the background and kept (owner 26.10.2026).

A model reads a page in 8–100 s — longer than a request may last behind the proxy (502). So nothing is recognised
inside a request: the scan (from the scanner agent, «Файл…» or a merge of sheets) gets a done scan job, the reading
runs as a background task (`recognise_job`), and the screen polls the job until its `result` is stored. Each file
keeps its own reading (`scan_reading`), so a merge of sheets combines the kept readings and reads nothing again.

The answer — `{scan_file, recognised: {supplier, inn, upd_number, upd_date, invoice_number, invoice_date}, lines:
[{article, name, quantity, unit, packs, pack, pack_kind, supplier_code, source}], note}`: the УПД purchasing holds
under the recognised number first (header and lines from it), else the rows read off the scan, their articles snapped
to known ones (stock, purchasing, reference DB)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request

from app.shared.file_types import sniffed_type
from app.workflows.stock_receipt import known_articles, upd_document

from . import receipt_llm
from .application import UnitOfWork
from .domain import RECEIPT_SCAN_PATH
from .receipt_scan import NO_TEXT, RecognisedUpd, parse_upd_text
from .receipts_application import (
    keep_scan_recognition,
    scan_file,
    scan_recognition,
    upd_llm_settings,
)
from .scan_jobs import DONE, ScanJob, save_result, stored_result
from .scan_reading import ScanReading, merge_readings, read_scan, reading_from_json, reading_json
from .upd_lines import line_answer, snap_articles

OCR_LINES = "Строки прочитаны со скана ({count}) — сверьте артикулы и количество"
FAILED = "УПД не удалось распознать — заполните вручную"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanSources:
    """Where a scan's answer reads from besides the file: the purchasing УПД registry, and the known articles of the
    warehouse, purchasing and the reference DB (factories; None — that source is skipped)."""

    purchasing: Callable | None = None
    warehouse: Callable | None = None
    references: Callable | None = None


def _factory(request: Request, name: str):
    factory = getattr(request.app.state, name, None)
    return factory if callable(factory) else None


def scan_sources(request: Request, warehouse: Callable | None) -> ScanSources:
    return ScanSources(
        purchasing=_factory(request, "purchasing_uow_factory"), warehouse=warehouse,
        references=_factory(request, "references_uow_factory"),
    )


def recognised_dict(found: RecognisedUpd) -> dict:
    return {
        "supplier": found.supplier, "inn": found.inn, "upd_number": found.upd_number,
        "upd_date": found.upd_date.isoformat() if found.upd_date else None,
        "invoice_number": found.invoice_number,
        "invoice_date": found.invoice_date.isoformat() if found.invoice_date else None,
    }


def scan_answer(sources: ScanSources, link: str, reading: ScanReading) -> dict:
    """What «Отсканировать УПД» answers for a read scan. Blocking (the registries) — off the event loop."""
    found, note = reading.found, reading.note
    lines: list[dict] = []
    document = upd_document(
        sources.purchasing, number=found.upd_number, day=found.upd_date, inn=found.inn, seller=found.supplier,
    ) if found.upd_number else None
    if document is not None:
        basis = parse_upd_text(document["basis"])
        found = RecognisedUpd(
            supplier=document["supplier"] or found.supplier,
            inn=document["inn"] or found.inn,
            upd_number=document["upd_number"] or found.upd_number,
            upd_date=document["upd_date"] or found.upd_date,
            invoice_number=found.invoice_number or basis.invoice_number,
            invoice_date=found.invoice_date or basis.invoice_date,
        )
        lines = document["lines"]
        note = "Найден УПД в документах закупки — шапка и строки взяты из него"
    if not lines and reading.lines:
        read = snap_articles(list(reading.lines), known_articles(sources.warehouse, sources.purchasing,
                                                                  sources.references))
        lines = [line_answer(line) for line in read]
        read_note = OCR_LINES.format(count=len(lines))
        note = f"Шапка не распознана — заполните вручную. {read_note}" if note == NO_TEXT else note or read_note
    if document is None and reading.warning:
        note = f"{note}. {reading.warning}" if note else reading.warning
    return {"scan_file": link, "recognised": recognised_dict(found), "lines": lines, "note": note}


def llm_config(uow_factory: Callable[[], UnitOfWork]) -> receipt_llm.LlmConfig:
    """The model API: the server env, else the administrator's saved settings, else the defaults."""
    return receipt_llm.llm_config(upd_llm_settings(uow_factory()))


def file_reading(uow_factory: Callable[[], UnitOfWork], file_id: UUID,
                 config: Callable[[], receipt_llm.LlmConfig]) -> ScanReading:
    """The kept reading of a stored scan, else read now (model / tesseract) and kept. Blocking: seconds to minutes."""
    kept = reading_from_json(scan_recognition(uow_factory(), file_id))
    if kept is not None:
        return kept
    found = scan_file(uow_factory(), file_id)
    content = found[1] if found else b""
    reading = read_scan(content, sniffed_type(content), config())
    keep_scan_recognition(uow_factory(), file_id, reading_json(reading))
    return reading


def readings_kept(uow_factory: Callable[[], UnitOfWork], files: list[UUID]) -> bool:
    return all(reading_from_json(scan_recognition(uow_factory(), file_id)) is not None for file_id in files)


# One recognition per job at a time in this process: the screen's poll restarts one only when none runs.
_guard = threading.Lock()
_running: set[UUID] = set()


def recognising(job_id: UUID) -> bool:
    with _guard:
        return job_id in _running


def recognise_job(uow_factory: Callable[[], UnitOfWork], sources: ScanSources, job: ScanJob,
                  pages: list[UUID] | None = None) -> None:
    """The background task: the done job's file read (or, for a merge, its sheets' kept readings combined), the
    answer stored once. Any failure stores an answer that asks to fill the delivery by hand — the poll never hangs."""
    if job.status != DONE or job.file_id is None or job.result:
        return
    with _guard:
        if job.id in _running:
            return
        _running.add(job.id)
    try:
        if stored_result(uow_factory(), job.id):
            return
        link = f"{RECEIPT_SCAN_PATH}{job.file_id}"
        settings: list[receipt_llm.LlmConfig] = []

        def config() -> receipt_llm.LlmConfig:
            if not settings:
                settings.append(llm_config(uow_factory))
            return settings[0]

        try:
            if pages:
                reading = merge_readings([file_reading(uow_factory, page, config) for page in pages])
                keep_scan_recognition(uow_factory(), job.file_id, reading_json(reading))
            else:
                reading = file_reading(uow_factory, job.file_id, config)
            answer = scan_answer(sources, link, reading)
        except Exception:  # noqa: BLE001 — the storekeeper fills the delivery by hand rather than waits forever
            log.exception("Распознавание скана УПД по заданию %s не удалось", job.id)
            answer = {"scan_file": link, "recognised": recognised_dict(RecognisedUpd()), "lines": [], "note": FAILED}
        save_result(uow_factory(), job.id, json.dumps(answer, ensure_ascii=False))
    finally:
        with _guard:
            _running.discard(job.id)


def job_view(job: ScanJob) -> dict:
    """The job as the screen polls it: `result` once stored; `recognising` — done, the answer is being made."""
    return {
        "id": str(job.id), "status": job.status, "error": job.error,
        "result": json.loads(job.result) if job.result else None,
        "recognising": job.status == DONE and not job.result,
        "scan_file": f"{RECEIPT_SCAN_PATH}{job.file_id}" if job.file_id else "",
    }
