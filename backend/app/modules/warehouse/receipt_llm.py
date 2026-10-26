"""A paper УПД read by a vision model over an OpenAI-compatible API (owner 26.10.2026: «давай сразу апи»).

Measured on two real scans: `gpt-5.6-luna` (reasoning «medium», temperature 0), ONE upright grayscale JPEG per page
(long side 2400 px, quality 85) — ~6k tokens, ~8 s, every line right; `gemini-3.8-flash` — ~100 s, as accurate. An
upside-down page made the model invent lines, so every page is turned upright first (`receipt_ocr.page_turn`).

* The client (config, call, JSON, page JPEG) lives in `app.shared.vision_llm` since 26.10 — a quote scan in
  purchasing reads through it too; its names are imported here unchanged.
* Config: env `ERP_UPD_LLM_BASE_URL`, `ERP_UPD_LLM_API_KEY`, `ERP_UPD_LLM_MODEL`, `ERP_UPD_LLM_FALLBACK_MODEL`,
  `ERP_UPD_LLM_EFFORT` win field by field over the setting the administrator saves on «Склад» → «Распознавание УПД»
  (`warehouse_upd_llm_settings`), then the defaults below. No key — no model reading (tesseract, as before).
* A call: POST `{base}/chat/completions`, stdlib urllib, 120 s. 5xx / 429 / timeout → one retry; then the fallback
  model once; a page neither model read goes to tesseract (`receipt_ocr.page_text`). A 400 naming an optional
  parameter (`response_format`, `temperature`, `reasoning_effort`, `max_tokens`) drops it and asks again — relays
  differ in what they take. The whole document has DOCUMENT_BUDGET seconds, so the screen's 4-minute wait holds.
* Pages go in parallel (≤ PARALLEL); the answers merge in page order (`scan_reading.merge_readings`).
* The key is never logged nor answered: errors are cleaned of it; the log has model, seconds and tokens per page."""

from __future__ import annotations

import base64
import logging
import re
import time
import urllib.request  # noqa: F401 — the client moved to app.shared.vision_llm; tests patch urlopen through here
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

from app.shared.article_maker import normalize_maker
from app.shared.vision_llm import (  # noqa: F401 — the УПД path's public names, kept where callers import them
    CALL_TIMEOUT, CHECK_TIMEOUT, DEFAULT_BASE_URL, DEFAULT_EFFORT, DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL, EFFORTS, ENV,
    ERROR_TEXT, JPEG_QUALITY, LONG_SIDE, OPTIONAL, LlmConfig, LlmError, check, complete, env_fields, key_hint,
    llm_config, message_text, page_jpeg, parse_answer,
)

from .item_types import ITEM_TYPES_CANON, type_key
from .receipt_scan import PRD_NUMBER_LIMIT, RecognisedUpd, scan_day
from .upd_lines import UpdLine, item_line, quantity_of, unit_of

DOCUMENT_BUDGET = 200.0  # seconds for all pages; what is left after it goes to tesseract
NAMES_BUDGET = 40.0  # an ЭДО file's names are read inside the upload request: under the proxy's minute
PARALLEL = 4
ITEM_FIELDS = (
    '"article": "артикул производителя, если он есть в наименовании или коде; иначе пусто", '
    '"maker": "производитель, если назван; иначе пусто", '
    '"item_type": "ровно одно из: {types}; если не уверен — пусто", '
    '"short_name": "наше наименование: тип и главные параметры по-русски, коротко, без артикула, производителя '
    'и кодов продавца, например «Контакт обжимной JST SXH, 0,08–0,2 мм²»"'
)


def item_fields(types: Sequence[str]) -> str:
    """The per-row fields of what the model makes of a name (owner 26.10.2026: «и тип, и артикул, и наименование
    наше»): `item_type` only from the stock's own dictionary."""
    listed = "; ".join(f"«{item}»" for item in types if str(item).strip()) or "—"
    return ITEM_FIELDS.replace("{types}", listed)


def page_prompt(types: Sequence[str] = ITEM_TYPES_CANON) -> str:
    return (
        "Это скан листа российского УПД (универсального передаточного документа) или его продолжения. "
        "Верни ТОЛЬКО JSON без пояснений: "
        '{"supplier": "продавец (строка «Продавец»), как напечатан", '
        '"inn": "ИНН продавца (10 или 12 цифр)", '
        '"upd_number": "номер счёта-фактуры/УПД из первой строки документа", '
        '"upd_date": "YYYY-MM-DD", '
        '"invoice_number": "№ счёта ТОЛЬКО из строки «Основание передачи (сдачи) / получения (приёмки)»; '
        'если там не счёт — пусто", '
        '"invoice_date": "YYYY-MM-DD или пусто", '
        '"prd_number": "№ ТОЛЬКО из строки «К платёжно-расчётному документу № … от …»; несколько — через «, »; '
        'пусто, если не заполнено", '
        '"prd_date": "YYYY-MM-DD первого документа из этой же строки или пусто", '
        '"lines":[{"n": "№ п/п", "supplier_code": "код товара (колонка А)", '
        '"name": "наименование полностью, как напечатано, с переносами склеенными", '
        '"unit": "условное обозначение единицы (колонка 2а)", '
        '"quantity": "количество (колонка 3), число с точкой", '
        + item_fields(types) + "}]} "
        "Все строки таблицы товаров, без пропусков и без выдумок; нечитаемое — пустая строка. "
        "Если на листе нет шапки или таблицы — соответствующие поля пустые. "
        "Цифры переписывай посимвольно."
    )


PROMPT = page_prompt()

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageAnswer:
    page: int  # 1-based
    data: dict | None  # the model's JSON; None — no model read the page
    model: str = ""
    error: str = ""


def _row_numbers(data: Mapping) -> list[int]:
    rows = data.get("lines") if isinstance(data.get("lines"), list) else []
    return sorted({int(n) for row in rows if isinstance(row, Mapping) and str(n := row.get("n", "")).strip().isdigit()})


def numbering_gap(data: Mapping) -> bool:
    """«1, 2, …, 5, 7» — a row of the table is missing: the model glued two rows (seen on the first real scans, 26.10)."""
    numbers = _row_numbers(data)
    return len(numbers) >= 2 and numbers[-1] - numbers[0] + 1 > len(numbers)


def read_page(config: LlmConfig, jpeg: bytes, page: int, deadline: float,
              clock: Callable[[], float] = time.monotonic, gap_retry: bool = True,
              types: Sequence[str] = ITEM_TYPES_CANON) -> PageAnswer:
    """One page through the main model (one retry on 5xx / timeout), then the fallback model. A reading whose row
    numbers have a gap is read once more; the reading with more rows wins. `types` — the stock's item types."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": page_prompt(types)},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}},
    ]}]
    options: dict[str, object] = {"temperature": 0, "response_format": {"type": "json_object"}}
    if config.effort:
        options["reasoning_effort"] = config.effort
    errors: list[str] = []
    for model, tries in zip(config.models(), (2, 1)):
        for _attempt in range(tries):
            remaining = deadline - clock()
            if remaining < 5:
                errors.append("время на распознавание вышло")
                return PageAnswer(page, None, error="; ".join(errors))
            started = clock()
            try:
                answer = complete(config, model, messages, options, min(CALL_TIMEOUT, remaining))
                data = parse_answer(message_text(answer))
            except LlmError as error:
                log.warning("УПД: модель %s, стр. %d — %s (%.1f с)", model, page, error, clock() - started)
                errors.append(f"{model}: {error}")
                if error.retry:
                    continue
                break
            usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
            log.info("УПД: модель %s, стр. %d — %.1f с, токенов %s (вход %s, выход %s)", model, page,
                     clock() - started, usage.get("total_tokens", "?"), usage.get("prompt_tokens", "?"),
                     usage.get("completion_tokens", "?"))
            if gap_retry and numbering_gap(data):
                log.info("УПД: модель %s, стр. %d — пропуск в номерах строк %s, читаю ещё раз", model, page,
                         _row_numbers(data))
                again = read_page(config, jpeg, page, deadline, clock, gap_retry=False, types=types)
                if again.data is not None and len(_row_numbers(again.data)) > len(_row_numbers(data)):
                    return again
            return PageAnswer(page, data, model=model)
    return PageAnswer(page, None, error="; ".join(errors))


def read_pages(config: LlmConfig, jpegs: list[bytes], clock: Callable[[], float] = time.monotonic,
               types: Sequence[str] = ITEM_TYPES_CANON) -> list[PageAnswer]:
    """Every page at once (≤ PARALLEL calls), answers in page order."""
    if not jpegs:
        return []
    deadline = clock() + DOCUMENT_BUDGET
    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(jpegs)), thread_name_prefix="upd-llm") as pool:
        return list(pool.map(lambda pair: read_page(config, pair[1], pair[0], deadline, clock, types=types),
                             enumerate(jpegs, start=1)))


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit] if not isinstance(value, (dict, list)) else ""


def _day(value) -> date | None:
    text = _text(value, 32)
    try:
        return date.fromisoformat(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else scan_day(text)
    except ValueError:
        return None


def _prd_number(value) -> str:
    """«№ 916, №917» → «916, 917»: the numbers alone, joined by «, »."""
    parts = [part.replace("№", "").strip() for part in re.split(r"[,;]", _text(value, 512))]
    return ", ".join(part for part in parts if part)[:PRD_NUMBER_LIMIT]


def page_header(data: Mapping) -> RecognisedUpd:
    inn = re.sub(r"\D", "", _text(data.get("inn"), 32))
    return RecognisedUpd(
        supplier=_text(data.get("supplier"), 255), inn=inn if len(inn) in (10, 12) else "",
        upd_number=_text(data.get("upd_number"), 64), upd_date=_day(data.get("upd_date")),
        invoice_number=_text(data.get("invoice_number"), 64), invoice_date=_day(data.get("invoice_date")),
        prd_number=_prd_number(data.get("prd_number")), prd_date=_day(data.get("prd_date")),
    )


def item_type_of(value, types: Sequence[str]) -> str:
    """The model's type when it is one of `types` (without case and extra spaces), spelled as the dictionary has it;
    anything else — «»: the storekeeper picks it."""
    wanted = type_key(_text(value, 255))
    return next((item for item in types if wanted and type_key(item) == wanted), "")


def row_fields(row: Mapping, types: Sequence[str]) -> dict:
    """What the model made of one row's name: article, our short name, item type, maker. A reading kept before these
    fields existed has none of them — all «»."""
    return {
        "article": _text(row.get("article"), 255), "short_name": _text(row.get("short_name"), 255),
        "item_type": item_type_of(row.get("item_type"), types), "manufacturer": normalize_maker(_text(row.get("maker"), 64)),
    }


def page_lines(data: Mapping, types: Sequence[str] = ITEM_TYPES_CANON) -> list[UpdLine]:
    """The model's rows as warehouse lines: the name as printed, the article (the model's, else read off the name),
    the pack said in words (`upd_lines.item_line`), our short name, type and maker, the supplier's code kept; a row
    without a name and a code is dropped."""
    rows = data.get("lines")
    lines = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name, code = _text(row.get("name"), 1000), _text(row.get("supplier_code"), 64)
        if not (name or code):
            continue
        lines.append(item_line(
            name, quantity_of(_text(row.get("quantity"), 32)), unit_of(_text(row.get("unit"), 32)),
            supplier_code=code, number=_text(row.get("n"), 16), **row_fields(row, types),
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
