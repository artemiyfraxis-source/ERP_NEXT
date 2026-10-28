"""The vision model for a supplier's quote scan (purchasing «Внести КП», 26.10.2026) — a thin layer over the ERP's one
model client, `app.shared.doc_reader` (27.10.2026: one config, one HTTP call, one retry / fallback policy for the
warehouse's УПД scans, the УПД registry and the КП scans).

* Config, the call, the JSON answer, «Проверить» — `doc_reader.transport` (env `ERP_UPD_LLM_*` over the saved
  «Склад» → «Распознавание УПД», then the defaults; the key never logged nor answered), re-exported here.
* The page JPEG (upright, grayscale, long side 2400 px, quality 85) — `doc_reader.pages.page_jpeg`, re-exported.
* Here only what a КП scan adds: all its pages in ONE message (`image_messages`) and `ask_json` — the transport's
  `ask` (main model with one retry on 5xx / 429 / timeout, then the fallback once, within a budget) that raises
  LlmError with the last failure's status, as the buyer's message needs it (`quote_scan.unavailable_text`).
  The КП prompt stays in purchasing."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Sequence

from .doc_reader.pages import JPEG_QUALITY, LONG_SIDE, page_jpeg  # noqa: F401 — re-exported
from .doc_reader.transport import (  # noqa: F401 — re-exported: purchasing and its tests use these names
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
    Answer,
    LlmConfig,
    LlmError,
    ask,
    check,
    complete,
    env_fields,
    json_options,
    key_hint,
    llm_config,
    message_text,
    parse_answer,
)


class PagesUnavailable(RuntimeError):
    """The pages cannot be made into pictures on this server (no pdftoppm)."""


def image_messages(prompt: str, jpegs: Sequence[bytes]) -> list:
    """One user message: the prompt, then every page picture in order."""
    return [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        *({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}
          for jpeg in jpegs),
    ]}]


def ask_json(config: LlmConfig, messages: list, budget: float, label: str,
             clock: Callable[[], float] = time.monotonic) -> Answer:
    """The model's JSON for `messages` within `budget` seconds (`transport.ask`). LlmError — no model answered: its
    text names every failed call (no key in it), its status / retry are the last failure's. `label` names the
    document in the log."""
    if not config.ready:
        raise LlmError("модель не настроена")
    answer = ask(config, messages, json_options(config), deadline=clock() + budget, label=label, clock=clock)
    if answer.data is None:
        raise LlmError(answer.error, status=answer.status, retry=answer.retry)
    return answer
