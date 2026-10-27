"""What one stored УПД scan says, before the purchasing registry is asked (owner 26.10.2026).

`read_scan` picks the reader. With a model key — the ERP's document reader (`app.shared.doc_reader`, Витя 26.10.2026:
our parser and Артемий's «становятся одним целым»): an electronic PDF (a real text layer) in ONE text call, a scan page
by page in parallel; the pages merged and checked (`closing.closing_doc`) and the warehouse's part taken
(`receipt_llm.reading_header` / `reading_lines`); a page no model read — tesseract; the model not answering a text
layer — the text layer's rules, as before. Without a key — exactly as before: a PDF text layer that gives the header is
read by rules, anything else by tesseract.

The reading is kept with the file (`warehouse_receipt_files.recognition`) with the model's own answer per page
(`pages`), so a УПД scanned sheet by sheet is merged from the stored readings (`merge_readings`: the pages re-merged
and re-checked as one document), never read again, and the purchasing registry takes the money, the basis and the
payments from the same answer (`app.workflows.receipt_upd_registry`). A reading kept before 26.10.2026 has no pages and
still loads.

Pure apart from the programs and the HTTP calls of the readers it calls."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.shared.doc_reader import closing
from app.shared.doc_reader import reader as doc_reader
from app.shared.pdf_text import PdfLimitExceeded, pdf_text

from . import receipt_llm, receipt_ocr
from .item_types import ITEM_TYPES_CANON
from .receipt_scan import NO_TEXT, PARTLY, RECOGNISED_KINDS, RecognisedUpd, parse_upd_text, recognise_upd_text
from .upd_lines import UpdLine, parse_upd_lines

MODEL_FAILED = "Модель распознавания не ответила — скан прочитан запасным способом, сверьте строки"
PAGES_FAILED = "Модель не прочитала стр. {pages} — они прочитаны запасным способом, сверьте их строки"
TEXT_ENGINE = "текст PDF"
_HEADER = ("supplier", "inn", "upd_number", "upd_date", "invoice_number", "invoice_date", "prd_number", "prd_date")


@dataclass(frozen=True)
class ScanReading:
    found: RecognisedUpd
    lines: tuple[UpdLine, ...] = ()
    note: str = ""  # the header's note: «» found, PARTLY, NO_TEXT, OCR_MISSING, TOO_HEAVY
    warning: str = ""  # how the reading fell back, added to the note on the screen
    engine: str = ""  # «текст PDF», the model(s), «tesseract»
    # The document reader's answer per page (one entry for a text reading; None — a page no model read): what the
    # registry's money and links come from and what a merge of sheets re-checks. () — no model reading.
    pages: tuple[dict | None, ...] = ()

    @property
    def complete(self) -> bool:
        """Every page of the file was read by the model."""
        return bool(self.pages) and all(isinstance(page, dict) for page in self.pages)


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
    return None if found.empty else ScanReading(found, tuple(parse_upd_lines(text)), header_note(found), "", TEXT_ENGINE)


def model_view(pages: Sequence[dict | None], types: Sequence[str] = ITEM_TYPES_CANON, engine: str = "") -> ScanReading:
    """The warehouse's part of the reader's pages: the document merged and checked, its header and rows for the
    delivery screen; the pages kept. The rows are kept whatever kind the model gave the document (OTHER too): the
    storekeeper sees what was read."""
    doc = closing.closing_doc(pages, keep_lines=True)
    found = receipt_llm.reading_header(doc)
    return ScanReading(found, tuple(receipt_llm.reading_lines(doc, types)), header_note(found), "", engine, tuple(pages))


def _model_reading(content: bytes, kind: str, config: receipt_llm.LlmConfig,
                   types: Sequence[str] = ITEM_TYPES_CANON) -> ScanReading | None:
    """The file through the document reader: a text layer in one call, else the upright pages in parallel; a page no
    model read — tesseract. None — the model read nothing of a text layer, or there are no page pictures (no
    pdftoppm): the caller falls back as before."""
    text = doc_reader.text_layer(content) if kind == "application/pdf" else ""
    with tempfile.TemporaryDirectory(prefix="erp-upd-") as temporary:
        images: list[Path] = []

        def pictures() -> list[bytes]:
            images.extend(receipt_ocr.page_images(content, kind, Path(temporary)))
            return [receipt_llm.page_jpeg(image, receipt_ocr.page_turn(image)) for image in images]

        try:
            reading = doc_reader.read_closing(config, text=text, pictures=pictures, types=types,
                                              budget=receipt_llm.DOCUMENT_BUDGET)
        except receipt_ocr.OcrUnavailable:
            return None
        if not reading.pages or (reading.source == "text" and not reading.read):
            return None
        pages = _with_ocr_inns(reading.pages, images) if reading.source == "pages" else reading.pages
        reading = replace(reading, pages=pages)
        fallback = []
        for number in reading.failed:
            page_text = receipt_ocr.page_text(images[number - 1])
            fallback.append(ScanReading(parse_upd_text(page_text), tuple(parse_upd_lines(page_text)), engine="tesseract"))
    if not reading.read:
        return replace(merge_readings(fallback, types), warning=MODEL_FAILED)
    engine = reading.model + (f", {TEXT_ENGINE}" if reading.source == "text" else "")
    view = model_view(reading.pages, types, engine)
    if not fallback:
        return view
    # The model's pages first (merged as one document), then what tesseract read of the others.
    merged = merge_readings([replace(view, pages=()), *fallback], types)
    return replace(merged, warning=PAGES_FAILED.format(pages=", ".join(map(str, reading.failed))), pages=view.pages)


def _with_ocr_inns(pages: tuple[dict | None, ...], images: Sequence[Path]) -> tuple[dict | None, ...]:
    """A page whose seller ИНН fails its check digits (the model read «5256004951» off «5256084961», 27.10)
    gets the valid ИНН tesseract reads on the same picture (`closing.TEXT_INNS`) — what the misread one is put right
    by when it is a slip of one of them. Only those pages are read again: tesseract takes seconds a page."""
    return tuple(
        doc_reader.with_text_inns(page, receipt_ocr.page_text(images[number - 1]))
        if number <= len(images) and doc_reader.misread_inn(page, ("sellerInn",)) else page
        for number, page in enumerate(pages, start=1)
    )


def read_scan(content: bytes, kind: str | None, config: receipt_llm.LlmConfig | None,
              types: Sequence[str] = ITEM_TYPES_CANON) -> ScanReading:
    """The reading of one file (PDF / JPG / PNG): the document reader with a key, else text layer → tesseract."""
    if kind not in RECOGNISED_KINDS:
        return ScanReading(RecognisedUpd(), note=NO_TEXT)
    warning = ""
    if config is not None and config.ready:
        reading = _model_reading(content, kind, config, types)
        if reading is not None:
            return reading
        warning = MODEL_FAILED
    if kind == "application/pdf" and (reading := _text_layer(content)) is not None:
        return replace(reading, warning=warning)
    found, note, text = recognise_upd_text(content, kind)
    return ScanReading(found, tuple(parse_upd_lines(text)), note, warning, "tesseract")


def _line_key(line: UpdLine) -> tuple[str, str, str]:
    return line.number, line.supplier_code, line.name.casefold()


def merge_readings(readings: list[ScanReading], types: Sequence[str] = ITEM_TYPES_CANON) -> ScanReading:
    """Pages (sheets) in their order. Every sheet read by the model — their pages merged and checked as one document
    (`closing.merge_pages`: each header field from the first page that has it, the totals from the last, the rows one
    after another, a repeated row once). Otherwise as before: each header field from the first reading that has it,
    the lines one after another, a row repeated (the same № п/п, code and name — a sheet scanned twice) once; the
    model's pages kept when every sheet has them."""
    if not readings:
        return ScanReading(RecognisedUpd(), note=NO_TEXT)
    unique = lambda values: " · ".join(dict.fromkeys(value for value in values if value))  # noqa: E731
    warning, engine = unique(reading.warning for reading in readings), unique(reading.engine for reading in readings)
    if all(reading.complete for reading in readings):
        pages = tuple(page for reading in readings for page in reading.pages)
        return replace(model_view(pages, types, engine), warning=warning)
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
    note = header_note(found)
    if note == NO_TEXT and all(reading.note and reading.note != NO_TEXT for reading in readings):
        note = readings[0].note  # «Распознавание недоступно…» says more than «не распознан»
    pages = tuple(page for reading in readings for page in reading.pages) if all(r.pages for r in readings) else ()
    return ScanReading(found, tuple(lines), note, warning, engine, pages)


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
        "pages": list(reading.pages),
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
    """A kept reading; None — nothing kept or not readable (the file is read again). A reading kept before the
    document reader (26.10.2026) has no «pages»: it loads with none."""
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
        pages = data.get("pages") if isinstance(data.get("pages"), list) else []
        return ScanReading(header, lines, str(data.get("note") or ""), str(data.get("warning") or ""),
                           str(data.get("engine") or ""), tuple(page if isinstance(page, dict) else None
                                                                for page in pages))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
