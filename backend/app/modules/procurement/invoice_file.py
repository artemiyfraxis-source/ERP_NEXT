"""Invoice file: № and date recognised from its text, the stored file's name.

Port of `Invoices.gs` `parseInvoiceText_` / `normalizeInvoiceText_` / `wordDate_` / `numDate_` /
`invoiceExt_` and the file name built in `uploadInvoice`. SUPPLYMAP gets the text from Drive OCR; ERP
reads the PDF text layer (`app.shared.pdf_text`) — a scan or a picture gives no text, and the buyer types
№ and date by hand, as SUPPLYMAP asks when OCR fails.

Since 26.10.2026 (owner Витя: «нужен полноценный разборщик всех данных, как у нас в roller_buyer») the same file is also
read whole by Roller_Buyer's invoice parser (`app.shared.invoice_reading`): seller, INN, total, VAT and positions.
№ and date: SUPPLYMAP's reader, Roller_Buyer's title when it finds both («Счет-оферта 696419Z от 31.10.2025»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.shared.invoice_header import parse_invoice_title
from app.shared.invoice_reading import InvoiceReading, read_invoice
from app.shared.pdf_text import PdfLimitExceeded, pdf_text

# SUPPLYMAP sets no limit (Drive takes anything); ERP keeps the file in the database — 20 MB, as УПД.
INVOICE_FILE_LIMIT = 20 * 1024 * 1024
INVOICE_FILE_PATH = "/api/procurement/files/"
# The PDF would unpack past the reading budget (audit F05): the file is kept, № and date are typed by hand.
TOO_HEAVY_HINT = "Файл слишком тяжёлый для распознавания (превышен лимит обработки) — № и дату счёта впишите вручную."

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


@dataclass(frozen=True)
class InvoiceRecognition:
    info: InvoiceInfo
    hint: str
    reading: InvoiceReading = field(default_factory=InvoiceReading)  # empty for a scan or a file too heavy to read


def _dmy(iso: str | None) -> str:
    """«2026-10-20» → «20.10.2026»; '' for none."""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{match.group(3)}.{match.group(2)}.{match.group(1)}" if match else ""


def recognise_invoice_file(content: bytes) -> InvoiceRecognition:
    """№, date and the hint as before (SUPPLYMAP's reader, Roller_Buyer's title when it finds both), and the whole
    reading of the file (`app.shared.invoice_reading`)."""
    try:
        text = pdf_text(content)
    except PdfLimitExceeded:
        return InvoiceRecognition(InvoiceInfo(), TOO_HEAVY_HINT)
    info = parse_invoice_text(text)
    # Roller_Buyer's title reader (26.10) when it finds both: «Счет-оферта 696419Z от 31.10.2025», «СЧЕТ - ДОГОВОР № …».
    number, day = parse_invoice_title(re.sub(r"\s+", " ", normalize_invoice_text(text)))
    if number and day:
        info = InvoiceInfo(number=number, date=_dmy(day))
    try:
        reading = read_invoice(content, plain=text)
    except PdfLimitExceeded:  # bigger than the reader's limit: № and date as read, no positions
        reading = InvoiceReading()
    # A text only pypdf reads (the small reader can't decode the font): № and date from the full reading.
    if not info.number and not info.date and reading.header.number and reading.header.date:
        info = InvoiceInfo(number=reading.header.number, date=_dmy(reading.header.date))
    if not text.strip() and reading.scan:
        return InvoiceRecognition(info, "Текст из файла не читается (скан или картинка) — № и дату счёта впишите вручную.",
                                  reading)
    if not info.number and not info.date:
        return InvoiceRecognition(info, "Не распознаны № и дата — впишите вручную.", reading)
    return InvoiceRecognition(info, "", reading)


def recognise_invoice(content: bytes) -> tuple[InvoiceInfo, str]:
    """Text of the file and what it says; the hint is the one SUPPLYMAP shows when nothing was found."""
    found = recognise_invoice_file(content)
    return found.info, found.hint
