"""Positions of a supplier's invoice from its PDF text layer — words with their positions.

Port of Roller_Buyer `apps/api/src/domain/invoice-lines.ts` (`parseInvoiceLines`, ported 26.10.2026). Sellers print
the table in a hundred layouts, so the reader leans on what they share: a header with the name column and at least two
of «Кол-во / Цена / Сумма / НДС», rows numbered 1, 2, 3… in the leftmost column (or, unnumbered, one line of figures
per row), and qty × price = sum. Pure: takes the words with their positions (PDF coordinates, y grows upwards; w — the
word's width when known), as `app.shared.pdf_layout` reads them.

`parse_invoice_table` also returns the header's words (ERP decides with them whether the prices carry НДС);
`parse_invoice_lines` is Roller_Buyer's function as is.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PdfWord:
    s: str
    x: float
    y: float
    w: float | None = None


@dataclass(frozen=True)
class InvoiceLine:
    """Roller_Buyer `InvoiceLine`: price and sum in kopecks, as printed (with or without НДС — the header says)."""

    seq: int
    name: str
    article: str
    qty: float | None
    unit: str
    price: int | None
    sum: int | None


@dataclass
class _Line:
    y: float
    words: list[PdfWord] = field(default_factory=list)


@dataclass(frozen=True)
class _Figure:
    kind: str  # qty / price / sum / other
    c: float


@dataclass(frozen=True)
class _Columns:
    name_x: float
    name_end: float  # where the name column ends: the first header word to its right
    num_x: float | None
    article: object  # "left", (from, to) — a zone right of the name, or None
    figures: tuple[_Figure, ...]  # header words of the figure columns, by their middle
    qty_first: bool  # the quantity column stands left of the price column (the usual order)


_NAME_HEAD = re.compile(r"^(Наименование|Название|Товар|Услуг|Работ|Номенклатура)")
_COL_HEAD = re.compile(r"^(Кол|Цена|Сумма|НДС|Стоимость|К оплате|Ед|Срок|Наличие|Статус|Артикул|Код|Всего)")
_ARTICLE_HEAD = re.compile(r"^(Артикул|Код|Арт\.|Каталожный)")
_FIGURE_HEAD = re.compile(r"^(Кол|Цена|Сумма|НДС|Стоимость|К оплате)")
_TOTALS = re.compile(r"^(Итого|ИТОГО|Итог\b|Всего|ВСЕГО|В том числе НДС|Включая НДС|\* Включая|Сч[её]т содержит|К оплате:)")
_UNIT = re.compile(
    r"^(шт|м|мп|м\.п|пог\.?\s?м|км|кг|г|т|л|уп|упак|компл|к-т|кмп|пар[аы]?|рул|рулон|бухт[аы]?|ед|усл|услуга|м2|м3"
    r"|набор|лист|ч|час)\.?$",
    re.I,
)
_NUMBER = re.compile(r"^-?[0-9]{1,3}(?:[\s ]?[0-9]{3})*(?:[.,][0-9]{1,4})?$")
_ROW_NO = re.compile(r"^([0-9]{1,3})\.?(?:\s+(\S.*))?$")
_CURRENCY = re.compile(r"\s*(р\.|руб\.?|₽|RUB)$", re.I)


def _text(w: PdfWord) -> str:
    return w.s.strip()


def _mid(w: PdfWord) -> float:
    return w.x + (w.w or 0) / 2


def _to_lines(words: list[PdfWord]) -> list[_Line]:
    lines: list[_Line] = []
    for w in sorted(words, key=lambda word: -word.y):
        if not w.s.strip():
            continue
        line = next((item for item in lines if abs(item.y - w.y) <= 2.5), None)
        if line:
            line.words.append(w)
        else:
            lines.append(_Line(w.y, [w]))
    for line in lines:
        line.words.sort(key=lambda word: word.x)
    return sorted(lines, key=lambda line: -line.y)


def read_number(s: str) -> float | None:
    t = _CURRENCY.sub("", s).strip()
    if not _NUMBER.match(t):
        return None
    return float(re.sub(r"[\s ]", "", t).replace(",", ".", 1))


def _figure_kind(s: str) -> str:
    if s.startswith("Кол"):
        return "qty"
    if s.startswith("Цена"):
        return "price"
    if re.match(r"^(Сумма|Стоимость|Всего|К оплате)", s) and not re.match(r"^Сумма\s+НДС", s):
        return "sum"
    return "other"


def _find_header(lines: list[_Line]) -> tuple[_Columns, float, list[PdfWord]] | None:
    """The header of the positions table on a page: the line with the name column and its neighbours."""
    for i, start in enumerate(lines):
        name = next((w for w in start.words if _NAME_HEAD.match(_text(w))), None)
        if name is None:
            continue

        # Neighbouring header lines: no amounts, and column words, «№», or wrapped header words (lower case, brackets,
        # or all to the right of the name header).
        def is_header(line: _Line, name: PdfWord = name) -> bool:
            return not any(re.search(r"[0-9]{2,}", re.sub(r"[0-9]{1,3}\s?%", "", _text(w))) for w in line.words) and (
                any(_COL_HEAD.match(_text(w)) or re.match(r"^[№#]", _text(w)) for w in line.words)
                or all(w.x > name.x - 5 or re.match(r"^[а-яё(]", _text(w)) for w in line.words)
            )

        block = [start]
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(lines):
                line = lines[j]
                if not any(abs(b.y - line.y) <= 12 for b in block) or not is_header(line):
                    break
                block.append(line)
                j += step
        words = [w for line in block for w in line.words]
        kinds = {m.group(1) for m in (_FIGURE_HEAD.match(_text(w)) for w in words) if m}
        if len(kinds) < 2:
            continue
        right = [w for w in words if w.x > name.x + 5 and re.match(r"^[А-ЯЁA-Z№]", _text(w)) and not _NAME_HEAD.match(_text(w))]
        if not right:
            continue
        name_end = min(w.x for w in right)
        num = next((w for w in words if re.match(r"^[№#]", _text(w)) and len(_text(w)) <= 6 and w.x < name.x), None)
        art = next((w for w in words if _ARTICLE_HEAD.match(_text(w))), None)
        article: object = None
        end = name_end
        if art is not None and art.x < name.x:
            article = "left"
        elif art is not None and art.x == name_end:
            after = [w.x for w in right if w.x > art.x + 5]
            article = (art.x - 8, min(after) - 8 if after else math.inf)
            end = art.x
        figures = tuple(_Figure(_figure_kind(_text(w)), _mid(w)) for w in right if w.x >= end)
        qty_at = next((f.c for f in figures if f.kind == "qty"), None)
        price_at = next((f.c for f in figures if f.kind == "price"), None)
        columns = _Columns(
            name_x=name.x, name_end=end, num_x=num.x if num is not None else None, article=article, figures=figures,
            qty_first=qty_at is None or price_at is None or qty_at < price_at,
        )
        return columns, min(line.y for line in block), sorted(words, key=lambda w: (-w.y, w.x))
    return None


@dataclass
class _RawRow:
    name: str
    article: str
    unit: str
    nums: list[tuple[float, float]]  # (column middle, number)
    cols: _Columns


def _raw_row(lines: list[_Line], anchor: PdfWord | None, cols: _Columns) -> _RawRow | None:
    zone = cols.article if isinstance(cols.article, tuple) else None
    value_from = zone[1] if zone else cols.name_end - 8

    # A word of the name starts inside its column; a note running across the whole table is not a name.
    def fits(w: PdfWord) -> bool:
        return w.x < cols.name_end - 8 and (w.w is None or w.x + w.w <= cols.name_end + 80)

    anchor_line = next((line for line in lines if any(w is anchor for w in line.words)), None) if anchor else None
    name_from = -math.inf
    article_words: list[PdfWord] = []
    if cols.article == "left" and anchor_line is not None:
        # The code is the first word after the number; the name starts with the word after it.
        rest = [w for w in anchor_line.words if w is not anchor and fits(w)]
        if len(rest) >= 2 and rest[0].x < cols.name_x:
            article_words.append(rest[0])
            name_from = rest[1].x - 3
    per_line: list[list[PdfWord]] = []
    for line in lines:
        own: list[PdfWord] = []
        for w in line.words:
            if w is anchor or any(w is a for a in article_words):
                continue
            if zone and zone[0] <= w.x < zone[1]:
                article_words.append(w)
            elif fits(w):
                (article_words if w.x < name_from else own).append(w)
        per_line.append(own)
    # The name is one run of text from the column's left edge; a word far to the right belongs to a narrow column
    # printed in the name's zone («Срок: 1-2 рабочих дня»).
    starts = [own[0].x for own in per_line if own]
    left = min(starts) if starts else math.inf
    name_words: list[PdfWord] = []
    for own in per_line:
        if not own or own[0].x > left + 40:
            continue
        for k, word in enumerate(own):
            prev = own[k - 1] if k else None
            if prev is not None and prev.w is not None and word.x - (prev.x + prev.w) > 20:
                break
            name_words.append(word)
    name = re.sub(r"\s+", " ", " ".join(_text(w) for w in name_words)).strip()
    if not name:
        return None
    values = [w for line in lines for w in line.words if w is not anchor and w.x >= value_from]
    unit = next((_text(w) for w in values if _UNIT.match(_text(w))), "")
    nums = [(_mid(w), read_number(_text(w))) for w in values if not _text(w).endswith("%")]
    return _RawRow(
        name=name,
        article=" ".join(_text(w) for w in article_words).strip(),
        unit=re.sub(r"\.$", "", unit),
        nums=sorted(((c, n) for c, n in nums if n is not None), key=lambda item: item[0]),
        cols=cols,
    )


def _kop(n: float) -> int:
    return math.floor(n * 100 + 0.5)  # JS Math.round


@dataclass(frozen=True)
class _Trio:
    qty: float
    price: float
    sum: float
    at: dict
    rest: tuple[float, ...]  # ERP: the row's numbers right of the sum (the «с НДС» / «НДС» columns)


def _trio(r: _RawRow) -> _Trio | None:
    """qty × price = sum, the leftmost such trio (later ones are VAT columns that happen to fit)."""
    v = r.nums
    for a in range(len(v)):
        for b in range(a + 1, len(v)):
            for c in range(b + 1, len(v)):
                p, q, s = v[a][1], v[b][1], v[c][1]
                if p <= 0 or q <= 0 or s <= 0:
                    continue
                if abs(p * q - s) > 0.01 + 0.005 * max(p, q) + s * 0.0005:
                    continue
                qi, pi = (a, b) if r.cols.qty_first else (b, a)
                at = {"qty": v[qi][0], "price": v[pi][0], "sum": v[c][0]}
                return _Trio(v[qi][1], v[pi][1], s, at, tuple(n for _, n in v[c + 1:]))
    return None


def _median(xs: list[float]) -> float | None:
    return sorted(xs)[len(xs) // 2] if xs else None


def _resolve(rows: list[_RawRow]) -> tuple[list[InvoiceLine], list[tuple[float, ...]]]:
    """Rows without a fitting trio (a delivery with «-» for quantity, a sum printed elsewhere) take their figures from
    the columns the other rows showed, or else from the nearest header word."""
    trios = [_trio(r) for r in rows]
    found = [t for t in trios if t is not None]
    learned = {kind: _median([t.at[kind] for t in found]) for kind in ("qty", "price", "sum")}
    out: list[InvoiceLine] = []
    rests: list[tuple[float, ...]] = []
    for r, t in zip(rows, trios):
        rests.append(t.rest if t is not None else ())
        if t is not None:
            out.append(InvoiceLine(0, r.name, r.article, t.qty, r.unit, _kop(t.price), _kop(t.sum)))
            continue

        def pick(kind: str, r: _RawRow = r) -> float | None:
            at = learned[kind]
            if at is not None:
                return next((n for c, n in r.nums if abs(c - at) <= 25), None)
            # Nearest header column of each figure; the first «Сумма» column is the sum.
            first_sum = next((f for f in r.cols.figures if f.kind == "sum"), None)
            for c, n in r.nums:
                best = r.cols.figures[0] if r.cols.figures else None
                for f in r.cols.figures:
                    if abs(f.c - c) < abs(best.c - c):
                        best = f
                if best is not None and best.kind == kind and (kind != "sum" or best is first_sum):
                    return n
            return None

        qty = pick("qty")
        price = pick("price")
        total = pick("sum")
        if total is None and qty is not None and price is not None:
            total = qty * price
        if total is None:
            total = r.nums[-1][1] if r.nums else None
        # A service printed with «-» or 0 for quantity, its price equal to its sum: one of it.
        if not qty and price is not None and total is not None and abs(price - total) < 0.01:
            qty = 1
        out.append(InvoiceLine(
            0, r.name, r.article, qty, r.unit, None if price is None else _kop(price), None if total is None else _kop(total),
        ))
    return out, rests


@dataclass(frozen=True)
class InvoiceTable:
    lines: list[InvoiceLine]
    header: list[PdfWord]  # the words of the first header found, top to bottom, left to right
    # ERP: per line, the numbers printed right of its sum (Roller_Buyer drops them) — the «с НДС» / «НДС» columns.
    after_sum: list[tuple[float, ...]] = field(default_factory=list)


def parse_invoice_table(pages: list[list[PdfWord]]) -> InvoiceTable:
    raw: list[_RawRow] = []
    cols: _Columns | None = None
    header_words: list[PdfWord] = []
    for page in pages:
        lines = _to_lines(page)
        header = _find_header(lines)
        # A table running on from the previous page keeps its columns; a new header starts again.
        if header:
            cols = header[0]
            header_words = header_words or header[2]
        if cols is None:
            continue
        c = cols
        top = header[1] if header else math.inf
        end_idx = next(
            (i for i, line in enumerate(lines) if line.y < top and any(_TOTALS.match(_text(w)) for w in line.words)), -1,
        )
        region = [line for i, line in enumerate(lines) if line.y < top and (end_idx < 0 or i < end_idx)]

        # Row numbers: the leftmost word of a line, under «№» (or the leftmost column), counting up. Some sellers print
        # the number and the name as one piece of text («1 5-1814832-2 TE»).
        candidates = [line for line in region if _ROW_NO.match(_text(line.words[0])) and line.words[0].x < c.name_x]
        col_x = c.num_x if c.num_x is not None else min((line.words[0].x for line in candidates), default=math.inf)
        anchors: list[tuple[_Line, PdfWord | None]] = []
        for line in candidates:
            w = line.words[0]
            m = _ROW_NO.match(_text(w))
            if abs(w.x - col_x) > 14 or int(m.group(1)) != len(raw) + len(anchors) + 1:
                continue
            if m.group(2):
                # Split the number off; the rest of the text is the start of the name.
                line.words[0] = PdfWord(m.group(2), w.x, w.y, w.w)
                anchors.append((line, None))
            else:
                anchors.append((line, w))
        # No numbers: every line with figures under the value columns is a row.
        if not anchors:
            for line in region:
                figures = [w for w in line.words if w.x >= c.name_end - 8 and read_number(_text(w)) is not None]
                if len(figures) >= 2 and any(w.x < c.name_end - 8 for w in line.words):
                    anchors.append((line, None))
        if not anchors:
            if end_idx >= 0:
                cols = None
            continue
        # Rows printed with the number centred have text above it: each line goes to the nearest number. Rows with the
        # number on top own everything down to the next number.
        first_y = anchors[0][0].y
        centred = any(line.y > first_y + 2 for line in region)
        grouped: list[list[_Line]] = [[] for _ in anchors]
        for line in region:
            if centred:
                k = 0
                for i in range(1, len(anchors)):
                    if abs(anchors[i][0].y - line.y) < abs(anchors[k][0].y - line.y):
                        k = i
            else:
                k = -1
                for i, (anchor_line, _) in enumerate(anchors):
                    if anchor_line.y >= line.y - 2:
                        k = i
                if k < 0:
                    continue
            grouped[k].append(line)
        for (anchor_line, word), own in zip(anchors, grouped):
            row = _raw_row(own, word, c)
            if row is not None:
                raw.append(row)
        if end_idx >= 0:
            cols = None
    resolved, after_sum = _resolve(raw)
    lines_out = [InvoiceLine(i + 1, r.name, r.article, r.qty, r.unit, r.price, r.sum) for i, r in enumerate(resolved)]
    return InvoiceTable(lines_out, header_words, after_sum)


def parse_invoice_lines(pages: list[list[PdfWord]]) -> list[InvoiceLine]:
    return parse_invoice_table(pages).lines
