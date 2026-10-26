"""A paper УПД read by a vision model over an OpenAI-compatible API (owner 26.10.2026: «давай сразу апи»).

Measured on two real scans: `gpt-5.6-luna` (reasoning «medium», temperature 0), ONE upright grayscale JPEG per page
(long side 2400 px, quality 85) — ~6k tokens, ~8 s, every line right; `gemini-3.8-flash` — ~100 s, as accurate. An
upside-down page made the model invent lines, so every page is turned upright first (`receipt_ocr.page_turn`).

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
import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path

from .receipt_scan import RecognisedUpd, scan_day
from .upd_lines import UpdLine, item_line, quantity_of, unit_of

DEFAULT_BASE_URL = "https://api.relaymodels.com/v1"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_FALLBACK_MODEL = "gemini-3.8-flash"
DEFAULT_EFFORT = "medium"
EFFORTS = ("minimal", "low", "medium", "high")
ENV = {
    "base_url": "ERP_UPD_LLM_BASE_URL", "api_key": "ERP_UPD_LLM_API_KEY", "model": "ERP_UPD_LLM_MODEL",
    "fallback_model": "ERP_UPD_LLM_FALLBACK_MODEL", "effort": "ERP_UPD_LLM_EFFORT",
}
CALL_TIMEOUT = 120.0
CHECK_TIMEOUT = 25.0  # «Проверить» runs inside a request: two models stay under the proxy's minute
DOCUMENT_BUDGET = 200.0  # seconds for all pages; what is left after it goes to tesseract
PARALLEL = 4
LONG_SIDE = 2400
JPEG_QUALITY = 85
ERROR_TEXT = 300
OPTIONAL = ("response_format", "temperature", "reasoning_effort", "max_tokens")

PROMPT = (
    "Это скан листа российского УПД (универсального передаточного документа) или его продолжения. "
    "Верни ТОЛЬКО JSON без пояснений: "
    '{"supplier": "продавец (строка «Продавец»), как напечатан", '
    '"inn": "ИНН продавца (10 или 12 цифр)", '
    '"upd_number": "номер счёта-фактуры/УПД из первой строки документа", '
    '"upd_date": "YYYY-MM-DD", '
    '"invoice_number": "№ счёта ТОЛЬКО из строки «Основание передачи (сдачи) / получения (приёмки)»; '
    'если там не счёт — пусто", '
    '"invoice_date": "YYYY-MM-DD или пусто", '
    '"lines": [{"n": "№ п/п", "supplier_code": "код товара (колонка А)", '
    '"name": "наименование полностью, как напечатано, с переносами склеенными", '
    '"unit": "условное обозначение единицы (колонка 2а)", '
    '"quantity": "количество (колонка 3), число с точкой"}]} '
    "Все строки таблицы товаров, без пропусков и без выдумок; нечитаемое — пустая строка. "
    "Если на листе нет шапки или таблицы — соответствующие поля пустые. "
    "Цифры переписывай посимвольно."
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LlmConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    fallback_model: str = DEFAULT_FALLBACK_MODEL
    effort: str = DEFAULT_EFFORT

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def models(self) -> list[str]:
        """The main model, then the fallback one (when set and another)."""
        return [self.model] + ([self.fallback_model] if self.fallback_model and self.fallback_model != self.model
                               else [])


def env_fields(environ: Mapping[str, str] | None = None) -> list[str]:
    """The settings the server's env holds (they win over the saved ones)."""
    environ = os.environ if environ is None else environ
    return [name for name, variable in ENV.items() if str(environ.get(variable, "")).strip()]


def llm_config(stored: Mapping[str, str] | None, environ: Mapping[str, str] | None = None) -> LlmConfig:
    """Field by field: the env, else the saved setting, else the default (the key has no default)."""
    environ = os.environ if environ is None else environ
    stored = stored or {}
    defaults = LlmConfig()
    values = {}
    for name, variable in ENV.items():
        value = str(environ.get(variable, "")).strip() or str(stored.get(name) or "").strip()
        values[name] = value or getattr(defaults, name)
    return LlmConfig(**values)


def key_hint(key: str) -> str:
    """«sk-ib…AgnD» — enough to tell which key is set, never the key; a short key shows nothing of itself."""
    key = str(key or "")
    if not key:
        return ""
    return f"{key[:5]}…{key[-4:]}" if len(key) >= 16 else "…"


class LlmError(RuntimeError):
    def __init__(self, text: str, *, status: int | None = None, retry: bool = False):
        super().__init__(text)
        self.status = status
        self.retry = retry


def _clean(text: str, key: str) -> str:
    text = " ".join(str(text or "").split())
    if key:
        text = text.replace(key, "***")
    return re.sub(r"(?i)bearer\s+\S+", "Bearer ***", text)[:ERROR_TEXT]


def _post(config: LlmConfig, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        config.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — the administrator's URL
            body = response.read()
    except urllib.error.HTTPError as error:
        try:
            detail = error.read()[:2000].decode("utf-8", "replace")
        except OSError:
            detail = ""
        status = error.code
        raise LlmError(_clean(f"HTTP {status}: {detail or error.reason}", config.api_key), status=status,
                       retry=status >= 500 or status == 429) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
        reason = getattr(error, "reason", None) or error
        raise LlmError(_clean(f"нет ответа: {reason}", config.api_key), retry=True) from None
    try:
        answer = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LlmError("ответ сервиса не JSON", retry=True) from None
    if not isinstance(answer, dict):
        raise LlmError("ответ сервиса не JSON", retry=True)
    return answer


def complete(config: LlmConfig, model: str, messages: list, options: Mapping[str, object], timeout: float) -> dict:
    """One chat completion; a 400 that names an optional parameter drops it and asks again."""
    options = dict(options)
    while True:
        try:
            return _post(config, {"model": model, "messages": messages, **options}, timeout)
        except LlmError as error:
            named = next((name for name in options if name in str(error)), None) if error.status == 400 else None
            if named is None:
                raise
            options.pop(named)


def message_text(answer: dict) -> str:
    try:
        content = answer["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LlmError("в ответе нет текста") from None
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def parse_answer(text: str) -> dict:
    """The model's JSON: bare, in ```json fences, or the first {…} of a chatty answer. LlmError otherwise."""
    text = _FENCE.sub("", str(text or "").strip())
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise LlmError("модель ответила не JSON")


def page_jpeg(image: Path, turn: int) -> bytes:
    """The page as the model reads it best: upright, grayscale, long side ≤ LONG_SIDE, JPEG quality 85."""
    from PIL import Image

    with Image.open(image) as picture:
        page = picture.convert("L")
        if turn:
            page = page.rotate(turn, expand=True, fillcolor=255)
        page.thumbnail((LONG_SIDE, LONG_SIDE))
        buffer = BytesIO()
        page.save(buffer, "JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


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
              clock: Callable[[], float] = time.monotonic, gap_retry: bool = True) -> PageAnswer:
    """One page through the main model (one retry on 5xx / timeout), then the fallback model. A reading whose row
    numbers have a gap is read once more; the reading with more rows wins."""
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
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
                again = read_page(config, jpeg, page, deadline, clock, gap_retry=False)
                if again.data is not None and len(_row_numbers(again.data)) > len(_row_numbers(data)):
                    return again
            return PageAnswer(page, data, model=model)
    return PageAnswer(page, None, error="; ".join(errors))


def read_pages(config: LlmConfig, jpegs: list[bytes], clock: Callable[[], float] = time.monotonic) -> list[PageAnswer]:
    """Every page at once (≤ PARALLEL calls), answers in page order."""
    if not jpegs:
        return []
    deadline = clock() + DOCUMENT_BUDGET
    with ThreadPoolExecutor(max_workers=min(PARALLEL, len(jpegs)), thread_name_prefix="upd-llm") as pool:
        return list(pool.map(lambda pair: read_page(config, pair[1], pair[0], deadline, clock),
                             enumerate(jpegs, start=1)))


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit] if not isinstance(value, (dict, list)) else ""


def _day(value) -> date | None:
    text = _text(value, 32)
    try:
        return date.fromisoformat(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else scan_day(text)
    except ValueError:
        return None


def page_header(data: Mapping) -> RecognisedUpd:
    inn = re.sub(r"\D", "", _text(data.get("inn"), 32))
    return RecognisedUpd(
        supplier=_text(data.get("supplier"), 255), inn=inn if len(inn) in (10, 12) else "",
        upd_number=_text(data.get("upd_number"), 64), upd_date=_day(data.get("upd_date")),
        invoice_number=_text(data.get("invoice_number"), 64), invoice_date=_day(data.get("invoice_date")),
    )


def page_lines(data: Mapping) -> list[UpdLine]:
    """The model's rows as warehouse lines: the name as printed, its article and the pack said in words
    (`upd_lines.item_line`), the supplier's code kept; a row without a name and a code is dropped."""
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
            supplier_code=code, number=_text(row.get("n"), 16),
        ))
    return lines


def check(config: LlmConfig) -> list[dict]:
    """«Проверить»: a one-token call to each model → [{model, ok, seconds, error}]."""
    results = []
    for model in config.models():
        started = time.monotonic()
        try:
            complete(config, model, [{"role": "user", "content": "Ответь одним словом: OK"}], {"max_tokens": 1},
                     CHECK_TIMEOUT)
            results.append({"model": model, "ok": True, "seconds": round(time.monotonic() - started, 1), "error": ""})
        except LlmError as error:
            results.append({"model": model, "ok": False, "seconds": round(time.monotonic() - started, 1),
                            "error": str(error)})
    return results
