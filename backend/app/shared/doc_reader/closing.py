"""The CLOSING task of the document reader: one closing document (УПД, СФ, ТН+СФ, ТОРГ-12, акт, М-15, КСФ) → its
header, lines and money, checked (Витя, 26.10.2026: «Наш парсер идёт не рядом с парсером Артемия, а они становятся
одним целым … он идёт как модификация парсера Артемия»).

A port of Roller_Buyer's Google Apps Script parser (google/upd-parser: UpdSchema.js — prompt and answer fields,
UpdCore.js — values, line arithmetic, document checks), which filled the УПД registry until now through `rb_import`,
plus Артемий's warehouse fields of a row (his `receipt_llm.ITEM_FIELDS`: the maker's article, the maker, the item type
of the stock dictionary, our short name) and his header rules — the invoice only from «Основание передачи…» (our
`basis`), the ПРД only from «К платёжно-расчётному документу» (our `paymentDoc`). One reading serves the warehouse
(«Отсканировать УПД»: names, quantities, units) and the registry (money, the basis and payment links).

A scan is read page by page (each page with the full field list, `prompt(page=True)`), the pages merged
(`merge_pages`) and only then normalized and checked (`closing_doc`: line sum = total etc.). Pure: no database, no
HTTP — the calls are `reader`, the writes the callers'.

Measured before the port (Roller_Buyer's Google PDFs that also came as an exact EDO XML, 14 documents / 51 lines, on
gemini-3.5-flash-lite): number, date, KPP and line count always right; a wrong INN, a lost VAT, three wrong payment
numbers, one line with quantity and price swapped and ~1 line in 4 with wrong characters in the name.
`python -m app.workflows.upd_pdf_parse --benchmark` repeats the measurement on the server's model."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.shared.inn import OWN_INNS, inn_valid

# The parser's verdict (the registry's «status» column).
OK = "Разобран"
CHECK = "Требует проверки"
OTHER = "Не документ закупки"

KIND_LABELS = {
    "UPD": "УПД",
    "INVOICE": "Счёт-фактура",
    "TORG12_INVOICE": "ТН + СФ",
    "TORG12": "Товарная накладная",
    "ACT": "Акт",
    "M15": "М-15",
    "CORRECTION": "Корректировочный СФ",
    "OTHER": "Прочее",
}
TAG_LABELS = {"M15": "М-15", "TN": "ТН + СФ", "": ""}
OWN_INN = "5040191181"
MISSING_PAGES = "модель не прочитала стр. {pages} — их строк в разборе нет"

# ---- the file name ------------------------------------------------------------------------------------------------

_PDF = re.compile(r"\.pdf$", re.IGNORECASE)
_TAG_M15 = re.compile(r"(^|[\s_(\-])[МM]\s*-?\s*15(?=$|[\s_).\-])", re.IGNORECASE)
_TAG_TN = re.compile(r"(^|[\s_(])[ТT][НH](\s*\+\s*(СФ|CF|SF))?(?=$|[\s_).])", re.IGNORECASE)


def file_tag(file_name: str) -> str:
    """The owner's tag in the file name: 'M15' | 'TN' | ''. Latin or Cyrillic letters (М/M, Т/T, Н/H), any case;
    «Давальческое» means М-15. A tag wins over the model's opinion of the kind."""
    name = _PDF.sub("", str(file_name or ""))
    if re.search("давальч", name, re.IGNORECASE) or _TAG_M15.search(name):
        return "M15"
    if _TAG_TN.search(name):
        return "TN"
    return ""


_TAIL_BRACKETS = re.compile(r"\s*\([^)]*\)\s*$")
_TAIL_YEAR = re.compile(r"\s+\d{2}\s*г\.?$", re.IGNORECASE)
_TAIL_WORD = re.compile(r"\s+(СФ|УПД|ТН|Акт|ТОРГ-?12)$", re.IGNORECASE)
_AMOUNT = re.compile(r"(?:^|\s)(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](\d{1,2}))?$")


def file_amount(file_name: str) -> float | None:
    """The amount at the end of «Инт Техника 13 100.pdf» → 13100.0, checked against the document's total. Tails
    «(2)», «26г», «СФ», «УПД», «Акт», «ТОРГ-12» are dropped; names with «+» (two documents) and М-15 have none."""
    name = _PDF.sub("", str(file_name or "")).strip()
    if "+" in name or file_tag(name) == "M15":
        return None
    previous = None
    while name != previous:
        previous = name
        name = _TAIL_WORD.sub("", _TAIL_YEAR.sub("", _TAIL_BRACKETS.sub("", name))).strip()
    match = _AMOUNT.search(name)
    if not match:
        return None
    if name.replace(match.group(0), "", 1).strip() == "":
        return None  # a name that is only a number is not «supplier + amount»
    value = float(re.sub(r"[  ]", "", match.group(1)) + ("." + match.group(2) if match.group(2) else ""))
    return value if value > 0 else None


# ---- values -------------------------------------------------------------------------------------------------------

_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def parse_num(value: Any) -> float | None:
    """«1 234,56» / «1234.56» / 1234.56 → a number; empty, a dash or garbage → None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value and abs(value) != float("inf") else None
    text = re.sub(r"[\s  ]", "", str(value))
    text = re.sub(r"[₽р]|руб\.?", "", text, flags=re.IGNORECASE)
    if not text or text in ("-", "—", "–"):
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".", 1)
    else:
        text = text.replace(",", ".", 1)
    if not _NUMBER.match(text):
        return None
    return float(text)


def parse_vat_rate(value: Any) -> tuple[float | None, bool]:
    """(rate in percent | None, no VAT). «22%», 22, 0.22, «без НДС»."""
    text = "" if value is None else str(value).strip()
    if re.search(r"без\s*ндс|не\s*облага", text, re.IGNORECASE):
        return 0.0, True
    rate = parse_num(text.replace("%", ""))
    if rate is None:
        return None, False
    if 0 < rate < 1:
        rate = float(round(rate * 100))
    return rate, False


_MONTHS = (("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма", 5), ("июн", 6), ("июл", 7),
           ("август", 8), ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12))


def parse_date(value: Any) -> date | None:
    """«2026-04-21», «21.04.2026», «21.04.26», «21 апреля 2026 г.» → a date; anything else → None."""
    text = str(value or "").strip().lower()
    if match := re.search(r"(\d{4})-(\d{2})-(\d{2})", text):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", text):
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif match := re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", text):
        day, year, word = int(match.group(1)), int(match.group(3)), match.group(2)
        # «ма» is май/мая only, not «март».
        month = next((number for stem, number in _MONTHS
                      if word.startswith(stem) and not (stem == "ма" and word.startswith("мар"))), 0)
    else:
        return None
    if year < 100:
        year += 2000
    if not 2000 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


_SERVICE = re.compile(
    r"доставк|упаковк|транспортн|перевозк|экспедир|курьер|сдэк|почтов|услуг[аи]\s+по|погрузк|разгрузк", re.IGNORECASE,
)


def is_service_line(name: str) -> bool:
    """Delivery, packing, services — not goods: they go neither into the nomenclature nor into prices."""
    return bool(_SERVICE.search(str(name or "")))


# ---- one line's arithmetic ----------------------------------------------------------------------------------------

def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.05, abs(b) * 0.01)


def _round2(value: float) -> float:
    return float(Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class LineMoney:
    qty: float | None
    price: float | None
    net: float | None
    vat_rate: float | None
    no_vat: bool
    vat: float | None
    gross: float | None


def repair_line(line: LineMoney, price_may_include_vat: bool = False) -> tuple[LineMoney, list[str], list[str]]:
    """Fills and swaps a line's sums where it is unambiguous → (line, fixes, issues). `price_may_include_vat`: an act
    may print prices with VAT («в том числе НДС»); in УПД, СФ and ТОРГ-12 the price column is net by the form."""
    l = LineMoney(line.qty, line.price, line.net, line.vat_rate, line.no_vat, line.vat, line.gross)
    fixes: list[str] = []
    issues: list[str] = []
    r = 0.0 if l.no_vat else l.vat_rate

    # The «with VAT» column holds the tax itself: exactly net × rate. Checked BEFORE the swap below, which would
    # otherwise take such a tax for swapped sums.
    vat_in_gross = False
    if r and l.net is not None and l.gross is not None and l.gross < l.net and _close(l.gross, l.net * r / 100):
        if l.vat is None or _close(l.vat, l.gross):
            l.vat = l.gross
            l.gross = _round2(l.net + l.vat)
            vat_in_gross = True
            fixes.append("НДС стоял в графе «с НДС»")
    if not vat_in_gross and l.net is not None and l.gross is not None and l.net > l.gross and not _close(l.net, l.gross):
        l.net, l.gross = l.gross, l.net
        fixes.append("без НДС и с НДС переставлены")
    if (price_may_include_vat and r and r > 0 and l.net is None and l.gross is not None and l.qty and l.price is not None
            and _close(l.qty * l.price, l.gross)):
        l.price = None
        fixes.append("цена указана с НДС")
    if l.net is None and l.qty is not None and l.price is not None:
        l.net = _round2(l.qty * l.price)
        fixes.append("без НДС = кол-во × цена")
    if l.net is None and l.gross is not None and l.vat is not None:
        l.net = _round2(l.gross - l.vat)
        fixes.append("без НДС = с НДС − НДС")
    if l.net is None and l.vat is None and l.gross is not None and r is not None:
        l.vat = _round2(l.gross * r / (100 + r))
        l.net = _round2(l.gross - l.vat)
        fixes.append("НДС выделен из суммы с НДС" if r else "без НДС = с НДС")
    if l.vat is None and l.net is not None and l.gross is not None:
        l.vat = _round2(l.gross - l.net)
        fixes.append("НДС = с НДС − без НДС")
    if l.vat is None and l.net is not None and r is not None:
        l.vat = _round2(l.net * r / 100)
        fixes.append("НДС = без НДС × ставка")
    if l.gross is None and l.net is not None and l.vat is not None:
        l.gross = _round2(l.net + l.vat)
        fixes.append("с НДС = без НДС + НДС")
    if l.price is None and l.net is not None and l.qty:
        l.price = _round2(l.net / l.qty)
        fixes.append("цена = без НДС / кол-во")

    if l.qty is not None and l.price is not None and l.net is not None and not _close(l.qty * l.price, l.net):
        issues.append("кол-во × цена ≠ без НДС")
    if r is not None and r > 0 and l.net is not None and l.vat is not None and not _close(l.net * r / 100, l.vat):
        issues.append(f"НДС не равен ставке {_plain(r)}%")
    if l.net is not None and l.vat is not None and l.gross is not None and not _close(l.net + l.vat, l.gross):
        issues.append("без НДС + НДС ≠ с НДС")
    return l, fixes, issues


def _plain(value: float) -> str:
    """22.0 → «22», 13100.5 → «13100.5» — numbers in messages as the Google parser printed them."""
    return str(int(value)) if float(value).is_integer() else repr(value)


# ---- the whole document -------------------------------------------------------------------------------------------

@dataclass
class ParsedLine:
    seq: int
    name: str
    article: str  # column «А — код товара»: often the seller's own code («00-00042353»)
    product_type_code: str
    unit_code: str
    unit: str
    qty: float | None
    price: float | None
    net: float | None
    excise: str
    vat_rate: str  # «22», «без НДС», «» — as the registry keeps it
    vat: float | None
    gross: float | None
    country_code: str
    country: str
    declaration: str
    is_service: bool
    issues: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    # What the model made of the name for the warehouse (asked only with the stock's item types, `prompt(types=…)`):
    # the maker's article, the maker, the item type as the model wrote it (the warehouse keeps it only when it is one
    # of its dictionary), our short name. «» — not asked or not said.
    maker_article: str = ""
    maker: str = ""
    item_type: str = ""
    short_name: str = ""


@dataclass
class ParsedDoc:
    kind: str  # UPD | INVOICE | TORG12_INVOICE | TORG12 | ACT | M15 | CORRECTION | OTHER
    number: str
    doc_date: date | None
    seller_name: str
    seller_inn: str
    seller_kpp: str
    buyer_inn: str
    basis: str
    payment_doc: str
    total_net: float | None
    total_vat: float | None
    total_gross: float | None
    file_amount: float | None
    lines: list[ParsedLine] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    fixes: int = 0

    @property
    def status(self) -> str:
        if self.kind == "OTHER":
            return OTHER
        return CHECK if self.issues else OK


def _str(value: Any) -> str:
    return "" if value is None or isinstance(value, (dict, list)) else str(value).strip()


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", _str(value))


def normalize(raw: dict | None, tag: str, file_name: str) -> ParsedDoc:
    """The model's JSON (the fields of `prompt`) → the normalized document with its remarks."""
    raw = raw if isinstance(raw, dict) else {}
    kind = _str(raw.get("docKind")).upper()
    if kind not in KIND_LABELS:
        kind = "M15" if tag == "M15" else "TORG12_INVOICE" if tag == "TN" else "UPD"
    if tag == "M15":  # the owner's tag in the name wins over the model
        kind = "M15"
    elif tag == "TN":
        kind = "TORG12_INVOICE"

    doc = ParsedDoc(
        kind=kind, number=re.sub(r"^№\s*", "", _str(raw.get("number"))), doc_date=parse_date(raw.get("date")),
        seller_name=_str(raw.get("sellerName")), seller_inn=_digits(raw.get("sellerInn")),
        seller_kpp=_digits(raw.get("sellerKpp")), buyer_inn=_digits(raw.get("buyerInn")),
        basis=re.sub(r"^[:\s]+", "", _str(raw.get("basis"))),
        payment_doc=re.sub(r"^[:\s]+", "", _str(raw.get("paymentDoc"))),
        total_net=parse_num(raw.get("totalNet")), total_vat=parse_num(raw.get("totalVat")),
        total_gross=parse_num(raw.get("totalGross")), file_amount=file_amount(file_name),
    )
    if kind == "OTHER":
        return doc

    act = kind == "ACT"
    raw_lines = raw.get("lines") if isinstance(raw.get("lines"), list) else []
    seen: dict[str, int] = {}
    used: set[int] = set()
    for index, src in enumerate(raw_lines, start=1):
        src = src if isinstance(src, dict) else {}
        rate, no_vat = parse_vat_rate(src.get("vatRate"))
        line, fixes, issues = repair_line(LineMoney(
            qty=parse_num(src.get("qty")), price=parse_num(src.get("price")), net=parse_num(src.get("net")),
            vat_rate=rate, no_vat=no_vat, vat=parse_num(src.get("vat")), gross=parse_num(src.get("gross")),
        ), price_may_include_vat=act)
        name = _str(src.get("name"))
        if not name:
            issues.append("нет наименования")
        if kind != "M15" and line.gross is None and line.net is None:  # М-15 often has no prices: not an error
            issues.append("нет сумм")
        key = "|".join(str(part) for part in (name, line.qty, line.price, line.gross))
        seq_read = parse_num(src.get("seq"))
        seq = int(seq_read) if seq_read is not None and seq_read.is_integer() and seq_read > 0 else index
        if key in seen:
            issues.append(f"повтор строки {seen[key]}")
        seen[key] = seq
        # The registry keeps one line per number: a repeated or missing number takes the next free one.
        if seq in used:
            seq = max(used) + 1
        used.add(seq)
        doc.fixes += len(fixes)
        doc.lines.append(ParsedLine(
            seq=seq, name=name, article=_str(src.get("article")), product_type_code=_str(src.get("productTypeCode")),
            unit_code=_str(src.get("unitCode")), unit=_str(src.get("unit")), qty=line.qty, price=line.price,
            net=line.net, excise=_str(src.get("excise")),
            vat_rate="без НДС" if no_vat else _plain(rate) if rate is not None else "",
            vat=line.vat, gross=line.gross, country_code=_str(src.get("countryCode")), country=_str(src.get("country")),
            declaration=_str(src.get("declaration")), is_service=act or is_service_line(name), issues=issues,
            fixes=fixes, maker_article=_str(src.get("makerArticle")), maker=_str(src.get("maker")),
            item_type=_str(src.get("itemType")), short_name=_str(src.get("shortName")),
        ))

    if not doc.lines:
        doc.issues.append("нет ни одной позиции")
    if not doc.number:
        doc.issues.append("нет номера документа")
    if doc.doc_date is None:
        doc.issues.append("нет даты документа")
    if not doc.seller_name:
        doc.issues.append("нет отправителя" if kind == "M15" else "нет продавца")
    # ТОРГ-12 and acts need the INN too: the supplier and the invoice are found by it.
    if kind != "M15":
        if not doc.seller_inn:
            doc.issues.append("нет ИНН продавца")
        elif doc.seller_inn in OWN_INNS:
            doc.issues.append(f"в продавце наш ИНН {doc.seller_inn} — продавец и покупатель перепутаны?")
        elif not inn_valid(doc.seller_inn):
            doc.issues.append(f"ИНН продавца не проходит проверку: {doc.seller_inn}")

    grosses = [line.gross for line in doc.lines]
    if doc.lines and all(gross is not None for gross in grosses):
        sum_gross = _round2(sum(grosses))
        if doc.total_gross is None:
            doc.total_gross = sum_gross
        elif abs(sum_gross - doc.total_gross) > max(1, doc.total_gross * 0.005):
            doc.issues.append(f"сумма строк {_plain(sum_gross)} ≠ итог документа {_plain(doc.total_gross)}")
    if doc.file_amount is not None and doc.total_gross is not None and abs(doc.file_amount - doc.total_gross) > 1:
        doc.issues.append(f"сумма в имени файла {_plain(doc.file_amount)} ≠ итог {_plain(doc.total_gross)}")
    cross = parse_num(raw.get("crossCheckTotal"))
    if kind == "TORG12_INVOICE" and cross is not None and doc.total_gross is not None and abs(cross - doc.total_gross) > 1:
        doc.issues.append(f"итог ТОРГ-12 {_plain(cross)} ≠ итог счёта-фактуры {_plain(doc.total_gross)}")
    flagged = sum(1 for line in doc.lines if line.issues)
    if flagged:
        doc.issues.append(f"строк с замечаниями: {flagged}")
    return doc


# ---- a document read page by page ---------------------------------------------------------------------------------

HEADER_KEYS = ("number", "date", "sellerName", "sellerInn", "sellerKpp", "buyerName", "buyerInn", "paymentDoc", "basis")
TOTAL_KEYS = ("totalNet", "totalVat", "totalGross")


def _last_number(pages: Sequence[Mapping], key: str):
    return next((page.get(key) for page in reversed(pages) if parse_num(page.get(key)) is not None), None)


def merge_pages(pages: Sequence[Mapping | None]) -> dict:
    """The pages' answers as one document (a page no model read — None — is skipped): each header field from the first
    page that has it; the totals and crossCheckTotal from the last page that has them (the totals stand under the
    table); the lines one after another, a row repeated with the same №, code and name (a sheet scanned twice) once.

    A PDF of two documents — pages of a ТОРГ-12 and of its счёт-фактура (docKind TORG12_INVOICE on a page, or TORG12
    pages next to INVOICE / UPD pages) — is TORG12_INVOICE: lines, header and totals from the счёт-фактура pages, the
    basis and crossCheckTotal (its «всего с НДС») from the ТОРГ-12 pages, as the one-call prompt asks."""
    read = [dict(page) for page in pages if isinstance(page, Mapping)]
    if not read:
        return {}
    kinds = [_str(page.get("docKind")).upper() for page in read]
    two = "TORG12_INVOICE" in kinds or ("TORG12" in kinds and bool({"INVOICE", "UPD"} & set(kinds)))
    torg = [page for page, kind in zip(read, kinds) if kind == "TORG12"] if two else []
    invoice = [page for page, kind in zip(read, kinds) if kind != "TORG12"] if two else read
    kind = "TORG12_INVOICE" if two else next((k for k in kinds if k and k != "OTHER"), next((k for k in kinds if k), ""))
    merged: dict[str, Any] = {"docKind": kind}
    for key in HEADER_KEYS:
        order = torg + invoice if key == "basis" else invoice + torg
        merged[key] = next((page.get(key) for page in order if _str(page.get(key))), None)
    for key in TOTAL_KEYS:
        merged[key] = _last_number(invoice, key)
    merged["crossCheckTotal"] = _last_number(read, "crossCheckTotal")
    if two and merged["crossCheckTotal"] is None and torg:
        merged["crossCheckTotal"] = _last_number(torg, "totalGross")
    lines: list = []
    seen: set[tuple[str, str, str]] = set()
    for page in invoice:
        for line in page.get("lines") if isinstance(page.get("lines"), list) else []:
            if not isinstance(line, Mapping):
                continue
            key = (_str(line.get("seq")), _str(line.get("article")), _str(line.get("name")).casefold())
            if key[0] and key[2] and key in seen:
                continue
            seen.add(key)
            lines.append(dict(line))
    merged["lines"] = lines
    return merged


def closing_doc(pages: Sequence[Mapping | None], tag: str = "", file_name: str = "",
                header: Mapping[str, Any] | None = None) -> ParsedDoc:
    """The pages merged, normalized and checked. `header` — fields that win over the reading (the storekeeper's saved
    № / date / seller, in the reading's own keys: «number», «date», «sellerName», «sellerInn», «basis», «paymentDoc»).
    A page no model read is named in the remarks: its lines are not in the document."""
    merged = merge_pages(pages)
    if header:
        merged.update(header)
    doc = normalize(merged, tag, file_name)
    missing = [str(number) for number, page in enumerate(pages, start=1) if not isinstance(page, Mapping)]
    if missing:
        doc.issues.insert(0, MISSING_PAGES.format(pages=", ".join(missing)))
    return doc


# ---- money as the registry keeps it -------------------------------------------------------------------------------

def kopecks(rubles: float | None) -> int | None:
    if rubles is None:
        return None
    return int((Decimal(repr(rubles)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(repr(value)).quantize(Decimal("0.000001"))


# ---- the model's task ---------------------------------------------------------------------------------------------

_COMMON = (
    "Ты распознаёшь российский первичный документ (скан, возможен шум, поворот, печати поверх текста). "
    "Верни ТОЛЬКО JSON без пояснений с полями: docKind, number, date, sellerName, sellerInn, sellerKpp, buyerName, "
    "buyerInn, paymentDoc, basis, totalNet, totalVat, totalGross, crossCheckTotal, lines (массив: seq, name, article, "
    "productTypeCode, unitCode, unit, qty, price, net, excise, vatRate, vat, gross, countryCode, country, "
    "declaration{items}). Правила:\n"
    "• Графа, которой в документе нет или она пустая/прочерк, — null. Ничего не придумывай и не сдвигай значения в "
    "соседние поля.\n"
    "• Числа — числом JSON с точкой: 1234.56, без пробелов и валюты.\n"
    "• date — дата документа в формате ГГГГ-ММ-ДД. number — только номер, без «№» и без даты.\n"
    "• ИНН и КПП — только цифры. Если ИНН/КПП записаны через дробь «ИНН/КПП», раздели. "
    "ИНН организации — 10 цифр, ИП — 12, КПП — 9: переписывай каждую цифру внимательно.\n"
    "• seller* — продавец (поставщик, исполнитель, отправитель), buyer* — покупатель (плательщик, заказчик, "
    "получатель). Покупатель обычно наша компания ООО «ЗЭП «ФРАКСИС» (ИНН " + OWN_INN + ") — её реквизиты не клади в "
    "seller*.\n"
    "• vatRate — как в документе: «22%», «20%», «10%», «0%» или «без НДС».\n"
    "• basis — ВСЕ документы-основания дословно, как напечатаны: у каждого название, номер и дата; несколько — через "
    "«; » (например «Счет-договор № 15956237 от 29.09.2025; Договор поставки № 55 от 04.02.2021»). "
    "Номер и дату не сокращай и не придумывай; основания нет — null.\n"
    "• paymentDoc — КАЖДЫЙ платёжный документ в виде «№ <номер> от <ДД.ММ.ГГГГ>», несколько — через «, » "
    "(например «№ 1036 от 27.10.2025, № 1040 от 28.10.2025»). Только номер и дата: без «Пл. пор.», без ссылок на счёт "
    "и другого текста; платёжных документов нет — null.\n"
    "• Перечисли ВСЕ позиции таблицы по порядку, включая доставку и упаковку, без пропусков и без выдумок. Строки "
    "«Всего», «Итого», «Всего к оплате» — НЕ позиции: их суммы клади в totalNet / totalVat / totalGross.\n"
    "{pages}"
    "• Наименования, артикулы и номера переписывай посимвольно, переносы внутри наименования склеивай; латиницу и "
    "кириллицу не путай (AT и АТ, CP и СР, 3M и 3М), цифру 0 и букву O не путай.\n"
)
_ALL_PAGES = "• Если документ многостраничный — позиции со всех страниц.\n"
# Артемий's warehouse reading reads a scan page by page (26.10.2026: ~8 s a page, in parallel); so does this one.
_ONE_PAGE = (
    "• Перед тобой ОДНА страница документа: многостраничный документ читается по страницам и собирается в один. "
    "Верни то, что есть на этой странице: шапки на ней нет — поля шапки null; таблицы нет — lines пустой; итоги "
    "(«Итого», «Всего к оплате») — только если они на этой странице. Страница без заголовка — продолжение таблицы: "
    "docKind — вид документа, к которому она относится (обычно UPD), её позиции перечисли все.\n"
)
# Артемий's fields of a row (warehouse `receipt_llm.ITEM_FIELDS`, owner 26.10.2026: «и тип, и артикул, и наименование
# наше»), asked when the caller gives the stock's item types.
_ITEMS = (
    "• Для каждой позиции ещё: makerArticle — артикул производителя, если он есть в наименовании или в коде товара; "
    "у типовых изделий без заводского номера (наконечники НШВ/НКИ/РПИ, трубка ТУТ, провод ПВАМ) — их марка из "
    "наименования, например «НШВ 6,0-12»; код продавца с упаковкой вроде «VLnhv-6.0-12(100)» — не артикул "
    "(иначе null); maker — производитель, если назван (иначе null); itemType — ровно одно из: {types}; «Терминал» — "
    "обжимной контакт разъёма (JST, Molex…), «Наконечник» — НШВ, НШВИ, НКИ, РПИ, ПМ и подобные; если не "
    "уверен — null; shortName — наше наименование: тип и главные параметры по-русски, коротко, без артикула, "
    "производителя и кодов продавца, без количества в упаковке, например «Контакт обжимной JST SXH, 0,08–0,2 мм²».\n"
)

_KINDS = (
    "docKind — вид документа по его заголовку:\n"
    "• UPD — универсальный передаточный документ (УПД);\n"
    "• INVOICE — счёт-фактура без передаточной части;\n"
    "• CORRECTION — корректировочный счёт-фактура или корректировочный УПД;\n"
    "• TORG12_INVOICE — в одном PDF товарная накладная ТОРГ-12 и счёт-фактура на ту же поставку;\n"
    "• TORG12 — товарная накладная ТОРГ-12 без счёта-фактуры;\n"
    "• ACT — акт выполненных работ, оказанных услуг, сдачи-приёмки работ (в строках — работы и услуги);\n"
    "• M15 — накладная на отпуск материалов на сторону (форма М-15);\n"
    "• OTHER — не документ поставки: акт сверки взаиморасчётов, счёт на оплату, коммерческое предложение, "
    "договор, письмо, запрос — тогда lines пустой. Акт сверки — всегда OTHER, не ACT.\n"
)

_UPD = (
    "Шаблон УПД / счёта-фактуры. Графы таблицы: А — код товара (article); 1 — № п/п (seq); "
    "1а — наименование (name); 1б — код вида товара (productTypeCode); 2 — код единицы (unitCode); "
    "2а — условное обозначение единицы (unit); 3 — количество (qty); 4 — цена за единицу (price); "
    "5 — стоимость без налога (net); 6 — акциз (excise); 7 — налоговая ставка (vatRate); "
    "8 — сумма налога (vat); 9 — стоимость с налогом (gross); 10 — код страны (countryCode); "
    "10а — страна (country); 11 — регистрационный номер декларации/партии (declaration).\n"
    "Шапка: продавец → sellerName/sellerInn/sellerKpp; покупатель → buyerName/buyerInn; "
    "«К платёжно-расчётному документу № … от …» (строка 5 шапки) → paymentDoc; "
    "«Основание передачи (сдачи)/получения (приёмки)» (строка [8] под таблицей УПД) → basis.\n"
)

_TORG12 = (
    "Шаблон ТОРГ-12 «Товарная накладная» без счёта-фактуры. docKind = TORG12.\n"
    "Шапка: «Поставщик» → sellerName/sellerInn/sellerKpp (ИНН и КПП — в реквизитах поставщика; поставщика нет — "
    "грузоотправитель); «Плательщик» → buyerName/buyerInn (нет — грузополучатель); «Номер документа» и «Дата "
    "составления» → number/date; «Основание» (договор, заказ-наряд, счёт; номер и дата — в клетках справа) → basis. "
    "Платёжного документа в ТОРГ-12 нет — paymentDoc = null.\n"
    "Графы таблицы: номер по порядку (seq); товар — наименование (name) и код (article); единица измерения — "
    "наименование (unit) и код по ОКЕИ (unitCode); количество (масса нетто) (qty); цена (price); сумма без учёта НДС "
    "(net); НДС — ставка (vatRate) и сумма (vat); сумма с учётом НДС (gross). Вид упаковки, количество мест и массу "
    "брутто не бери. «Итого» и «Всего по накладной» — не позиции.\n"
)

_ACT = (
    "Шаблон «Акт» выполненных работ / оказанных услуг. docKind = ACT.\n"
    "Шапка: «Исполнитель» → sellerName/sellerInn/sellerKpp; «Заказчик» → buyerName/buyerInn; номер и дата акта → "
    "number/date. basis — договор, заказ или счёт, на который ссылается акт: строка «Основание», «по договору № … от "
    "…», «по счёту № … от …» в шапке, над таблицей или в наименовании услуги. paymentDoc — только если акт прямо "
    "называет платёжный документ, иначе null.\n"
    "Позиции — работы и услуги: name, qty, unit, price и сумма строки. Куда класть сумму, смотри по итогам акта:\n"
    "— «Итого», затем отдельно «Сумма НДС» (или «НДС») и «Всего с НДС» — суммы строк без НДС: сумма → net;\n"
    "— «В том числе НДС» — цены и суммы строк уже с НДС: сумма → gross, net = null, price — как в акте;\n"
    "— «Без налога (НДС)» — vatRate = «без НДС», сумма → gross.\n"
    "Если ставки нет в строках, а в итогах она указана — vatRate бери из итогов (одна на все строки).\n"
)

_M15 = (
    "Шаблон М-15 «Накладная на отпуск материалов на сторону» (давальческие материалы). docKind = M15.\n"
    "Шапка: организация-отправитель → sellerName/sellerInn; получатель → buyerName/buyerInn; "
    "основание (договор/заказ) → basis; номер и дата накладной → number/date.\n"
    "Графы таблицы: материальные ценности — наименование (name), номенклатурный номер (article); "
    "единица измерения — код (unitCode), наименование (unit); количество — бери графу «отпущено» "
    "(если её нет — «надлежит отпустить») (qty); цена (price); сумма без НДС (net); сумма НДС (vat); "
    "всего с НДС (gross). Корреспондирующий счёт, инвентарный номер, номер паспорта и номер по "
    "складской картотеке не нужны. Цен в М-15 может не быть — тогда price/net/vat/gross = null.\n"
)

_TN = (
    "В этом PDF ДВА документа: товарная накладная ТОРГ-12 и СЧЁТ-ФАКТУРА на ту же поставку. docKind = "
    "TORG12_INVOICE.\n"
    "Позиции (lines) и реквизиты шапки бери ТОЛЬКО ИЗ СЧЁТА-ФАКТУРЫ (там ИНН/КПП продавца и графы НДС). "
    "Позиции ТОРГ-12 в lines НЕ добавляй — иначе они задвоятся. Итог ТОРГ-12 «всего с НДС» положи в crossCheckTotal. "
    "Исключение — basis: его бери из строки «Основание» ТОРГ-12 (номер и дата — в клетках справа).\n" + _UPD
)

TEXT_LAYER_LIMIT = 60_000  # characters of a text layer sent; ~10 pages of a 1С УПД


def item_types_text(types: Sequence[str]) -> str:
    return "; ".join(f"«{item}»" for item in types if str(item).strip()) or "—"


def text_lines(text: str) -> str:
    """A text layer as the model gets it: spaces squeezed, one line per baseline kept (a table row is a line)."""
    return "\n".join(" ".join(line.split()) for line in str(text or "").splitlines() if line.strip())


def prompt(tag: str, text_layer: str = "", *, types: Sequence[str] | None = None, page: bool = False) -> str:
    """The task for the file's tag ('M15' | 'TN' | '' — the model tells the kind). `text_layer` — the PDF's own text
    (an electronic document): it is read instead of pictures, characters exact. `types` — the stock's item types: the
    warehouse fields of a row are asked too. `page` — the model sees one page of a scan read page by page."""
    common = (_COMMON.replace("{items}", ", makerArticle, maker, itemType, shortName" if types is not None else "")
              .replace("{pages}", _ONE_PAGE if page else _ALL_PAGES))
    if types is not None:
        common += _ITEMS.replace("{types}", item_types_text(types))
    if tag == "M15":
        task = common + _M15
    elif tag == "TN" and not page:
        task = common + _TN
    else:
        task = (
            common + _KINDS + "Правила по видам.\nUPD, INVOICE, CORRECTION — " + _UPD
            + "TORG12_INVOICE — позиции и шапка только из счёта-фактуры по шаблону УПД выше, позиции ТОРГ-12 не "
            "добавляй; итог ТОРГ-12 «всего с НДС» в crossCheckTotal; basis — из строки «Основание» ТОРГ-12.\n"
            + ("Если на странице ТОРГ-12 из пары «ТОРГ-12 + счёт-фактура» — docKind = TORG12 и её позиции как есть: "
               "пары собираются при сборке страниц.\n" if page else "")
            + "TORG12 — " + _TORG12 + "ACT — " + _ACT + "M15 — " + _M15
        )
    text = text_lines(text_layer)
    if text:
        task += (
            "\nДокумент электронный: ниже его текстовый слой (изображений нет). Символы в нём точные — наименования, "
            "артикулы, ИНН, номера и суммы переписывай из него; строка текста — строка документа, графы таблицы идут "
            "в ней по порядку.\n<<<ТЕКСТ PDF\n" + text[:TEXT_LAYER_LIMIT] + "\nТЕКСТ PDF>>>\n"
        )
    return task
