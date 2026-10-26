"""A paper УПД read by a vision model over an OpenAI-compatible API (owner 26.10.2026: «давай сразу апи») — since
26.10.2026 through the ERP's one document reader (`app.shared.doc_reader`, Витя: «Наш парсер идёт не рядом с парсером
Артемия, а они становятся одним целым»): Roller_Buyer's УПД prompt, kinds, money and checks on Артемий's transport.

Measured by Артемий on two real scans (his own prompt): `gpt-5.6-luna` (reasoning «medium», temperature 0), ONE upright
grayscale JPEG per page (long side 2400 px, quality 85) — ~6k tokens, ~8 s, every line right; `gemini-3.8-flash` —
~100 s, as accurate. An upside-down page made the model invent lines, so every page is turned upright first.

What lives where now:
* the call, its config (env `ERP_UPD_LLM_*` over the saved «Распознавание УПД», then the defaults), retries, the
  fallback model and the key kept out of logs — `doc_reader.transport`, re-exported here (`receipt_llm_api` and the
  tests use these names);
* page pictures and their upright turn — `doc_reader.pages` (`page_jpeg` re-exported);
* the page-by-page reading (≤ PARALLEL at once, the numbering-gap reread) — `doc_reader.reader`; `read_page` /
  `read_pages` below ask it with the CLOSING prompt and the stock's item types;
* what the warehouse takes of a reading — `reading_header` (supplier, ИНН, № / date, the invoice of «Основание», the
  ПРД) and `reading_lines` (name, quantity, unit, the codes, maker, type, our short name, packs) — here;
* an ЭДО file's names (`name_fields`) — here, unchanged: the file gives the rows exactly, the model only says what they
  are. The whole scan has DOCUMENT_BUDGET seconds, so the screen's 4-minute wait holds."""

from __future__ import annotations

import logging
import re
import time
import urllib.request  # noqa: F401 — the calls go through urllib.request.urlopen (tests patch it from here)
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from decimal import Decimal

from app.shared.article_maker import normalize_maker
from app.shared.doc_reader import closing
from app.shared.doc_reader import reader as doc_reader
from app.shared.doc_reader.pages import JPEG_QUALITY, LONG_SIDE, page_jpeg  # noqa: F401 — re-exported
from app.shared.doc_reader.reader import PARALLEL, PageAnswer, numbering_gap  # noqa: F401 — re-exported
from app.shared.doc_reader.transport import (  # noqa: F401 — re-exported: the warehouse's settings screen uses them
    CALL_TIMEOUT,
    CHECK_TIMEOUT,
    DEFAULT_BASE_URL,
    DEFAULT_EFFORT,
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    EFFORTS,
    ENV,
    ERROR_TEXT,
    OPTIONAL,
    LlmConfig,
    LlmError,
    check,
    complete,
    env_fields,
    key_hint,
    llm_config,
    message_text,
    parse_answer,
)

from .item_types import ITEM_TYPES_CANON, type_key
from .receipt_scan import PRD_NUMBER_LIMIT, RecognisedUpd, parse_upd_text, prd_refs, scan_day
from .upd_lines import UpdLine, item_line, quantity_of, unit_of

DOCUMENT_BUDGET = 200.0  # seconds for all pages; what is left after it goes to tesseract
NAMES_BUDGET = 40.0  # an ЭДО file's names are read inside the upload request: under the proxy's minute

ITEM_FIELDS = (
    '"article": "артикул производителя, если он есть в наименовании или коде; иначе пусто", '
    '"maker": "производитель, если назван; иначе пусто", '
    '"item_type": "ровно одно из: {types}; если не уверен — пусто", '
    '"short_name": "наше наименование: тип и главные параметры по-русски, коротко, без артикула, производителя '
    'и кодов продавца, например «Контакт обжимной JST SXH, 0,08–0,2 мм²»"'
)

log = logging.getLogger(__name__)


def item_fields(types: Sequence[str]) -> str:
    """The per-row fields of what the model makes of a name (owner 26.10.2026: «и тип, и артикул, и наименование
    наше»): `item_type` only from the stock's own dictionary. (The ЭДО names call; a scan asks the same in the reader's
    words, `closing.prompt(types=…)`.)"""
    listed = "; ".join(f"«{item}»" for item in types if str(item).strip()) or "—"
    return ITEM_FIELDS.replace("{types}", listed)


def page_prompt(types: Sequence[str] = ITEM_TYPES_CANON) -> str:
    """The task of one scanned page: the CLOSING prompt (all kinds, money, basis, payments) with the warehouse fields."""
    return closing.prompt("", types=types, page=True)


PROMPT = page_prompt()


def read_page(config: LlmConfig, jpeg: bytes, page: int, deadline: float,
              clock: Callable[[], float] = time.monotonic, gap_retry: bool = True,
              types: Sequence[str] = ITEM_TYPES_CANON) -> PageAnswer:
    """One scanned page through the document reader (main model with one retry, then the fallback; a gap in the row
    numbers is read once more). `types` — the stock's item types."""
    return doc_reader.read_page(config, jpeg, page, deadline, clock, gap_retry, prompt=page_prompt(types))


def read_pages(config: LlmConfig, jpegs: list[bytes], clock: Callable[[], float] = time.monotonic,
               types: Sequence[str] = ITEM_TYPES_CANON) -> list[PageAnswer]:
    """Every page at once (≤ PARALLEL calls), answers in page order."""
    return doc_reader.read_pages(config, jpegs, prompt=page_prompt(types), clock=clock, budget=DOCUMENT_BUDGET)


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit] if not isinstance(value, (dict, list)) else ""


def payment_refs(text: str | None) -> tuple[str, date | None]:
    """The ПРД of the reading's paymentDoc («№ 916 от 19.10.2026, № 917 от 20.10.2026») → («916, 917», 19.10.2026);
    a model that left out «№» — the parts before «от»."""
    number, day = prd_refs(text)
    if number or not str(text or "").strip():
        return number, day
    parts = [re.split(r"\s+от\s+", part.strip(), maxsplit=1) for part in re.split(r"[,;]", str(text))]
    numbers = [bits[0].replace("№", "").strip() for bits in parts if bits and re.search(r"\d", bits[0])]
    first = next((found for bits in parts if len(bits) == 2 and (found := scan_day(bits[1]))), None)
    return ", ".join(dict.fromkeys(numbers))[:PRD_NUMBER_LIMIT], first


def reading_header(doc: closing.ParsedDoc) -> RecognisedUpd:
    """The delivery's header from a reading: the seller and its ИНН, № and date; the invoice only from the basis
    («Основание передачи…»: a «Счёт № … от …», not a contract), the ПРД only from paymentDoc («К платёжно-расчётному
    документу») — Артемий's rules, as his own prompt asked them."""
    inn = re.sub(r"\D", "", doc.seller_inn)
    basis = parse_upd_text(doc.basis)
    prd_number, prd_date = payment_refs(doc.payment_doc)
    return RecognisedUpd(
        supplier=_text(doc.seller_name, 255), inn=inn if len(inn) in (10, 12) else "",
        upd_number=_text(doc.number, 64), upd_date=doc.doc_date,
        invoice_number=basis.invoice_number, invoice_date=basis.invoice_date, prd_number=prd_number, prd_date=prd_date,
    )


def item_type_of(value, types: Sequence[str]) -> str:
    """The model's type when it is one of `types` (without case and extra spaces), spelled as the dictionary has it;
    anything else — «»: the storekeeper picks it."""
    wanted = type_key(_text(value, 255))
    return next((item for item in types if wanted and type_key(item) == wanted), "")


def row_fields(row: Mapping, types: Sequence[str]) -> dict:
    """What the model made of one ЭДО row's name: article, our short name, item type, maker."""
    return {
        "article": _text(row.get("article"), 255), "short_name": _text(row.get("short_name"), 255),
        "item_type": item_type_of(row.get("item_type"), types), "manufacturer": normalize_maker(_text(row.get("maker"), 64)),
    }


def _quantity(value: float | None) -> Decimal | None:
    return quantity_of(format(Decimal(repr(value)), "f")) if value is not None else None


def reading_lines(doc: closing.ParsedDoc, types: Sequence[str] = ITEM_TYPES_CANON) -> list[UpdLine]:
    """The reading's rows as warehouse lines: the name as printed, the seller's code (column «А»), the maker's article
    (the model's, else read off the name), the pack said in words (`upd_lines.item_line`), our short name, the type
    (only one of the stock dictionary) and the maker; a row without a name and a code is dropped."""
    lines = []
    for row in doc.lines:
        name, code = _text(row.name, 1000), _text(row.article, 64)
        if not (name or code):
            continue
        lines.append(item_line(
            name, _quantity(row.qty), unit_of(row.unit or row.unit_code), supplier_code=code, number=str(row.seq),
            article=_text(row.maker_article, 255), short_name=_text(row.short_name, 255),
            item_type=item_type_of(row.item_type, types), manufacturer=normalize_maker(_text(row.maker, 64)),
        ))
    return lines


def names_prompt(names: Sequence[str], types: Sequence[str] = ITEM_TYPES_CANON) -> str:
    listed = "\n".join(f"{index}. {name}" for index, name in enumerate(names, start=1))
    return (
        "Это наименования товаров из электронного УПД российского поставщика. Для каждой строки верни ТОЛЬКО JSON "
        'без пояснений: {"lines": [{"n": номер строки, ' + item_fields(types) + "}]} "
        "Все строки, в их порядке; не выдумывай: чего нет в наименовании — пусто.\n" + listed
    )


def name_fields(config: LlmConfig, names: Sequence[str], types: Sequence[str] = ITEM_TYPES_CANON,
                clock: Callable[[], float] = time.monotonic) -> list[dict] | None:
    """An ЭДО УПД gives the names exactly but not what they are: ONE text call over all of them → per name {article,
    short_name, item_type, manufacturer} (`row_fields`), in order. It runs inside the upload request, so the whole call
    has NAMES_BUDGET seconds (the main model, then the fallback with what is left). No key, no answer, a broken answer
    — None: the lines come as the file gives them."""
    if not names or not config.ready:
        return None
    messages = [{"role": "user", "content": names_prompt(names, types)}]
    options: dict[str, object] = {"temperature": 0, "response_format": {"type": "json_object"},
                                  "reasoning_effort": "low"}
    deadline = clock() + NAMES_BUDGET
    for model in config.models():
        remaining = deadline - clock()
        if remaining < 5:
            break
        started = clock()
        try:
            data = parse_answer(message_text(complete(config, model, messages, options, remaining)))
        except LlmError as error:
            log.warning("УПД ЭДО: модель %s, наименования — %s (%.1f с)", model, error, clock() - started)
            continue
        log.info("УПД ЭДО: модель %s, наименований %d — %.1f с", model, len(names), clock() - started)
        rows = data.get("lines") if isinstance(data.get("lines"), list) else []
        by_number = {str(row.get("n", "")).strip(): row for row in rows if isinstance(row, dict)}
        return [row_fields(by_number.get(str(index), {}), types) for index in range(1, len(names) + 1)]
    return None
