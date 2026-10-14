"""References to other documents in a UPD's free text (ported from Roller_Buyer domain/doc-refs.ts).

«Основание передачи» (basis) and «К платёжно-расчётному документу» (payment_doc), as the Google OCR parser or the EDO
import writes them. Pure functions; OCR slips of «от» and «№» are tolerated, nothing is guessed. Also the payment
orders of a Kaiten invoice card, read from the file names (domain/invoice-parse.ts parsePaymentFileName).

Regex notes for the port: a letter is ``[^\\W\\d_]`` (JS ``\\p{L}``), «letter or digit» is ``[^\\W_]``; sticky JS
regexes become ``pattern.match(text, pos)``; JS ``$`` is ``\\Z``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_L = r"[^\W\d_]"  # one letter
_W = _L + "*"

# Compound invoice names first: «Счёт-договор», «Договор-счёт на оплату», «счёта-оферты».
_CUES: tuple[tuple[str, str], ...] = (
    ("invoice", rf"сч[её]т{_W}[\s-]*(?:договор|оферт){_W}|договор{_W}[\s-]*сч[её]т{_W}|сч[её]т{_W}|инвойс{_W}|проформ{_W}"),
    ("order", rf"заказ{_W}(?:[\s-]*наряд{_W})?|заявк{_W}"),
    ("contract", rf"договор{_W}|контракт{_W}|соглашени{_W}"),
    ("act", r"акт(?:а|у|ом|ы)?"),
    ("payment", rf"плат[её]жн{_W}|пл\.\s*пор{_W}\.?|п/п|пп"),
    ("doc", rf"упд|накладн{_W}|передаточн{_W}|перв{_W}\.?|пер\.|прав{_W}\.?|дов{_W}\.?|приказ{_W}|спецификаци{_W}"),
)
_CUE_RE = re.compile(
    r"(?<![^\W_])(?:" + "|".join(f"(?P<{cue}>{pattern})" for cue, pattern in _CUES) + r")(?![^\W_])",
    re.IGNORECASE,
)

LATIN_LOWER = {"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м", "t": "т", "h": "н", "b": "в"}
LATIN_UPPER = {"A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У"}
_FOLD_LATIN = str.maketrans({**LATIN_LOWER, **LATIN_UPPER})
_UPPER_LATIN = str.maketrans(LATIN_UPPER)


def _fold_latin(text: str) -> str:
    """Same-length fold of Latin look-alikes («Прaв.»), for the keyword search only."""
    return text.translate(_FOLD_LATIN)


_DATE_AHEAD = rf"(?=\s?\d{{1,2}}(?:[./]\d{{1,2}}[./]\d{{2}}|\s+{_L}))"
_OT_GLUED = re.compile(r"(?<=\d)0[mMтТ]" + _DATE_AHEAD)
_OT_SPACED = re.compile(r"(?:(?<=\d)|(?<=\d\s))[oOоО0][mMтТtTr]" + _DATE_AHEAD)


def _fix_ot(text: str) -> str:
    """Same-length repair of an OCR-mangled «от» between a number and a date: «3650m21.07.2025» (= «365 от»),
    «147OM13.03.2026», «910 or 25.09.2025», «ОТ»."""
    return _OT_SPACED.sub("от", _OT_GLUED.sub("от", text))


_MONTHS: tuple[tuple[re.Pattern[str], str], ...] = tuple((re.compile(p), m) for p, m in (
    ("^янв", "01"), ("^фев", "02"), ("^мар", "03"), ("^апр", "04"), ("^ма[йя]", "05"), ("^июн", "06"),
    ("^июл", "07"), ("^авг", "08"), ("^сен", "09"), ("^окт", "10"), ("^ноя", "11"), ("^дек", "12"),
))
_DATE_RE = re.compile(
    r"(\d{1,2})(?:[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)|\s+((?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр"
    rf"|октябр|ноябр|декабр){_W})\s+(\d{{4}})(?!\d))(?:\s*(?:года|г\.?)(?!{_L}))?",
    re.IGNORECASE,
)


def _read_date(text: str, pos: int) -> tuple[str, int] | None:
    """A date starting exactly at `pos`: «26.10.2025», «16.03.26», «20/04/26», «7 октября 2025 г.» → (ISO, end)."""
    m = _DATE_RE.match(text, pos)
    if not m:
        return None
    month = m.group(2)
    if month is None:
        word = m.group(4).lower()
        month = next(number for pattern, number in _MONTHS if pattern.search(word))
    year = m.group(3) or m.group(5)
    if not 1 <= int(month) <= 12 or not 1 <= int(m.group(1)) <= 31:
        return None
    full_year = f"20{year}" if len(year) == 2 else year
    return f"{full_year}-{month.zfill(2)}-{m.group(1).zfill(2)}", m.end()


@dataclass(frozen=True)
class _Cue:
    cue: str
    start: int
    end: int


# «от» (or plain space) then a date; a couple of short words may stand between («№259 оф. от 08.10.2025»).
_AFTER_RE = re.compile(r"\s*(?:(?:[а-яё]{1,4}\.?\s+){0,2}?от\s*|\s+)(?=\d)", re.IGNORECASE)


def _date_after(text: str, pos: int, cues: list[_Cue]) -> tuple[str, int] | None:
    m = _AFTER_RE.match(text, pos)
    if not m:
        return None
    at = m.end()
    # The skipped words must not name another document («№ 12 Счет от …»).
    if any(pos <= c.start < at for c in cues):
        return None
    return _read_date(text, at)


_TOKEN_RE = re.compile(r"[0-9A-Za-zА-ЯЁа-яё](?:[0-9A-Za-zА-ЯЁа-яё/\\._-]*[0-9A-Za-zА-ЯЁа-яё])?")
_MARKER_END = re.compile(r"(?<![^\W_])(?:№|N[oOоОeеº°]?\.?|#)[\s-]*\Z")
# An upper-case series split from the digits by a space: «№Б 132068/1», «Договор ВРС 27215-06/2025».
_PREFIX_END = re.compile(r"(?<![^\W_])([A-ZА-ЯЁ]{1,4}) \Z")
_GLUED_OT = re.compile(r"(.*?\d)от(?=\d|\Z)")
_SPACES_DASHES = re.compile(r"[\s-]*")
_DIGIT = re.compile(r"\d")
_WORDS = re.compile(_L + "+")


@dataclass(frozen=True)
class _Found:
    cue: str | None
    number: str
    date: str | None
    start: int
    end: int


def _scan(text: str) -> list[_Found]:
    """A number is a reference when «№» stands before it, a document keyword precedes it by at most two words, or a
    date follows it. Its kind is the last keyword since the previous reference."""
    fixed = _fix_ot(text)
    cues = [
        _Cue(next(name for name, value in m.groupdict().items() if value is not None), m.start(), m.end())
        for m in _CUE_RE.finditer(_fold_latin(fixed))
    ]
    found: list[_Found] = []
    seg_start = 0
    pos = 0
    while True:
        m = _TOKEN_RE.search(fixed, pos)
        if not m:
            break
        pos = m.end()
        date = _read_date(fixed, m.start())
        if date:
            pos = date[1]
            continue
        # «365от21.07.2025», «47от 30.01.2026»: the number ends where a (repaired) «от» begins.
        glued = _GLUED_OT.match(m.group(0))
        token = glued.group(1) if glued else m.group(0)
        if not _DIGIT.search(token):
            continue
        number_end = m.start() + len(token)
        start = m.start()
        before = fixed[seg_start:start]
        prefix = _PREFIX_END.search(before)
        if prefix:
            at = seg_start + prefix.start()
            head = before[:prefix.start()]
            led = bool(_MARKER_END.search(head)) or any(
                seg_start <= c.end <= at and _SPACES_DASHES.fullmatch(fixed[c.end:at]) for c in cues
            )
            if led and not any(c.start == at for c in cues):
                start = at
                before = head
        marker = bool(_MARKER_END.search(before))
        inside = [c for c in cues if c.start >= seg_start and c.end <= start]
        cue = inside[-1] if inside else None
        gap = fixed[cue.end:start] if cue else ""
        near = cue is not None and not _DIGIT.search(gap) and len(_WORDS.findall(gap)) <= 2
        after = _date_after(fixed, number_end, cues)
        if not marker and not near and not after:
            continue
        end = after[1] if after else number_end
        found.append(_Found(
            cue.cue if cue else None, text[start:number_end], after[0] if after else None, cue.start if cue else start, end,
        ))
        seg_start = end
        pos = end
    return found


REF_KINDS = ("invoice", "order", "contract", "act", "other")


@dataclass(frozen=True)
class DocRef:
    """A document named in a UPD's «Основание передачи»."""

    kind: str  # invoice | order | contract | act | other
    number: str
    date: str | None  # ISO; None when the text gives none
    raw: str  # the fragment of the text the reference was read from

    def as_json(self) -> dict:
        return {"kind": self.kind, "number": self.number, "date": self.date, "raw": self.raw}


@dataclass(frozen=True)
class PaymentRef:
    """One of our payment orders named in «К платёжно-расчётному документу»."""

    number: str
    date: str | None

    def as_json(self) -> dict:
        return {"number": self.number, "date": self.date}


def _ref_kind(cue: str | None) -> str:
    return cue if cue in ("invoice", "order", "contract", "act") else "other"


def parse_basis_refs(text: str) -> list[DocRef]:
    """«Счет № 77639 от 26.10.2025», «Заказ клиента № АУ-4496 от 7 октября 2025 г.», «Счет 25100024 от 20.07.2025,
    Заказ 08968582 от 20.07.2025», «327 от 15.05.2025» → references."""
    out: list[DocRef] = []
    seen: set[str] = set()
    for f in _scan(text or ""):
        kind = _ref_kind(f.cue)
        key = f"{kind}|{norm_doc_number(f.number)}|{f.date}"
        if key in seen:
            continue
        seen.add(key)
        out.append(DocRef(kind, f.number, f.date, text[f.start:f.end].strip()))
    return out


def parse_payment_refs(text: str) -> list[PaymentRef]:
    """Our payment orders named in «К платёжно-расчётному документу»: «Пл. пор. № 1036 от 27.10.2025», «330 от
    03.06.2025, 331 от 03.06.2025», «№ 3650m21.07.2025». Other documents mentioned there («(Счет-договор № 15956237)»,
    «УПД № …») are not payments."""
    out: list[PaymentRef] = []
    seen: set[str] = set()
    for f in _scan(text or ""):
        if f.cue is not None and f.cue != "payment":
            continue
        key = f"{loose_doc_number(f.number)}|{f.date}"
        if key in seen:
            continue
        seen.add(key)
        out.append(PaymentRef(f.number, f.date))
    return out


_NUMBER_MARK = re.compile(r"^(?:№|N[oOº°]?\.?(?=[\s\d-]))")
_NUMBER_SEPARATORS = re.compile(r"[\s№#/\\._\-‐‑–—]")
_NOT_DIGITS = re.compile(r"\D")


def norm_doc_number(text: str) -> str:
    """Strong key of a document number: no spaces, «№», leading «N», dashes, slashes, dots or underscores; Latin
    look-alikes folded to Cyrillic, Ё to Е and the letter О to zero; upper case. «АНС/2337» = «АНС 2337», «K47285201» =
    «К47285201», «0VT/34» = «OVT/34»."""
    s = _NUMBER_MARK.sub("", (text or "").strip(), count=1).upper().replace("Ё", "Е").translate(_UPPER_LATIN)
    return _NUMBER_SEPARATORS.sub("", s.replace("О", "0"))


def loose_doc_number(text: str) -> str:
    """Weak key: digits only, without leading zeros («Т-000155» → «155»); empty when there are no digits."""
    return _NOT_DIGITS.sub("", text or "").lstrip("0")


# ---- payment orders of a Kaiten card (domain/invoice-parse.ts) ----------------------------------------------------

@dataclass(frozen=True)
class PaymentOrder:
    number: str
    date: str  # ISO
    sum: int  # kopecks (Kaiten's file name carries whole roubles)

    def as_json(self) -> dict:
        return {"number": self.number, "date": self.date, "sum": self.sum}


_RU_MONTHS = {
    "января": "01", "февраля": "02", "марта": "03", "апреля": "04", "мая": "05", "июня": "06",
    "июля": "07", "августа": "08", "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
}
_DOTTED = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)")
_WORDED = re.compile(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})")


def ru_date_to_iso(text: str) -> str | None:
    """«15.09.2025», «15 сентября 2025 г.», «15.09.25» → ISO; None when not a date."""
    t = (text or "").strip().lower()
    # Four-digit year first: «03.08.2026» must not read as «03.08.20».
    m = _DOTTED.match(t)
    if m:
        year = f"20{m.group(3)}" if len(m.group(3)) == 2 else m.group(3)
        return f"{year}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    m = _WORDED.match(t)
    if m and m.group(2) in _RU_MONTHS:
        return f"{m.group(3)}-{_RU_MONTHS[m.group(2)]}-{m.group(1).zfill(2)}"
    return None


_MONEY = re.compile(r"\d+(\.\d{1,2})?")
_SPACE = re.compile(r"\s")


def money_to_kop(text: str) -> int | None:
    """«4 815,00», «4815.00», «4 815» → kopecks."""
    t = _SPACE.sub("", text or "").replace(",", ".", 1)
    if not _MONEY.fullmatch(t):
        return None
    try:
        return int((Decimal(t) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


_PAYMENT_FILE = re.compile(
    r"Плат[её]жное[_\s]поручение[_\s]*№\s*(\d+)[_\s]+от[_\s]+(\d{2}[._]\d{2}[._]\d{4})[_\s]+на[_\s]+сумму[_\s]+"
    r"([\d.,\s]+?)(?:[-_ ]\(?\d{1,2}\)?)?(?:\.pdf)?\Z",
    re.IGNORECASE,
)
_TRAILING_POINT = re.compile(r"[.,]\Z")


def parse_payment_file_name(name: str) -> PaymentOrder | None:
    """Kaiten file names of bank payment orders: «Платежное_поручение_№880_от_16.09.2025_на_сумму_4815.pdf». Copies get
    «-1» before the extension; some dates come with underscores («20_11_2025»)."""
    m = _PAYMENT_FILE.search(name or "")
    if not m:
        return None
    amount = money_to_kop(_TRAILING_POINT.sub("", m.group(3)))
    day = ru_date_to_iso(m.group(2).replace("_", "."))
    return PaymentOrder(m.group(1), day, amount) if amount is not None and day else None


def invoice_payments(names: list[str]) -> list[PaymentOrder]:
    """Payment orders attached to an invoice card, read from the file names; a copy («…-1.pdf») is the same payment."""
    out: dict[tuple[str, str, int], PaymentOrder] = {}
    for name in names:
        order = parse_payment_file_name(name)
        if order is not None:
            out.setdefault((order.number, order.date, order.sum), order)
    return list(out.values())
