"""«XHP-4@JST» → article «XHP-4», manufacturer «JST» (owner 23.10.2026: «можем вырезать производителя из артикула и
писать его отдельно»).

The old warehouse wrote the maker into the article after «@», sometimes with the pack size («SXH-001T-P0.6##6000@JST»,
«SPH-002T-P0.5L##8000@JST упаковка 8000 шт»), a space before «@» («0500588001 @MOLEX») or a description after a comma
(«SMA-LC174P@RUICHI, SMA-Male R/A RG174/RG316»). Kits, BOMs, the reference DB and receiving hold the bare article, so
the lot keeps the bare one and the maker goes to «Производитель».

Conservative: only a trailing «@token» of 2..20 letters, digits, spaces, «&» or «-» (with at least one letter) is a
maker — «info@mail.ru» or «@MOLEX SH12501-7Y china (на тесты)» stay as they are. The article itself is never
normalized (Molex hyphens and leading zeros stay as written: matching works on keys elsewhere).

Pure rules, no I/O. Migration 0146_split_article_maker holds a frozen copy; the frontend mirror is
`frontend/src/warehouseReceiptLogic.ts` `splitArticleMaker`.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# «упаковка 8000 шт» (also «упаковки по 5 000 шт.») at the very end.
_PACK_WORDS = re.compile(r"[\s,;]*упаковк[аеиу]\s*(?:по\s*)?(\d[\d\s]*?)\s*шт\.?\s*\Z", re.IGNORECASE)
# «##6000» at the very end of the article.
_PACK_HASH = re.compile(r"\s*##\s*(\d+)\s*\Z")
_MAKER = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё &\-]{0,18}[0-9A-Za-zА-Яа-яЁё]")
_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")
_LATIN = re.compile(r"[A-Za-z]")
# Cyrillic letters drawn like Latin ones: «МOLЕХ» typed with a Russian М, Е, Х is MOLEX.
_TWINS = str.maketrans("АВЕКМНОРСТХУаеорсху", "ABEKMHOPCTXYaeopcxy")
_ALIASES = {
    "CONFLY": "CONNFLY",
    "CONDAR": "SCONDAR",  # SCT… housings are Scondar's
    "CHINA": "Китай",
    "КИТАЙ": "Китай",
}


class ArticleSplit(NamedTuple):
    article: str  # the bare article, trimmed
    maker: str  # normalized maker, "" when the text had none
    pack_qty: int | None  # «##6000» / «упаковка 6000 шт»
    note: str  # text after «@MAKER,» — a description, not part of the article


def normalize_maker(value: object) -> str:
    """«Molex» → «MOLEX», «RUICHI,» → «RUICHI», «CONFLY» → «CONNFLY», «china» → «Китай»."""
    text = " ".join(str(value or "").split()).strip(" .,;:")
    if not text:
        return ""
    if _LATIN.search(text):
        text = text.translate(_TWINS)
    text = text.upper()
    return _ALIASES.get(text, text)


def _pack(digits: str) -> int | None:
    number = re.sub(r"\s", "", digits)
    return int(number) if number.isdigit() and int(number) > 0 else None


def split_article_maker(text: object) -> ArticleSplit:
    """The article as typed → (bare article, maker, pack size, note). Text without a maker tail comes back trimmed."""
    value = str(text or "").strip()
    pack: int | None = None
    note = ""
    maker = ""
    words = _PACK_WORDS.search(value)
    if words and words.start() > 0:
        pack = _pack(words.group(1))
        value = value[: words.start()].rstrip()
    at = value.rfind("@")
    if at > 0:
        head, tail = value[:at], value[at + 1:]
        name, _, rest = tail.partition(",")
        name = name.strip().rstrip(".;:")
        if "@" not in head and head.strip() and _MAKER.fullmatch(name) and _LETTER.search(name):
            maker = normalize_maker(name)
            note = " ".join(rest.split())
            value = head.rstrip()
    hashed = _PACK_HASH.search(value)
    if hashed and hashed.start() > 0:
        pack = pack or _pack(hashed.group(1))
        value = value[: hashed.start()].rstrip()
    return ArticleSplit(value, maker, pack, note)


def split_extras(split: ArticleSplit) -> str:
    """What the split took off besides the maker, as a line for «Описание»: the note and «упаковка N шт»."""
    parts = [split.note] if split.note else []
    if split.pack_qty:
        parts.append(f"упаковка {split.pack_qty} шт")
    return "; ".join(parts)


def with_extras(description: object, extras: str) -> str:
    """«Описание» with the extras appended once (a description that already says it stays as it is)."""
    current = str(description or "").strip()
    if not extras or extras.casefold() in current.casefold():
        return current
    return f"{current}; {extras}" if current else extras
