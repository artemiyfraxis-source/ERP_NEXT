"""Invoice file: № and date recognised from its text, the stored file's name.

Port of `Invoices.gs` `parseInvoiceText_` / `normalizeInvoiceText_` / `wordDate_` / `numDate_` /
`invoiceExt_` and the file name built in `uploadInvoice`. SUPPLYMAP gets the text from Drive OCR; ERP
reads the PDF text layer (`app.shared.pdf_text`) — a scan or a picture gives no text, and the buyer types
№ and date by hand, as SUPPLYMAP asks when OCR fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.shared.pdf_text import pdf_text

# SUPPLYMAP sets no limit (Drive takes anything); ERP keeps the file in the database — 20 MB, as УПД.
INVOICE_FILE_LIMIT = 20 * 1024 * 1024
INVOICE_FILE_PATH = "/api/procurement/files/"

_SPACES = re.compile("[    ]")
# «Счет [на оплату] № / No / N° / N <номер>»; {0,40} — чтобы не утянуть далёкую метку.
_NUMBER = re.compile(r"Сч[ёе]т[^\n]{0,40}?(?:№|No|N[°º]|N(?=\s*[0-9]))\s*([0-9A-Za-zА-Яа-я/-]+)", re.I)
_WORD_DATE_AFTER_OT = re.compile(r"(?:^|\s)от\s+[0-9]{1,2}\s+[А-Яа-яЁё]+\s+[0-9]{4}", re.I)
_NUM_DATE_AFTER_OT = re.compile(r"(?:^|\s)от\s+([0-9]{1,2})[./]([0-9]{1,2})[./]([0-9]{2,4})", re.I)
_NUM_DATE = re.compile(r"([0-9]{1,2})[./]([0-9]{1,2})[./]([0-9]{2,4})")
_WORD_DATE = re.compile(r"([0-9]{1,2})\s+([А-Яа-яЁё]+)\s+([0-9]{4})")
_MONTHS = (
    ("январ", "01"), ("феврал", "02"), ("март", "03"), ("апрел", "04"), ("ма[йя]", "05"), ("июн", "06"),
    ("июл", "07"), ("август", "08"), ("сентябр", "09"), ("октябр", "10"), ("ноябр", "11"), ("декабр", "12"),
)
_EXTENSION = re.compile(r"(\.[A-Za-z0-9]{1,5})$")


@dataclass(frozen=True)
class InvoiceInfo:
    number: str = ""
    date: str = ""  # «dd.mm.yyyy», as SUPPLYMAP parses it


def normalize_invoice_text(text: str) -> str:
    """`normalizeInvoiceText_`: non-breaking and narrow spaces become plain spaces."""
    return _SPACES.sub(" ", str(text or ""))


def _num_date(match: re.Match | None) -> str:
    """`numDate_`: [_, dd, mm, yy(yy)] → «dd.mm.yyyy», a two-digit year is 20yy."""
    if not match:
        return ""
    year = match.group(3)
    if len(year) == 2:
        year = "20" + year
    return f"{match.group(1).zfill(2)}.{match.group(2).zfill(2)}.{year}"


def _word_date(text: str) -> str:
    """`wordDate_`: the first «число слово год» whose word is a month, «11 июня 2026» → «11.06.2026»."""
    for match in _WORD_DATE.finditer(str(text)):
        word = match.group(2).lower()
        for prefix, month in _MONTHS:
            if re.match(prefix, word):
                return f"{match.group(1).zfill(2)}.{month}.{match.group(3)}"
    return ""


def parse_invoice_text(text: str) -> InvoiceInfo:
    """`parseInvoiceText_`: only № and date — totals are typed by hand in SUPPLYMAP too."""
    normalized = normalize_invoice_text(text)
    number = ""
    found = _NUMBER.search(normalized)
    if found:
        number = re.sub(r"[.,;]+$", "", found.group(1))
    after_ot = _WORD_DATE_AFTER_OT.search(normalized)
    day = (
        _word_date(after_ot.group(0) if after_ot else "")
        or _num_date(_NUM_DATE_AFTER_OT.search(normalized))
        or _word_date(normalized)
        or _num_date(_NUM_DATE.search(normalized))
    )
    return InvoiceInfo(number=number, date=day)


def invoice_date(value: str) -> date | None:
    """«dd.mm.yyyy» → date (`dmyToDate_`); an impossible day is no date."""
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", value or "")
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def invoice_extension(name: str) -> str:
    """`invoiceExt_`: the extension with its dot, or ''."""
    found = _EXTENSION.search(str(name or ""))
    return found.group(1) if found else ""


def invoice_file_name(info: InvoiceInfo, supplier: str, designation: str, order_number: str, original: str) -> str:
    """«Счёт № <№> от <дата> <поставщик> <назначение> <№ заказа>.ext», empty parts dropped."""
    parts = ["Счёт"]
    if info.number:
        parts.append("№ " + info.number)
    if info.date:
        parts.append("от " + info.date)
    for part in (supplier, designation, order_number):
        if str(part or "").strip():
            parts.append(str(part).strip())
    return " ".join(parts) + invoice_extension(original)


def kp_file_name(info: InvoiceInfo, supplier: str, order_number: str, original: str) -> str:
    """«КП № <№> от <дата> <поставщик> <№ заказа>.ext» — the offer kept next to the order's invoices."""
    parts = ["КП"]
    if info.number:
        parts.append("№ " + info.number)
    if info.date:
        parts.append("от " + info.date)
    for part in (supplier, order_number):
        if str(part or "").strip():
            parts.append(str(part).strip())
    return " ".join(parts) + invoice_extension(original)


def recognise_invoice(content: bytes) -> tuple[InvoiceInfo, str]:
    """Text of the file and what it says; the hint is the one SUPPLYMAP shows when nothing was found."""
    text = pdf_text(content)
    info = parse_invoice_text(text)
    if not text.strip():
        return info, "Текст из файла не читается (скан или картинка) — № и дату счёта впишите вручную."
    if not info.number and not info.date:
        return info, "Не распознаны № и дата — впишите вручную."
    return info, ""
