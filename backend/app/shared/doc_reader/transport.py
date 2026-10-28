"""The model API of the document reader: one OpenAI-compatible chat completion (moved here from the warehouse's
`receipt_llm`, 26.10.2026 — Артемий's transport, as he measured and hardened it; `receipt_llm` re-exports it).

* Config: env `ERP_UPD_LLM_BASE_URL`, `ERP_UPD_LLM_API_KEY`, `ERP_UPD_LLM_MODEL`, `ERP_UPD_LLM_FALLBACK_MODEL`,
  `ERP_UPD_LLM_EFFORT` win field by field over the setting the administrator saves on «Склад» → «Распознавание УПД»
  (the warehouse keeps that table and its screen; other modules get it through an `app/workflows` port), then the
  defaults below. No key — no model reading.
* A call: POST `{base}/chat/completions`, stdlib urllib. 5xx / 429 / timeout → one retry; then the fallback model once
  (`ask`). A 400 naming an optional parameter (`response_format`, `temperature`, `reasoning_effort`, `max_tokens`)
  drops it and asks again — relays differ in what they take.
* The key is never logged nor answered: errors are cleaned of it; the log has model, seconds and tokens per call."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

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
MIN_LEFT = 5.0  # a call with less time left than this is not started
ERROR_TEXT = 300
OPTIONAL = ("response_format", "temperature", "reasoning_effort", "max_tokens")

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


def json_options(config: LlmConfig) -> dict[str, object]:
    """The parameters Артемий measured on the relay (26.10.2026): temperature 0, a JSON object, the saved effort."""
    options: dict[str, object] = {"temperature": 0, "response_format": {"type": "json_object"}}
    if config.effort:
        options["reasoning_effort"] = config.effort
    return options


@dataclass(frozen=True)
class Answer:
    data: dict | None  # the model's JSON; None — no model answered
    model: str = ""
    error: str = ""  # every failed call, «model: why», joined by «; »
    status: int | None = None  # the last failed call's HTTP status (None — no HTTP answer)
    retry: bool = False  # the last failure was a 5xx / 429 / timeout (or the time ran out)


def ask(config: LlmConfig, messages: list, options: Mapping[str, object], *, deadline: float, label: str,
        clock: Callable[[], float] = time.monotonic, call_timeout: float = CALL_TIMEOUT) -> Answer:
    """The main model (one retry on 5xx / 429 / timeout), then the fallback model once — each call within what is left
    of `deadline`. `label` names the call in the log («стр. 2», «текст PDF»)."""
    errors: list[str] = []
    last: LlmError | None = None
    for model, tries in zip(config.models(), (2, 1)):
        for _attempt in range(tries):
            remaining = deadline - clock()
            if remaining < MIN_LEFT:
                errors.append("время на распознавание вышло")
                return Answer(None, error="; ".join(errors), status=last.status if last else None, retry=True)
            started = clock()
            try:
                answer = complete(config, model, messages, options, min(call_timeout, remaining))
                data = parse_answer(message_text(answer))
            except LlmError as error:
                log.warning("Документ: модель %s, %s — %s (%.1f с)", model, label, error, clock() - started)
                errors.append(f"{model}: {error}")
                last = error
                if error.retry:
                    continue
                break
            usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
            log.info("Документ: модель %s, %s — %.1f с, токенов %s (вход %s, выход %s)", model, label,
                     clock() - started, usage.get("total_tokens", "?"), usage.get("prompt_tokens", "?"),
                     usage.get("completion_tokens", "?"))
            return Answer(data, model)
    return Answer(None, error="; ".join(errors) or "модель не ответила", status=last.status if last else None,
                  retry=last.retry if last else False)


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
