"""Item rows of a paper УПД read by OCR (owner 23.10.2026, first real scan: «не считал артикул из наименования, не
считал кол-во в наименовании и не умножил на кол-во бухт — нужно прям поработать»).

Works on the plain text tesseract gives (`receipt_ocr`, psm 6), not on its word boxes: the same text comes from a PDF
text layer too, and an item row of a УПД is recognisable by its own shape — `[код] № наименование … ОКЕИ ед. кол-во
цена …` — so the columns are found by that shape, not by pixel positions that change with every form and scan.

* A row: an optional supplier code (column «Код товара», `00-00042353`), the row number, the start of the name, the
  OKEI unit code (796, 006, …) with the unit word, the quantity («3,000»). OCR noise around them is skipped: «|», «!»,
  «‚», «}», «--» of the empty «Код вида товара» column.
* The name wraps onto the next lines; they are glued until the next row, a blank line or the end of the table.
  What other columns leave on those lines is dropped: «— акциза» / «|| акциза 923» (the «без акциза» column, the end
  of a customs number), a customs number «10620010/220426/5063», lone OCR marks.
* The pack the name says in words — «(в рулоне 100 м)», «в бухте 305 м», «упаковка 6000 шт», «уп. 100 шт»,
  «100 шт/уп», «##6000» — turns invoiced pieces into what lies on the shelf: 3 шт × 100 м = 300 м. An invoice already
  in the pack's unit (305 м of a cable «в бухте 305 м») is not multiplied. A size form «9мм*16м» is no pack (owner
  26.10: tape stays in rolls / шт, «Тр-I090EW» 2 шт stays 2 шт): it only keeps the roll length out of the article.
* The article is the maker / series code of the name («МТ-2К», «Тр-I090EW») with OCR fixes (W read as «\\М/» or
  «\\\\/», I as 1 when the digits after it spell the tape width, Cyrillic look-alikes in a Latin part, O / l between
  digits); a series code (fewer than 3 digits) takes the size and colour of the name: «МТ-2К 6,4/3,2 белая».
  `snap_articles` then replaces it with a known article of the same key (stock, purchasing, reference DB).

Pure: no I/O."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from .word_search import article_key

SOURCE = "скан УПД"
_SPACES = re.compile("[\xa0 -​  　]")
_DASHES = re.compile("[‐-―−]")

# OKEI codes of the units a shop delivery comes in → the unit the warehouse writes.
OKEI = {
    "796": "шт", "657": "шт", "006": "м", "018": "м", "055": "м2", "778": "упак", "736": "рул", "166": "кг",
    "163": "г", "839": "компл", "704": "набор", "715": "пар", "112": "л", "625": "лист",
}
_UNIT_WORDS = (
    ("шт", "шт"), ("упак", "упак"), ("уп", "упак"), ("рул", "рул"), ("бух", "бух"), ("компл", "компл"),
    ("к-т", "компл"), ("набор", "набор"), ("кг", "кг"), ("пар", "пар"), ("лист", "лист"), ("м2", "м2"),
    ("пог", "м"), ("м", "м"), ("г", "г"), ("л", "л"),
)
_UNIT = r"(?P<unit>шт|упак|уп|рул|бух|компл|к-т|набор|кг|пар|лист|пог\.?\s?м|м2|м|г|л)\.?(?![А-Яа-яЁё0-9])"
# 1С prints the quantity with three decimals («3,000», «1 000,000»); a bare integer is taken only when that fails.
_QTY = r"(?P<qty>\d{1,3}(?:\s\d{3})*,\d{3}|\d+(?:[,.]\d{1,3})?)(?=[\s|!\]}]|$)"
_HEAD = r"^\s*(?:(?P<code>[0-9A-ZА-ЯЁ][0-9A-ZА-ЯЁ./_-]*\d[0-9A-ZА-ЯЁ./_-]*)\s+)?(?P<no>\d{1,3})(?!\d)\s*[|!\]\[]?\s*"
_ROW = re.compile(
    _HEAD + r"(?P<name>\S.*?)[\s|!\]\-]+(?P<okei>" + "|".join(OKEI) + r")(?=[\s|!\]])[\s|!\]]*(?:" + _UNIT
    + r")?[\s|!\]‚,'`’]*" + _QTY
)
# A form without the OKEI column: the unit word and a quantity in 1С's «3,000» shape.
_ROW_NO_OKEI = re.compile(
    _HEAD + r"(?P<name>\S.*?)[\s|!\]\-]+" + _UNIT + r"[\s|!\]‚,'`’]+(?P<qty>\d{1,3}(?:\s\d{3})*,\d{3})(?=[\s|!\]}]|$)"
)
_TABLE_END = re.compile(
    r"Всего\s+к\s+оплате|Итого|Товар\s*\(груз\)|Основание\s+передачи|Данные\s+о\s+транспорт|Руководитель|"
    r"Главный\s+бухгалтер|Индивидуальный\s+предприниматель|Наименование\s+товара|Код\s+товара|Универсальный|"
    r"Счет-фактура|Счёт-фактура|Продавец|Покупатель",
    re.I,
)
_MAX_WRAPPED = 6
# Other columns on a wrapped name line: «- акциза», «- i акциза», «|| акциза 923» and all after it; a customs number.
_EXCISE = re.compile(r"\s*(?:[|!]+\s*)?(?:-+\s*)?(?:(?<!\S)[A-Za-z|]\s+)?(?:б[еe]з\s*)?акциз.*$", re.I)
_CUSTOMS = re.compile(r"\s*\d{8}/\d{6}/\d{3,}.*$")
_EDGES = re.compile(r"^[\s|!_~:;,.\-—]+|[\s|!_~:;,\-—]+$")
_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")

_NUM = r"\d+(?:[.,]\d+)?"
_PACKS = (
    # «(в рулоне 100 м)», «в бухте 305 м», «(рулон 50 м)», «катушка 500 м»
    (re.compile(
        r"\(?\s*(?:в\s+)?(?P<kind>рулон|бухт|катушк|бобин|барабан)[а-яё]*\s*(?:по\s*)?(?P<n>" + _NUM + r")\s*"
        r"(?P<u>м|шт|кг)\.?(?![а-яёa-z])\s*\)?", re.I), None),
    # «9мм*16м», «12мм x 16м», «9мм16м» (the «*» lost by OCR): the length after the width, a roll
    (re.compile(r"(?P<w>" + _NUM + r")\s*мм\s*[*xх×X]?\s*(?P<n>" + _NUM + r")\s*(?P<u>м)(?![а-яёa-z])", re.I), "рул."),
    # «упаковка 6000 шт», «упак. по 100 шт», «уп. 100 шт»
    (re.compile(r"(?:упаковк[аеиу]|упак\.?|уп\.?)\s*(?:по\s*)?(?P<n>\d[\d ]*?)\s*(?P<u>шт|м)\.?(?![а-яё])", re.I),
     "уп."),
    # «(100 шт/уп)»
    (re.compile(r"(?P<n>\d[\d ]*?)\s*(?P<u>шт|м)\.?\s*/\s*уп", re.I), "уп."),
    # «##6000»
    (re.compile(r"##\s*(?P<n>\d+)(?P<u>)"), "уп."),
)
_KINDS = {"рулон": "рул.", "бухт": "бух.", "катушк": "кат.", "бобин": "боб.", "барабан": "бар."}
_PACKAGED = {"шт", "упак", "рул", "бух", "компл", "набор"}

# W read as «\М/», «\\/», «\/\/», «VV» right after a letter or digit of a code.
_W = re.compile(r"(?<=[0-9A-Za-zА-Яа-яЁё])(?:\\\\/|\\[МMNИ]/|\\/\\/|VV)")
_TOKEN = re.compile(r"[^\s,;«»\"()]+")
_UNIT_SUFFIX = re.compile(r"(?<=\d)(?:мм|см|м|шт|кг|г|mm|cm|m)")
_SIZE_ONLY = re.compile(r"[\d,./*xх×X²]+")
_TO_LATIN = str.maketrans("АВЕКМНОРСТХУаеорсху", "ABEKMHOPCTXYaeopcxy")
_SIZE = re.compile(r"(?<![\d,.])(\d+(?:[,.]\d+)?(?:\s*[/xх×*]\s*\d+(?:[,.]\d+)?)+)\s*(?:мм|mm)?(?![а-яёa-z])", re.I)
_ONE_SIZE = re.compile(r"(?<![\d,./])(\d+(?:[,.]\d+)?)\s*(?:мм|mm)(?![а-яёa-z])", re.I)
_WIDTH = re.compile(r"(\d+(?:[,.]\d+)?)\s*мм", re.I)
_COLOUR = re.compile(
    r"^\s*,?\s*((?:бел|ч[её]рн|красн|син|зел[её]н|ж[её]лт|прозрачн|сер|оранжев|коричнев|фиолетов|голуб|розов)"
    r"[а-яё]*)", re.I,
)


@dataclass(frozen=True)
class UpdLine:
    """One item row: what to put on stock. `quantity` / `unit` — after the pack; `packs` × `pack` — how it came
    («3» × «100 м», `pack_kind` «рул.»); the invoiced figures stay in `invoice_quantity` / `invoice_unit`."""

    article: str
    name: str
    quantity: Decimal | None  # None — the quantity could not be read (the row stays, the storekeeper types it)
    unit: str
    invoice_quantity: Decimal | None
    invoice_unit: str
    packs: Decimal | None = None
    pack: str = ""
    pack_kind: str = ""
    supplier_code: str = ""
    source: str = SOURCE
    colour: str = ""  # the colour word the article took from the name — the snap tries the article without it too
    number: str = ""  # «№ п/п» of the row (the page merge tells a repeated sheet from a new row by it)


def number_text(value: Decimal | None) -> str:
    """300.000 → «300», 2.5 → «2.5»; None → «»."""
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return text


def _number(text: str) -> Decimal | None:
    try:
        value = Decimal(re.sub(r"\s", "", text).replace(",", "."))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _unit(word: str | None, okei: str | None) -> str:
    word = (word or "").casefold()
    for stem, unit in _UNIT_WORDS:
        if word.startswith(stem):
            return unit
    return OKEI.get(okei or "", "шт")


def unit_of(text: str | None) -> str:
    """The unit a model read off the УПД («шт», «м», «упак.», «796») as the warehouse writes it; an unknown word stays
    as printed, nothing — «шт»."""
    word = " ".join(str(text or "").split()).strip(" .")
    if not word:
        return "шт"
    if word in OKEI:
        return OKEI[word]
    folded = word.casefold()
    return next((unit for stem, unit in _UNIT_WORDS if folded.startswith(stem)), word[:16])


def _row(line: str):
    return _ROW.match(line) or _ROW_NO_OKEI.match(line)


def _wrapped_part(line: str) -> str:
    """A wrapped name line without what the other columns leave on it."""
    line = _CUSTOMS.sub("", _EXCISE.sub("", line))
    return _EDGES.sub("", line)


def _glue(parts: list[str]) -> str:
    name = ""
    for part in parts:
        if not part:
            continue
        if name.endswith("-") and part[:1].islower() and name[-2:-1].isalpha():
            name = name[:-1] + part  # «термоусажи-» + «ваемая»
        else:
            name = f"{name} {part}" if name else part
    name = re.sub(r"\s+", " ", name)
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", name)).strip()


@dataclass(frozen=True)
class _Pack:
    size: Decimal
    unit: str
    kind: str
    explicit: bool  # said in words («упаковка», «в рулоне») rather than read off a width × length
    span: tuple[int, int]  # what of the name is the pack (the length only for a width × length: the width is a size)


def find_pack(name: str) -> _Pack | None:
    """The pack the name names: «(в рулоне 100 м)» → 100 м, рул.; «9мм*16м» → 16 м; «##6000» → 6000 шт."""
    for pattern, kind in _PACKS:
        found = pattern.search(name)
        if found is None:
            continue
        size = _number(found["n"])
        if not size or size <= 0:
            continue
        unit = (found["u"] or "шт").casefold()
        if kind is None:
            stem = next(stem for stem in _KINDS if found["kind"].casefold().startswith(stem))
            kind = _KINDS[stem]
        tape = "w" in pattern.groupindex
        span = (found.start("n"), found.end()) if tape else found.span()
        return _Pack(size=size, unit=unit, kind=kind, explicit=not tape, span=span)
    return None


def _apply_pack(quantity: Decimal | None, unit: str, pack: _Pack | None) -> tuple[Decimal | None, str, Decimal | None]:
    """(quantity, unit, packs) on the shelf. Pieces / rolls / packs of a pack in another unit are multiplied; pieces of
    a pack in pieces only when fewer were invoiced than one pack holds (2 шт «упаковка 6000 шт» → 12000 шт, 150 шт «уп.
    100 шт» stay 150); metres of a pack in metres stay, their count of packs shown when whole."""
    if pack is None or quantity is None:
        return quantity, unit, None
    if unit in _PACKAGED and (pack.unit != unit or (unit == "шт" and pack.explicit and quantity < pack.size)):
        return quantity * pack.size, pack.unit, quantity
    if pack.unit == unit and quantity % pack.size == 0:
        return quantity, unit, quantity / pack.size
    return quantity, unit, None


def _is_size(token: str) -> bool:
    return bool(_SIZE_ONLY.fullmatch(_UNIT_SUFFIX.sub("", token)))


def _is_code(token: str) -> bool:
    if token.isdigit():
        return len(token) >= 6  # Molex 0430250600
    if len(token) < 2 or _is_size(token):
        return False
    digits = sum(char.isdigit() for char in token)
    letters = _LETTER.findall(token)
    if digits and letters:
        return True
    head, separator, tail = token.partition("-")
    return bool(separator and any(c.isupper() for c in head) and any(c.isupper() for c in tail))


def _score(token: str) -> int:
    return (2 * ("-" in token) + bool(_LETTER.match(token)) + any(c.isupper() for c in token)
            + (len(token) >= 4))


def _fix_code(token: str, name: str) -> str:
    """OCR fixes inside a code: Cyrillic look-alikes in a Latin part, O / l / I between digits, and a leading «1» that
    is the letter I when the digits after it spell the width of the name («Тр-1090EW … 9мм» → «Тр-I090EW»)."""
    widths = {width for raw in _WIDTH.findall(name) if (width := _number(raw)) is not None}
    parts = []
    for part in token.split("-"):
        if re.search(r"[A-Za-z]", part):
            part = part.translate(_TO_LATIN)
        part = re.sub(r"(?<=\d)[OoОо](?=\d)", "0", part)
        part = re.sub(r"(?<=\d)[lI|](?=\d)", "1", part)
        lead = re.match(r"1(\d{3})(?=[A-Z])", part)
        if parts and lead and Decimal(lead[1]) / 10 in widths:
            part = "I" + part[1:]
        parts.append(part)
    return "-".join(parts)


def _size_and_colour(name: str, after: int) -> tuple[str, str]:
    for pattern in (_SIZE, _ONE_SIZE):
        found = pattern.search(name, after) or pattern.search(name)
        if found:
            size = re.sub(r"\s+", "", found[1])
            colour = _COLOUR.match(name[found.end():])
            return size, (colour[1].casefold() if colour else "")
    return "", ""


def name_article(name: str) -> tuple[str, str, str]:
    """(the article read from the name, the name with that code fixed, the colour the article took or «»)."""
    name = _W.sub("W", name)
    tokens = [(match.group().strip("|!.:"), match.start()) for match in _TOKEN.finditer(name)]
    codes = [(token, start) for token, start in tokens if _is_code(token)]
    if not codes:
        return "", name, ""
    token, start = max(codes, key=lambda pair: (_score(pair[0]), -pair[1]))
    fixed = _fix_code(token, name)
    name = name[:start] + name[start:].replace(token, fixed, 1)
    if sum(char.isdigit() for char in fixed) >= 3:
        return fixed, name, ""
    pack = find_pack(name)
    sized = name if pack is None else name[:pack.span[0]] + " " * (pack.span[1] - pack.span[0]) + name[pack.span[1]:]
    size, colour = _size_and_colour(sized, start + len(fixed))
    return " ".join(part for part in (fixed, size, colour) if part), name, colour


def parse_upd_lines(text: str) -> list[UpdLine]:
    """The item rows of a УПД's recognised text, in their order; no rows found → []."""
    lines = _DASHES.sub("-", _SPACES.sub(" ", str(text or ""))).splitlines()
    found: list[UpdLine] = []
    index = 0
    while index < len(lines):
        row = _row(lines[index])
        index += 1
        if row is None:
            continue
        quantity = _number(row["qty"])
        if quantity is None or quantity <= 0:
            continue
        parts = [_EDGES.sub("", row["name"])]
        for _ in range(_MAX_WRAPPED):
            if index >= len(lines):
                break
            line = lines[index]
            if not line.strip() or _row(line) or _TABLE_END.search(line):
                break
            index += 1
            if _LETTER.search(line):
                parts.append(_wrapped_part(line))
        found.append(item_line(
            _glue(parts), quantity, _unit(row["unit"], row.groupdict().get("okei")), supplier_code=row["code"] or "",
            number=row["no"],
        ))
    return found


def item_line(name: str, quantity: Decimal | None, unit: str, *, supplier_code: str = "", number: str = "") -> UpdLine:
    """One item row from its printed name, invoiced quantity and unit (OCR rows and a model's rows alike): the
    article read off the name, the pack said in words applied (`find_pack`; a size form «9мм*16м» is not a pack)."""
    article, name, colour = name_article(" ".join(str(name or "").split()))
    pack = find_pack(name)
    if pack is not None and not pack.explicit:
        pack = None
    total, total_unit, packs = _apply_pack(quantity, unit, pack)
    return UpdLine(
        article=article, name=name, quantity=total, unit=total_unit, invoice_quantity=quantity, invoice_unit=unit,
        packs=packs, pack=f"{number_text(pack.size)} {pack.unit}" if pack else "", pack_kind=pack.kind if pack else "",
        supplier_code=str(supplier_code or "").strip()[:64], colour=colour, number=str(number or "").strip()[:16],
    )


def quantity_of(text: str | None) -> Decimal | None:
    """«3», «3.000», «1 000,000» → the number; unreadable or not above zero — None."""
    value = _number(str(text or "")) if str(text or "").strip() else None
    return value if value is not None and value > 0 else None


def snap_key(text: str) -> str:
    """The article key OCR cannot tell apart: separators, case, alphabet (word_search.article_key), I / l = 1, O = 0
    next to a digit."""
    key = re.sub(r"[il|]", "1", article_key(text))
    return re.sub(r"(?<=\d)[оo]|[оo](?=\d)", "0", key)


def snap_articles(lines: list[UpdLine], known: Iterable[tuple[str, str]]) -> list[UpdLine]:
    """Each line's article replaced by a known one of the same `snap_key` — `known` is (article, where from) in
    priority order, the first of a key wins. A size-family article («МТ-2К 6,4/3,2 белая») is also tried without its
    colour. `source` names where the article came from."""
    keyed: dict[str, tuple[str, str]] = {}
    for article, origin in known:
        key = snap_key(article)
        if key and key not in keyed:
            keyed[key] = (str(article).strip(), origin)
    if not keyed:
        return lines
    snapped = []
    for line in lines:
        tries = [line.article]
        if line.colour and line.article.endswith(line.colour):
            tries.append(line.article[: -len(line.colour)].strip())
        hit = next((keyed[key] for text in tries if text and (key := snap_key(text)) in keyed), None)
        snapped.append(line if hit is None else replace(line, article=hit[0], source=f"{SOURCE}, артикул из {hit[1]}"))
    return snapped


def line_answer(line: UpdLine) -> dict:
    """The line as «Отсканировать УПД» answers it."""
    return {
        "article": line.article, "name": line.name, "quantity": number_text(line.quantity), "unit": line.unit,
        "packs": number_text(line.packs), "pack": line.pack, "pack_kind": line.pack_kind,
        "supplier_code": line.supplier_code, "source": line.source,
    }
