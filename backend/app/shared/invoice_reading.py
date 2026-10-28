"""The whole reading of a supplier's invoice file: header with money, positions, the text, remarks.

What Roller_Buyer's Kaiten sync does with each invoice PDF (`apps/api/src/kaiten.ts`): the text layer with word positions
(`pdf_layout`), the header (`invoice_header`), the positions (`invoice_lines`), and the remarks it writes into
`parse_issues` — recomputed from the result, never stored by hand. No model and no OCR: a scan has no text and is typed
by the buyer (owner Витя, 26.10.2026: problem invoices he enters by hand).

Budgeted like every PDF reader of ERP (audit F05): the budgeted `pdf_text` goes first and raises `PdfLimitExceeded` for
a file that unpacks past its budget; its text is also the header's text when pypdf decodes a font wrongly (a single-byte
Cyrillic font without an encoding reads as Latin there).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .invoice_header import InvoiceHeader, parse_invoice_text
from .invoice_lines import InvoiceLine, parse_invoice_lines
from .pdf_layout import pdf_layout
from .pdf_text import PdfLimitExceeded, pdf_text

# Bumped when the reading learns something (RB kaiten.ts PARSER_VERSION was 3 when it was ported).
PARSER_VERSION = "rb3"
MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 200_000
# A text layer shorter than this is a scan with a stray caption, as RB decides.
SCAN_TEXT = 50
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")

NO_SELLER = "поставщик не найден в тексте (скан?)"
NO_TOTAL = "сумма не найдена"
NO_LINES = "позиции не прочитаны"


def line_json(line: InvoiceLine) -> dict:
    """The registry's `lines` item (RB InvoiceLine): a whole quantity as an integer, as JSON.stringify writes it."""
    qty: float | int | None = line.qty
    if isinstance(qty, float) and qty.is_integer():
        qty = int(qty)
    return {"seq": line.seq, "name": line.name, "article": line.article, "qty": qty, "unit": line.unit,
            "price": line.price, "sum": line.sum}


@dataclass(frozen=True)
class InvoiceReading:
    header: InvoiceHeader = field(default_factory=InvoiceHeader)
    lines: tuple[InvoiceLine, ...] = ()
    text: str = ""  # the text layer the header was read from; '' for a scan
    scan: bool = True  # no text layer to read
    issues: tuple[str, ...] = ()

    def as_json(self) -> dict:
        h = self.header
        return {
            "number": h.number, "date": h.date, "seller_name": h.seller_name, "seller_inn": h.seller_inn,
            "total": h.total, "vat": h.vat, "vat_free": h.vat_free, "scan": self.scan,
            "lines": [line_json(line) for line in self.lines], "issues": list(self.issues),
        }


def reading_issues(header: InvoiceHeader, lines: tuple[InvoiceLine, ...] | list[InvoiceLine]) -> tuple[str, ...]:
    """RB's remarks on an invoice whose PDF is there (kaiten.ts): no seller INN, no total, no positions."""
    issues = []
    if not header.seller_inn:
        issues.append(NO_SELLER)
    if header.total is None:
        issues.append(NO_TOTAL)
    if header.seller_inn and not lines:
        issues.append(NO_LINES)
    return tuple(issues)


def read_invoice(content: bytes, plain: str | None = None) -> InvoiceReading:
    """The reading of one file (PDF; anything else reads as a scan). `plain` — `pdf_text`'s text when the caller has
    read it already; otherwise it is read here and `PdfLimitExceeded` raised past the budget."""
    if len(content) > MAX_BYTES:
        raise PdfLimitExceeded("PDF is bigger than the processing limit")
    if plain is None:
        plain = pdf_text(content)
    layout = pdf_layout(content)
    text, pages = layout.text, layout.pages
    if len(_CYRILLIC.findall(text)) < 10 <= len(_CYRILLIC.findall(plain)):
        text, pages = plain, []
    if len(text.strip()) < SCAN_TEXT:
        return InvoiceReading(issues=reading_issues(InvoiceHeader(), ()))
    header = parse_invoice_text(text)
    lines = tuple(parse_invoice_lines(pages))
    return InvoiceReading(header, lines, text[:MAX_TEXT], False, reading_issues(header, lines))
