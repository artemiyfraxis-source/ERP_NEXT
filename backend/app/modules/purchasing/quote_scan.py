"""«КП со скана»: a supplier's quote that came as a scan or a photo is read by the vision model (26.10.2026).

Roller_Buyer sent every quote PDF to Gemini through Google Apps Script (`updQuoteSchema_` / `updQuotePrompt_`); ERP
read only the text layer and refused scans. Now a file goes: the text layer first (`quote_import.parse_quote`, no
model, no cost); a scan, a photo (JPG / PNG) or a text layer with no row read goes as page pictures to the model the
warehouse reads paper УПД with (`app.shared.vision_llm`, settings «Склад» → «Распознавание УПД»). The model gets the
request's lines numbered and answers, per row, which of them it is (`requested_id`, Roller's `requestedId`); a number
outside the list or given twice is ignored. The rows it left unmatched go the usual way: remembered wording → part
number → name (`quote_import.match_lines`). Packs are priced per unit as for text (`apply_packs`).

The model unavailable (no key, no pages, quota, no answer) → a Russian reason; a text-layer PDF that reads by text never
asks the model, so that path keeps working without it. The call runs inside the request: QUOTE_BUDGET seconds keep it
under the proxy's minute."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from app.shared import vision_llm
from app.shared.file_types import sniffed_type
from app.shared.identity import Actor

from . import quote_import as qi
from . import rfq as rfq_service
from .upd_sellers import OWN_INNS

Refused = rfq_service.Refused
QUOTE_PAGES = 6  # a quote is a page or two; more pages cost tokens and time, not rows
QUOTE_BUDGET = 50.0  # seconds for the model inside the import request (the proxy cuts at a minute)
SCAN_KINDS = ("application/pdf", "image/jpeg", "image/png")
MAX_ROWS = 500

NOT_SET = "модель распознавания не настроена (администратор: «Склад» → «Распознавание УПД»)"
NO_PAGES = "на сервере нет программы, которая делает из PDF картинки страниц (pdftoppm)"
NO_PICTURE = "файл не открылся как картинка"
QUOTA = "квота модели распознавания исчерпана"
KEY = "сервис модели не принял ключ — администратор проверит его в «Склад» → «Распознавание УПД»"
NO_ANSWER = "модель распознавания не ответила — повторите через минуту"
BROKEN = "модель распознавания ответила ошибкой — повторите через минуту"
BY_HAND = "вставьте текст КП, таблицу из Excel или введите строки вручную"


class ScanPages(Protocol):
    """What purchasing needs of the warehouse's УПД reading (`app.workflows.purchasing_quote_scan`)."""

    def config(self) -> vision_llm.LlmConfig: ...

    def page_jpegs(self, content: bytes, kind: str, limit: int) -> list[bytes]: ...


class ModelUnavailable(Exception):
    """The model could not read the file; the text says why, for the buyer."""


def unavailable_text(error: vision_llm.LlmError) -> str:
    text = str(error).lower()
    if error.status == 429 or re.search(r"quota|insufficient|billing|баланс|квот", text):
        return QUOTA
    if error.status in (401, 403):
        return KEY
    if error.status is None and error.retry:
        return NO_ANSWER
    return BROKEN


def quote_prompt(targets: Sequence[qi.Target], pages: int) -> str:
    """Roller's `updQuotePrompt_` with the request's lines numbered 1…N: the model answers by number."""
    asked = "\n".join(
        f"• {index}: {target.name}{f' [{target.article}]' if target.article else ''} — "
        f"{qi._size_text(target.quantity)} {target.unit}"
        for index, target in enumerate(targets, start=1))
    own = ", ".join(sorted(OWN_INNS))
    return (
        f"Это коммерческое предложение (КП) или счёт поставщика в ответ на наш запрос — скан или фото, страниц: {pages}. "
        "Верни ТОЛЬКО JSON без пояснений: "
        '{"supplier_name": "поставщик (продавец, исполнитель) — кто прислал КП, как напечатан", '
        '"supplier_inn": "ИНН поставщика, только цифры", "number": "номер КП или счёта", '
        '"date": "дата КП, ГГГГ-ММ-ДД", "valid_until": "срок действия КП, ГГГГ-ММ-ДД или пусто", '
        '"prices_include_vat": true | false | null, "vat_rate": число процентов или null, '
        '"lines": [{"requested_id": номер позиции нашего запроса или null, "name": "наименование как в документе", '
        '"article": "артикул как в документе или пусто", "qty": число или null, "unit": "единица", '
        '"price": цена за ЕДИНИЦУ числом или null, "lead_days": целое или null, "in_stock": true | false, '
        '"declined": true | false, "analog": true | false, "analog_note": "что предложено вместо или пусто"}]}\n'
        f"• Покупатель — мы (ИНН {own}): его название и ИНН не бери в supplier_name и supplier_inn.\n"
        "• Срок действия «N дней» — посчитай дату valid_until от даты КП.\n"
        "• prices_include_vat: true, если цены за единицу указаны С НДС (колонка «Цена с НДС», «в т.ч. НДС» в итогах "
        "и т.п.), false — если без НДС, null — если непонятно. vat_rate — ставка НДС в процентах, если указана.\n"
        "• lines — КАЖДАЯ позиция предложения: name и article как в документе, qty и unit, price — цена за ЕДИНИЦУ "
        "числом (не сумма строки).\n"
        "• lead_days — срок поставки в календарных днях: недели × 7, месяцы × 30, рабочие дни × 1.4 (округлить вверх). "
        "«В наличии/на складе» → in_stock = true, lead_days = 0.\n"
        "• declined = true, если поставщик пишет, что позицию поставить не может (нет, снято с производства, отказ) — "
        "тогда price = null.\n"
        "• analog = true, если предложена замена вместо запрошенного; в analog_note — что именно предложено.\n"
        "• requested_id — номер запрошенной позиции из списка ниже, к которой относится строка (в том числе аналог). "
        "Если строка ни к одной не относится (доставка, упаковка, лишняя позиция) — null. Одну запрошенную позицию не "
        "присваивай двум строкам.\n"
        "Строки «Итого», «НДС», «Всего» — не позиции. Цифры переписывай посимвольно; нечитаемое — пусто, не выдумывай.\n\n"
        "Наш запрос:\n" + (asked or "—")
    )


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if value is not None and not isinstance(value, (dict, list)) else ""


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    found = rfq_service.parse_ru_number(_text(value, 64))
    return found if found is not None and found >= 0 else None


def _flag(value: Any) -> bool:
    return value is True or _text(value, 8).lower() in {"true", "да", "yes", "1"}


def _day(value: Any) -> date | None:
    text = _text(value, 32)
    try:
        return date.fromisoformat(text) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    except ValueError:
        return None


def _lead(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number < 3650 else None


@dataclass
class ScanQuote:
    header: dict  # number, valid_until, inn, document_name, date — the same keys as `quote_import.read_header`
    vat_included: bool | None  # None — the document does not say
    lines: list[qi.ImportedLine]  # `rfq_line_id` set where the model named a request line (matched_by «model»)
    model: str = ""


def quote_from_answer(data: Mapping, targets: Sequence[qi.Target]) -> ScanQuote:
    """The model's JSON → header and lines. A requested_id outside 1…N, or one already taken, is dropped."""
    inn = re.sub(r"\D", "", _text(data.get("supplier_inn"), 32))
    inn = inn if len(inn) in (10, 12) and inn not in OWN_INNS else ""
    name = _text(data.get("supplier_name"), 255)
    if re.search(r"фраксис", name, re.I):
        name = ""
    quote_date, valid_until = _day(data.get("date")), _day(data.get("valid_until"))
    include = data.get("prices_include_vat")
    vat_rate = _number(data.get("vat_rate"))
    vat_rate = vat_rate if vat_rate is not None and vat_rate < 100 else None
    header = {"number": _text(data.get("number"), 64).lstrip("№ "), "valid_until": valid_until.isoformat() if valid_until else None,
              "inn": inn, "document_name": name, "date": quote_date.isoformat() if quote_date else None}
    lines: list[qi.ImportedLine] = []
    taken: set[UUID] = set()
    rows = data.get("lines") if isinstance(data.get("lines"), list) else []
    for row in rows[:MAX_ROWS]:
        if not isinstance(row, Mapping):
            continue
        source_name, article = _text(row.get("name"), 512), _text(row.get("article"), 255)
        if not (source_name or article):
            continue
        declined, in_stock = _flag(row.get("declined")), _flag(row.get("in_stock"))
        lead = _lead(row.get("lead_days"))
        line = qi.ImportedLine(
            source_name=source_name or article, source_article=article, qty=_number(row.get("qty")),
            unit=_text(row.get("unit"), 32), price=None if declined else _number(row.get("price")), vat_rate=vat_rate,
            lead_days=0 if lead is None and in_stock else lead, in_stock=in_stock, declined=declined,
            analog=_flag(row.get("analog")), analog_note=_text(row.get("analog_note"), 512),
        )
        wanted = row.get("requested_id")
        index = int(wanted) if isinstance(wanted, int) and not isinstance(wanted, bool) else (
            int(wanted) if isinstance(wanted, str) and wanted.strip().isdigit() else 0)
        if 1 <= index <= len(targets) and targets[index - 1].rfq_line_id not in taken:
            line.rfq_line_id, line.matched_by = targets[index - 1].rfq_line_id, "model"
            taken.add(line.rfq_line_id)
        lines.append(line)
    return ScanQuote(header, include if isinstance(include, bool) else None, lines)


def read_quote(scanner: ScanPages, content: bytes, kind: str, targets: Sequence[qi.Target]) -> ScanQuote:
    """The file's pages through the model. ModelUnavailable — why it could not be read."""
    config = scanner.config()
    if not config.ready:
        raise ModelUnavailable(NOT_SET)
    try:
        jpegs = scanner.page_jpegs(content, kind, QUOTE_PAGES)
    except vision_llm.PagesUnavailable:
        raise ModelUnavailable(NO_PAGES) from None
    except OSError:  # Pillow: not a picture after all
        raise ModelUnavailable(NO_PICTURE) from None
    if not jpegs:
        raise ModelUnavailable(NO_PICTURE)
    try:
        answer = vision_llm.ask_json(config, vision_llm.image_messages(quote_prompt(targets, len(jpegs)), jpegs),
                                     QUOTE_BUDGET, "КП")
    except vision_llm.LlmError as error:
        raise ModelUnavailable(unavailable_text(error)) from None
    found = quote_from_answer(answer.data, targets)
    found.model = answer.model
    return found


def match_scan(lines: list[qi.ImportedLine], targets: Sequence[qi.Target], aliases: dict[str, UUID]) -> list[qi.ImportedLine]:
    """Roller's order: the model's own match first, then remembered wording → part number → name for the rest."""
    taken = {line.rfq_line_id for line in lines if line.rfq_line_id}
    rest = [line for line in lines if not line.rfq_line_id]
    qi.match_lines(rest, [target for target in targets if target.rfq_line_id not in taken], aliases)
    return qi.apply_packs(lines, targets)


def _refusal(text_refusal: str | None, reason: str, scan: bool) -> Refused:
    if scan:
        return Refused(f"В файле нет текстового слоя (скан или фото), а {reason} — {BY_HAND}")
    return Refused(f"{text_refusal} Модель не помогла: {reason}.")


def import_file(uow: Any, actor: Actor, rfq_id: UUID, content: bytes, supplier_id: UUID | None, today: date,
                scanner: ScanPages | None) -> dict:
    """A quote file (PDF / JPG / PNG) → proposed lines for the form, `reader` «text» or «model»; nothing saved."""
    kind = sniffed_type(content)
    if kind not in SCAN_KINDS:
        raise Refused("Это не PDF и не фото КП (JPG, PNG)")
    if len(content) > qi.PDF_LIMIT:
        raise Refused("Файл больше 20 МБ — сожмите его или вставьте текст")
    text_refusal: str | None = None
    if kind == "application/pdf":
        text = qi.text_layer(content)
        if text.strip():
            try:
                return {**qi.parse_quote(uow, actor, rfq_id, text, supplier_id, "file", today), "reader": "text", "model": ""}
            except Refused as error:  # a text layer with no row in it: the model may read the pages
                text_refusal = str(error)
    with uow:
        session = uow.session
        _, targets, aliases = qi._context(session, actor, rfq_id, supplier_id)
        default_vat = Decimal(rfq_service.purchasing_settings.load(session)["default_vat_rate"])
    scan = text_refusal is None
    if scanner is None:
        raise _refusal(text_refusal, NOT_SET, scan)
    try:
        found = read_quote(scanner, content, kind, targets)
    except ModelUnavailable as error:
        raise _refusal(text_refusal, str(error), scan) from None
    if not found.lines:
        raise Refused(f"Модель не нашла в {'скане' if scan else 'файле'} строк КП — {BY_HAND}")
    lines = match_scan(found.lines, targets, aliases)
    with uow:
        header = qi._header_supplier(uow.session, found.header, rfq_id, supplier_id)
    return {
        "header": header, "source": "file", "reader": "model", "model": found.model,
        "vat_included": found.vat_included if found.vat_included is not None else True,
        "default_vat_rate": rfq_service.num(default_vat),
        "lines": [qi._json(line) for line in lines if line.rfq_line_id],
        "unmatched": [qi._json(line) for line in lines if not line.rfq_line_id],
        "unread": [],
    }
