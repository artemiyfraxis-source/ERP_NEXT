"""Prices from a supplier document: the items table of a Russian invoice («Счёт на оплату») or offer (КП).

Owner, 05.10: the buyer drops the КП or the invoice on the supplier order and «Цена факт» is read from it instead of
typed. Pure rules, no SUPPLYMAP counterpart. Reads the PDF text layer (`app.shared.pdf_text`), the first sheet of an
.xlsx/.xlsm and a .csv (the «Внести КП» readers in `kp.py`) and the tables of a .docx. A scan, a photo or an old .xls
gives no items and a hint; the buyer then types the prices as before.

A PDF text layer has no columns: cells of one baseline come joined by single spaces, so «1 250,00» can be one number
or two cells. A row is therefore read by arithmetic — the quantity, the unit and the money numbers whose
КОЛ-ВО × ЦЕНА = СУММА — and the name is what is left. Whether the prices include НДС comes from the headers
(«Цена с НДС», «без НДС»), the totals («в т.ч. НДС», «НДС сверху», «Всего к оплате») and the row arithmetic.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from xml.etree import ElementTree

from app.shared.pdf_text import PdfLimitExceeded, pdf_text

from .invoice_file import normalize_invoice_text, parse_invoice_text
from .kp import KpFileError, _csv_grid, _xlsx_grid
from .money import parse_number_loose

RUB = "RUB"
DEFAULT_VAT = Decimal("22")
KNOWN_RATES = (Decimal("22"), Decimal("20"), Decimal("10"), Decimal("18"), Decimal("12"), Decimal("7"), Decimal("5"))
HEADER_SCAN_ROWS = 40
_MAX_SEGMENTATIONS = 64
_MAX_ROW_TOKENS = 14

UNREADABLE = "Текст из файла не читается (скан или картинка) — цены впишите вручную."
NO_TABLE = "Не нашёл в файле таблицу «Наименование | Кол-во | Цена | Сумма» — цены впишите вручную."
XLS = "Старый формат .xls не читается — сохраните файл как .xlsx или PDF."
UNKNOWN_FORMAT = "Формат файла не читается — нужен PDF с текстом, .xlsx, .csv или .docx."
TOO_HEAVY = "Файл слишком тяжёлый для распознавания (превышен лимит обработки) — цены впишите вручную."


@dataclass(frozen=True)
class DocumentItem:
    """One row of the document's items table; prices per unit, `sum` with НДС."""

    name: str
    article: str = ""
    quantity: Decimal | None = None
    unit: str = ""
    price: Decimal | None = None
    price_with_vat: Decimal | None = None
    sum: Decimal | None = None
    vat_rate: Decimal | None = None
    source: str = ""


@dataclass(frozen=True)
class DocumentTotals:
    sum: Decimal | None = None
    vat: Decimal | None = None
    sum_with_vat: Decimal | None = None


@dataclass(frozen=True)
class SupplierDocument:
    items: tuple[DocumentItem, ...] = ()
    totals: DocumentTotals = DocumentTotals()
    currency: str = RUB
    vat_mode: str = ""  # «included» (prices with НДС), «on_top» (НДС added), «none» (no НДС)
    vat_rate: Decimal | None = None
    number: str = ""
    date: str = ""  # «dd.mm.yyyy»
    hint: str = ""


@dataclass
class _Raw:
    """A row as read, before НДС is resolved: `price`/`sum` of a column whose kind is not known."""

    name: str
    article: str = ""
    quantity: Decimal | None = None
    unit: str = ""
    price: Decimal | None = None
    sum: Decimal | None = None
    price_net: Decimal | None = None
    price_gross: Decimal | None = None
    sum_net: Decimal | None = None
    sum_gross: Decimal | None = None
    vat_rate: Decimal | None = None
    source: str = ""


@dataclass
class _Totals:
    total: Decimal | None = None
    total_net: Decimal | None = None
    total_gross: Decimal | None = None
    payable: Decimal | None = None
    vat: Decimal | None = None
    vat_included: Decimal | None = None
    on_top: bool = False
    no_vat: bool = False


@dataclass
class _Layout:
    """What the table header says."""

    text: str = ""
    unit_first: bool = False
    article: str = ""  # «before» / «after» the name column, or ''
    gross: bool = False
    net: bool = False
    columns: dict[str, int] = field(default_factory=dict)


# --- reading the file ---------------------------------------------------------------------------------------------


def read_supplier_document(file_name: str, content: bytes, kind: str = "invoice") -> SupplierDocument:
    """Items, totals, currency, № and date of the uploaded document; never raises on a bad file — a hint instead."""
    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    if content[:4] == b"\xd0\xcf\x11\xe0" or suffix == "xls":
        return SupplierDocument(hint=XLS)
    if content.startswith(b"%PDF") or b"%PDF" in content[:1024]:
        try:
            text = pdf_text(content)
        except PdfLimitExceeded:
            return SupplierDocument(hint=TOO_HEAVY)
        if not text.strip():
            return SupplierDocument(hint=UNREADABLE)
        return parse_document_text(text, kind)
    if content[:2] == b"PK":
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
            names = set(archive.namelist())
        except zipfile.BadZipFile:
            return SupplierDocument(hint=UNKNOWN_FORMAT)
        if "word/document.xml" in names:
            grid = _docx_grid(archive)
        else:
            try:
                grid = _xlsx_grid(content)
            except KpFileError:
                return SupplierDocument(hint="Файл не читается как таблица .xlsx.")
        return parse_document_grid(grid, kind)
    if suffix in {"csv", "txt"}:
        return parse_document_grid(_csv_grid(content), kind)
    return SupplierDocument(hint=UNKNOWN_FORMAT)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_grid(archive: zipfile.ZipFile) -> list[list[str]]:
    """Rows of every table, then the paragraphs outside them (number, date and totals live there)."""
    try:
        info = archive.getinfo("word/document.xml")
        if info.file_size > 32 * 1024 * 1024:
            return []
        root = ElementTree.fromstring(archive.read(info))
    except (KeyError, ElementTree.ParseError):
        return []
    body = root.find(f"{_W}body")
    if body is None:
        return []

    def text(node: ElementTree.Element) -> str:
        paragraphs = ["".join(t.text or "" for t in p.iter(f"{_W}t")) for p in node.iter(f"{_W}p")]
        return " ".join(part for part in paragraphs if part.strip())

    grid: list[list[str]] = []
    for node in body:
        if node.tag == f"{_W}tbl":
            for row in node.iter(f"{_W}tr"):
                grid.append([text(cell) for cell in row.findall(f"{_W}tc")])
        elif node.tag == f"{_W}p":
            line = text(node)
            if line.strip():
                grid.append([line])
    return grid


# --- numbers ------------------------------------------------------------------------------------------------------

_NUMBER_TOKEN = re.compile(r"^\d+(?:[.,]\d+)?$")
_LEAD = re.compile(r"^[1-9]\d{0,2}$")
_GROUP = re.compile(r"^\d{3}(?:[.,]\d+)?$")
_PERCENT = re.compile(r"^(\d{1,2}(?:[.,]\d+)?)%$")
_CURRENCY_TAIL = re.compile(r"(?:руб\.?|р\.|₽)$", re.I)
_DOTTED = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d+$")


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None


def _join_value(tokens: list[str]) -> Decimal | None:
    """«1» «250,00» → 1250.00: a leading 1–3 digits, then groups of exactly three, only the last with decimals."""
    if len(tokens) == 1:
        token = tokens[0]
        whole = re.split(r"[.,]", token)[0]
        if len(whole) > 1 and whole.startswith("0"):
            return None
        return _decimal(token)
    if not _LEAD.match(tokens[0]):
        return None
    for index, token in enumerate(tokens[1:], start=1):
        if not _GROUP.match(token) or (index < len(tokens) - 1 and not token.isdigit()):
            return None
    return _decimal("".join(tokens))


def _segmentations(tokens: list[str]) -> list[list[Decimal]]:
    """Every way the space-split digits can be read as numbers (thousands groups), at most 64."""
    out: list[list[Decimal]] = []

    def walk(start: int, acc: list[Decimal]) -> None:
        if len(out) >= _MAX_SEGMENTATIONS:
            return
        if start == len(tokens):
            out.append(acc)
            return
        for end in range(start, min(len(tokens), start + 5)):
            value = _join_value(tokens[start:end + 1])
            if value is None:
                if end > start:
                    break
                return
            walk(end + 1, acc + [value])

    if tokens:
        walk(0, [])
    return out


def _round_like(value: Decimal, like: Decimal | None = None) -> Decimal:
    """Kopecks, or up to four places when the price it comes from is written finer (0,4583 ₽ per piece)."""
    places = 2
    if like is not None:
        exponent = like.normalize().as_tuple().exponent
        places = max(2, min(4, -exponent if isinstance(exponent, int) else 2))
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _close(a: Decimal | None, b: Decimal | None, quantity: Decimal = Decimal(1)) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= max(Decimal("0.011"), abs(quantity) * Decimal("0.0051"), abs(b) * Decimal("0.0002"))


def _snap_rate(value: Decimal) -> Decimal | None:
    for rate in KNOWN_RATES + (Decimal(0),):
        if abs(value - rate) <= Decimal("0.3"):
            return rate
    return None


# --- tokens of a PDF row ------------------------------------------------------------------------------------------

UNITS = {
    "шт", "штук", "штука", "штуки", "м", "метр", "метра", "метров", "мп", "м.п", "пм", "пог.м", "км", "кг", "г", "гр",
    "т", "тн", "л", "мл", "уп", "упак", "упаковка", "упаковки", "компл", "комплект", "к-т", "кмп", "комп", "набор",
    "пар", "пара", "рул", "рулон", "бухта", "бух", "лист", "м2", "м²", "кв.м", "м3", "м³", "ед", "усл", "усл.ед",
    "услуга", "час", "ч", "кат", "катушка", "бобина", "пач", "пачка", "фл", "флакон", "бан", "банка", "тюб", "ролик",
    "pcs", "pc", "ea", "m", "kg",
}
_UNIT_ALIASES = {
    "штук": "шт", "штука": "шт", "штуки": "шт", "pcs": "шт", "pc": "шт", "ea": "шт",
    "метр": "м", "метра": "м", "метров": "м", "мп": "м", "м.п": "м", "пм": "м", "пог.м": "м", "m": "м",
    "упак": "уп", "упаковка": "уп", "упаковки": "уп",
    "комплект": "компл", "к-т": "компл", "кмп": "компл", "комп": "компл",
    "рулон": "рул", "бух": "бухта", "кв.м": "м2", "м²": "м2", "м³": "м3", "kg": "кг", "гр": "г", "тн": "т",
}


def unit_key(value: str | None) -> str:
    """«шт.», «штук», «pcs» → «шт»: one key for units written differently."""
    text = str(value or "").strip().lower().replace("ё", "е").strip("()[],;:").rstrip(".")
    return _UNIT_ALIASES.get(text, text)


def _clean(token: str) -> str:
    return token.strip("()[],;:«»\"'")


@dataclass(frozen=True)
class _Token:
    raw: str
    kind: str  # num / pct / novat / dash / unit / word
    value: str = ""


def _tokens(text: str) -> list[_Token]:
    words = text.split()
    out: list[_Token] = []
    index = 0
    while index < len(words):
        raw = words[index]
        word = _clean(raw)
        low = word.lower().replace("ё", "е")
        number = _CURRENCY_TAIL.sub("", word)
        if low == "без" and index + 1 < len(words) and _clean(words[index + 1]).lower().startswith("ндс"):
            out.append(_Token(raw + " " + words[index + 1], "novat"))
            index += 2
            continue
        if _DOTTED.match(number):
            number = number.replace(".", "")  # «1.220,00»
        if _PERCENT.match(word):
            out.append(_Token(raw, "pct", _PERCENT.match(word).group(1)))
        elif word == "%" and out and out[-1].kind == "num":
            out[-1] = _Token(out[-1].raw + " %", "pct", out[-1].value)
        elif number and _NUMBER_TOKEN.match(number):
            out.append(_Token(raw, "num", number))
        elif word in {"-", "—", "–"}:
            out.append(_Token(raw, "dash"))
        elif low.rstrip(".") in UNITS or low in UNITS:
            out.append(_Token(raw, "unit", word))
        else:
            out.append(_Token(raw, "word"))
        index += 1
    return out


_RUN = {"num", "pct", "novat", "dash"}


def _run_end(tokens: list[_Token], start: int) -> int:
    end = start
    while end < len(tokens) and tokens[end].kind in _RUN:
        end += 1
    return end


def _run_parts(tokens: list[_Token]) -> tuple[list[str], Decimal | None]:
    numbers = [token.value for token in tokens if token.kind == "num"]
    rate = None
    for token in tokens:
        if token.kind == "pct":
            rate = _decimal(token.value)
        elif token.kind == "novat":
            rate = Decimal(0)
    return numbers, rate


@dataclass
class _Reading:
    score: float
    raw: _Raw
    used: set[int]


def _assign(quantity: Decimal, values: list[Decimal], rate: Decimal | None) -> tuple[float, _Raw] | None:
    """Roles of the money numbers after the quantity: the pair whose quantity × price = sum, then НДС columns."""
    if quantity <= 0 or not values:
        return None
    raw = _Raw(name="", quantity=quantity, vat_rate=rate)
    last = len(values) - 1
    pairs = [
        (i, j) for i in range(len(values)) for j in range(i + 1, len(values))
        if values[i] > 0 and _close(quantity * values[i], values[j], quantity)
    ]
    if not pairs:
        raw.price = values[0]
        return (3.0 if len(values) == 1 else 1.0), raw
    pairs.sort(key=lambda pair: (pair[1] != last, pair[0] != 0, -pair[1]))
    i, j = pairs[0]
    score = 10.0 + (2 if j == last else 0) + (2 if i == 0 else 0)
    distinct = {values[b] for _, b in pairs}
    if len(distinct) >= 2:
        low = min(pairs, key=lambda pair: values[pair[1]])
        high = max(pairs, key=lambda pair: values[pair[1]])
        ratio = (values[high[1]] / values[low[1]] - 1) * 100
        snapped = _snap_rate(ratio)
        if snapped is not None and snapped > 0:
            raw.price_net, raw.sum_net = values[low[0]], values[low[1]]
            raw.price_gross, raw.sum_gross = values[high[0]], values[high[1]]
            raw.vat_rate = rate if rate is not None else snapped
            return score + 2, raw
    price, total = values[i], values[j]
    others = [value for index, value in enumerate(values) if index not in (i, j)]
    candidates = [rate] if rate is not None else list(KNOWN_RATES)
    for candidate in candidates:
        if candidate is None or candidate == 0:
            continue
        for other in others:
            if _close(other, total * (1 + candidate / 100), quantity):
                raw.price_net, raw.sum_net, raw.sum_gross, raw.vat_rate = price, total, other, candidate
                return score + 2, raw
            if _close(other, total * candidate / 100):
                raw.price_net, raw.sum_net, raw.vat_rate = price, total, candidate
                raw.sum_gross = total + other
                return score + 1, raw
            if _close(other, total * candidate / (100 + candidate)):
                raw.price_gross, raw.sum_gross, raw.vat_rate = price, total, candidate
                return score + 1, raw
    raw.price, raw.sum = price, total
    return score, raw


def _qty_options(tokens: list[_Token], unit: int) -> list[tuple[int, Decimal]]:
    """Quantity just before the unit: the last one, two or three digit tokens joined as thousands."""
    options = []
    for count in (1, 2, 3):
        start = unit - count
        if start < 0 or any(token.kind != "num" for token in tokens[start:unit]):
            break
        value = _join_value([token.value for token in tokens[start:unit]])
        if value is not None:
            options.append((start, value))
    return options


def _read_row(text: str, layout: _Layout) -> _Raw | None:
    tokens = _tokens(text)
    best: _Reading | None = None

    def consider(score: float, raw: _Raw, used: set[int]) -> None:
        nonlocal best
        if not any(index not in used for index, token in enumerate(tokens) if token.kind == "word"):
            return  # a row without a name is not an item
        if best is None or score > best.score:
            best = _Reading(score, raw, used)

    for unit, token in enumerate(tokens):
        if token.kind != "unit":
            continue
        end = _run_end(tokens, unit + 1)
        run = tokens[unit + 1:end]
        numbers, rate = _run_parts(run)
        if len(numbers) > _MAX_ROW_TOKENS:
            continue
        tail = 1 if end == len(tokens) else 0
        # «Кол-во | Ед. | Цена | Сумма»
        for start, quantity in _qty_options(tokens, unit):
            for values in _segmentations(numbers):
                assigned = _assign(quantity, values, rate)
                if assigned:
                    score, raw = assigned
                    raw.unit = token.value
                    consider(score + tail + (0 if layout.unit_first else 1), raw, set(range(start, end)))
        # «Ед. | Кол-во | Цена | Сумма»
        for values in _segmentations(numbers):
            if len(values) >= 2:
                assigned = _assign(values[0], values[1:], rate)
                if assigned:
                    score, raw = assigned
                    raw.unit = token.value
                    consider(score + tail + (1 if layout.unit_first else 0), raw, set(range(unit, end)))
    if best is None or best.score < 10:
        # No unit column: «Наименование | Кол-во | Цена | Сумма».
        index = 0
        while index < len(tokens):
            if tokens[index].kind not in _RUN:
                index += 1
                continue
            end = _run_end(tokens, index)
            numbers, rate = _run_parts(tokens[index:end])
            if 3 <= len(numbers) <= _MAX_ROW_TOKENS:
                for values in _segmentations(numbers):
                    if len(values) >= 3:
                        assigned = _assign(values[0], values[1:], rate)
                        if assigned and assigned[0] >= 10:
                            consider(assigned[0] - 1 + (1 if end == len(tokens) else 0), assigned[1], set(range(index, end)))
            index = end
    if best is None:
        return None
    raw = best.raw
    name_tokens = [token.raw for index, token in enumerate(tokens) if index not in best.used]
    raw.name, raw.article = _split_article(name_tokens, layout)
    raw.source = text.strip()
    return raw if raw.name else None


def is_code(token: str) -> bool:
    """A part number: digits with Latin letters (SHR-02V-S-B, M3x10) or six and more digits (510471000)."""
    word = _clean(token)
    if re.fullmatch(r"\d+[.,]\d+", word):
        return False
    digits = sum(char.isdigit() for char in word)
    if not digits:
        return False
    if re.search(r"[A-Za-z]", word):
        return len(re.sub(r"[\s\-./_]", "", word)) >= 4
    return digits >= 6


def _split_article(tokens: list[str], layout: _Layout) -> tuple[str, str]:
    words = [token for token in tokens if _clean(token)]
    article = ""
    if layout.article == "before" and len(words) > 1 and is_code(words[0]):
        article, words = _clean(words[0]), words[1:]
    elif layout.article == "after" and len(words) > 1 and is_code(words[-1]):
        article, words = _clean(words[-1]), words[:-1]
    else:
        article = next((_clean(word) for word in words if is_code(word)), "")
    name = " ".join(words).strip(" ,;:-–—")
    return name, article


# --- the table in text --------------------------------------------------------------------------------------------

_NAME_HEADERS = ("наименование", "товар", "номенклатура", "описание", "продукци", "работ")
_ROW_START = re.compile(r"^(\d{1,4})[.)]?(?:\s+(.*))?$")
_TABLE_END = re.compile(r"^(итого|всего|итог\b|в том числе|в т\.\s?ч|сумма ндс|без налога)", re.I)
_PAGE_LINE = re.compile(r"^(стр\.?|страница|лист)\s*\d", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()


def _is_header(line: str) -> bool:
    low = _norm(line)
    return any(key in low for key in _NAME_HEADERS) and any(key in low for key in ("кол", "цена", "сумма", "ед."))


def _layout(header: str) -> _Layout:
    low = _norm(header)
    layout = _Layout(text=low)
    qty = re.search(r"кол", low)
    unit = re.search(r"\bед\b|ед\.|единиц", low)
    layout.unit_first = bool(qty and unit and unit.start() < qty.start())
    article = re.search(r"артикул|\bкод\b|\bарт\.|каталожн|p/n|part", low)
    name = next((m for m in (re.search(key, low) for key in _NAME_HEADERS) if m), None)
    if article and name:
        layout.article = "before" if article.start() < name.start() else "after"
    layout.gross = bool(re.search(r"(цена|сумма|стоимость)[^|]{0,15}?(с ндс|с учетом ндс|включая ндс|вкл\.? ндс)", low))
    layout.net = bool(re.search(r"(цена|сумма|стоимость)[^|]{0,15}?без ндс", low))
    return layout


def parse_document_text(text: str, kind: str = "invoice") -> SupplierDocument:
    lines = [line.strip() for line in normalize_invoice_text(text).replace("­", "").split("\n") if line.strip()]
    header_at = next((index for index, line in enumerate(lines) if _is_header(line)), None)
    start = header_at + 1 if header_at is not None else 0
    header = [lines[header_at]] if header_at is not None else []
    rows: list[str] = []
    expected = 1
    current: list[str] | None = None
    table_end = len(lines)
    paused = False
    for index in range(start, len(lines)):
        line = lines[index]
        found = _ROW_START.match(line)
        rest = (found.group(2) or "").split() if found else []
        if found and rest and _clean(rest[0]).lower().rstrip(".") in UNITS:
            found = None  # «2 м, ГОСТ …» continues a name, it does not open row 2
        if found and int(found.group(1)) == expected:
            if current is not None:
                rows.append(" ".join(current))
            current = [found.group(2) or ""]
            expected += 1
            paused = False
            continue
        if _TABLE_END.match(_norm(line)) and expected > 1:
            if re.search(r"по (странице|листу)", _norm(line)):
                paused = True
                continue
            table_end = index
            break
        if current is None:
            if header_at is not None and expected == 1:
                header.append(line)
            continue
        if paused or _is_header(line) or _PAGE_LINE.match(line):
            paused = paused or _is_header(line)
            continue
        current.append(line)
    if current is not None:
        rows.append(" ".join(current))
    layout = _layout(" ".join(header))
    raws = [raw for raw in (_read_row(row, layout) for row in rows) if raw is not None]
    tail = lines[table_end:] if rows else lines
    return _finish(raws, layout, tail, "\n".join(lines), kind, found_table=bool(header) or bool(raws))


# --- the table in a grid (.xlsx / .csv / .docx) --------------------------------------------------------------------


def _column_kind(header: str) -> str | None:
    h = _norm(header)
    if not h:
        return None
    if h in {"№", "n", "no", "№ п/п", "п/п", "№п/п", "nn"}:
        return "row"
    if re.search(r"артикул|\bкод\b|\bарт\b|каталожн|p/n|part|партномер", h) and not re.search("наименование", h):
        return "article"
    if any(key in h for key in _NAME_HEADERS):
        return "name"
    if re.search(r"кол-?во|количество|\bкол\b", h):
        return "qty"
    if re.match(r"(ед|единиц)", h):
        return "unit"
    net = re.search(r"без ндс|без налога", h)
    gross = re.search(r"с ндс|с учетом ндс|включая ндс|с налогом", h)
    if "цена" in h:
        return "price_net" if net else "price_gross" if gross else "price"
    if "ставка" in h or ("ндс" in h and "%" in h and "сумма" not in h):
        return "vat_rate"
    if re.search(r"сумма|стоимость|всего|итого", h):
        if net:
            return "sum_net"
        if gross:
            return "sum_gross"
        if "ндс" in h or "налог" in h:
            return "vat_sum"
        return "sum"
    if "ндс" in h:
        return "vat"
    return None


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row) or row[index] is None:
        return ""
    return normalize_invoice_text(str(row[index])).strip()


def _number_cells(row: list[str]) -> int:
    return sum(1 for cell in row if parse_number_loose(cell) is not None and re.search(r"\d", str(cell)))


def parse_document_grid(grid: list[list[str]], kind: str = "invoice") -> SupplierDocument:
    head = None
    columns: dict[str, int] = {}
    for index in range(min(len(grid), HEADER_SCAN_ROWS)):
        row = grid[index] or []
        kinds = {col: _column_kind(cell) for col, cell in enumerate(row)}
        if "name" in kinds.values() and ({"qty", "price", "price_net", "price_gross", "sum"} & set(kinds.values())):
            head = index
            following = [str(cell or "") for cell in (grid[index + 1] if index + 1 < len(grid) else [])]
            merged = list(row)
            # A two-row header («Цена» over «с НДС»): the row below has words, not the numbers of an item.
            if any(cell.strip() for cell in following) and _number_cells(following) < 2:
                merged = [
                    f"{_cell(row, col)} {_cell(following, col)}".strip() for col in range(max(len(row), len(following)))
                ]
                head += 1
            for col, cell in enumerate(merged):
                found = _column_kind(cell)
                if found and found not in columns:
                    columns[found] = col
            break
    lines = [" ".join(cell for cell in map(str, row) if cell and str(cell).strip()) for row in grid]
    if head is None:
        text = "\n".join(lines)
        return _finish([], _Layout(text=""), lines, text, kind, found_table=False)
    header_text = " ".join(_cell(grid[i], col) for i in range(max(0, head - 1), head + 1) for col in range(len(grid[i])))
    layout = _layout(header_text)
    layout.columns = columns
    raws: list[_Raw] = []
    end = len(grid)
    for index in range(head + 1, len(grid)):
        row = [str(cell) if cell is not None else "" for cell in grid[index] or []]
        if any(_TABLE_END.match(_norm(cell)) for cell in row if cell):
            end = index
            break
        if row and all(re.fullmatch(r"\d{1,2}", cell.strip()) for cell in row if cell.strip()) and _cell(
            row, columns.get("name")
        ).isdigit():
            continue  # the «1 2 3 4 5» column-number row of 1С forms
        raw = _grid_row(row, columns)
        if raw is not None:
            raws.append(raw)
    return _finish(raws, layout, lines[end:], "\n".join(lines), kind, found_table=True)


def _grid_number(row: list[str], columns: dict[str, int], key: str) -> Decimal | None:
    text = _cell(row, columns.get(key))
    if not re.search(r"\d", text):
        return None
    return parse_number_loose(text)


def _grid_row(row: list[str], columns: dict[str, int]) -> _Raw | None:
    name = _cell(row, columns.get("name"))
    if not name:
        return None
    raw = _Raw(name=re.sub(r"\s+", " ", name), source=" | ".join(cell.strip() for cell in row if cell.strip()))
    raw.article = _cell(row, columns.get("article")) or next((_clean(w) for w in name.split() if is_code(w)), "")
    raw.quantity = _grid_number(row, columns, "qty")
    raw.unit = _cell(row, columns.get("unit"))
    for key in ("price", "price_net", "price_gross", "sum", "sum_net", "sum_gross"):
        setattr(raw, key, _grid_number(row, columns, key))
    rate_text = _cell(row, columns.get("vat_rate")) or _cell(row, columns.get("vat"))
    if rate_text:
        if "без" in rate_text.lower():
            raw.vat_rate = Decimal(0)
        else:
            value = parse_number_loose(rate_text)
            if value is not None and ("%" in rate_text or ("vat_rate" in columns and value <= 30)):
                raw.vat_rate = value
            elif value is not None and "vat" in columns and raw.sum is not None and raw.sum_gross is None:
                # A plain «НДС» column with amounts: «Сумма» beside it is without НДС when it adds up.
                raw.sum_net = raw.sum
                raw.sum_gross = raw.sum + value
                raw.sum = None
    if raw.quantity is None and raw.price is None and raw.price_net is None and raw.price_gross is None:
        return None
    if raw.price is None and raw.price_net is None and raw.price_gross is None and raw.quantity:
        if raw.sum is not None:
            raw.price = _round_like(raw.sum / raw.quantity, Decimal("0.0001"))
    return raw


# --- totals, НДС, currency ----------------------------------------------------------------------------------------


def _line_amount(line: str) -> Decimal | None:
    """The last amount of a totals line, thousands groups joined («Итого: 2 562,00» → 2562.00)."""
    words = [_CURRENCY_TAIL.sub("", _clean(word)) for word in line.split()]
    index = len(words) - 1
    while index >= 0 and not _NUMBER_TOKEN.match(words[index] or "x"):
        index -= 1
    if index < 0 or _PERCENT.match(words[index]):
        return None
    start = index
    while start > 0:
        current, previous = words[start], words[start - 1]
        if not _GROUP.match(current) or not re.fullmatch(r"\d{1,3}", previous or ""):
            break
        if previous in {"0", "10", "18", "20", "22"} and start > 1 and "ндс" in words[start - 2].lower():
            break  # «НДС 20 462,00»: the rate, then the amount
        start -= 1
    value = _join_value(words[start:index + 1])
    return value if value is not None else _decimal(words[index])


def _totals(lines: list[str]) -> _Totals:
    totals = _Totals()
    for line in lines:
        low = re.sub(r"[\s.]+", " ", _norm(line))
        if "наименован" in low:
            continue
        amount = _line_amount(line)
        if "не облагается" in low or "без налога" in low and "ндс" in low and not re.search(r"итог|всего|сумма", low):
            totals.no_vat = True
            continue
        if "ндс" in low or "налог" in low:
            if re.search(r"без (ндс|налога)", low):
                if amount is not None and re.search(r"итог|всего|сумма|стоимость", low):
                    totals.total_net = totals.total_net or amount
                elif amount is None:
                    totals.no_vat = True
            elif re.search(r"\bв т ?ч\b|в том числе|включая", low):
                if amount is not None:
                    totals.vat_included = totals.vat_included or amount
            elif re.search(r"(итог|всего|сумма)[^0-9]*(с ндс|с учетом ндс)|к оплате", low) and amount is not None:
                totals.total_gross = totals.total_gross or amount
            elif amount is not None:
                totals.vat = totals.vat or amount
                totals.on_top = totals.on_top or "сверху" in low
        elif "к оплате" in low and amount is not None:
            totals.payable = totals.payable or amount
        elif re.match(r"^(итого|всего|итог)", low) and amount is not None:
            totals.total = totals.total or amount
    return totals


_RATE_IN_TEXT = re.compile(r"ндс[^0-9\n%]{0,15}?(\d{1,2}(?:[.,]\d+)?)\s*%", re.I)
_FOREIGN = (
    ("USD", re.compile(r"\busd\b|\$|долл", re.I)),
    ("EUR", re.compile(r"\beur\b|€|\bевро\b", re.I)),
    ("CNY", re.compile(r"\bcny\b|\brmb\b|юан|¥", re.I)),
    ("у.е.", re.compile(r"\bу\.\s?е\.", re.I)),
)
_RUB_MARK = re.compile(r"руб|₽|\brub\b", re.I)


def document_currency(scope: str, text: str) -> str:
    """RUB unless the header, totals or currency lines name another currency (and no roubles), or a $ € ¥ sign."""
    for code, pattern in _FOREIGN:
        if pattern.search(scope) and not _RUB_MARK.search(scope):
            return code
    for code, sign in (("USD", "$"), ("EUR", "€"), ("CNY", "¥")):
        if sign in text:
            return code
    return RUB


def _kp_number(text: str) -> str:
    found = re.search(
        r"(?:предложени[ея]|\bКП\b)[^\n]{0,30}?(?:№|N[°º]?(?=\s*\d))\s*([0-9A-Za-zА-Яа-я/-]+)", text, re.I,
    )
    return re.sub(r"[.,;]+$", "", found.group(1)) if found else ""


def _finish(
    raws: list[_Raw], layout: _Layout, tail: list[str], text: str, kind: str, *, found_table: bool,
) -> SupplierDocument:
    totals = _totals(tail)
    low = _norm(text)
    rate_match = _RATE_IN_TEXT.search(text)
    rate = _snap_rate(_decimal(rate_match.group(1))) if rate_match else None
    row_rates = {raw.vat_rate for raw in raws if raw.vat_rate is not None}
    no_vat = totals.no_vat or "не облагается" in low or (row_rates == {Decimal(0)})
    if rate is None and len(row_rates - {Decimal(0)}) == 1:
        rate = next(iter(row_rates - {Decimal(0)}))
    if no_vat and not (totals.vat or totals.vat_included):
        mode, rate = "none", Decimal(0)
    else:
        rate = rate if rate is not None else DEFAULT_VAT
        mode = _vat_mode(raws, layout, totals, low, rate)
    items = tuple(_resolve(raw, mode, rate) for raw in raws)
    info = parse_invoice_text(text)
    number = info.number if kind == "invoice" else (_kp_number(text) or info.number)
    scope = "\n".join([layout.text, *tail, *(line for line in text.split("\n") if re.search(r"валют|курс", line, re.I))])
    hint = "" if items else NO_TABLE
    return SupplierDocument(
        items=items,
        totals=_document_totals(items, totals, mode, rate),
        currency=document_currency(scope, text),
        vat_mode=mode,
        vat_rate=rate,
        number=number,
        date=info.date,
        hint=hint,
    )


def _vat_mode(raws: list[_Raw], layout: _Layout, totals: _Totals, low: str, rate: Decimal) -> str:
    if layout.net and not layout.gross:
        return "on_top"
    if layout.gross and not layout.net:
        return "included"
    unknown = [raw for raw in raws if raw.price is not None or raw.sum is not None]
    sigma = sum(
        (raw.sum if raw.sum is not None else (raw.price or 0) * (raw.quantity or 0) for raw in unknown), Decimal(0),
    )
    if sigma:
        if _close(sigma, totals.total_net) and not _close(sigma, totals.total_gross):
            return "on_top"
        if _close(sigma, totals.total_gross) or _close(sigma, totals.payable):
            return "included"
        if _close(sigma * (1 + rate / 100), totals.total_gross) or _close(sigma * (1 + rate / 100), totals.payable):
            return "on_top"
        if totals.vat_included is not None:
            return "included"
        if totals.vat is not None and _close(sigma, totals.total):
            return "on_top" if totals.on_top or totals.payable or totals.total_gross else "included"
    if totals.vat_included is not None or re.search(r"в т\.\s?ч\.?\s?ндс|в том числе ндс|включая ндс", low):
        return "included"
    if totals.on_top or "сверху" in low:
        return "on_top"
    return "included"


def _resolve(raw: _Raw, mode: str, doc_rate: Decimal) -> DocumentItem:
    rate = raw.vat_rate if raw.vat_rate is not None else (Decimal(0) if mode == "none" else doc_rate)
    multiplier = 1 + rate / 100
    quantity = raw.quantity
    net, gross = raw.price_net, raw.price_gross
    sum_net, sum_gross = raw.sum_net, raw.sum_gross
    if net is None and gross is None and raw.price is not None:
        if mode == "on_top":
            net = raw.price
        else:
            gross = raw.price
    if sum_net is None and sum_gross is None and raw.sum is not None:
        if mode == "on_top":
            sum_net = raw.sum
        else:
            sum_gross = raw.sum
    if net is None and gross is None and quantity:
        if sum_gross is not None:
            gross = _round_like(sum_gross / quantity, Decimal("0.0001"))
        elif sum_net is not None:
            net = _round_like(sum_net / quantity, Decimal("0.0001"))
    if gross is None and net is not None:
        gross = _round_like(net * multiplier, net)
    if net is None and gross is not None:
        net = _round_like(gross / multiplier, gross)
    if sum_gross is None:
        if sum_net is not None:
            sum_gross = _round_like(sum_net * multiplier)
        elif gross is not None and quantity is not None:
            sum_gross = _round_like(gross * quantity)
    return DocumentItem(
        name=raw.name,
        article=raw.article,
        quantity=quantity,
        unit=raw.unit,
        price=net.normalize() if net is not None else None,
        price_with_vat=gross.normalize() if gross is not None else None,
        sum=sum_gross,
        vat_rate=rate,
        source=raw.source,
    )


def _document_totals(items: tuple[DocumentItem, ...], totals: _Totals, mode: str, rate: Decimal) -> DocumentTotals:
    items_gross = sum((item.sum for item in items if item.sum is not None), Decimal(0)) if items else None
    gross = totals.total_gross or totals.payable or (totals.total if mode != "on_top" else None)
    if gross is None and totals.total_net is not None and totals.vat is not None:
        gross = totals.total_net + totals.vat
    if gross is None and mode == "on_top" and totals.total is not None and totals.vat is not None:
        gross = totals.total + totals.vat
    gross = gross if gross is not None else items_gross
    if mode == "none":
        return DocumentTotals(sum=gross, vat=Decimal("0.00") if gross is not None else None, sum_with_vat=gross)
    vat = totals.vat_included if totals.vat_included is not None else totals.vat
    if vat is None and gross is not None:
        vat = _round_like(gross * rate / (100 + rate))
    net = totals.total_net or (totals.total if mode == "on_top" else None)
    if net is None and gross is not None and vat is not None:
        net = gross - vat
    return DocumentTotals(sum=net, vat=vat, sum_with_vat=gross)
