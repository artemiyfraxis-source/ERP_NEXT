"""«Отсканировать УПД»: what a delivery's УПД says, read from the PDF text layer or, without one, by OCR.

A PDF's text layer is read first (`app.shared.pdf_text`); when nothing is recognised in it — a scan from the shop's
Pantum BM2300W, a photo, a PDF without text — the file goes through OCR (`receipt_ocr`: tesseract, rus+eng). Read here:
№ and date of the УПД (счёт-фактура, else «Документ об отгрузке»), the seller and its ИНН, and the invoice named in
«Основание». The patterns take OCR noise: № read as «N°» / «No» / «Ne», long dashes, Latin look-alikes in «Счет» and
«от», spaces and commas inside a date, a marker «(2)» read as «(2]», table rules «|» around the seller."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.shared.pdf_text import PdfLimitExceeded, pdf_text

from . import receipt_ocr

NO_TEXT = "Текст не распознан — заполните вручную"
TOO_HEAVY = "Файл слишком тяжёлый для распознавания — заполните вручную"
PARTLY = "Распознано не всё — проверьте и дополните"
OCR_MISSING = "Распознавание недоступно на сервере — заполните вручную"
RECOGNISED_KINDS = ("application/pdf", "image/jpeg", "image/png")

_SPACES = re.compile("[\xa0   ]")
_DASHES = re.compile("[‐-―−]")
_NO = r"(?:№|N[°ºoOеe9]?\.?)"  # OCR reads «№» as N°, No, Ne, N9
_OT = r"\s*[оo]т\.?\s*"
_DATE = r"(\d{1,2}\s?[./,]\s?\d{1,2}\s?[./,]\s?\d{2,4}|\d{1,2}\s+[А-Яа-яЁё]+\s+\d{4})"
_NUMBER = r"([0-9A-Za-zА-Яа-яЁё/_\-]+)"
_SCHET = r"[СC]ч[еeё]т"
_UPD = (
    re.compile(_SCHET + r"\s*-\s*фактура\s*" + _NO + r"\s*" + _NUMBER + _OT + _DATE, re.I),
    re.compile(r"(?:УПД|передаточный\s+документ)[^\n№]{0,40}" + _NO + r"\s*" + _NUMBER + _OT + _DATE, re.I),
    # (5а) «Документ об отгрузке № п/п 1-3, № 992177 от 25.09.2026» — when the first line is not read.
    re.compile(r"Документ\s+об\s+отгрузке[^\n]{0,40}?" + _NO + r"\s*" + _NUMBER + _OT + _DATE, re.I),
)
# «Основание …: Счёт № 45 от 01.10.2026» — a «Счёт №» that is not «Счёт-фактура»; the one in «Основание» first.
_INVOICE_TAIL = (
    _SCHET + r"(?!\s*-\s*фактур)(?:\s+на\s+оплату)?\s*" + _NO + r"\s*" + _NUMBER + r"(?:" + _OT + _DATE + ")?"
)
_INVOICE = (
    re.compile(r"Основани[ея][^\n]{0,80}?" + _INVOICE_TAIL, re.I),
    re.compile(_INVOICE_TAIL, re.I),
    # (5) «К платёжно-расчётному документу № 916 от 19.10.2026» — the shop's suppliers put the invoice here (owner 23.10).
    re.compile(r"К\s+пл[аa]т[её]жно\s*-\s*р[аa]сч[её]тному\s+документу\s*" + _NO + r"\s*" + _NUMBER + _OT + _DATE, re.I),
)
_SELLER = re.compile(r"Продавец\s*:?\s*([^\n]+)", re.I)
_SELLER_INN = re.compile(r"ИН[НH]\s*/\s*КПП\s+продавца\s*:?\s*(\d{12}|\d{10})(?!\d)", re.I)
_ANY_INN = re.compile(r"ИН[НH]\s*:?\s*(\d{12}|\d{10})(?!\d)", re.I)
_MARK = re.compile(r"\s*[(\[{]\s*\d+\s*[а-яa-z]?\s*[)\]}]\s*$", re.I)
_EDGE_NOISE = re.compile(r"^[\s|_.,;:-]+|[\s|_,;:-]+$")  # a trailing dot stays: «ИП Иванов И.И.»
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
    """«12.10.2026», «12/10/26», «12 октября 2026», OCR's «25 .09,2026» → the date; anything else None."""
    raw = re.sub(r"\s*([./,])\s*", r"\1", str(text or "").strip())
    numeric = re.fullmatch(r"(\d{1,2})[./,](\d{1,2})[./,](\d{2,4})", raw)
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


# A scanned УПД reads the seller and buyer columns as one line: «ООО "Сафетин" (2) Покупатель: ООО …» (23.10).
_NEXT_FIELD = re.compile(r"\s*(?:\(\s*\d+\s*[а-яa-z]?\s*\)|Покупатель|Адрес\b|ИН[НH]\s*/)", re.I)


def _supplier(raw: str) -> str:
    raw = _NEXT_FIELD.split(raw, maxsplit=1)[0]
    name = _EDGE_NOISE.sub("", _MARK.sub("", _EDGE_NOISE.sub("", raw)))
    return "" if not name or name.upper().startswith(("ИНН", "ИНH")) else name[:255]


def parse_upd_text(text: str) -> RecognisedUpd:
    text = _DASHES.sub("-", _SPACES.sub(" ", str(text or "")))
    upd = next((match for pattern in _UPD if (match := pattern.search(text))), None)
    invoice = next((match for pattern in _INVOICE if (match := pattern.search(text))), None)
    seller = _SELLER.search(text)
    inn = _SELLER_INN.search(text)
    if inn is None and seller is not None:
        inn = _ANY_INN.search(text, seller.end())
    return RecognisedUpd(
        supplier=_supplier(seller[1]) if seller else "",
        inn=inn[1] if inn else "",
        upd_number=upd[1][:64] if upd else "",
        upd_date=scan_day(upd[2]) if upd else None,
        invoice_number=invoice[1][:64] if invoice else "",
        invoice_date=scan_day(invoice[2]) if invoice and invoice[2] else None,
    )


def recognise_upd(content: bytes, kind: str | None) -> tuple[RecognisedUpd, str]:
    """What the file says and a note for the screen («» — everything that is looked for was found). The text layer of
    a PDF first; nothing recognised in it (or a picture) — OCR; no OCR on this server — `OCR_MISSING`."""
    if kind not in RECOGNISED_KINDS:
        return RecognisedUpd(), NO_TEXT
    text, heavy = "", False
    if kind == "application/pdf":
        try:
            text = pdf_text(content)
        except PdfLimitExceeded:
            heavy = True
    found = parse_upd_text(text)
    if found.empty:
        try:
            found = parse_upd_text(receipt_ocr.ocr_text(content, kind))
        except receipt_ocr.OcrUnavailable:
            return found, TOO_HEAVY if heavy else OCR_MISSING
    if found.empty:
        return found, NO_TEXT
    if not (found.supplier and found.upd_number and found.upd_date):
        return found, PARTLY
    return found, ""
