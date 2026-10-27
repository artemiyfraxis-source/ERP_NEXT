"""«Цепочка документов» (ported from Roller_Buyer doc-chain.ts): computed on read in one pass.

Kaiten invoices with their payment orders, the UPDs (and acts, invoices-factures) that close them, and what disagrees.
Roller_Buyer orders are not ported: in ERP the chain continues past the UPD into receiving (app/workflows/
purchasing_documents_receiving.py), attached by the HTTP layer to the chains it returns. Only the buyer's decisions
are stored (UpdInvoiceLinkRow); everything else is recomputed from the documents' own data on every read.

`load_world` reads the tables once; `compute_world` is pure (lists in, chains out) so that 1000 × 700 documents stay
well under a second."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session, load_only

from app.shared.events import AuditEvent
from app.shared.identity import Actor

from .doc_match import (
    CHAIN_TOLERANCE_KOP,
    MatchDecision,
    MatchInvoice,
    MatchPayment,
    MatchUpd,
    days_between,
    match_documents,
)
from .doc_refs import PaymentOrder, invoice_payments, loose_doc_number, norm_doc_number, parse_basis_refs, parse_payment_refs
from .infrastructure import (
    InvoiceFileRow,
    InvoiceRow,
    SupplierRow,
    UpdDocumentRow,
    UpdInvoiceLinkRow,
    UpdLineRow,
)
from .upd_checks import classify_buyers, registry_status
from .upd_sellers import (
    effective_header,
    resolve_sellers,
    seller_facts,
    seller_inn_of,
    seller_name_key,
)

CHAIN_STATES = ("closed", "partial", "over", "waiting", "missing", "unpaid", "return", "orphan")
# A paid invoice without closing documents is a problem after this many days.
CLOSING_DOCS_DAYS = 14
# Kinds that are not closing documents of a purchase.
NOT_CLOSING = frozenset({"М-15", "Прочее"})
CORRECTION = "Корректировочный СФ"
FACTURA = "Счёт-фактура"
TORG12 = "Товарная накладная"
# The UPD card lists the seller's invoices this many days around the document.
CANDIDATE_DAYS = 180
CANDIDATE_LIMIT = 12
# Sellers date our payment by the day the money came: a few days' slip is the same payment.
PAYMENT_DAYS = 3


class DocumentNotFound(LookupError):
    pass


# ---- words ---------------------------------------------------------------------------------------------------------

def rub(kop: int) -> str:
    """Kopecks → «1 234,50 ₽» (thousands by a no-break space, as ru-RU writes them)."""
    sign = "−" if kop < 0 else ""
    rubles, rest = divmod(abs(kop), 100)
    return f"{sign}{rubles:,}".replace(",", " ") + f",{rest:02d} ₽"


def ru_date(iso: str | None) -> str:
    return ".".join(reversed(iso.split("-"))) if iso else "без даты"


# ---- status and buyer of a UPD: one copy, in upd_checks.py ---------------------------------------------------------

def upd_status(row: Any) -> str:
    """duplicate | not_purchase | verified | waiting | parsed | needs_review."""
    return registry_status(row.status, row.verified_at, row.twin_of)


# ---- facts ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ChainInvoice:
    id: UUID
    title: str
    number: str | None
    date: str | None
    seller_name: str | None
    seller_inn: str | None
    supplier_id: UUID | None
    supplier_name: str | None
    total: int | None  # kopecks with VAT
    status: str
    payments: tuple[PaymentOrder, ...]
    invoice_file_id: UUID | None  # the downloaded invoice PDF of the card (InvoiceFileRow id)

    def as_json(self) -> dict:
        return {
            "id": str(self.id), "title": self.title, "number": self.number, "date": self.date,
            "seller_name": self.seller_name, "seller_inn": self.seller_inn,
            "supplier_id": str(self.supplier_id) if self.supplier_id else None, "supplier_name": self.supplier_name,
            "total": self.total, "status": self.status, "payments": [p.as_json() for p in self.payments],
            "invoice_file_id": str(self.invoice_file_id) if self.invoice_file_id else None,
        }


@dataclass(frozen=True)
class ChainUpd:
    id: str
    file_name: str
    number: str
    date: str | None
    kind: str
    origin: str
    status: str
    seller_name: str
    seller_inn: str
    supplier_id: UUID | None
    total_gross: int | None  # kopecks with VAT; the buyer's version when saved
    services_only: bool  # an act-like document: it closes the invoice but brings no goods
    basis: str
    payment_doc: str
    buyer_kind: str
    buyer_org_inn: str | None

    def as_json(self, method: str) -> dict:
        return {
            "id": self.id, "file_name": self.file_name, "number": self.number, "date": self.date, "kind": self.kind,
            "origin": self.origin, "status": self.status, "seller_name": self.seller_name, "seller_inn": self.seller_inn,
            "supplier_id": str(self.supplier_id) if self.supplier_id else None, "total_gross": self.total_gross,
            "services_only": self.services_only, "method": method, "basis": self.basis, "payment_doc": self.payment_doc,
            "buyer_kind": self.buyer_kind, "buyer_org_inn": self.buyer_org_inn,
        }


@dataclass(frozen=True)
class Check:
    code: str
    level: str  # error | warning | info
    text: str

    def as_json(self) -> dict:
        return {"code": self.code, "level": self.level, "text": self.text}


@dataclass
class Chain:
    key: str  # «invoice:<id>» or «upd:<id>» for a document without an invoice
    invoice: ChainInvoice | None
    upds: list[tuple[ChainUpd, str]]  # the document and how it was found
    suggestions: list[ChainUpd]
    closed_gross: int
    state: str
    checks: list[Check]

    @property
    def problem(self) -> bool:
        """Money paid without documents, sums that disagree, or any warning. A document without an invoice is a
        problem only when its check says so (older than Kaiten, a landlord's act — info)."""
        return self.state in ("missing", "partial", "over") or any(c.level != "info" for c in self.checks)

    @property
    def sort_date(self) -> str:
        return (self.invoice.date if self.invoice else None) or (self.upds[0][0].date if self.upds else None) or ""

    def as_json(self, receiving: dict[str, Any] | None = None) -> dict:
        def upd(u: ChainUpd, method: str) -> dict:
            out = u.as_json(method)
            if receiving is not None:
                out["receiving"] = receiving.get(u.id)
            return out

        return {
            "key": self.key, "invoice": self.invoice.as_json() if self.invoice else None,
            "upds": [upd(u, method) for u, method in self.upds], "suggestions": [upd(u, "amount") for u in self.suggestions],
            "closed_gross": self.closed_gross, "state": self.state, "problem": self.problem,
            "checks": [c.as_json() for c in self.checks],
        }


@dataclass
class World:
    chains: list[Chain]
    invoices: dict[UUID, ChainInvoice]
    upds: dict[str, ChainUpd]
    links: list  # doc_match.DocLink
    suggestions: list  # doc_match.DocSuggestion
    decisions: dict[tuple[str, UUID], str]
    upd_checks: dict[str, list[Check]] = field(default_factory=dict)
    chain_of_invoice: dict[UUID, Chain] = field(default_factory=dict)


def _doc_name(u: ChainUpd) -> str:
    return f"{u.kind or 'Документ'} № {u.number or '—'} от {ru_date(u.date)}"


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return (value.date() if isinstance(value, datetime) else value).isoformat()


# ---- loading -------------------------------------------------------------------------------------------------------

_PDF = re.compile(r"\.pdf$", re.IGNORECASE)


def invoice_pdf(files: list[InvoiceFileRow]) -> InvoiceFileRow | None:
    """The invoice PDF of a card: a file called an invoice, else another PDF; only downloaded files count."""
    stored = [f for f in files if f.file_id is not None]
    return (
        next((f for f in stored if f.kind == "invoice" and _PDF.search(f.name)), None)
        or next((f for f in stored if f.kind == "other" and _PDF.search(f.name)), None)
    )


def load_invoice_files(session: Session, invoice_ids: list[UUID] | None = None) -> dict[UUID, list[InvoiceFileRow]]:
    query = select(InvoiceFileRow).order_by(InvoiceFileRow.invoice_id, InvoiceFileRow.seq, InvoiceFileRow.name)
    if invoice_ids is not None:
        query = query.where(InvoiceFileRow.invoice_id.in_(invoice_ids))
    out: dict[UUID, list[InvoiceFileRow]] = {}
    for row in session.scalars(query):
        out.setdefault(row.invoice_id, []).append(row)
    return out


def chain_invoice(row: InvoiceRow, files: list[InvoiceFileRow], supplier_name: str | None) -> ChainInvoice:
    pdf = invoice_pdf(files)
    return ChainInvoice(
        id=row.id, title=row.title, number=row.number, date=_iso(row.doc_date), seller_name=row.seller_name,
        seller_inn=row.seller_inn, supplier_id=row.supplier_id, supplier_name=supplier_name, total=row.total,
        status=row.status, payments=tuple(invoice_payments([f.name for f in files])), invoice_file_id=pdf.id if pdf else None,
    )


def _line_kinds(session: Session) -> dict[str, tuple[int, int]]:
    """Goods vs services per document, for the version in force (the buyer's lines when saved): (lines, services)."""
    services = func.sum(case((UpdLineRow.is_service, 1), else_=0))
    by_doc: dict[str, dict[str, tuple[int, int]]] = {}
    for doc_id, source, count, service_count in session.execute(
        select(UpdLineRow.document_id, UpdLineRow.source, func.count(), services)
        .group_by(UpdLineRow.document_id, UpdLineRow.source)
    ):
        by_doc.setdefault(doc_id, {})[source] = (int(count), int(service_count or 0))
    return {doc_id: kinds.get("manual") or kinds.get("google") or (0, 0) for doc_id, kinds in by_doc.items()}


@dataclass
class Facts:
    """What the chain is computed from, as read from the tables."""

    invoices: list[ChainInvoice]
    upds: list[ChainUpd]  # closing documents only (duplicates, «not a purchase», queued, М-15 left out)
    decisions: dict[tuple[str, UUID], str]
    all_upds: dict[str, ChainUpd] = field(default_factory=dict)  # every document, for its card


# resolve_sellers is pure but compares INNs pairwise (OCR slips): the last answer is reused while the registry and
# the supplier directory stay the same — every read of the chain, the invoices and a UPD card asks it again.
_seller_memo: tuple[tuple, list] | None = None


def _seller_verdicts(docs: list, known: list[tuple[str, str | None]]) -> list:
    global _seller_memo
    key = (tuple(docs), tuple(known))
    memo = _seller_memo
    if memo is not None and memo[0] == key:
        return memo[1]
    verdicts = resolve_sellers(docs, known)
    _seller_memo = (key, verdicts)
    return verdicts


def load_facts(session: Session) -> Facts:
    suppliers = list(session.execute(select(SupplierRow.id, SupplierRow.name, SupplierRow.inn)))
    supplier_names = {sid: name for sid, name, _ in suppliers}
    rows = list(session.scalars(select(InvoiceRow).options(load_only(
        InvoiceRow.id, InvoiceRow.title, InvoiceRow.number, InvoiceRow.doc_date, InvoiceRow.seller_name,
        InvoiceRow.seller_inn, InvoiceRow.supplier_id, InvoiceRow.total, InvoiceRow.status,
    ))))
    files = load_invoice_files(session)
    invoices = [chain_invoice(row, files.get(row.id, []), supplier_names.get(row.supplier_id)) for row in rows]

    docs = list(session.scalars(select(UpdDocumentRow)))
    kinds = _line_kinds(session)
    seller_rows = [seller_facts(d) for d in docs]
    buyers = classify_buyers([(f.buyer_inn, f.seller_inn) for f in seller_rows])
    sellers = _seller_verdicts(seller_rows, [(name, inn) for _, name, inn in suppliers])
    upds: list[ChainUpd] = []
    all_upds: dict[str, ChainUpd] = {}
    for doc, buyer, seller in zip(docs, buyers, sellers):
        header = effective_header(doc)
        lines, services = kinds.get(doc.id, (0, 0))
        status = upd_status(doc)
        upd = ChainUpd(
            id=doc.id, file_name=doc.file_name, number=header["number"] or "", date=header["date"], kind=doc.kind,
            origin=doc.origin, status=status, seller_name=header["seller_name"] or "",
            # A misread INN still matches the seller's invoices.
            seller_inn=seller_inn_of(doc, seller), supplier_id=doc.supplier_id, total_gross=header["total_gross"],
            services_only=doc.kind == "Акт" or (lines > 0 and services == lines), basis=doc.basis or "",
            payment_doc=doc.payment_doc or "", buyer_kind=buyer.kind, buyer_org_inn=buyer.inn,
        )
        all_upds[doc.id] = upd
        if status in ("duplicate", "not_purchase", "waiting") or doc.kind in NOT_CLOSING:
            continue
        upds.append(upd)
    decisions = {
        (upd_id, invoice_id): decision
        for upd_id, invoice_id, decision in session.execute(
            select(UpdInvoiceLinkRow.upd_id, UpdInvoiceLinkRow.invoice_id, UpdInvoiceLinkRow.decision)
        )
    }
    return Facts(invoices, upds, decisions, all_upds)


# ---- the computation -----------------------------------------------------------------------------------------------

def _foreign_payments(u: ChainUpd, inv: ChainInvoice) -> list[str]:
    """The UPD's payment orders that are not among the invoice's own."""
    if not inv.payments:
        return []
    refs = [p for p in parse_payment_refs(u.payment_doc) if p.date is not None]
    if not refs:
        return []

    def ours(p) -> bool:
        return any(
            loose_doc_number(x.number) == loose_doc_number(p.number) and abs(days_between(x.date, p.date)) <= PAYMENT_DAYS
            for x in inv.payments
        )

    # One of them belonging to the invoice is enough: sellers list the day's other payments too.
    if any(ours(p) for p in refs):
        return []
    return [f"№ {p.number} от {ru_date(p.date)}" for p in refs]


def _payment_link_note(u: ChainUpd, method: str, inv: ChainInvoice) -> Check | None:
    """Why a document found by our payment names something else in its basis — said, not hidden."""
    if method != "payment" or not inv.number:
        return None
    own = (norm_doc_number(inv.number), loose_doc_number(inv.number))
    other = [
        r for r in parse_basis_refs(u.basis)
        if r.kind in ("invoice", "order") and norm_doc_number(r.number) not in own and loose_doc_number(r.number) not in own
    ]
    if not other:
        return None
    named = "; ".join(r.raw for r in other)
    return Check("paid_by_other_invoice", "info", f"{_doc_name(u)} найден по платёжке счёта; в его основании — {named}")


def _upd_checks(u: ChainUpd) -> list[Check]:
    checks: list[Check] = []
    # Purchases for a related company are normal: noted, not flagged. OCR slips of our INN are not another buyer.
    if u.buyer_kind == "other":
        checks.append(Check("buyer_not_us", "info", f"{_doc_name(u)}: покупатель — другая организация, ИНН {u.buyer_org_inn}"))
    if u.kind == CORRECTION:
        checks.append(Check("correction", "info", f"{_doc_name(u)} — корректировка, в сумму закрытия не входит"))
    return checks


def _share_of(gross: int, invoice_id: UUID, invoice_ids: list[UUID], invoices: dict[UUID, ChainInvoice]) -> int:
    """A UPD closing several invoices is split between them in proportion to their totals."""
    if len(invoice_ids) <= 1:
        return gross
    totals = [invoices[i].total if i in invoices else None for i in invoice_ids]
    if any(t is None or t <= 0 for t in totals):
        return _round(gross / len(invoice_ids))
    return _round(gross * (invoices[invoice_id].total or 0) / sum(totals))


def _round(value: float) -> int:
    """Math.round: halves up."""
    return math.floor(value + 0.5)


# An invoice uploaded in procurement whose lines were cancelled or which a newer invoice replaced (0155) is no live
# invoice: it closes nothing and has no chain; it stays listed in «Счета» with its status.
VOID_STATUSES = frozenset({"cancelled", "replaced"})


def compute_world(facts: Facts, today: str) -> World:
    invoices = {i.id: i for i in facts.invoices}
    upds = {u.id: u for u in facts.upds}
    decisions = facts.decisions
    live = [i for i in facts.invoices if i.status not in VOID_STATUSES]
    live_ids = {i.id for i in live}
    result = match_documents(
        [
            MatchUpd(u.id, u.seller_inn, u.seller_name, u.supplier_id, u.date, u.total_gross, u.basis, u.payment_doc)
            for u in facts.upds
        ],
        [
            MatchInvoice(
                i.id, i.seller_inn, i.seller_name, i.supplier_id, i.number, i.date, i.total,
                tuple(MatchPayment(p.number, p.date, p.sum) for p in i.payments),
            )
            for i in live
        ],
        [
            MatchDecision(upd_id, invoice_id, d) for (upd_id, invoice_id), d in decisions.items()
            if upd_id in upds and invoice_id in live_ids
        ],
    )

    links_by_invoice: dict[UUID, list] = {}
    invoices_by_upd: dict[str, list[UUID]] = {}
    for link in result.links:
        links_by_invoice.setdefault(link.invoice_id, []).append(link)
        invoices_by_upd.setdefault(link.upd_id, []).append(link.invoice_id)
    suggestions_by_invoice: dict[UUID, list[str]] = {}
    for s in result.suggestions:
        suggestions_by_invoice.setdefault(s.invoice_id, []).append(s.upd_id)
    dated = sorted(i.date for i in facts.invoices if i.date)
    first_kaiten = dated[0] if dated else None
    # A scan without the seller's INN is still known by its name.
    sellers_with_invoices: set[str] = set()
    for i in facts.invoices:
        if i.seller_inn:
            sellers_with_invoices.add(i.seller_inn)
        if i.supplier_id is not None:
            sellers_with_invoices.add(f"s{i.supplier_id}")
        if seller_name_key(i.seller_name):
            sellers_with_invoices.add(f"n{seller_name_key(i.seller_name)}")

    per_upd: dict[str, list[Check]] = {}

    def about(upd_id: str, into: list[Check], *add: Check) -> None:
        into.extend(add)
        per_upd.setdefault(upd_id, []).extend(add)

    chains: list[Chain] = []
    chain_of_invoice: dict[UUID, Chain] = {}
    for inv in live:
        linked = [(upds[link.upd_id], link.method) for link in links_by_invoice.get(inv.id, [])]
        checks: list[Check] = []
        # The buyer's lines decide; a ТОРГ-12 of the same number and date as its счёт-фактура is one shipment.
        counted = [u for u, _ in linked if u.kind != CORRECTION]
        facturas = {f"{norm_doc_number(u.number)}|{u.date}" for u in counted if u.kind == FACTURA}
        closed_gross = 0
        unknown_sum = False
        for u in counted:
            if u.kind == TORG12 and f"{norm_doc_number(u.number)}|{u.date}" in facturas:
                continue
            if u.total_gross is None:
                unknown_sum = True
            else:
                closed_gross += _share_of(u.total_gross, inv.id, invoices_by_upd.get(u.id, [inv.id]), invoices)
        for u, method in linked:
            about(u.id, checks, *_upd_checks(u))
            note = _payment_link_note(u, method, inv)
            if note:
                about(u.id, checks, note)
            other_name = seller_name_key(inv.seller_name) != seller_name_key(u.seller_name)
            if inv.seller_inn and u.seller_inn and inv.seller_inn != u.seller_inn and other_name:
                about(u.id, checks, Check(
                    "other_seller", "info",
                    f"{_doc_name(u)} от {u.seller_name}, счёт — от {inv.seller_name or 'другого продавца'} (маркетплейс?)",
                ))
            foreign = _foreign_payments(u, inv)
            if foreign:
                paid = ", ".join(f"№ {p.number} от {ru_date(p.date)}" for p in inv.payments)
                about(u.id, checks, Check(
                    "payment_not_in_invoice", "warning",
                    f"{_doc_name(u)} ссылается на платёжку {', '.join(foreign)}, а счёт оплачен {paid}",
                ))

        paid_sum = sum(p.sum for p in inv.payments)
        paid = inv.status == "paid" or bool(inv.payments)
        paid_on = max((p.date for p in inv.payments), default=None) or inv.date
        if inv.status == "return":
            state = "return"
        elif linked:
            if inv.total is None or unknown_sum:
                state = "closed"
            elif closed_gross < inv.total - CHAIN_TOLERANCE_KOP:
                state = "partial"
            elif closed_gross > inv.total + CHAIN_TOLERANCE_KOP:
                state = "over"
            else:
                state = "closed"
        elif paid:
            days = days_between(paid_on, today) if paid_on else CLOSING_DOCS_DAYS
            state = "missing" if days >= CLOSING_DOCS_DAYS else "waiting"
            if state == "missing":
                when = f" {days} дн. назад" if paid_on else ""
                checks.insert(0, Check("no_closing", "error", f"Оплачен{when}, закрывающих документов нет"))
        else:
            state = "unpaid"

        if state == "partial":
            checks.insert(0, Check(
                "partial", "warning", f"Закрыто {rub(closed_gross)} из {rub(inv.total)} — ждём остальные документы",
            ))
        if state == "over":
            checks.insert(0, Check(
                "over", "error",
                f"Закрывающих на {rub(closed_gross - inv.total)} больше счёта ({rub(closed_gross)} при счёте {rub(inv.total)})",
            ))
        if inv.total is not None and inv.payments and abs(paid_sum - inv.total) >= CHAIN_TOLERANCE_KOP:
            checks.append(Check("payment_sum", "warning", f"Оплачено {rub(paid_sum)}, счёт на {rub(inv.total)}"))
        if inv.total is None and state != "return":
            checks.append(Check("invoice_unparsed", "info", "Сумма счёта не прочитана из PDF — по сумме не сверить"))
        if unknown_sum:
            checks.append(Check("upd_sum_unknown", "info", "У одного из документов нет суммы — по сумме не сверить"))

        chain = Chain(
            key=f"invoice:{inv.id}", invoice=inv, upds=sorted(linked, key=lambda pair: pair[0].date or ""),
            suggestions=[upds[upd_id] for upd_id in suggestions_by_invoice.get(inv.id, [])],
            closed_gross=closed_gross, state=state, checks=checks,
        )
        chains.append(chain)
        chain_of_invoice[inv.id] = chain

    # Documents that close no invoice.
    conflicts_by_upd: dict[str, list[UUID]] = {}
    for c in result.conflicts:
        conflicts_by_upd.setdefault(c.upd_id, []).append(c.invoice_id)
    suggestion_by_upd = {s.upd_id: s.invoice_id for s in result.suggestions}
    for u in facts.upds:
        if u.id in invoices_by_upd:
            continue
        checks = []

        def note(*add: Check, _u=u, _checks=checks) -> None:
            about(_u.id, _checks, *add)

        note(*_upd_checks(u))
        refs = [r for r in parse_basis_refs(u.basis) if r.kind in ("invoice", "order")]
        named = f" (в основании: {'; '.join(r.raw for r in refs)})" if refs else ""
        name = seller_name_key(u.seller_name)
        seller_known = (
            u.seller_inn in sellers_with_invoices
            or (u.supplier_id is not None and f"s{u.supplier_id}" in sellers_with_invoices)
            or (name != "" and f"n{name}" in sellers_with_invoices)
        )
        if first_kaiten and u.date and u.date < first_kaiten:
            note(Check(
                "no_invoice", "info", f"Счёт не найден: документ старше первого счёта в Kaiten ({ru_date(first_kaiten)}){named}",
            ))
        elif not seller_known:
            note(Check(
                "no_invoice", "info", f"У продавца нет счетов в Kaiten — не закупка через Kaiten (аренда, услуги)?{named}",
            ))
        else:
            note(Check("no_invoice", "warning", f"Счёт этого документа в Kaiten не найден{named}"))
        for invoice_id in conflicts_by_upd.get(u.id, []):
            other = invoices[invoice_id]
            seller = other.seller_name or other.supplier_name or "продавец не прочитан"
            note(Check(
                "seller_mismatch", "error",
                f"Платёжка или счёт из документа относятся к счёту другого продавца: "
                f"{seller}, № {other.number or '—'} от {ru_date(other.date)}",
            ))
        maybe = suggestion_by_upd.get(u.id)
        if maybe is not None:
            inv = invoices[maybe]
            note(Check(
                "maybe_invoice", "info",
                f"Возможно, это счёт № {inv.number or '—'} от {ru_date(inv.date)} — совпала сумма; свяжите в карточке документа",
            ))
        chains.append(Chain(
            key=f"upd:{u.id}", invoice=None, upds=[(u, "manual")], suggestions=[], closed_gross=u.total_gross or 0,
            state="orphan", checks=checks,
        ))

    return World(
        chains=chains, invoices=invoices, upds=upds, links=result.links, suggestions=result.suggestions,
        decisions=decisions, upd_checks=per_upd, chain_of_invoice=chain_of_invoice,
    )


def load_world(session: Session, today: date) -> tuple[World, Facts]:
    facts = load_facts(session)
    return compute_world(facts, today.isoformat()), facts


# ---- the list ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ChainQuery:
    view: str = "problems"  # problems | all
    q: str = ""
    supplier_id: UUID | None = None
    supplier_inn: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    closing: str | None = None  # edo | paper | none
    buyer: str | None = None  # ours | other | unknown | an INN


def _buyer_of(c: Chain) -> str:
    """Kaiten invoices are ours; a chain without one is whoever its document is for."""
    if c.invoice:
        return "ours"
    u = c.upds[0][0] if c.upds else None
    if u is None or u.buyer_kind == "unknown":
        return "unknown"
    return "ours" if u.buyer_kind == "ours" else (u.buyer_org_inn or "unknown")


def _matches(c: Chain, q: ChainQuery) -> bool:
    if q.buyer:
        b = _buyer_of(c)
        if (b in ("ours", "unknown")) if q.buyer == "other" else b != q.buyer:
            return False
    if q.closing == "none" and c.upds:
        return False
    if q.closing == "edo" and not any(u.origin == "edo" for u, _ in c.upds):
        return False
    if q.closing == "paper" and not any(u.origin == "google" for u, _ in c.upds):
        return False
    day = c.sort_date
    if q.date_from and day and day < q.date_from:
        return False
    if q.date_to and day and day > q.date_to:
        return False
    words = q.q.lower().split()
    if not words:
        return True
    inv = c.invoice
    parts = [
        inv.title, inv.number, inv.seller_name, inv.seller_inn, inv.supplier_name, *(p.number for p in inv.payments),
    ] if inv else []
    for u, _ in c.upds:
        parts += [u.number, u.seller_name, u.seller_inn, u.file_name]
    hay = " ".join(part for part in parts if part).lower()
    return all(word in hay for word in words)


def _own(c: Chain, q: ChainQuery) -> bool:
    if c.invoice:
        return c.invoice.supplier_id == q.supplier_id
    return any(
        u.supplier_id == q.supplier_id or (q.supplier_inn is not None and u.seller_inn == q.supplier_inn) for u, _ in c.upds
    )


def list_chains(world: World, q: ChainQuery) -> tuple[list[Chain], dict]:
    """The chains to show (problems first, then the newest) and the summary of the current filters."""
    # Everything but the «closed by» switch: its own counts come from here.
    base = [c for c in world.chains if (q.supplier_id is None or _own(c, q)) and _matches(c, replace(q, closing=None))]
    by_closing = {"edo": 0, "paper": 0, "none": 0}
    for c in (x for x in base if x.problem) if q.view == "problems" else base:
        if not c.upds:
            by_closing["none"] += 1
        if any(u.origin == "edo" for u, _ in c.upds):
            by_closing["edo"] += 1
        if any(u.origin == "google" for u, _ in c.upds):
            by_closing["paper"] += 1
    filtered = [c for c in base if _matches(c, replace(q, q="", date_from=None, date_to=None))] if q.closing else base
    by_state = dict.fromkeys(CHAIN_STATES, 0)
    for c in filtered:
        by_state[c.state] += 1
    problems = [c for c in filtered if c.problem]
    shown = problems if q.view == "problems" else filtered
    shown = sorted(shown, key=lambda c: c.sort_date, reverse=True)
    shown.sort(key=lambda c: not c.problem)
    return shown, {"by_state": by_state, "problems": len(problems), "by_closing": by_closing}


# ---- one UPD's invoices --------------------------------------------------------------------------------------------

def _invoice_json(i: ChainInvoice, method: str, decision: str | None) -> dict:
    return {**i.as_json(), "method": method, "decision": decision}


def upd_invoices(session: Session, world: World, facts: Facts, upd_id: str) -> dict:
    """Invoices for the UPD card: linked, possible (the seller's near the date, the amount suggestion first), and
    rejected by the buyer — with the references read from the document and what disagrees."""
    row = session.get(UpdDocumentRow, upd_id)
    if row is None:
        raise DocumentNotFound("УПД не найден")

    def decided(invoice_id: UUID) -> str | None:
        return world.decisions.get((upd_id, invoice_id))

    own_links = [link for link in world.links if link.upd_id == upd_id]
    linked = [_invoice_json(world.invoices[link.invoice_id], link.method, decided(link.invoice_id)) for link in own_links]
    linked_ids = {link.invoice_id for link in own_links}
    rejected_ids = [
        invoice_id for (uid, invoice_id), d in world.decisions.items()
        if uid == upd_id and d == "rejected" and invoice_id in world.invoices
    ]
    rejected = [_invoice_json(world.invoices[i], "manual", "rejected") for i in rejected_ids]
    upd = facts.all_upds.get(upd_id)
    inn = upd.seller_inn if upd else ""
    name = seller_name_key(upd.seller_name if upd else "")
    day = upd.date if upd else None
    suggested = {s.invoice_id for s in world.suggestions if s.upd_id == upd_id}

    def candidate(i: ChainInvoice) -> bool:
        if i.id in linked_ids or i.id in rejected_ids:
            return False
        if i.id in suggested:
            return True
        same = (inn and i.seller_inn == inn) or (row.supplier_id is not None and i.supplier_id == row.supplier_id) or (
            name != "" and seller_name_key(i.seller_name) == name
        )
        return bool(same) and (not day or not i.date or abs(days_between(i.date, day)) <= CANDIDATE_DAYS)

    def distance(i: ChainInvoice) -> int:
        start = i.date or day or "2000-01-01"
        end = day or i.date or "2000-01-01"
        return abs(days_between(start, end))

    found = sorted((i for i in world.invoices.values() if candidate(i)), key=lambda i: (i.id not in suggested, distance(i)))
    return {
        "linked": linked,
        # The seller's invoices near the date; `suggested` — the amount agrees too (the one candidate by sum).
        "candidates": [{**_invoice_json(i, "amount", None), "suggested": i.id in suggested} for i in found[:CANDIDATE_LIMIT]],
        "rejected": rejected,
        "basis_refs": [r.as_json() for r in parse_basis_refs(row.basis or "")],
        "payment_refs": [r.as_json() for r in parse_payment_refs(row.payment_doc or "")],
        "checks": [c.as_json() for c in world.upd_checks.get(upd_id, [])],
    }


def set_decision(uow, upd_id: str, invoice_id: UUID, decision: str | None, actor: Actor, now: datetime) -> None:
    """The buyer's word on a pair: confirmed, rejected, or None to let the data decide again. Audited."""
    session = uow.session
    if session.get(UpdDocumentRow, upd_id) is None:
        raise DocumentNotFound("УПД не найден")
    if session.get(InvoiceRow, invoice_id) is None:
        raise DocumentNotFound("Счёт не найден")
    previous = session.scalar(select(UpdInvoiceLinkRow.decision).where(
        UpdInvoiceLinkRow.upd_id == upd_id, UpdInvoiceLinkRow.invoice_id == invoice_id,
    ))
    if previous == decision:
        return
    session.execute(delete(UpdInvoiceLinkRow).where(
        UpdInvoiceLinkRow.upd_id == upd_id, UpdInvoiceLinkRow.invoice_id == invoice_id,
    ))
    if decision:
        session.add(UpdInvoiceLinkRow(
            id=uuid4(), upd_id=upd_id, invoice_id=invoice_id, decision=decision, decided_at=now, decided_by=actor.id,
        ))
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=invoice_id, event_type="purchasing.upd_invoice_decided", actor=actor, occurred_at=now,
        details={"upd_id": upd_id, "decision": decision or "", "previous": previous or ""},
    ))
