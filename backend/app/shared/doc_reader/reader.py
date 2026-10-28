"""How the document reader reads a file (Витя, 26.10.2026: one reader for the whole ERP — the warehouse's scans and the
УПД registry's PDFs read the same way, with the same prompt and checks, `closing`).

* An electronic PDF (a text layer with ≥ MIN_TEXT_LETTERS Cyrillic letters) — ONE text-only call: the characters are
  exact and it is cheap.
* A scan or a photo — the pages in parallel (≤ PARALLEL calls), each an upright grayscale JPEG (`pages`) read with the
  full field list; a reading whose row numbers have a gap is read once more and the fuller one wins (Артемий's
  `read_page`, 26.10: the model once glued two rows). The answers come back in page order; the caller merges and
  checks them (`closing.closing_doc`).
* A seller's or buyer's ИНН failing its check digits gets the valid ИНН of the document's own text beside it
  (`with_text_inns`: the text layer here, tesseract's page in the warehouse) — `closing` puts the misread one right
  by them (27.10.2026: the model read «5256004951» off «5256084961»).
* A page neither model read stays None: the warehouse reads it by tesseract, the registry names it in the remarks.

The whole document has `budget` seconds (the warehouse screen waits four minutes; the registry reads in the
background). Pure apart from the HTTP calls (`transport`) and the programs of the pictures the caller hands over."""

from __future__ import annotations

import base64
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.shared.inn import inn_valid, inns_in_text
from app.shared.pdf_text import PdfLimitExceeded, pdf_text

from . import closing
from .transport import CALL_TIMEOUT, LlmConfig, ask, json_options

MIN_TEXT_LETTERS = 200  # fewer Cyrillic letters — a scan without a real text layer
PARALLEL = 4
DOCUMENT_BUDGET = 200.0  # seconds for a whole document by default (the warehouse screen's wait)

log = logging.getLogger(__name__)


def text_layer(content: bytes) -> str:
    """The PDF's own text when it is a real one (an electronic document, not a scan); '' otherwise."""
    try:
        text = pdf_text(content)
    except PdfLimitExceeded:
        return ""
    except Exception:  # noqa: BLE001 — a broken text layer only means «read the pictures»
        return ""
    return text if len(re.findall("[А-Яа-яЁё]", text or "")) >= MIN_TEXT_LETTERS else ""


@dataclass(frozen=True)
class PageAnswer:
    page: int  # 1-based
    data: dict | None  # the model's JSON; None — no model read the page
    model: str = ""
    error: str = ""


def _row_numbers(data: Mapping) -> list[int]:
    rows = data.get("lines") if isinstance(data.get("lines"), list) else []
    numbers = set()
    for row in rows:
        if isinstance(row, Mapping):
            value = str(row.get("seq", row.get("n", "")) or "").strip()
            if value.endswith(".0"):
                value = value[:-2]
            if value.isdigit():
                numbers.add(int(value))
    return sorted(numbers)


def numbering_gap(data: Mapping) -> bool:
    """«1, 2, …, 5, 7» — a row of the table is missing: the model glued two rows (seen on the first real scans, 26.10)."""
    numbers = _row_numbers(data)
    return len(numbers) >= 2 and numbers[-1] - numbers[0] + 1 > len(numbers)


def read_page(config: LlmConfig, jpeg: bytes, page: int, deadline: float, clock: Callable[[], float] = time.monotonic,
              gap_retry: bool = True, *, prompt: str, call_timeout: float = CALL_TIMEOUT) -> PageAnswer:
    """One page through the main model (one retry on 5xx / timeout), then the fallback model. A reading whose row
    numbers have a gap is read once more; the reading with more rows wins."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}},
    ]}]
    answer = ask(config, messages, json_options(config), deadline=deadline, label=f"стр. {page}", clock=clock,
                 call_timeout=call_timeout)
    if answer.data is None:
        return PageAnswer(page, None, error=answer.error)
    if gap_retry and numbering_gap(answer.data):
        log.info("Документ: модель %s, стр. %d — пропуск в номерах строк %s, читаю ещё раз", answer.model, page,
                 _row_numbers(answer.data))
        again = read_page(config, jpeg, page, deadline, clock, gap_retry=False, prompt=prompt, call_timeout=call_timeout)
        if again.data is not None and len(_row_numbers(again.data)) > len(_row_numbers(answer.data)):
            return again
    return PageAnswer(page, answer.data, model=answer.model)


def read_pages(config: LlmConfig, jpegs: Sequence[bytes], *, prompt: str, clock: Callable[[], float] = time.monotonic,
               budget: float = DOCUMENT_BUDGET, call_timeout: float = CALL_TIMEOUT) -> list[PageAnswer]:
    """Every page at once (≤ PARALLEL calls), answers in page order."""
    if not jpegs:
        return []
    deadline = clock() + budget
    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(jpegs)), thread_name_prefix="doc-llm") as pool:
        return list(pool.map(
            lambda pair: read_page(config, pair[1], pair[0], deadline, clock, prompt=prompt, call_timeout=call_timeout),
            enumerate(jpegs, start=1)))


def read_text(config: LlmConfig, prompt: str, *, clock: Callable[[], float] = time.monotonic,
              budget: float = DOCUMENT_BUDGET, call_timeout: float = CALL_TIMEOUT) -> PageAnswer:
    """An electronic document: the prompt carries its text layer, no pictures — one call."""
    answer = ask(config, [{"role": "user", "content": prompt}], json_options(config), deadline=clock() + budget,
                 label="текст PDF", clock=clock, call_timeout=call_timeout)
    return PageAnswer(1, answer.data, model=answer.model, error=answer.error)


def misread_inn(data: Mapping | None, keys: Sequence[str] = ("sellerInn", "buyerInn")) -> bool:
    """A page's answer whose seller (or buyer) ИНН fails its check digits."""
    if not isinstance(data, Mapping):
        return False
    read = (re.sub(r"\D", "", str(data.get(key) or "")) for key in keys)
    return any(inn and not inn_valid(inn) for inn in read)


def with_text_inns(data: dict | None, text: str) -> dict | None:
    """A page's answer whose seller or buyer ИНН fails its check digits, with the valid ИНН of the page's own text
    (`closing.TEXT_INNS`: a text layer, tesseract's reading): what the misread one is put right by. Otherwise — or
    nothing valid in the text — the answer as it is."""
    if not misread_inn(data):
        return data
    found = inns_in_text(text)
    return {**data, closing.TEXT_INNS: found} if found else data


@dataclass(frozen=True)
class ClosingReading:
    """What the reader made of one file: the model's JSON per page (one entry for a text reading), None — a page no
    model read. Nothing to read (no text, no pictures) — no pages."""

    pages: tuple[dict | None, ...] = ()
    source: str = ""  # «text» | «pages» | «» (nothing to read)
    models: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def read(self) -> bool:
        return any(page is not None for page in self.pages)

    @property
    def failed(self) -> list[int]:
        return [number for number, page in enumerate(self.pages, start=1) if page is None]

    @property
    def model(self) -> str:
        return " · ".join(dict.fromkeys(model for model in self.models if model))

    def doc(self, tag: str = "", file_name: str = "", header: Mapping | None = None) -> closing.ParsedDoc:
        return closing.closing_doc(self.pages, tag, file_name, header)


def read_closing(config: LlmConfig, *, text: str, pictures: Callable[[], list[bytes]], tag: str = "",
                 types: Sequence[str] | None = None, clock: Callable[[], float] = time.monotonic,
                 budget: float = DOCUMENT_BUDGET, call_timeout: float = CALL_TIMEOUT) -> ClosingReading:
    """One closing document: `text` — its text layer (`text_layer`; '' — a scan), `pictures` — its pages as JPEGs,
    asked for only when there is no text. `types` — the stock's item types (the warehouse fields of a row)."""
    if text:
        answer = read_text(config, closing.prompt(tag, text, types=types), clock=clock, budget=budget,
                           call_timeout=call_timeout)
        return ClosingReading((with_text_inns(answer.data, text),), "text", (answer.model,),
                              (answer.error,) if answer.error else ())
    jpegs = pictures()
    if not jpegs:
        return ClosingReading()
    answers = read_pages(config, jpegs, prompt=closing.prompt(tag, types=types, page=True), clock=clock,
                         budget=budget, call_timeout=call_timeout)
    return ClosingReading(
        tuple(answer.data for answer in answers), "pages", tuple(answer.model for answer in answers if answer.model),
        tuple(f"стр. {answer.page}: {answer.error}" for answer in answers if answer.error),
    )
