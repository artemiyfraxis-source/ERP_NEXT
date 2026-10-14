"""Which Kaiten invoices a UPD closes (ported from Roller_Buyer domain/doc-match.ts).

Computed on read from facts: the references in its «Основание передачи» / «К платёжно-расчётному документу» and the
payment orders on the invoice cards. Only the buyer's decisions are stored: a confirmed pair always links, a rejected
one never. Pure; invoices are indexed by seller and by payment, so hundreds × hundreds stay instant.

Rules learned from live data (spec 2026-10-06 «Как сделано»): our payment date may slip ±3 days within one seller only
(another bank account repeats numbers); the same seller name is the same seller when OCR broke an INN; a marketplace
(«ЗЭП») invoice links UPDs of other sellers by a number of 10+ characters with its date."""

from __future__ import annotations

import re
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from .doc_refs import DocRef, PaymentRef, loose_doc_number, norm_doc_number, parse_basis_refs, parse_payment_refs
from .upd_sellers import seller_name_key

# Sums equal to the rouble are equal (Kaiten writes payments in whole roubles).
CHAIN_TOLERANCE_KOP = 100
# Amount suggestion: ±1 ₽, the UPD dated from 7 days before to 120 days after the invoice.
AMOUNT_TOLERANCE = CHAIN_TOLERANCE_KOP
AMOUNT_DAYS = (-7, 120)
# A number-only match: the invoice at most a year older and a month younger than the UPD.
BASIS_DAYS = (-31, 365)
# Sellers date our payment order by the day the money came: «808 от 19.09» for our «808 от 18.09».
PAY_DAYS = 3
# A marketplace bills, other sellers ship («Счет-Оферта № 0215864011-0012»): a number this long with its date pins the
# invoice anyway; a shorter one only together with our payment of the same day.
MARKETPLACE_NUMBER = 10
MARKETPLACE_PAID_NUMBER = 8

LINK_METHODS = ("manual", "basis_date", "basis", "payment")
_RANK = {"manual": 0, "basis_date": 1, "basis": 2, "payment": 3}


@dataclass(frozen=True)
class MatchPayment:
    number: str
    date: str | None
    sum: int | None = None


@dataclass(frozen=True)
class MatchUpd:
    id: str
    seller_inn: str
    # Only to see through an INN misread by OCR: the same name is the same seller.
    seller_name: str | None
    supplier_id: Hashable | None
    date: str | None
    total_gross: int | None  # kopecks
    basis: str
    payment_doc: str


@dataclass(frozen=True)
class MatchInvoice:
    id: Hashable
    seller_inn: str | None
    seller_name: str | None
    supplier_id: Hashable | None
    number: str | None
    date: str | None
    total: int | None  # kopecks
    payments: tuple[MatchPayment, ...] = ()


@dataclass(frozen=True)
class MatchDecision:
    upd_id: str
    invoice_id: Hashable
    decision: str  # confirmed | rejected


@dataclass(frozen=True)
class DocLink:
    upd_id: str
    invoice_id: Hashable
    method: str  # manual | basis_date | basis | payment


@dataclass(frozen=True)
class DocSuggestion:
    """Same seller and amount, nothing else: shown as «возможно», never counted as a link."""

    upd_id: str
    invoice_id: Hashable
    method: str = "amount"


@dataclass(frozen=True)
class DocConflict:
    """The UPD's payment order (or invoice number + date) belongs to an invoice of another seller."""

    upd_id: str
    invoice_id: Hashable
    code: str = "seller_mismatch"


@dataclass
class DocMatchResult:
    links: list[DocLink] = field(default_factory=list)
    suggestions: list[DocSuggestion] = field(default_factory=list)
    conflicts: list[DocConflict] = field(default_factory=list)


@lru_cache(maxsize=8192)
def _ordinal(iso: str) -> int:
    return date.fromisoformat(iso).toordinal()


def days_between(start: str, end: str) -> int:
    """Days from `start` to `end` (ISO dates)."""
    return _ordinal(end) - _ordinal(start)


def _within(window: tuple[int, int], start: str | None, end: str | None) -> bool:
    if not start or not end:
        return True
    days = days_between(start, end)
    return window[0] <= days <= window[1]


_LEADING_NOT_DIGITS = re.compile(r"^\D+")
_SHORT_DIGITS = re.compile(r"\d{1,4}")


def _core_key(strong: str) -> str:
    """The strong key without its letter series and zero padding: «СЧТ61019» → «61019», «G0000029302» → «29302»."""
    return _LEADING_NOT_DIGITS.sub("", strong).lstrip("0")


def _nearly_equal(a: str, b: str) -> bool:
    """One OCR slip between a letter and a digit in a long number: «80000000703» read for «Д0000000703»."""
    if len(a) != len(b) or len(a) < 6:
        return False
    at = -1
    for k, (x, y) in enumerate(zip(a, b)):
        if x == y:
            continue
        if at >= 0:
            return False
        at = k
    return at >= 0 and not (a[at].isdigit() and b[at].isdigit())


def _pay_key(number: str, day: str) -> str:
    return f"{loose_doc_number(number)}|{day}"


def _needs_date(ref: DocRef) -> bool:
    """Numbers that need their date to match: up to three significant digits («Счет 8», «Т-331» recur every year), and a
    bare 1–4-digit «№ 1073 от …» outside an invoice keyword — that is how our own payment order numbers look, and a
    seller's invoice may carry the same digits."""
    return len(loose_doc_number(ref.number)) <= 3 or (ref.kind == "other" and bool(_SHORT_DIGITS.fullmatch(ref.number.strip())))


@dataclass
class _Inv:
    src: MatchInvoice
    strong: str
    loose: str
    core: str
    pay_keys: list[str]
    name: str

    @property
    def id(self) -> Hashable:
        return self.src.id


@dataclass
class _Upd:
    src: MatchUpd
    name: str
    own: list[_Inv]
    num_refs: list[DocRef]
    pay_refs: list[PaymentRef]


def _same_seller(u: _Upd, i: _Inv) -> bool:
    if u.src.seller_inn and i.src.seller_inn and u.src.seller_inn == i.src.seller_inn:
        return True
    # Two INNs that differ under one name: OCR misread one of them.
    if u.name and u.name == i.name:
        return True
    if u.src.seller_inn and i.src.seller_inn:
        return False
    return u.src.supplier_id is not None and u.src.supplier_id == i.src.supplier_id


def _unknown_seller(i: _Inv) -> bool:
    """A scanned invoice PDF gives no seller: neither the same nor another one."""
    return not i.src.seller_inn and i.src.supplier_id is None


def _push(index: dict, key, value) -> None:
    index.setdefault(key, []).append(value)


def _facts(u: MatchUpd, name: str, own: list[_Inv]) -> _Upd:
    """References the UPD makes: seller documents (contracts aside) and our payment orders."""
    basis = [r for r in parse_basis_refs(u.basis) if r.kind != "contract"]
    # «№ 930 от 30.09.2025 (Счет-договор № 15956237)»: the invoice named next to the payment counts too.
    num_refs = basis + [r for r in parse_basis_refs(u.payment_doc) if r.kind == "invoice"]
    # A keyword-less «1046 от 29.10.2025» in the basis is as often our payment order as a seller's number.
    bare = [PaymentRef(r.number, r.date) for r in basis if r.kind == "other"]
    pay_refs = [p for p in parse_payment_refs(u.payment_doc) + bare if p.date is not None]
    return _Upd(u, name, own, num_refs, pay_refs)


def match_documents(
    upds: Iterable[MatchUpd], invoices: Iterable[MatchInvoice], decisions: Iterable[MatchDecision],
) -> DocMatchResult:
    decisions = list(decisions)
    decided = {(d.upd_id, d.invoice_id): d.decision for d in decisions}
    invs = [
        _Inv(
            src=i,
            strong=norm_doc_number(i.number) if i.number else "",
            loose=loose_doc_number(i.number) if i.number else "",
            core=_core_key(norm_doc_number(i.number)) if i.number else "",
            pay_keys=[_pay_key(p.number, p.date) for p in i.payments if p.date is not None],
            name=seller_name_key(i.seller_name),
        )
        for i in invoices
    ]
    by_id = {i.id: i for i in invs}
    by_inn: dict[str, list[_Inv]] = {}
    by_supplier: dict[Hashable, list[_Inv]] = {}
    by_name: dict[str, list[_Inv]] = {}
    # Our payment order number → the invoices it paid, with the payment's own key and date.
    by_payment: dict[str, list[tuple[_Inv, str, str]]] = {}
    by_number_date: dict[str, list[_Inv]] = {}
    for i in invs:
        if i.src.seller_inn:
            _push(by_inn, i.src.seller_inn, i)
        if i.src.supplier_id is not None:
            _push(by_supplier, i.src.supplier_id, i)
        if i.name:
            _push(by_name, i.name, i)
        for p in i.src.payments:
            if p.date is not None:
                _push(by_payment, loose_doc_number(p.number), (i, _pay_key(p.number, p.date), p.date))
        if i.strong and i.src.date:
            _push(by_number_date, f"{i.strong}|{i.src.date}", i)

    def paid_with(p: PaymentRef, slip: int = 0) -> list[tuple[_Inv, str, str]]:
        """Invoices paid by our order «number от date», each with the invoice's own key. A few days' slip only within
        one seller: across sellers the numbers of another bank account collide («729 от 24.08»)."""
        seen: set = set()
        out = []
        for x in by_payment.get(loose_doc_number(p.number), ()):
            if x[0].id in seen or abs(days_between(x[2], p.date)) > slip:
                continue
            seen.add(x[0].id)
            out.append(x)
        return out

    def own_invoices(u: MatchUpd, name: str) -> list[_Inv]:
        pool: dict[Hashable, _Inv] = {}
        for i in by_inn.get(u.seller_inn, ()):
            pool.setdefault(i.id, i)
        if u.supplier_id is not None:
            for i in by_supplier.get(u.supplier_id, ()):
                pool.setdefault(i.id, i)
        for i in by_name.get(name, ()):
            pool.setdefault(i.id, i)
        probe = _Upd(u, name, [], [], [])
        return [i for i in pool.values() if _same_seller(probe, i)]

    confirmed_for: dict[str, list[Hashable]] = {}
    for d in decisions:
        if decided.get((d.upd_id, d.invoice_id)) == "confirmed" and d.invoice_id in by_id:
            _push(confirmed_for, d.upd_id, d.invoice_id)

    facts: list[_Upd] = []
    for u in upds:
        name = seller_name_key(u.seller_name)
        facts.append(_facts(u, name, own_invoices(u, name)))
    result = DocMatchResult()

    for f in facts:
        u = f.src
        found: dict[Hashable, str] = {}

        def add(invoice_id: Hashable, method: str) -> None:
            if decided.get((u.id, invoice_id)) == "rejected":
                return
            previous = found.get(invoice_id)
            if previous is None or _RANK[method] < _RANK[previous]:
                found[invoice_id] = method

        for invoice_id in confirmed_for.get(u.id, ()):
            add(invoice_id, "manual")
        for r in f.num_refs:
            strong = norm_doc_number(r.number)
            loose = loose_doc_number(r.number)
            if not strong:
                continue
            if r.date is not None:
                # With the date agreeing the digits alone are enough («Т-000155» = «155»), or a single OCR slip.
                dated = [
                    i for i in f.own
                    if i.src.date == r.date
                    and (i.strong == strong or (loose != "" and i.loose == loose) or _nearly_equal(i.strong, strong))
                ]
                # A scanned invoice has no seller: the same long number on the same day still pins it.
                if len(strong) >= 5:
                    dated += [i for i in by_number_date.get(f"{strong}|{r.date}", ()) if _unknown_seller(i)]
                for i in dated:
                    add(i.id, "basis_date")
                if dated:
                    continue
            if _needs_date(r):
                continue
            core = _core_key(strong)
            for i in f.own:
                if (i.strong == strong or (len(core) >= 5 and i.core == core)) and _within(BASIS_DAYS, i.src.date, u.date):
                    add(i.id, "basis")
        paid_by: dict[Hashable, list[str]] = {}
        for p in f.pay_refs:
            for i, key, day in paid_with(p, PAY_DAYS):
                if not _same_seller(f, i) and not (_unknown_seller(i) and day == p.date):
                    continue
                add(i.id, "payment")
                _push(paid_by, i.id, key)
        # Marketplace: the invoice is the platform's, the UPD a shipping seller's — named and paid, it is theirs.
        for r in f.num_refs:
            strong = norm_doc_number(r.number)
            if r.date is None or len(strong) < MARKETPLACE_PAID_NUMBER:
                continue
            for i in by_number_date.get(f"{strong}|{r.date}", ()):
                if _same_seller(f, i) or _unknown_seller(i):
                    continue
                if len(strong) >= MARKETPLACE_NUMBER or any(x[0].id == i.id for p in f.pay_refs for x in paid_with(p)):
                    add(i.id, "basis_date")
        # The invoices the basis (or the buyer) names win over payment orders once they account for the UPD's total:
        # sellers list the day's other payments too («Мицар» names 912 on the UPD of 913's invoice).
        named = [by_id[invoice_id] for invoice_id, method in found.items() if method != "payment"]
        if named:
            named_total = sum(i.src.total for i in named) if all(i.src.total is not None for i in named) else None
            unexplained = u.total_gross is not None and named_total is not None and u.total_gross - named_total > AMOUNT_TOLERANCE
            named_paid = {key for i in named for key in i.pay_keys}
            for invoice_id, keys in paid_by.items():
                if found.get(invoice_id) == "payment" and (not unexplained or all(k in named_paid for k in keys)):
                    del found[invoice_id]
        result.links += [DocLink(u.id, invoice_id, method) for invoice_id, method in found.items()]

    linked_upds = {link.upd_id for link in result.links}
    linked_invoices = {link.invoice_id for link in result.links}

    def is_open(u: MatchUpd, i: _Inv) -> bool:
        return decided.get((u.id, i.id)) != "rejected"

    # Amount only: the one invoice of the seller with that total near the UPD's date, and the only UPD for it.
    tentative: list[DocSuggestion] = []
    for f in facts:
        u = f.src
        if u.id in linked_upds or u.total_gross is None or u.date is None:
            continue
        fits = [
            i for i in f.own
            if i.src.total is not None and i.src.date is not None and abs(i.src.total - u.total_gross) <= AMOUNT_TOLERANCE
            and _within(AMOUNT_DAYS, i.src.date, u.date) and i.id not in linked_invoices and is_open(u, i)
        ]
        if len(fits) == 1:
            tentative.append(DocSuggestion(u.id, fits[0].id))
    per_invoice: dict[Hashable, int] = {}
    for s in tentative:
        per_invoice[s.invoice_id] = per_invoice.get(s.invoice_id, 0) + 1
    result.suggestions = [s for s in tentative if per_invoice[s.invoice_id] == 1]

    # The UPD points at another seller's invoice and at nothing of its own.
    for f in facts:
        u = f.src
        if u.id in linked_upds:
            continue
        hits: dict[Hashable, _Inv] = {}
        for p in f.pay_refs:
            for i, _, _ in paid_with(p):
                hits.setdefault(i.id, i)
        for r in f.num_refs:
            if r.date is None or _needs_date(r):
                continue
            for i in by_number_date.get(f"{norm_doc_number(r.number)}|{r.date}", ()):
                hits.setdefault(i.id, i)
        for i in hits.values():
            if not _same_seller(f, i) and not _unknown_seller(i) and is_open(u, i):
                result.conflicts.append(DocConflict(u.id, i.id))
    return result
