"""What one stored УПД scan says, before the purchasing registry is asked (owner 26.10.2026).

`read_scan` picks the reader: a PDF text layer that gives the header (an electronic УПД) — read as text; a scan — by
the model (`receipt_llm`) page by page, a page no model read by tesseract; no model key on this server — tesseract
for the whole file, as before. The reading is kept with the file (`warehouse_receipt_files.recognition`), so a УПД
scanned sheet by sheet is merged from the stored page readings (`merge_readings`), never read again.

Pure apart from the programs and the HTTP calls of the readers it calls."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.shared.pdf_text import PdfLimitExceeded, pdf_text

from . import receipt_llm, receipt_ocr
from .item_types import ITEM_TYPES_CANON
from .receipt_scan import NO_TEXT, PARTLY, RECOGNISED_KINDS, RecognisedUpd, parse_upd_text, recognise_upd_text
from .upd_lines import UpdLine, parse_upd_lines

MODEL_FAILED = "Модель распознавания не ответила — скан прочитан запасным способом, сверьте строки"
PAGES_FAILED = "Модель не прочитала стр. {pages} — они прочитаны запасным способом, сверьте их строки"
_HEADER = ("supplier", "inn", "upd_number", "upd_date", "invoice_number", "invoice_date", "prd_number", "prd_date")


@dataclass(frozen=True)
class ScanReading:
    found: RecognisedUpd
    lines: tuple[UpdLine, ...] = ()
    note: str = ""  # the header's note: «» found, PARTLY, NO_TEXT, OCR_MISSING, TOO_HEAVY
    warning: str = ""  # how the reading fell back, added to the note on the screen
    engine: str = ""  # «текст PDF», the model(s), «tesseract»


def header_note(found: RecognisedUpd) -> str:
    if found.empty:
        return NO_TEXT
    return "" if found.supplier and found.upd_number and found.upd_date else PARTLY


def _text_layer(content: bytes) -> ScanReading | None:
    try:
        text = pdf_text(content)
    except PdfLimitExceeded:
        return None
    found = parse_upd_text(text)
    return None if found.empty else ScanReading(found, tuple(parse_upd_lines(text)), header_note(found), "", "текст PDF")


def _model_reading(content: bytes, kind: str, config: receipt_llm.LlmConfig,
                   types: Sequence[str] = ITEM_TYPES_CANON) -> ScanReading | None:
    """The pages through the model; a page it did not read — tesseract. None — no page pictures (no pdftoppm)."""
    with tempfile.TemporaryDirectory(prefix="erp-upd-") as temporary:
        try:
            images = receipt_ocr.page_images(content, kind, Path(temporary))
        except receipt_ocr.OcrUnavailable:
            return None
        if not images:
            return None
        jpegs = [receipt_llm.page_jpeg(image, receipt_ocr.page_turn(image)) for image in images]
        answers = receipt_llm.read_pages(config, jpegs, types=types)
        pages, failed = [], []
        for image, answer in zip(images, answers):
            if answer.data is not None:
                found = receipt_llm.page_header(answer.data)
                pages.append(ScanReading(found, tuple(receipt_llm.page_lines(answer.data, types)), engine=answer.model))
                continue
            failed.append(str(answer.page))
            text = receipt_ocr.page_text(image)
            pages.append(ScanReading(parse_upd_text(text), tuple(parse_upd_lines(text)), engine="tesseract"))
    merged = merge_readings(pages)
    if not failed:
        return merged
    warning = MODEL_FAILED if len(failed) == len(pages) else PAGES_FAILED.format(pages=", ".join(failed))
    return replace(merged, warning=warning)


def read_scan(content: bytes, kind: str | None, config: receipt_llm.LlmConfig | None,
              types: Sequence[str] = ITEM_TYPES_CANON) -> ScanReading:
    """The reading of one file (PDF / JPG / PNG): text layer → model → tesseract."""
    if kind not in RECOGNISED_KINDS:
        return ScanReading(RecognisedUpd(), note=NO_TEXT)
    if kind == "application/pdf" and (reading := _text_layer(content)) is not None:
        return reading
    warning = ""
    if config is not None and config.ready:
        reading = _model_reading(content, kind, config, types)
        if reading is not None:
            return reading
        warning = MODEL_FAILED
    found, note, text = recognise_upd_text(content, kind)
    return ScanReading(found, tuple(parse_upd_lines(text)), note, warning, "tesseract")


def _line_key(line: UpdLine) -> tuple[str, str, str]:
    return line.number, line.supplier_code, line.name.casefold()


def merge_readings(readings: list[ScanReading]) -> ScanReading:
    """Pages in their order: each header field from the first page that has it, the lines one after another, a row
    repeated (the same № п/п, code and name — a sheet scanned twice) once."""
    if not readings:
        return ScanReading(RecognisedUpd(), note=NO_TEXT)
    empty = RecognisedUpd()
    found = RecognisedUpd(**{
        name: next((value for reading in readings if (value := getattr(reading.found, name))), getattr(empty, name))
        for name in _HEADER
    })
    lines, seen = [], set()
    for reading in readings:
        for line in reading.lines:
            if (key := _line_key(line)) not in seen:
                seen.add(key)
                lines.append(line)
    unique = lambda values: " · ".join(dict.fromkeys(value for value in values if value))  # noqa: E731
    note = header_note(found)
    if note == NO_TEXT and all(reading.note and reading.note != NO_TEXT for reading in readings):
        note = readings[0].note  # «Распознавание недоступно…» says more than «не распознан»
    return ScanReading(found, tuple(lines), note, unique(reading.warning for reading in readings),
                       unique(reading.engine for reading in readings))


# --- kept with the file ------------------------------------------------------------------------------------------

def _plain(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def reading_json(reading: ScanReading) -> str:
    return json.dumps({
        "found": {name: _plain(getattr(reading.found, name)) for name in _HEADER},
        "lines": [{key: _plain(value) for key, value in asdict(line).items()} for line in reading.lines],
        "note": reading.note, "warning": reading.warning, "engine": reading.engine,
    }, ensure_ascii=False)


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def reading_from_json(text: str | None) -> ScanReading | None:
    """A kept reading; None — nothing kept or not readable (the file is read again)."""
    if not text:
        return None
    try:
        data = json.loads(text)
        found = data["found"]
        header = RecognisedUpd(**{
            name: _date(found.get(name)) if name.endswith("_date") else str(found.get(name) or "") for name in _HEADER
        })
        lines = tuple(UpdLine(**{
            **line,
            **{name: _decimal(line.get(name)) for name in ("quantity", "invoice_quantity", "packs")},
        }) for line in data["lines"])
        return ScanReading(header, lines, str(data.get("note") or ""), str(data.get("warning") or ""),
                           str(data.get("engine") or ""))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
