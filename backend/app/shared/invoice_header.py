"""Header of a supplier's invoice («Счёт на оплату») read from the PDF's own text layer.

Port of Roller_Buyer `apps/api/src/domain/invoice-parse.ts` (`parseInvoiceText`, Витя's reader of the Kaiten invoices,
ported 26.10.2026): number and date, the seller's name and INN, the total from the amount in words, VAT or «без НДС».
1С and most sellers produce text PDFs, so no OCR is needed. Pure functions; every field is None when not found, never
guessed. Money is kopecks, dates ISO.

Left in Roller_Buyer: `parsePaymentFileName` (ERP has `purchasing.doc_refs.parse_payment_file_name`) and `fileKind`
(Kaiten file names — ERP's uploads are invoices by the button they came through).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class InvoiceHeader:
    number: str | None = None
    date: str | None = None  # ISO
    seller_name: str | None = None
    seller_inn: str | None = None
    total: int | None = None  # kopecks
    vat: int | None = None  # kopecks
    vat_free: bool = False


_MONTHS = {
    "января": "01", "февраля": "02", "марта": "03", "апреля": "04", "мая": "05", "июня": "06",
    "июля": "07", "августа": "08", "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
}


def ru_date_to_iso(text: str) -> str | None:
    """«15.09.2025», «15 сентября 2025 г.», «15.09.25» → ISO; None when not a date."""
    t = text.strip().lower()
    # Four-digit year first: «03.08.2026» must not read as «03.08.20».
    m = re.match(r"([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4}|[0-9]{2})(?![0-9])", t)
    if m:
        year = f"20{m.group(3)}" if len(m.group(3)) == 2 else m.group(3)
        return f"{year}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    m = re.match(r"([0-9]{1,2})\s+([а-я]+)\s+([0-9]{4})", t)
    if m and m.group(2) in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(2)]}-{m.group(1).zfill(2)}"
    return None


def money_to_kop(text: str) -> int | None:
    """«4 815,00», «4815.00», «4 815» → kopecks."""
    t = re.sub(r"\s", "", text).replace(",", ".", 1)
    if not re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?", t):
        return None
    return int((Decimal(t) * 100).to_integral_value(ROUND_HALF_UP))


# A money amount WITH kopecks — a bare integer next to a label is too often a line number.
MONEY_CENTS = r"([0-9]{1,3}(?:\s[0-9]{3})*[.,][0-9]{2}|[0-9]+[.,][0-9]{2})"

_TITLE = re.compile(
    r"сч[её]т(?:\s*-\s*(?:договор|оферта)|\s+на\s+оплату)?\s*(?:№\s*)?([A-ZА-ЯЁ0-9][A-Za-z0-9_А-ЯЁа-яё\-/ ]{0,30}?)"
    r"\s+от\s+([0-9]{1,2}(?:\.[0-9]{1,2}\.[0-9]{2,4}|\s+[а-яё]+\s+[0-9]{4}))",
    re.I,
)


def parse_invoice_title(text: str) -> tuple[str | None, str | None]:
    """«Счет № 43851 от 15.09.2025», «Счёт на оплату № УТ-1714 от 17 сентября 2025 г.»,
    «СЧЕТ - ДОГОВОР № 57148683 от 29.01.2026», «Счет ПП25-062156 от 19.11.2025» (no №) → (number, ISO date)."""
    m = _TITLE.search(text)
    if not m:
        return None, None
    return m.group(1).strip(), ru_date_to_iso(m.group(2))


# ───────────── Amount in words: every Russian invoice carries it ─────────────

_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100, "двести": 200, "триста": 300,
    "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}
_SCALES = ((re.compile(r"^миллиард"), 1_000_000_000), (re.compile(r"^миллион"), 1_000_000), (re.compile(r"^тысяч"), 1000))


def words_to_number(words: list[str]) -> int | None:
    """«Двадцать шесть тысяч триста тридцать» → 26330; None when a word is not a number word."""
    total = 0
    group = 0
    seen = False
    for raw in words:
        w = raw.lower()
        if w in _UNITS:
            group += _UNITS[w]
            seen = True
            continue
        scale = next((value for pattern, value in _SCALES if pattern.search(w)), None)
        if scale is None:
            return None
        total += (group or 1) * scale
        group = 0
        seen = True
    return total + group if seen else None


_IN_WORDS = re.compile(r"((?:[А-ЯЁа-яё]+\s+){1,12})рубл[а-яё]*\.?\s*([0-9]{1,2})\s*коп")


def amount_in_words(text: str) -> int | None:
    """«… Двадцать шесть тысяч триста тридцать рублей 40 копеек» → kopecks. Several invoices (ДАРТ, Деловые Линии,
    Резонит) also spell the VAT in words, after the total — so the LARGEST amount in words wins: the total is never
    smaller than its VAT."""
    found: int | None = None
    for m in _IN_WORDS.finditer(text):
        words = m.group(1).strip().split()
        # Walk back from «рублей» while the words are number words.
        start = len(words)
        while start > 0 and words_to_number([words[start - 1]]) is not None:
            start -= 1
        rub = words_to_number(words[start:])
        if rub is not None:
            found = max(found or 0, rub * 100 + int(m.group(2)))
    return found


_FORMS = (
    r"ООО|АО|ЗАО|ПАО|ОАО|НАО|ТОО|ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ"
    r"|(?:НЕПУБЛИЧНОЕ |ЗАКРЫТОЕ |ПУБЛИЧНОЕ )?АКЦИОНЕРНОЕ ОБЩЕСТВО"
)
ORG = rf"(?:{_FORMS})\s*(?:ТД\s*|НПП\s*|НПФ\s*|ПКФ\s*)?[«\"„“]\s*[^«»\"„“”]{{1,60}}?\s*[»\"”“]"
IP = r"(?:ИП|Индивидуальный предприниматель)\s+[А-ЯЁ][А-ЯЁа-яё-]+\s+[А-ЯЁ][А-ЯЁа-яё-]+(?:\s+[А-ЯЁ][А-ЯЁа-яё-]+)?"

_SHORT_FORMS = (
    (re.compile(r"^общество с ограниченной ответственностью", re.I), "ООО"),
    (re.compile(r"^непубличное акционерное общество", re.I), "НАО"),
    (re.compile(r"^закрытое акционерное общество", re.I), "ЗАО"),
    (re.compile(r"^публичное акционерное общество", re.I), "ПАО"),
    (re.compile(r"^акционерное общество", re.I), "АО"),
)


def tidy_org(name: str) -> str:
    """Tidy «ЗАО " МПО Электромонтаж "» → «ЗАО "МПО Электромонтаж"»; full legal forms shortened."""
    n = re.sub(r"\s+", " ", name)
    n = re.sub(r"([«\"„“])\s+", r"\1", n)
    n = re.sub(r"\s+([»\"”“])", r"\1", n).strip()
    for pattern, short in _SHORT_FORMS:
        n = pattern.sub(short, n, count=1)
    # «ООО"АКомпани"» → «ООО "АКомпани"».
    return re.sub(r"^(ООО|АО|ЗАО|ПАО|ОАО|НАО|ТОО)(?=[«\"„“])", r"\1 ", n, count=1)


# Banks and the buyer itself show up as organisations too — they are never the seller.
_NOT_SELLER = re.compile(r"банк|фраксис", re.I)

# Buyers of the group — never the seller.
OWN_INNS = frozenset({"5040191181"})


def seller_name_near(text: str, inn: str | None) -> str | None:
    """The seller's name: next to its INN (with «ИНН», «ИНН/КПП» or a bare «7729108750 / 770201001»), after
    «Поставщик / Продавец», an individual entrepreneur by name, or the first organisation in the text that is neither
    a bank nor the buyer."""
    if inn:
        near = re.search(rf"({ORG}|{IP})[\s,]*(?:ИНН(?:\s*/\s*КПП)?\s*:?\s*)?{inn}", text, re.I)
        if near and not _NOT_SELLER.search(near.group(1)):
            return tidy_org(near.group(1))
    after = re.search(rf"(?:Поставщик|Продавец|Исполнитель)(?:\s*\([^)]*\))?\s*:?\s*({ORG}|{IP})", text, re.I)
    if after and not _NOT_SELLER.search(after.group(1)):
        return tidy_org(after.group(1))
    if inn and len(inn) == 12:
        ip = re.search(IP, text)
        if ip:
            return tidy_org(ip.group(0))
    for m in re.finditer(ORG, text, re.I):
        if not _NOT_SELLER.search(m.group(0)):
            return tidy_org(m.group(0))
    return None


def inn_pair_after_org(text: str) -> tuple[str, str] | None:
    """Table layouts (ЧИП и ДИП) print «АО "ЧИП и ДИП" 7729108750 / 770201001» without the word ИНН → (name, inn)."""
    for m in re.finditer(rf"({ORG})\s*([0-9]{{12}}|[0-9]{{10}})\s*/\s*[0-9]{{9}}", text, re.I):
        if m.group(2) not in OWN_INNS and not _NOT_SELLER.search(m.group(1)):
            return tidy_org(m.group(1)), m.group(2)
    return None


_SELLER_BLOCK = re.compile(r"Поставщик(?:\s*\([^)]*\))?\s*:\s*(.{3,200}?)ИНН\s*:?\s*([0-9]{12}|[0-9]{10})(?![0-9])", re.I)
_ANY_INN = re.compile(r"ИНН\s*:?\s*([0-9]{12}|[0-9]{10})(?![0-9])", re.I)
_RECEIVER = re.compile(r"Сч\.\s*№\s*[0-9]{20}\s+(.{3,120}?)\s+Получатель", re.I)
_NAMED = re.compile(r"[«\"„“]|Индивидуальный|^ИП ", re.I)
_LABELLED = (
    r"Всего к оплате", r"Итого подлежит оплате", r"Итого к оплате", r"Итого включая налоги",
    r"Всего с НДС(?:\s*\(руб\.?\))?",
)
_VAT = re.compile(rf"(?:в том числе\s+|сумма\s+)?НДС(?:\s*\(?[0-9]{{1,2}}\s*%\)?)?\s*:?\s*{MONEY_CENTS}", re.I)
_VAT_FREE = re.compile(r"(?:Без\s+(?:налога\s*\(?)?НДС|НДС\s+не\s+облагается)", re.I)


def parse_invoice_text(text: str) -> InvoiceHeader:
    flat = re.sub(r"\s+", " ", text)
    number, day = parse_invoice_title(flat)

    # Seller: the «Поставщик (Исполнитель):» block of the 1С form, else the first foreign INN.
    seller_name: str | None = None
    seller_inn: str | None = None
    block = _SELLER_BLOCK.search(flat)
    if block and block.group(2) not in OWN_INNS:
        seller_name = re.sub(r"[,\s]+$", "", block.group(1)).strip()
        seller_inn = block.group(2)
    else:
        # Twelve digits first: an entrepreneur's INN must not be cut to its first ten.
        seller_inn = next((m.group(1) for m in _ANY_INN.finditer(flat) if m.group(1) not in OWN_INNS), None)
        # The bank block of 1С ends with «<Seller> Получатель».
        receiver = _RECEIVER.search(flat)
        if receiver:
            seller_name = receiver.group(1).strip()
    if not seller_inn:
        pair = inn_pair_after_org(flat)
        if pair:
            seller_name, seller_inn = pair

    if not seller_name or not _NAMED.search(seller_name):
        seller_name = seller_name_near(flat, seller_inn) or seller_name
    # A block may carry the address after the name («ООО "ДАРТ ХОЛДИНГ", 111024, г. Москва…») or the bank before it:
    # the organisation itself is what we keep.
    org = re.search(f"{ORG}|{IP}", seller_name, re.I) if seller_name else None
    if org and not _NOT_SELLER.search(org.group(0)):
        seller_name = org.group(0)
    if seller_name:
        seller_name = tidy_org(seller_name)
    # The amount in words is the one field every invoice has; labelled totals are the fallback.
    labelled = next(
        (m.group(1) for m in (re.search(rf"{label}\s*:?\s*{MONEY_CENTS}", flat, re.I) for label in _LABELLED) if m),
        None,
    )
    total = amount_in_words(flat)
    if total is None and labelled is not None:
        total = money_to_kop(labelled)
    vat_match = _VAT.search(flat)
    vat_amount = money_to_kop(vat_match.group(1)) if vat_match else None
    # «Без НДС» only counts when no VAT amount is printed: column headers say «Цена без НДС» too.
    vat_free = vat_amount is None and bool(_VAT_FREE.search(flat))
    # A «VAT» as large as half the total is the total itself caught by a loose label — drop it.
    if vat_free:
        vat: int | None = 0
    elif vat_amount is not None and total is not None and vat_amount * 2 > total:
        vat = None
    else:
        vat = vat_amount
    return InvoiceHeader(number, day, seller_name, seller_inn, total, vat, vat_free)
