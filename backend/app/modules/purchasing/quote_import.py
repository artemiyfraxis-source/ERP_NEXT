"""«КП из PDF и текста» (stage option procurement.quote_import): the text of a supplier's quote — pasted, or the text
layer of a PDF — becomes proposed quote lines mapped to the request's lines. Nothing is saved here: the buyer reviews
the form and saves it through `rfq.add_quote`.

Port of Roller_Buyer `packages/shared/src/quote-import.ts` (`applyPacks`, matching) and `apps/api/src/quote-import.ts`.
Roller read PDFs with Google; ERP has no such service. A PDF is read laid out (pypdf, a table row per line, columns as
runs of spaces), and a row is found by its arithmetic: quantity × price ≈ sum, whatever columns stand around them
(14.10.2026, nine real quotes: every row read, sums equal the documents' totals). A row with no sum falls back to the
fixed shape «наименование кол-во ед. цена»; what fits neither is returned as unread, not guessed.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from pypdf import PdfReader
from sqlalchemy import or_, select

from app.shared.identity import Actor
from app.shared.pdf_text import PdfLimitExceeded, pdf_text
from app.workflows.article_key import code_key

from . import items as items_repo
from . import rfq as rfq_service
from .infrastructure import ItemAliasRow, RfqLineRow, RfqSupplierRow, SupplierRow
from .upd_sellers import OWN_INNS

log = logging.getLogger(__name__)

PDF_LIMIT = 20 * 1024 * 1024
LAYOUT_PAGES = 10  # a quote is a page or two; a 300-page catalogue is not read to the end
MAX_PDF_TEXT = 400_000
Refused = rfq_service.Refused

# ── packs: «1 уп × 8000 шт» is 8000 pieces, not one ─────────────────────────────────────────────────────────────

_PACK_WORDS = re.compile(
    r"(?:^|[^а-яa-z])(уп(?:ак(?:овк[аиу])?)?|упк|pack|pkg|reel|tray|tube|бобин[аы]?|катушк[аи]|рулон|бухт[аы]|коробк[аи]|кор)"
    r"\.?(?=$|[^а-яa-z])", re.I)
_END = r"\.?(?=$|[^а-яa-z])"
_COUNT = r"(\d[\d\s]*(?:[.,]\d+)?)\s*"
_UNIT_WORDS = {
    "шт": re.compile(_COUNT + r"(?:шт|штук|pcs|pc|ea)" + _END, re.I),
    "м": re.compile(_COUNT + r"(?:м|метр(?:ов|а)?|m)" + _END, re.I),
    "кг": re.compile(_COUNT + r"(?:кг|kg)" + _END, re.I),
    "л": re.compile(_COUNT + r"(?:л|литр(?:ов|а)?|l)" + _END, re.I),
}
# «уп. 8000», «упаковка по 8000», «reel 4000» — a number right after the pack word.
_AFTER_PACK = re.compile(
    r"(?:уп(?:ак(?:овк[аиу])?)?|pack|reel|бобин[аы]?|катушк[аи]|бухт[аы])\.?\s*(?:по\s*)?[x×/]?\s*(\d[\d\s]*)", re.I)
_SAME_UNIT = {
    "шт": re.compile(r"^(?:шт|штук[аи]?|pcs|pc|ea)\.?$", re.I),
    "м": re.compile(r"^(?:м|метр(?:ов|а)?|m)\.?$", re.I),
    "кг": re.compile(r"^(?:кг|kg)\.?$", re.I),
    "л": re.compile(r"^(?:л|литр(?:ов|а)?|l)\.?$", re.I),
}


@dataclass
class ImportedLine:
    source_name: str
    source_article: str = ""
    qty: Decimal | None = None
    unit: str = ""
    price: Decimal | None = None  # rubles per unit as written in the quote (per unit asked after `apply_packs`)
    vat_rate: Decimal | None = None
    lead_days: int | None = None
    in_stock: bool = False
    declined: bool = False
    analog: bool = False
    analog_note: str = ""
    rfq_line_id: UUID | None = None
    matched_by: str | None = None
    pack: Decimal | None = None  # pack size in the requested unit when the quote priced a pack
    moq: Decimal | None = None  # the smallest quantity the supplier sells, in the requested unit
    pack_note: str = ""  # what `apply_packs` did or could not do, for the buyer to check


@dataclass(frozen=True)
class Target:
    rfq_line_id: UUID
    name: str
    article: str | None
    quantity: Decimal
    unit: str  # normalised: шт / м / кг / л / уп / компл, or as typed
    item_id: UUID | None = None


def _size_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _pack_size(text: str, unit: str) -> Decimal | None:
    by_unit = _UNIT_WORDS.get(unit)
    found = by_unit.search(text) if by_unit else None
    after = _AFTER_PACK.search(text)
    raw = (found.group(1) if found else None) or (after.group(1) if after else None)
    if not raw:
        return None
    try:
        size = Decimal(re.sub(r"\s", "", raw).replace(",", "."))
    except ArithmeticError:
        return None
    return size if size > 1 else None


def apply_packs(lines: Sequence[ImportedLine], targets: Sequence[Target]) -> list[ImportedLine]:
    """A quote priced per pack («1 уп», the price of the pack, «8000 шт» in the name or the unit) is turned into the
    requested unit: price per unit = pack price / pack size, pack = moq = pack size. A pack of unknown size leaves the
    price empty (a pack price shown as a piece price is the error the owner hit in Roller, 12.10.2026). A quote for
    more than requested in the same unit becomes the minimum batch. Lines not matched to a request line, declined or
    without a price stay as they are."""
    by_id = {target.rfq_line_id: target for target in targets}
    result: list[ImportedLine] = []
    for line in lines:
        target = by_id.get(line.rfq_line_id) if line.rfq_line_id else None
        if target is None or line.declined or line.price is None:
            result.append(line)
            continue
        unit_text = line.unit.strip()
        short = target.unit
        if target.unit != "уп" and _PACK_WORDS.search(f" {unit_text} "):
            size = _pack_size(f"{unit_text} {line.source_name} {line.source_article}", target.unit)
            if size is None:
                line.price = None
                line.pack_note = (f"КП за упаковку («{unit_text}»), размер упаковки не указан — "
                                  f"впишите упаковку и цену за {short}")
            else:
                line.price = (line.price / size).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                line.pack = line.moq = size
                line.pack_note = (f"КП за упаковку {_size_text(size)} {short}: цена пересчитана за {short}, "
                                  f"упаковка и мин. партия — {_size_text(size)}")
        elif (line.qty is not None and line.qty > target.quantity and target.unit in _SAME_UNIT
              and _SAME_UNIT[target.unit].match(unit_text)):
            line.moq = line.qty
            line.pack_note = (f"КП на {_size_text(line.qty)} {short} при запросе {_size_text(target.quantity)} — "
                              f"записано как минимальная партия")
        result.append(line)
    return result


# ── reading the text ────────────────────────────────────────────────────────────────────────────────────────────

_NUM = r"(?:\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_UNIT_TOKEN = (
    r"(?:шт|штук[аи]?|pcs|pc|ea|м|метр(?:ов|а)?|m|кг|kg|л|литр(?:ов|а)?|l|уп(?:ак(?:овк[аиу])?)?|упк|pack|"
    r"компл(?:ект)?|set|бобин[аы]?|катушк[аи]|рулон|бухт[аы]|reel)"
)
_MONEY = r"(?:\s*(?:руб\.?|₽|р\.))?"
_LINE = re.compile(
    rf"^\s*(?:\d{{1,3}}[.)]?\s+)?(?P<name>.+?)\s+(?P<qty>{_NUM})\s*(?P<unit>{_UNIT_TOKEN})\.?\s+"
    rf"(?P<price>{_NUM}){_MONEY}(?:\s+(?P<total>{_NUM}){_MONEY})?(?P<tail>.*)$", re.I)
_LEAD_WORD = re.compile(r"^\s*(?:%|дн|дней|день|нед|мес|раб|week|month)", re.I)
_TAIL_VAT = re.compile(r"(\d{1,2}(?:[.,]\d+)?)\s*%")
_PRICE_HINT = re.compile(r"\d[.,]\d{2}\b")


def _number(text: str) -> Decimal | None:
    return rfq_service.parse_ru_number(text)


def _regex_line(raw: str) -> ImportedLine | None:
    """The fixed shape «наименование кол-во ед. цена [сумма] [срок]» — for a line with no sum to check against."""
    match = _LINE.match(raw)
    price = _number(match.group("price")) if match else None
    qty = _number(match.group("qty")) if match else None
    if match is None or price is None or price <= 0 or qty is None or qty <= 0:
        return None
    tail = match.group("tail") or ""
    total = match.group("total")
    if total and _LEAD_WORD.match(tail):  # «305,00 9 дн», «8000 22%»: not the sum
        tail = f"{total}{tail}"
    days, in_stock, declined = rfq_service.parse_lead(tail)
    vat = _TAIL_VAT.search(tail)
    name = match.group("name").strip(" -–—;,")
    return ImportedLine(
        source_name=name, qty=qty, unit=match.group("unit"), price=price, lead_days=days, in_stock=in_stock,
        declined=declined, analog=bool(re.search(r"аналог", name, re.I)),
        vat_rate=Decimal(vat.group(1).replace(",", ".")) if vat and Decimal(vat.group(1).replace(",", ".")) < 100 else None,
    )


# A row of a quote table is found by its arithmetic, not by the order of its columns: somewhere in it stand a quantity,
# a unit price and their sum (kol × price ≈ sum, the price rounded to kopecks). Suppliers add columns of their own
# (срок, УАМ, ГОСТ, скидка, код), leave the unit out, write «2 788,81» with a space — the check finds the three anyway.
# Columns of a PDF table come from the layout text as runs of 2+ spaces («cells»); a thousands group never crosses one.
_ATOM_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
_ATOM_GROUP = re.compile(r"^\d{3}(?:[.,]\d+)?$")
_ATOM_UNIT = re.compile(rf"^{_UNIT_TOKEN}\.?$", re.I)
_ATOM_BETWEEN_PRICE_AND_SUM = re.compile(r"^(?:-?\d{1,2}(?:[.,]\d+)?%|руб\.?|₽|р\.|rub)$", re.I)
_YES_NO = re.compile(r"^(?:да|нет|yes|no)$", re.I)
_LEAD_CELL = re.compile(r"(\d+)\s*(?:-\s*\d+\s*)?(р\.?\s*д\.?|рд|раб[а-я.]*(?:\s*д[а-я.]*)?|дн[а-я.]*|дней|день|нед[а-я.]*|мес[а-я.]*)",
                        re.I)
_ROW_NUMBER = re.compile(r"^\d{1,3}[.)]?$")
_LETTERS = re.compile(r"[a-zа-яё]{2}", re.I)
_LATIN_CODE = re.compile(r"^[A-Z0-9][A-Z0-9\-./]*(?: [A-Z0-9\-./]+)?$")
_AT_ARTICLE = re.compile(r"(\S+?)(?:##\d+)?@([A-Za-z0-9]+)")
_PACK_IN_NAME = re.compile(r"(?:упаковк[аеи]|уп\.)\s*(?:по\s*)?\d[\d\s]*\s*(?:шт|м)\b", re.I)
_NOT_A_NAME = re.compile(r"^(?:итого|всего|в том числе|ндс|страница|цены|сумма|директор|конфиденциально|товары|"
                         r"наименование|№|без ндс|с ндс|оплат)", re.I)
_VAT_RATES = (Decimal(22), Decimal(20), Decimal(10))
_ENDS_WITH_MONEY = re.compile(r"\d[.,]\d{2}\s*(?:руб\.?|₽|р\.)?\s*$", re.I)


@dataclass(frozen=True)
class _Atom:
    text: str
    cell: int


@dataclass(frozen=True)
class _Num:
    start: int
    end: int  # inclusive
    value: Decimal


def _atoms(row: str) -> list[_Atom]:
    atoms: list[_Atom] = []
    for cell, part in enumerate(re.split(r"\t| {2,}", row)):
        atoms += [_Atom(word, cell) for word in part.split()]
    return atoms


def _whole_cells(atoms: list[_Atom]) -> dict[int, _Num]:
    """Cells that are one number and nothing else («1 783.61» between runs of spaces): read whole, never split."""
    cells: dict[int, list[int]] = {}
    for index, atom in enumerate(atoms):
        cells.setdefault(atom.cell, []).append(index)
    whole: dict[int, _Num] = {}
    for cell, indexes in cells.items():
        words = [atoms[i].text for i in indexes]
        if len(cells) > 1 and re.fullmatch(r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?", " ".join(words)):
            whole[cell] = _Num(indexes[0], indexes[-1], Decimal("".join(words).replace(",", ".")))
    return whole


def _numbers(atoms: list[_Atom]) -> list[_Num]:
    """Every number the atoms can spell, «2 788,81» both as 2, 788.81 and 2788.81 (inside one cell only); a cell of a
    laid-out table that is one number is that number only."""
    whole = _whole_cells(atoms)
    found: list[_Num] = list(whole.values())
    for start, atom in enumerate(atoms):
        if atom.cell in whole or not _ATOM_NUMBER.match(atom.text):
            continue
        text = atom.text
        found.append(_Num(start, start, Decimal(text.replace(",", "."))))
        end = start
        while (re.match(r"^\d{1,3}$", atoms[start].text) and not re.search(r"[.,]", text) and end + 1 < len(atoms)
               and atoms[end + 1].cell == atom.cell and _ATOM_GROUP.match(atoms[end + 1].text)):
            end += 1
            text += atoms[end].text
            found.append(_Num(start, end, Decimal(text.replace(",", "."))))
    return found


def _close(qty: Decimal, price: Decimal, total: Decimal) -> bool:
    return abs(qty * price - total) <= qty * Decimal("0.0051") + Decimal("0.02") or abs(qty * price - total) <= total * Decimal("0.001")


def _triple(atoms: list[_Atom]) -> tuple[_Num, _Num, _Num] | None:
    """(qty, price, sum) nearest to the row's end that multiply right; the price stands right before the sum (a
    discount % or «руб.» may sit between), the quantity anywhere before the price — the nearest one that fits."""
    numbers = _numbers(atoms)
    for total in sorted(numbers, key=lambda n: (-n.end, n.start)):
        if total.value <= 0:
            continue
        for price in sorted((n for n in numbers if n.end < total.start), key=lambda n: (-n.end, n.start)):
            between = atoms[price.end + 1:total.start]
            if not all(_ATOM_BETWEEN_PRICE_AND_SUM.match(atom.text) for atom in between):
                break
            if price.value <= 0:
                continue
            for qty in sorted((n for n in numbers if n.end < price.start), key=lambda n: (-n.end, n.start)):
                if qty.value > 0 and qty.value < 10_000_000 and _close(qty.value, price.value, total.value):
                    return qty, price, total
    return None


def _junk(atom: str) -> bool:
    return bool(_YES_NO.match(atom) or _ATOM_UNIT.match(atom) or re.match(r"^\d{6,}$", atom))


def _name_and_article(atoms: list[_Atom], qty: _Num) -> tuple[str, str]:
    """The first worded cell before the quantity is the name (layout text); in a pasted line with no cells — the words
    before the quantity without the row number, «Да/Нет», the lead time and the unit."""
    before = atoms[:qty.start]
    cells: dict[int, list[str]] = {}
    for atom in before:
        cells.setdefault(atom.cell, []).append(atom.text)
    name = ""
    article = ""
    if len(cells) > 1 or (cells and qty.start < len(atoms) and atoms[qty.start].cell not in cells):
        texts = [" ".join(words) for _, words in sorted(cells.items())]
        worded = [t for t in texts if _LETTERS.search(t) and not _LEAD_CELL.fullmatch(t.strip()) and not _YES_NO.match(t)]
        name = worded[0] if worded else ""
        article = next((t for t in texts if t != name and _LATIN_CODE.match(t) and not t.isdigit()), "")
    if not name:
        words = [atom.text for atom in before]
        while words:  # «… 0029110063@MOLEX Нет 1 рд» → «… 0029110063@MOLEX»; «Резистор 10» keeps its 10
            if len(words) >= 2 and _LEAD_CELL.fullmatch(f"{words[-2]} {words[-1]}"):
                del words[-2:]
            elif _junk(words[-1]):
                words.pop()
            else:
                break
        name = " ".join(words)
    name = re.sub(r"^\d{1,3}[.)]?\s+", "", name).strip(" -–—;,")
    at = _AT_ARTICLE.search(name)
    if at and not article:
        article = at.group(1)
    return name, article


def _row_line(row: str) -> tuple[ImportedLine, bool] | None:
    """One row → the line and whether its prices are without VAT (the row's sum with VAT stands after the sum)."""
    atoms = _atoms(row)
    found = _triple(atoms)
    if found is None:
        return None
    qty, price, total = found
    name, article = _name_and_article(atoms, qty)
    if not _LETTERS.search(name) or _NOT_A_NAME.match(name):
        return None
    near = atoms[max(0, qty.start - 1):price.start]  # the unit sits right before or after the quantity
    unit = next((atom.text for atom in near if _ATOM_UNIT.match(atom.text)), "")
    if not unit and _PACK_IN_NAME.search(name):
        unit = "уп"  # «… упаковка 8000 шт.», 1 × the pack price: apply_packs turns it into the price per piece
    lead = _LEAD_CELL.search(" ".join(atom.text for atom in atoms))
    days, in_stock, declined = (None, False, False)
    if lead:
        word = lead.group(2).lower()
        days, in_stock, declined = rfq_service.parse_lead(f"{lead.group(1)} {'раб. дн' if word.startswith(('р', 'раб')) else word}")
    elif re.search(r"в\s*налич|со\s*склада", row, re.I):
        days, in_stock = 0, True
    # Prices without VAT show themselves: after the sum stands its VAT (sum × 22 %) or the sum with VAT (sum × 1.22).
    after = [n.value for n in _numbers(atoms) if n.start > total.end]
    tolerance = max(Decimal("0.02"), total.value * Decimal("0.002"))
    vat_rate = next((rate for rate in _VAT_RATES for value in after
                     if abs(value - total.value * (1 + rate / 100)) <= tolerance
                     or abs(value - total.value * rate / 100) <= tolerance), None)
    line = ImportedLine(
        source_name=name[:512], source_article=article[:255], qty=qty.value, unit=unit, price=price.value,
        vat_rate=vat_rate, lead_days=days, in_stock=in_stock, declined=declined,
        analog=bool(re.search(r"аналог", name, re.I)),
    )
    return line, vat_rate is not None


def read_text_lines(text: str) -> tuple[list[ImportedLine], list[str]]:
    """Free text (a PDF's layout text, a letter, a web page) → quote lines and the lines that look like a priced
    position but were not read. A row is found by kol × price ≈ sum (`_row_line`), else by the fixed shape «наименование
    кол-во ед. цена»; a worded line right under a row continues its name (names wrap in PDF tables)."""
    return read_rows(text)[0]


def read_rows(text: str) -> tuple[tuple[list[ImportedLine], list[str]], bool]:
    """(lines, unread) and whether most rows are priced without VAT (their sum with VAT follows the sum)."""
    lines: list[ImportedLine] = []
    unread: list[str] = []
    without_vat = 0
    last: ImportedLine | None = None
    wrapped = 0
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw.strip():
            continue
        found = _row_line(raw.strip())
        line = found[0] if found else _regex_line(" ".join(raw.split()))
        if line is not None:
            lines.append(line)
            without_vat += bool(found and found[1])
            last, wrapped = line, 0
            continue
        first_cell = re.split(r"\t| {2,}", raw.strip())[0].strip()
        # A wrapped name may carry numbers («черный, ТУ 22.21.29-016 1005»), but a line ending in money is a position
        # of its own that was not read («Доставка 1500,50»), not the name's tail.
        if (last is not None and wrapped < 2 and _LETTERS.search(first_cell) and not _NOT_A_NAME.match(first_cell)
                and not _ENDS_WITH_MONEY.search(raw) and len(last.source_name) < 400):
            last.source_name = f"{last.source_name} {first_cell}"[:512]
            wrapped += 1
            continue
        last = None
        flat = " ".join(raw.split())
        if _PRICE_HINT.search(flat) and _LETTERS.search(flat) and len(unread) < 30:
            unread.append(flat[:200])
    return (lines, unread), bool(lines) and without_vat * 2 > len(lines)


def _without_vat(text: str) -> bool:
    """Prices «без НДС» in the document's own words (a column title or a caption), as the Excel paste reads it."""
    return bool(re.search(
        r"(?:цена|цены|стоимость|сумм[аы])[^\n]{0,40}без\s*(?:налога\s*)?ндс|без\s*(?:налога\s*)?ндс[^\n]{0,24}(?:цена|стоимость)",
        text, re.I))


def _table_lines(text: str) -> tuple[list[ImportedLine], bool]:
    """A tab-separated table (Excel's clipboard) → lines, and whether its price column says «без НДС»."""
    header, rows = rfq_service.parse_pasted_table(text)
    if not rows:
        return [], False
    columns = rfq_service.guess_columns(header, rows)
    if columns["name"] < 0 and columns["article"] < 0:
        return [], False
    title = header[columns["price"]] if header and columns["price"] >= 0 else ""
    lines = [
        ImportedLine(
            source_name=row.source_name, source_article=row.source_article, qty=row.qty, unit=row.unit, price=row.price,
            vat_rate=row.vat_rate, lead_days=row.lead_days, in_stock=row.in_stock, declined=row.declined,
            analog=row.analog,
        )
        for row in rfq_service.paste_rows(rows, columns)
    ]
    return lines, bool(re.search(r"без\s*ндс", title, re.I))


# ── the quote's header: number, valid until, the seller's INN (Roller read them with Gemini; here — by regex) ──────

_DATE = r"(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)"
_HEADER_NUMBER = re.compile(
    r"(?:коммерческое\s+предложение|предложение|кп|сч[её]т(?:\s*-?\s*оферта)?(?:\s+на\s+оплату)?)\s*"
    r"(?:№|n|no\.?|#)\s*([a-zа-яё0-9][a-zа-яё0-9\-/._]{0,40})", re.I)
_VALID_DATE = re.compile(
    r"(?:действительн\w*|действует|срок\s+действия(?:\s+\w+){0,2}|актуальн\w*)[^\n\d]{0,24}?(?:до|по)?\s*:?\s*" + _DATE, re.I)
_VALID_DAYS = re.compile(
    r"(?:действительн\w*|действует|срок\s+действия(?:\s+\w+){0,2})[^\n\d]{0,24}?(?:в\s+течени[еи]\s*)?(\d{1,3})\s*"
    r"(?:(раб\w*)\s+)?(?:календарн\w*\s+)?(?:дн|день|дня|дней)", re.I)
_INN = re.compile(r"ИНН(?:\s*/\s*КПП)?\s*:?\s*(\d{10}|\d{12})(?!\d)", re.I)


def _date(day: str, month: str, year: str) -> date | None:
    try:
        return date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))
    except ValueError:
        return None


def read_header(text: str, today: date) -> dict:
    """«КП № 123/45 от …», «Предложение действительно до 30.10.2026» / «… в течение 10 дней», the seller's ИНН (ours
    is skipped: it stands in the buyer's block). What is not found stays empty."""
    head = text[:20_000]
    number = ""
    found = _HEADER_NUMBER.search(head)
    if found:
        number = found.group(1).strip(" .,;")
    valid_until: date | None = None
    dated = _VALID_DATE.search(head)
    if dated:
        valid_until = _date(*dated.groups())
    else:
        days = _VALID_DAYS.search(head)
        if days:
            count = int(days.group(1))
            valid_until = today + timedelta(days=round(count * 1.4) if days.group(2) else count)
    inn = next((value for value in _INN.findall(head) if value not in OWN_INNS), "")
    return {"number": number[:64], "valid_until": valid_until.isoformat() if valid_until else None, "inn": inn}


def _header_supplier(session: Any, header: dict, rfq_id: UUID) -> dict:
    """The directory supplier with the document's INN, and whether he is in the request."""
    supplier = session.scalar(select(SupplierRow).where(SupplierRow.inn == header["inn"])) if header["inn"] else None
    in_request = supplier is not None and session.scalar(select(RfqSupplierRow.id).where(
        RfqSupplierRow.rfq_id == rfq_id, RfqSupplierRow.supplier_id == supplier.id)) is not None
    return {**header, "supplier_id": str(supplier.id) if supplier else None, "supplier_name": supplier.name if supplier else None,
            "in_request": in_request}


# ── matching ────────────────────────────────────────────────────────────────────────────────────────────────────


def _aliased(session: Any, supplier_id: UUID | None, targets: Sequence[Target]) -> dict[str, UUID]:
    """alias key → item id: the supplier's own wordings win over generic ones."""
    item_ids = [target.item_id for target in targets if target.item_id]
    if not item_ids:
        return {}
    where = ItemAliasRow.supplier_id.is_(None) if supplier_id is None else or_(
        ItemAliasRow.supplier_id.is_(None), ItemAliasRow.supplier_id == supplier_id)
    found: dict[str, UUID] = {}
    rows = session.execute(select(ItemAliasRow.alias_key, ItemAliasRow.item_id, ItemAliasRow.supplier_id).where(
        ItemAliasRow.item_id.in_(item_ids), where))
    for key, item_id, owner in sorted(rows, key=lambda row: row[2] is not None):  # generic first, own overwrite
        found[key] = item_id
    return found


def match_lines(lines: list[ImportedLine], targets: Sequence[Target], aliases: dict[str, UUID]) -> list[ImportedLine]:
    """Remembered wording of this supplier first, then the part number, then the name words (rfq.match_rows).
    A name match with a different part number is an analog."""
    by_item = {target.item_id: target for target in targets if target.item_id}
    taken: set[UUID] = set()
    for line in lines:
        for key in (items_repo.alias_key(f"{line.source_article} {line.source_name}"),
                    items_repo.alias_key(line.source_name), items_repo.alias_key(line.source_article)):
            item_id = aliases.get(key) if key else None
            target = by_item.get(item_id) if item_id else None
            if target is not None and target.rfq_line_id not in taken:
                line.rfq_line_id, line.matched_by = target.rfq_line_id, "alias"
                taken.add(target.rfq_line_id)
                break
    rest = [line for line in lines if line.rfq_line_id is None]
    free = [rfq_service.MatchTarget(target.rfq_line_id, target.name, target.article)
            for target in targets if target.rfq_line_id not in taken]
    adapters = [
        rfq_service.PastedRow(line.source_name, line.source_article, line.qty, line.unit, line.price, line.vat_rate,
                              line.lead_days, line.in_stock, line.declined, line.analog) for line in rest
    ]
    rfq_service.match_rows(adapters, free)
    by_id = {target.rfq_line_id: target for target in targets}
    for line, adapter in zip(rest, adapters):
        line.rfq_line_id, line.matched_by = adapter.rfq_line_id, adapter.matched_by
        target = by_id.get(adapter.rfq_line_id) if adapter.rfq_line_id else None
        if target and adapter.matched_by == "name" and line.source_article and target.article \
                and code_key(line.source_article) != code_key(target.article):
            line.analog = True
            line.analog_note = f"Предложено: {line.source_article} {line.source_name}".strip()[:512]
    return lines


# ── entry point ─────────────────────────────────────────────────────────────────────────────────────────────────


def _layout_text(content: bytes) -> str:
    """The text layer laid out as on the page (pypdf «layout», like `pdftotext -layout`): a table row is one line,
    its columns are runs of spaces. Reads what the small extractor cannot — FPDF's single-byte fonts with a
    /Differences table of glyph names, protected «against editing» with an empty user password, which opens without one
    (Элитан's quotes, 14.10.2026). '' when the file cannot be read this way."""
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            return ""  # a real password: nothing to read
        pages = [page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or ""
                 for page in reader.pages[:LAYOUT_PAGES]]
    except Exception:  # a damaged or exotic file: the small extractor's text, if any, is used instead
        log.info("quote PDF: layout text failed", exc_info=True)
        return ""
    return "\n".join(pages)[:MAX_PDF_TEXT]


def text_of_pdf(content: bytes) -> str:
    if len(content) > PDF_LIMIT:
        raise Refused("PDF больше 20 МБ — сожмите файл или вставьте текст")
    if b"%PDF" not in content[:1024]:
        raise Refused("Это не PDF")
    try:  # first the budgeted reader (audit F05): a file that unpacks past the budget is refused before pypdf sees it
        plain = pdf_text(content)
    except PdfLimitExceeded as error:
        raise Refused("PDF слишком сложный для чтения — вставьте текст КП или введите строки вручную") from error
    text = _layout_text(content)
    if not text.strip():
        text = plain
    if not text.strip():
        raise Refused("В PDF нет текстового слоя (скан или защищённый файл) — вставьте текст КП или введите строки вручную")
    return text


def _json(line: ImportedLine) -> dict:
    return {
        "source_name": line.source_name, "source_article": line.source_article, "qty": rfq_service.num(line.qty),
        "unit": line.unit, "price": rfq_service.num(line.price), "vat_rate": rfq_service.num(line.vat_rate),
        "lead_days": line.lead_days, "in_stock": line.in_stock, "declined": line.declined, "analog": line.analog,
        "analog_note": line.analog_note, "rfq_line_id": str(line.rfq_line_id) if line.rfq_line_id else None,
        "matched_by": line.matched_by, "pack": rfq_service.num(line.pack), "moq": rfq_service.num(line.moq),
        "pack_note": line.pack_note,
    }


def _context(session: Any, actor: Actor, rfq_id: UUID, supplier_id: UUID | None):
    """The request (the buyer's own, open), the lines asked of the supplier as targets, his remembered wordings."""
    rfq = rfq_service._open(rfq_service._own(rfq_service._rfq(session, rfq_id), actor))
    asked = None
    if supplier_id is not None:
        rfq_supplier = session.scalar(select(RfqSupplierRow).where(
            RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.supplier_id == supplier_id))
        asked = rfq_service.asked_lines(session, [rfq_supplier.id]).get(rfq_supplier.id) if rfq_supplier else None
    targets = [
        Target(line.id, line.name, line.article, line.quantity, items_repo.normalize_unit(line.unit), line.item_id)
        for line in rfq_service._live_lines(session, rfq.id) if asked is None or line.id in asked
    ]
    return rfq, targets, _aliased(session, supplier_id, targets)


COLUMN_LABELS = {"name": "Наименование", "article": "Артикул", "qty": "Кол-во", "unit": "Ед.", "price": "Цена",
                 "vat": "НДС", "lead": "Срок"}


def paste_table(
    uow: Any, actor: Actor, rfq_id: UUID, text: str, supplier_id: UUID | None, columns: dict[str, int] | None = None,
    vat_included: bool | None = None,
) -> dict:
    """Rows copied from Excel (Roller importPaste): the columns guessed from the header (or the buyer's own choice),
    matched by the supplier's remembered wording, the part number, the name; packs priced per unit. Nothing saved."""
    with uow:
        session = uow.session
        _, targets, aliases = _context(session, actor, rfq_id, supplier_id)
        default_vat = Decimal(rfq_service.purchasing_settings.load(session)["default_vat_rate"])
    header, rows = rfq_service.parse_pasted_table(text)
    if not rows:
        raise Refused("Во вставке нет строк")
    guessed = rfq_service.guess_columns(header, rows)
    chosen = {**guessed, **{key: index for key, index in (columns or {}).items() if key in guessed}}
    if chosen["name"] < 0 and chosen["article"] < 0:
        raise Refused("Укажите, в каком столбце наименование")
    title = header[chosen["price"]] if header and 0 <= chosen["price"] < len(header) else ""
    with_vat = vat_included if vat_included is not None else not re.search(r"без\s*ндс", title, re.I)
    pasted = rfq_service.paste_rows(rows, chosen)
    lines = [
        ImportedLine(source_name=row.source_name, source_article=row.source_article, qty=row.qty, unit=row.unit,
                     price=row.price, vat_rate=row.vat_rate, lead_days=row.lead_days, in_stock=row.in_stock,
                     declined=row.declined, analog=row.analog)
        for row in pasted
    ]
    lines = apply_packs(match_lines(lines[:500], targets, aliases), targets)
    width = max([len(header or [])] + [len(row) for row in rows])
    return {
        "header": header, "width": width, "columns": chosen, "column_labels": COLUMN_LABELS, "vat_included": with_vat,
        "default_vat_rate": rfq_service.num(default_vat),
        "rows": [_json(line) for line in lines[:200]],
    }


def parse_quote(uow: Any, actor: Actor, rfq_id: UUID, text: str, supplier_id: UUID | None, source: str,
                today: date | None = None) -> dict:
    """Proposed lines for the quote form. `source` — «paste» (typed or pasted) or «file» (a PDF's text layer)."""
    with uow:
        session = uow.session
        rfq = rfq_service._open(rfq_service._own(rfq_service._rfq(session, rfq_id), actor))
        asked = None
        if supplier_id is not None:
            rfq_supplier = session.scalar(select(RfqSupplierRow).where(
                RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.supplier_id == supplier_id))
            asked = rfq_service.asked_lines(session, [rfq_supplier.id]).get(rfq_supplier.id) if rfq_supplier else None
        targets = [
            Target(line.id, line.name, line.article, line.quantity, items_repo.normalize_unit(line.unit), line.item_id)
            for line in rfq_service._live_lines(session, rfq.id) if asked is None or line.id in asked
        ]
        aliases = _aliased(session, supplier_id, targets)
        default_vat = Decimal(rfq_service.purchasing_settings.load(session)["default_vat_rate"])

    without_vat = _without_vat(text)
    unread: list[str] = []
    if "\t" in text:
        lines, titled = _table_lines(text)
        without_vat = without_vat or titled
    else:
        (lines, unread), rows_without_vat = read_rows(text)
        without_vat = without_vat or rows_without_vat
        if not lines:  # columns separated by runs of spaces (copied from a viewer): the same table reader
            lines, titled = _table_lines(re.sub(r" {2,}", "\t", text))
            lines = [row for row in lines if row.price is not None or row.declined]  # prose is not a table
            without_vat = without_vat or titled
    if not lines:
        raise Refused("Не нашёл в тексте строки КП. Нужны наименование, количество и цена в одной строке (лучше и сумма) "
                      "или таблица из Excel с заголовком")
    lines = apply_packs(match_lines(lines[:500], targets, aliases), targets)
    matched = [line for line in lines if line.rfq_line_id]
    with uow:
        header = _header_supplier(uow.session, read_header(text, today or date.today()), rfq_id)
    return {
        "header": header,
        "source": source, "vat_included": not without_vat, "default_vat_rate": rfq_service.num(default_vat),
        "lines": [_json(line) for line in matched],
        "unmatched": [_json(line) for line in lines if not line.rfq_line_id],
        "unread": unread,
    }


def remember_aliases(
    make_uow: Callable[[], Any], actor: Actor, now: datetime, rfq_id: UUID, supplier_id: UUID,
    aliases: Sequence[tuple[UUID, str]],
) -> int:
    """After a quote is saved: the supplier's wording of a position → our item, so the next quote matches by itself.
    Best effort — the quote is already saved, a refused alias (taken by another item, no item behind the line) is
    skipped. Returns how many were stored."""
    stored = 0
    try:
        uow = make_uow()
        with uow:
            session = uow.session
            item_of = {
                row.id: row.item_id for row in session.scalars(
                    select(RfqLineRow).where(RfqLineRow.rfq_id == rfq_id, RfqLineRow.removed_at.is_(None)))
            }
            for line_id, text in aliases:
                item_id = item_of.get(line_id)
                if item_id is None:
                    continue
                try:
                    _, created = items_repo.add_alias(uow, item_id, text, supplier_id, actor, now)
                except (items_repo.ItemConflict, items_repo.ItemInvalid, items_repo.ItemNotFound):
                    continue
                stored += int(created)
            uow.commit()
    except Exception:  # noqa: BLE001 — the aliases are a convenience; the saved quote must not fail on them
        log.warning("quote aliases were not stored for rfq %s", rfq_id, exc_info=True)
        return 0
    return stored
