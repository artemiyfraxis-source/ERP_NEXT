"""«Поставка» from an ЭДО file (owner 26.10.2026): a УПД is paper or ЭДО, never both. The paper one is scanned; the ЭДО
one comes through the same «Файл…» as the Diadoc download — one XML (ON_NSCHFDOPPR) or a ZIP of them. The warehouse
does not import purchasing, so the ФНС reader (`purchasing.edo_xml`) is called here and its answer handed over as plain
rows. Pure: bytes in, a delivery read out — no database."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.modules.purchasing.edo_xml import EdoDoc, Unsupported, UploadRejected, parse_edo_xml, unpack_edo_upload

NOT_UPD = "Это не УПД в формате ФНС — {reason}"
UPD_KINDS = ("УПД", "Счёт-фактура")
NOTE = "Электронный УПД (ЭДО) — данные из файла"


class EdoRejected(ValueError):
    """The file is not a УПД this reader takes; the text is for the storekeeper."""


@dataclass(frozen=True)
class EdoItem:
    name: str
    code: str  # the seller's product code / article column of the XML
    unit: str  # the unit word of the XML
    okei: str  # its OKEI code
    quantity: Decimal


@dataclass(frozen=True)
class EdoDelivery:
    supplier: str
    inn: str
    number: str
    day: date | None
    basis: str  # «Счёт на оплату № 901 от 10.09.2026; …» as the XML names the grounds
    items: list[EdoItem]
    note: str
    # СвПРД — «К платёжно-расчётному документу» (owner 26.10.2026): the numbers joined by «, », the first one's date.
    prd_number: str = ""
    prd_date: date | None = None


# «№ 916 от 19.10.2026, № 917 от 20.10.2026» — `EdoHeader.payment_doc` (edo_xml.edo_doc_ref of each СвПРД).
_PRD_SPLIT = re.compile(r"\s*,\s*(?=№|от\s)")
_PRD = re.compile(r"(?:№\s*(?P<number>.*?))?\s*(?:\bот\s+(?P<day>\d{2}\.\d{2}\.\d{4}))?")
PRD_NUMBER_LIMIT = 255


def payment_docs(text: str) -> tuple[str, date | None]:
    """`payment_doc` → («916, 917», 19.10.2026): every number, the first date."""
    numbers: list[str] = []
    first: date | None = None
    for part in _PRD_SPLIT.split(str(text or "").strip()):
        found = _PRD.fullmatch(part.strip())
        if found is None:
            continue
        if found["number"] and found["number"].strip() not in numbers:
            numbers.append(found["number"].strip())
        if first is None and found["day"]:
            try:
                first = datetime.strptime(found["day"], "%d.%m.%Y").date()
            except ValueError:
                pass
    return ", ".join(numbers)[:PRD_NUMBER_LIMIT], first


def is_edo_upload(file_name: str, content: bytes) -> bool:
    """XML or ZIP by what the bytes are: a ZIP signature, or an XML declaration (a bare «<…» only under a .xml name —
    an HTML page keeps its «PDF, JPG или PNG» refusal)."""
    if content[:2] == b"PK" and content[2:3] in (b"\x03", b"\x05"):
        return True
    head = content[:64].lstrip(b"\xef\xbb\xbf\xff\xfe \t\r\n\x00")
    return head.startswith(b"<?xml") or (head.startswith(b"<") and str(file_name).lower().endswith(".xml"))


def file_kind(content: bytes) -> str:
    return "application/zip" if content[:2] == b"PK" else "application/xml"


def read_delivery(file_name: str, content: bytes) -> EdoDelivery:
    """The first УПД / счёт-фактура of the upload. Anything else — EdoRejected with the reason in words."""
    try:
        files = unpack_edo_upload(file_name, content)
    except UploadRejected as error:
        raise EdoRejected(NOT_UPD.format(reason=error)) from None
    docs: list[EdoDoc] = []
    reasons: list[str] = []
    for found in files:
        parsed = parse_edo_xml(found.xml, found.name)
        if isinstance(parsed, Unsupported):
            reasons.append(parsed.reason)
        elif parsed.kind in UPD_KINDS:
            docs.append(parsed)
        else:
            reasons.append("корректировочный счёт-фактура — не поставка")
    if not docs:
        raise EdoRejected(NOT_UPD.format(reason=reasons[0] if reasons else "в файле нет документов"))
    head = docs[0].header
    note = NOTE if len(docs) == 1 else f"В архиве {len(docs)} документов — взят № {head.number}. {NOTE}"
    items = [
        EdoItem(name=line.name, code=line.article, unit=line.unit, okei=line.unit_code, quantity=line.quantity)
        for line in docs[0].lines if not line.is_service and line.quantity is not None and line.quantity > 0
    ]
    day = date.fromisoformat(head.date) if head.date else None
    prd_number, prd_date = payment_docs(head.payment_doc)
    return EdoDelivery(
        supplier=head.seller_name, inn=head.seller_inn, number=head.number, day=day, basis=head.basis, items=items,
        note=note, prd_number=prd_number, prd_date=prd_date,
    )
