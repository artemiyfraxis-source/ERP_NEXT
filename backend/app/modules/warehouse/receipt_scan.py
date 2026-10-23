"""«Отсканировать УПД»: what a delivery's УПД says, read from the PDF text layer.

The shop's Pantum BM2300W can save a searchable PDF; ERP has no OCR (as procurement's invoice recognition,
`app.shared.pdf_text`), so a picture or a PDF without text gives nothing and the storekeeper types the header. Read here:
№ and date of the УПД (счёт-фактура), the seller and its ИНН, and the invoice named in «Основание»."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.shared.pdf_text import PdfLimitExceeded, pdf_text

NO_TEXT = "Текст не распознан — заполните вручную"
TOO_HEAVY = "Файл слишком тяжёлый для распознавания — заполните вручную"
PARTLY = "Распознано не всё — проверьте и дополните"

_SPACES = re.compile("[    ]")
_DATE = r"(\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}\s+[А-Яа-яЁё]+\s+\d{4})"
_NUMBER = r"([0-9A-Za-zА-Яа-яЁё/_\-]+)"
_UPD = (
    re.compile(r"Сч[её]т\s*-\s*фактура\s*№\s*" + _NUMBER + r"\s*от\s*" + _DATE, re.I),
    re.compile(r"(?:УПД|передаточный\s+документ)[^\n№]{0,40}№\s*" + _NUMBER + r"\s*от\s*" + _DATE, re.I),
)
# «Основание …: Счёт № 45 от 01.10.2026» — any «Счёт №» that is not «Счёт-фактура».
_INVOICE = re.compile(r"Сч[её]т(?!\s*-\s*фактур)(?:\s+на\s+оплату)?\s*№\s*" + _NUMBER + r"(?:\s*от\s*" + _DATE + ")?",
                      re.I)
_SELLER = re.compile(r"Продавец\s*:?\s*([^\n]+)", re.I)
_SELLER_INN = re.compile(r"ИНН\s*/\s*КПП\s+продавца\s*:?\s*(\d{10}|\d{12})", re.I)
_ANY_INN = re.compile(r"ИНН\s*:?\s*(\d{10}|\d{12})(?!\d)", re.I)
_MARK = re.compile(r"\s*\(\d+[а-я]?\)\s*$")
_MONTHS = (
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма[йя]", 5), ("июн", 6), ("июл", 7), ("август", 8),
    ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12),
)


@dataclass(frozen=True)
class RecognisedUpd:
    supplier: str = ""
    inn: str = ""
    upd_number: str = ""
    upd_date: date | None = None
    invoice_number: str = ""
    invoice_date: date | None = None

    @property
    def empty(self) -> bool:
        return not (self.supplier or self.inn or self.upd_number or self.upd_date or self.invoice_number)


def scan_day(text: str | None) -> date | None:
    """«12.10.2026», «12/10/26», «12 октября 2026» → the date; anything else None."""
    raw = str(text or "").strip()
    numeric = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", raw)
    try:
        if numeric:
            year = int(numeric[3]) + (2000 if len(numeric[3]) == 2 else 0)
            return date(year, int(numeric[2]), int(numeric[1]))
        worded = re.fullmatch(r"(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4})", raw)
        if worded:
            month = next((number for stem, number in _MONTHS if re.match(stem, worded[2], re.I)), 0)
            return date(int(worded[3]), month, int(worded[1])) if month else None
    except ValueError:
        return None
    return None


def parse_upd_text(text: str) -> RecognisedUpd:
    text = _SPACES.sub(" ", str(text or ""))
    upd = next((match for pattern in _UPD if (match := pattern.search(text))), None)
    invoice = _INVOICE.search(text)
    seller = _SELLER.search(text)
    inn = _SELLER_INN.search(text)
    if inn is None and seller is not None:
        inn = _ANY_INN.search(text, seller.end())
    supplier = _MARK.sub("", seller[1]).strip() if seller else ""
    supplier = supplier if supplier and not supplier.upper().startswith("ИНН") else ""
    return RecognisedUpd(
        supplier=supplier[:255],
        inn=inn[1] if inn else "",
        upd_number=upd[1][:64] if upd else "",
        upd_date=scan_day(upd[2]) if upd else None,
        invoice_number=invoice[1][:64] if invoice else "",
        invoice_date=scan_day(invoice[2]) if invoice and invoice[2] else None,
    )


def recognise_upd(content: bytes, kind: str | None) -> tuple[RecognisedUpd, str]:
    """What the file says and a note for the screen («» — everything that is looked for was found)."""
    if kind != "application/pdf":
        return RecognisedUpd(), NO_TEXT
    try:
        text = pdf_text(content)
    except PdfLimitExceeded:
        return RecognisedUpd(), TOO_HEAVY
    found = parse_upd_text(text)
    if not text.strip() or found.empty:
        return found, NO_TEXT
    if not (found.supplier and found.upd_number and found.upd_date):
        return found, PARTLY
    return found, ""
