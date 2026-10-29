"""The name a primary document is shown and downloaded under: «УПД № 1788 от 20.10.2026.pdf», «Счёт № 3789518 от
26.10.2026.pdf» — built at read time from what was recognised or entered (kind, number, date), so old files get it too
and nothing is migrated. The one helper every file route and every document card uses (owner, 29.10.2026: «почему ты не
можешь сохранять с нормальным названием сканы»).

No number — «УПД от 20.10.2026.pdf»; neither number nor date, or a kind the rules do not know — the original name stays.
The real extension is kept. The result is safe as a file name on Windows and Linux.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# Kind as the reader / registry stores it (lower-cased) → how the document is called in a name.
_TITLES = {
    "упд": "УПД", "упд до 2019 года": "УПД", "упд отправки": "УПД",
    "счёт-фактура": "Счёт-фактура", "счет-фактура": "Счёт-фактура", "сф": "Счёт-фактура",
    "корректировочный сф": "Корректировочный СФ",
    "тн + сф": "ТН + СФ", "тн+сф": "ТН + СФ",
    "торг-12": "ТОРГ-12", "товарная накладная": "Товарная накладная", "накладная": "Накладная",
    "м-15": "М-15", "акт": "Акт",
    "счёт": "Счёт", "счет": "Счёт", "invoice": "Счёт",
    "кп": "КП", "коммерческое предложение": "КП",
}
_UNSAFE = re.compile(r'[\x00-\x1f\\/:*?"<>|]+')
_SPACES = re.compile(r"\s+")
_NUMBER_SIGN = re.compile(r"^(?:№|N°)\s*")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_MAX_NUMBER = 80


def _number(value: str | None) -> str:
    text = _NUMBER_SIGN.sub("", str(value or "").strip())
    text = text.replace("/", "-").replace("\\", "-")
    text = _SPACES.sub(" ", _UNSAFE.sub(" ", text)).strip(" .")
    return text[:_MAX_NUMBER].strip(" .")


def _day(value: date | datetime | str | None) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return ""
    return f"{value:%d.%m.%Y}" if isinstance(value, date) else ""


def document_title(kind: str | None, number: str | None, doc_date: date | datetime | str | None) -> str:
    """«УПД № 1788 от 20.10.2026», «УПД от 20.10.2026», «УПД № 1788»; empty when the kind is unknown or neither number
    nor date is known."""
    title = _TITLES.get(_SPACES.sub(" ", str(kind or "")).strip().lower())
    num, day = _number(number), _day(doc_date)
    if title is not None:  # «КП-17» is already «КП № 17»: the kind is not said twice
        num = re.sub(rf"^{re.escape(title)}[\s\-№.]*(?=\S)", "", num, flags=re.IGNORECASE)
    if title is None or not (num or day):
        return ""
    return title + (f" № {num}" if num else "") + (f" от {day}" if day else "")


def document_file_name(kind: str | None, number: str | None, doc_date: date | datetime | str | None,
                       original: str | None) -> str:
    """The document's display and download name; `original` (the uploaded file's name) when nothing was recognised."""
    original = str(original or "").strip()
    title = document_title(kind, number, doc_date)
    if not title:
        return original
    match = _EXTENSION.search(original)
    return title + (match.group(0).lower() if match else "")
