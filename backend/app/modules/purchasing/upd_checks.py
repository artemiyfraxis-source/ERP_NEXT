"""Rules of the UPD registry that need no database (ported from Roller_Buyer packages/shared/src/upd-check.ts,
domain/upd.ts, domain/upd-buyer.ts and domain/doc-refs.ts normDocNumber).

The arithmetic checks are the same ones the buyer's edit form runs live (frontend purchasingUpdApi.ts), so the card
highlights exactly what the server reports. Money in kopecks, unit prices in rubles as printed."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .suppliers import inn_valid
from .upd_sellers import OWN_INNS, clean_inn, inn_distance, near_inn

# The buyer's view of a document, derived from the parser's verdict and the buyer's own review.
STATUSES = ("waiting", "parsed", "needs_review", "verified", "duplicate", "not_purchase")

_NO_VAT = re.compile(r"без\s*ндс", re.IGNORECASE)
_LATIN_UPPER = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
_NUMBER_PREFIX = re.compile(r"^(?:№|N[oOº°]?\.?(?=[\s\d-]))")
_NUMBER_NOISE = re.compile(r"[\s№#/\\._\-‐‑–—]")


def registry_status(parser_status: str, verified_at: Any, twin_of: str | None) -> str:
    if parser_status == "Дубликат" or twin_of is not None:
        return "duplicate"
    if parser_status == "Не документ закупки":
        return "not_purchase"
    if verified_at:
        return "verified"
    if parser_status == "В очереди":
        return "waiting"
    if parser_status == "Разобран":
        return "parsed"
    return "needs_review"


def _number(value: str | Decimal) -> str:
    """A decimal as people write it: «22», «12.5», never «1.25E+1»."""
    return format(Decimal(value).normalize(), "f")


def vat_rate(text: str | None) -> Decimal | None:
    """«22», «22%», «без НДС» → rate in percent (0 for no VAT) or None when unknown; «0.22» is read as 22."""
    raw = str(text or "")
    if _NO_VAT.search(raw):
        return Decimal(0)
    if not raw.strip():
        return None
    try:
        value = Decimal(raw.replace("%", "").replace(",", ".").strip())
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return (value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP) if 0 < value < 1 else value


def _close(a: Decimal, b: Decimal) -> bool:
    """Same tolerance as the Google parser: 5 kopecks or 1 %."""
    return abs(a - b) <= max(Decimal(5), abs(b) * Decimal("0.01"))


@dataclass(frozen=True)
class LineFacts:
    quantity: Decimal | None
    price: Decimal | None  # rubles per unit without VAT
    net: int | None  # kopecks
    vat_rate: str
    vat: int | None
    gross: int | None


def line_issues(line: LineFacts) -> list[str]:
    issues: list[str] = []
    rate = vat_rate(line.vat_rate)
    if line.quantity is not None and line.price is not None and line.net is not None:
        if not _close(Decimal(line.quantity) * Decimal(line.price) * 100, Decimal(line.net)):
            issues.append("кол-во × цена ≠ без НДС")
    if rate is not None and rate > 0 and line.net is not None and line.vat is not None:
        if not _close(Decimal(line.net) * rate / 100, Decimal(line.vat)):
            issues.append(f"НДС не равен ставке {_number(rate)}%")
    vat = line.vat if line.vat is not None else (0 if rate == 0 else None)
    if line.net is not None and vat is not None and line.gross is not None:
        if not _close(Decimal(line.net + vat), Decimal(line.gross)):
            issues.append("без НДС + НДС ≠ с НДС")
    return issues


def _rub(kopecks: int) -> str:
    return _number(Decimal(kopecks) / 100)


def doc_issues(kind: str, header: Mapping[str, Any], lines: Sequence[LineFacts]) -> list[str]:
    """Checks applied to the buyer's own version of a document (the parser checks its own)."""
    issues: list[str] = []
    m15 = kind == "М-15"
    if not lines:
        issues.append("нет ни одной позиции")
    if not header.get("number"):
        issues.append("нет номера документа")
    if not header.get("date"):
        issues.append("нет даты документа")
    if not header.get("seller_name"):
        issues.append("нет отправителя" if m15 else "нет продавца")
    if not m15:
        inn = str(header.get("seller_inn") or "")
        if not inn:
            issues.append("нет ИНН продавца")
        elif not inn_valid(re.sub(r"[^0-9]", "", inn)):
            issues.append(f"ИНН продавца не проходит проверку: {inn}")
    total = header.get("total_gross")
    if lines and total is not None and all(line.gross is not None for line in lines):
        found = sum(line.gross for line in lines if line.gross is not None)
        if abs(found - total) > max(100, total * 0.005):
            issues.append(f"сумма строк {_rub(found)} ≠ итог документа {_rub(total)}")
    bad = sum(1 for line in lines if line_issues(line))
    if bad:
        issues.append(f"строк с замечаниями: {bad}")
    return issues


def norm_doc_number(text: str | None) -> str:
    """Strong key of a document number: no spaces, «№», leading «N», dashes, slashes, dots or underscores; Latin
    look-alikes folded to Cyrillic, Ё to Е and the letter О to zero; upper case. «АНС/2337» = «АНС 2337»,
    «K47285201» = «К47285201», «0VT/34» = «OVT/34»."""
    value = _NUMBER_PREFIX.sub("", str(text or "").strip(), count=1).upper().replace("Ё", "Е")
    value = value.translate(_LATIN_UPPER).replace("О", "0")
    return _NUMBER_NOISE.sub("", value)


# ---- whose document: the buyer's INN -----------------------------------------------------------------------------

BUYER_KINDS = ("ours", "other", "unknown")


@dataclass(frozen=True)
class BuyerVerdict:
    kind: str  # ours | other | unknown
    inn: str | None  # the INN the document belongs to; None — unknown
    note: str  # why the printed INN was read as another one


UNKNOWN_BUYER = BuyerVerdict("unknown", None, "")


def classify_buyers(docs: Iterable[tuple[str, str]], own: frozenset[str] = OWN_INNS) -> list[BuyerVerdict]:
    """(buyer_inn, seller_inn) per document → whose document it is. OCR misreads a digit or two of an INN, so a near
    miss of an INN the registry really carries counts as that INN; a scan whose «seller» is our company has the two
    parties swapped."""
    facts = [(clean_inn(buyer), clean_inn(seller)) for buyer, seller in docs]
    # INNs that stand for an organisation: valid checksum, the most frequent first — a misread copy joins the
    # organisation the registry really has.
    freq: Counter[str] = Counter(
        inn for inn, seller in facts if inn and inn_valid(inn) and not near_inn(inn, own) and not near_inn(seller, own)
    )
    heads = [inn for inn, _ in sorted(freq.items(), key=lambda item: -item[1])]
    verdicts: list[BuyerVerdict] = []
    for inn, seller in facts:
        ours = near_inn(inn, own)
        if ours:
            verdicts.append(BuyerVerdict("ours", ours, "" if inn == ours else f"ИНН покупателя прочитан с ошибкой: {inn}"))
        elif near_inn(seller, own):
            verdicts.append(BuyerVerdict(
                "ours", sorted(own)[0], "в документе перепутаны продавец и покупатель: продавцом стоит наша организация",
            ))
        elif not inn:
            verdicts.append(UNKNOWN_BUYER)
        elif inn_valid(inn):
            verdicts.append(BuyerVerdict("other", inn, ""))
        else:
            # A failed checksum is certainly a misread: the nearest organisation within three digits takes it.
            near = sorted(((head, inn_distance(inn, head)) for head in heads), key=lambda item: item[1])
            head = next((candidate for candidate, distance in near if distance <= 3), None)
            if head:
                verdicts.append(BuyerVerdict("other", head, f"ИНН покупателя прочитан с ошибкой: {inn}"))
            else:
                verdicts.append(BuyerVerdict("other", inn, "ИНН покупателя не проходит проверку — возможно, прочитан с ошибкой"))
    return verdicts
