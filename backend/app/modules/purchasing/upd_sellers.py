"""Who really sold and whose a document is (ported from Roller_Buyer domain/upd-sellers.ts, upd-buyer.ts).

OCR misreads a digit or two of the seller's INN («3862192701» for «3662192701»), and then one supplier falls apart into
three in every list, its documents miss their twins and their supplier. The INN the registry really carries is
restored from the others: the same seller name with a near INN, a near INN of a known supplier, or the name alone when
the INN was not read at all. A scan whose «seller» is our own company has the parties swapped: the seller is in the
buyer's place. Pure functions: headers and the supplier directory in, one verdict per document out.

`effective_header` is the one place that says which header of a UPD is in force: the buyer's correction over the
parser's."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.shared.inn import OWN_INNS, inn_valid  # OWN_INNS: our company (ООО «ЭЗП «ФРАКСИС»), never a seller

HEADER_FIELDS = (
    "number", "date", "seller_name", "seller_inn", "seller_kpp", "buyer_inn", "total_net", "total_vat", "total_gross",
)

_LETTER = r"[^\W\d_]"
_LEGAL_FORM = re.compile(
    r"общество с ограниченной ответственностью|публичное акционерное общество|закрытое акционерное общество"
    r"|акционерное общество|индивидуальный предприниматель"
    rf"|(?<!{_LETTER})(?:ооо|ао|зао|пао|оао|ип)(?!{_LETTER})"
)
_PARENS = re.compile(r"\([^)]*\)")
_NOT_LETTER_OR_DIGIT = re.compile(r"[\W_]")
_NOT_DIGIT = re.compile(r"\D")


def effective_header(row: Any) -> dict[str, Any]:
    """The header in force: the buyer's correction (manual_header) over the parser's fields of the row."""
    parsed = {
        "number": row.number,
        "date": row.doc_date.isoformat() if row.doc_date else None,
        "seller_name": row.seller_name,
        "seller_inn": row.seller_inn,
        "seller_kpp": row.seller_kpp,
        "buyer_inn": row.buyer_inn,
        "total_net": row.total_net,
        "total_vat": row.total_vat,
        "total_gross": row.total_gross,
    }
    manual = row.manual_header or {}
    return {field: manual[field] if field in manual else parsed[field] for field in HEADER_FIELDS}


def seller_name_key(name: str | None) -> str:
    """«ООО "Галерея Коннект"» = «Общество с ограниченной ответственностью "Галерея Коннект" (ООО "Галерея Коннект")»;
    shorter than 5 letters — no key."""
    key = str(name or "").lower().replace("ё", "е")
    key = _PARENS.sub(" ", key)
    key = _LEGAL_FORM.sub(" ", key)
    key = _NOT_LETTER_OR_DIGIT.sub("", key)
    return key if len(key) >= 5 else ""


@lru_cache(maxsize=65536)
def inn_distance(a: str, b: str) -> int:
    """Edit distance of two short strings (INNs); the registry asks for the same pairs again and again."""
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i]
        for j in range(1, len(b) + 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (0 if a[i - 1] == b[j - 1] else 1)))
        previous = current
    return previous[len(b)]


def near_inn(inn: str, of: Iterable[str]) -> str | None:
    """«5040191161», «504019118»: one of these INNs read with a slip by OCR."""
    if not inn:
        return None
    for known in of:
        if inn_distance(inn, known) <= 2:
            return known
    return None


def clean_inn(value: str | None) -> str:
    """Digits only; OCR glues the КПП (9 digits) onto the INN: «772646440772401001» → «772646440»."""
    digits = _NOT_DIGIT.sub("", str(value or ""))
    return digits[:-9] if len(digits) >= 17 else digits


def _valid(inn: str) -> bool:
    return bool(inn) and inn_valid(inn)


@dataclass(frozen=True)
class SellerFacts:
    seller_name: str
    seller_inn: str
    buyer_inn: str


@dataclass(frozen=True)
class SellerVerdict:
    inn: str  # the seller's INN with OCR slips corrected; "" — not known
    note: str  # why it differs from the printed one, in words for the buyer; "" — as printed


def resolve_sellers(
    docs: list[SellerFacts], known: Iterable[tuple[str, str | None]], own: frozenset[str] = OWN_INNS,
) -> list[SellerVerdict]:
    """`known` — the supplier directory as (name, inn) pairs."""
    weight: dict[str, int] = {}
    names: dict[str, set[str]] = {}

    def vouch(inn: str, name: str, amount: int) -> None:
        if not _valid(inn) or near_inn(inn, own):
            return
        weight[inn] = weight.get(inn, 0) + amount
        key = seller_name_key(name)
        if key:
            names.setdefault(inn, set()).add(key)

    for name, inn in known:
        if inn:
            vouch(inn, name, 5)
    for doc in docs:
        vouch(clean_inn(doc.seller_inn), doc.seller_name, 1)

    def named(inn: str, key: str) -> bool:
        return key != "" and key in names.get(inn, set())

    # A valid checksum by chance: a lone INN beside a well-known one of the same seller is its misread copy.
    alias: dict[str, str] = {}
    for inn, amount in weight.items():
        if amount > 1:
            continue
        better = [
            other for other, other_amount in weight.items()
            if other != inn and other_amount > 1 and inn_distance(other, inn) <= 3
            and any(named(other, key) for key in names.get(inn, set()))
        ]
        if len(better) == 1:
            alias[inn] = better[0]
    organisations = [inn for inn in weight if inn not in alias]

    verdicts: list[SellerVerdict] = []
    for doc in docs:
        printed = clean_inn(doc.seller_inn)
        key = seller_name_key(doc.seller_name)
        swapped = ""
        if printed and near_inn(printed, own):
            # Our company in the seller's place: the seller is the other party.
            printed = clean_inn(doc.buyer_inn)
            key = ""
            swapped = "продавцом прочитана наша организация — продавец взят из графы покупателя"
            if not printed or near_inn(printed, own):
                verdicts.append(SellerVerdict("", swapped))
                continue

        def say(inn: str, why: str) -> SellerVerdict:
            return SellerVerdict(inn, "; ".join(part for part in (swapped, why) if part))

        misread = f"ИНН продавца прочитан с ошибкой: {printed}"
        if _valid(printed):
            target = alias.get(printed)
            verdicts.append(say(target, misread) if target else say(printed, ""))
            continue
        if printed:
            # A failed checksum is certainly a misread: a near INN of the same seller; with no name to compare, the
            # one INN a single digit away.
            near = [inn for inn in organisations if inn_distance(inn, printed) <= 2]
            same_name = [inn for inn in near if named(inn, key)]
            if len(same_name) == 1:
                verdicts.append(say(same_name[0], misread))
                continue
            close = [inn for inn in near if inn_distance(inn, printed) == 1]
            if not key and len(close) == 1:
                verdicts.append(say(close[0], misread))
                continue
        # Not read (or beyond repair): the name, when exactly one organisation carries it.
        by_name = [inn for inn in organisations if named(inn, key)]
        if len(by_name) == 1:
            verdicts.append(say(by_name[0], misread if printed else "ИНН продавца не прочитан — найден по названию"))
            continue
        verdicts.append(say("", ""))
    return verdicts


def seller_facts(row: Any) -> SellerFacts:
    header = effective_header(row)
    return SellerFacts(header["seller_name"] or "", header["seller_inn"] or "", header["buyer_inn"] or "")


def seller_inn_of(row: Any, verdict: SellerVerdict | None) -> str:
    """The seller's INN to group and match by: the corrected one, else as printed."""
    return (verdict.inn if verdict else "") or (effective_header(row)["seller_inn"] or "")
