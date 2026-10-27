"""«Принять по УПД» (owner 26.10.2026): «Приехала поставка, там УПД — отсканировал, и автоматом предложил на приёмку
позиции из листа ожидания».

The recognised УПД (`warehouse.scan_recognition` answer: the header and its rows) is laid over the open receiving
lines («В пути» / «Ожидание», not yet on stock) — read-only here; the storekeeper confirms and `accept_delivery` moves
the goods, nothing else does.

Which lines are the delivery's candidates:
* by the invoice — the «Основание передачи» of the УПД names the invoice («Счёт № 123 от 01.10.2026»; several split by
  «,» / «;»): the lines of the procurement lines with that invoice number (strong key `norm_doc_number`), the dates no
  further than 31 days apart when both are known, the line's supplier agreeing with the seller when both are known
  (or the invoice dates the same day — then the order's supplier text may be anything typed);
* by the supplier — the seller of the УПД (its INN, else its name, through the purchasing suppliers' book and their
  aliases; else the seller-name key) → every open line of that supplier across all orders. Nothing is known of the
  seller — the supplier of the «Ожидаются» group the scan was started from.

Which line a УПД row is (per row, best tier first): a part number (`code_key`: the row's article / supplier code /
codes in its name against the line's article, the order's design article and codes in its names) → «по артикулу»;
else the name words (Dice ≥ 0,6 → «по названию», ≥ 0,3 → «слабое»; both carry part numbers and none agree — no match).
A line of the invoice matched by article or name is «по счёту». Every line at the row's best tier (the same item in
several orders) shares the row: the line whose remaining order equals the row's quantity takes it all when there is
one, else the invoice's lines first, then the oldest expected arrival / order, each up to what is still ordered; what
is left goes onto the last one with a surplus warning. Several rows of one line add up. A line already taken at a
higher tier is not offered to a row matching it lower.

Quantities are compared in the line's unit: the row's shelf quantity, else its invoiced one when that unit is the
line's; neither — the shelf quantity with a warning. Ticked (to be accepted) — a line matched by invoice / article /
name with a known quantity in its unit; a weak match, an unread quantity or another unit stay unticked with the
reason. Waiting lines no row matched stay in the proposal unticked: their «По УПД» is never 0, so no false «Недовоз».
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select

from app.modules.procurement.document_items import is_code, unit_key
from app.modules.procurement.infrastructure import ProcurementLineRow as PL
from app.modules.procurement.kp import normalize_name
from app.modules.purchasing.doc_refs import norm_doc_number
from app.modules.purchasing.infrastructure import SupplierNameRow, SupplierRow
from app.modules.purchasing.suppliers import supplier_key
from app.modules.purchasing.upd_sellers import seller_name_key
from app.modules.receiving.application import DELIVERY_STATUSES
from app.modules.receiving.domain import ReceivingLine
from app.shared.inn import checked_inn
from app.workflows.article_key import code_key

INVOICE_DAYS = 31
WAITING_LIMIT = 5000
ROWS_LIMIT = 300
ARTICLE, NAME, WEAK = 3, 2, 1
CONFIDENCE = {ARTICLE: "article", NAME: "name", WEAK: "weak"}
_INVOICE_SPLIT = re.compile(r"[,;]|\sи\s")
_WORDS = re.compile(r"[\s,;()\[\]«»\"']+")


@dataclass(frozen=True)
class UpdHeader:
    supplier: str = ""
    inn: str = ""
    invoice_number: str = ""
    invoice_date: date | None = None

    @property
    def invoice_numbers(self) -> list[str]:
        parts = [part.strip() for part in _INVOICE_SPLIT.split(self.invoice_number or "")]
        return [part for part in parts if norm_doc_number(part)]

    @property
    def seller_known(self) -> bool:
        return bool(checked_inn(self.inn).inn or seller_name_key(self.supplier) or self.supplier.strip())


@dataclass(frozen=True)
class UpdRow:
    """One item row of the УПД as recognised: `quantity` / `unit` — on the shelf (after the pack), `invoice_*` — as
    invoiced."""

    index: int
    article: str = ""
    name: str = ""
    upd_name: str = ""
    quantity: Decimal | None = None
    unit: str = ""
    invoice_quantity: Decimal | None = None
    invoice_unit: str = ""
    supplier_code: str = ""

    @property
    def title(self) -> str:
        return " · ".join(dict.fromkeys(value for value in (self.article, self.upd_name or self.name) if value))


@dataclass(frozen=True)
class Waiting:
    """An open receiving line with what procurement knows of its order line (the invoice, the invoiced name)."""

    line: ReceivingLine
    invoice_number: str = ""
    invoice_date: date | None = None
    invoice_item_name: str = ""
    design_article: str = ""


@dataclass(frozen=True)
class SupplierBook:
    """The purchasing suppliers: INN → id, name key (own name and every alias) → id."""

    by_inn: Mapping[str, UUID] = field(default_factory=dict)
    by_key: Mapping[str, UUID] = field(default_factory=dict)

    def usable_inn(self, name: str, inn: str) -> str:
        """The seller's ИНН to match by: as read when it passes its check digits; a misread one put right to the ИНН of
        the supplier named so (one digit / transposition away, else two — `checked_inn`); else «» — by the name."""
        check = checked_inn(inn)
        if not check.remark:
            return check.inn
        key = supplier_key(name)
        named = self.by_key.get(key) if key else None
        known = [found for found, supplier in self.by_inn.items() if named is not None and supplier == named]
        return checked_inn(inn, known=known).inn

    def resolve(self, name: str, inn: str = "") -> UUID | None:
        inn = self.usable_inn(name, inn)
        if inn and inn in self.by_inn:
            return self.by_inn[inn]
        key = supplier_key(name)
        return self.by_key.get(key) if key else None

    def agrees(self, text: str, seller: str, inn: str = "") -> bool:
        """The supplier text of an order line is the seller of the УПД: the same purchasing supplier (INN or alias);
        else their names agree (one seller-name key inside the other) or read the same."""
        own, theirs = self.resolve(text), self.resolve(seller, inn)
        if own is not None and theirs is not None:
            return own == theirs
        a, b = seller_name_key(text), seller_name_key(seller)
        if a and b and (a == b or a in b or b in a):
            return True
        return bool(text.strip()) and text.strip().casefold() == seller.strip().casefold()


@dataclass
class Candidate:
    waiting: Waiting
    by_invoice: bool
    codes: frozenset[str] = frozenset()
    names: tuple[frozenset[str], ...] = ()

    @property
    def line(self) -> ReceivingLine:
        return self.waiting.line


@dataclass
class LineProposal:
    candidate: Candidate
    rows: list[tuple[int, Decimal]] = field(default_factory=list)
    tier: int = 0
    unit_ok: bool = True
    quantity_known: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def line(self) -> ReceivingLine:
        return self.candidate.line

    @property
    def document_quantity(self) -> Decimal | None:
        return sum((quantity for _, quantity in self.rows), Decimal("0")) if self.rows and self.quantity_known else None

    @property
    def confidence(self) -> str:
        if not self.rows:
            return ""
        if self.candidate.by_invoice and self.tier >= NAME:
            return "invoice"
        return CONFIDENCE[self.tier]

    @property
    def ticked(self) -> bool:
        quantity = self.document_quantity
        return bool(self.rows) and self.tier >= NAME and self.unit_ok and quantity is not None and quantity > 0


@dataclass
class Proposal:
    warehouse: str
    lines: list[LineProposal]
    rows: list[UpdRow]
    row_lines: dict[int, list[UUID]]
    row_tiers: dict[int, int]
    other_warehouses: dict[str, int]
    invoice_numbers: list[str]

    @property
    def unmatched(self) -> list[int]:
        return [row.index for row in self.rows if not self.row_lines.get(row.index)]

    @property
    def matched(self) -> list[LineProposal]:
        return [item for item in self.lines if item.rows]


def decimal_or_none(value) -> Decimal | None:
    text = re.sub(r"\s", "", str(value if value is not None else "")).replace(",", ".")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def number_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value.normalize(), "f")
    return text


# --- matching (pure) -------------------------------------------------------------------------------------------


def _words(text: str | None) -> list[str]:
    return [word for word in _WORDS.split(str(text or "")) if word]


def _codes(*texts: str | None) -> set[str]:
    return {code_key(word) for text in texts for word in _words(text) if is_code(word)}


def _name_words(text: str | None) -> frozenset[str]:
    return frozenset(word for word in normalize_name(text).split() if len(word) >= 2 or word.isdigit())


def _row_codes(row: UpdRow) -> set[str]:
    keys = {code_key(value) for value in (row.article, row.supplier_code)}
    keys = {key for key in keys if len(key) >= 3}
    return keys | _codes(row.name, row.upd_name, row.article)


def candidate(waiting: Waiting, by_invoice: bool) -> Candidate:
    line = waiting.line
    codes = {code_key(value) for value in (line.article, waiting.design_article)}
    codes = {key for key in codes if len(key) >= 3}
    codes |= _codes(line.article, line.name, line.document_name, waiting.invoice_item_name, waiting.design_article)
    names = tuple(words for words in (_name_words(text) for text in (
        line.name, line.document_name, waiting.invoice_item_name, f"{line.article} {line.name}")) if words)
    return Candidate(waiting=waiting, by_invoice=by_invoice, codes=frozenset(codes), names=names)


def candidates(header: UpdHeader, waiting: Iterable[Waiting], book: SupplierBook, *, hint_supplier: str = "") -> list[Candidate]:
    """The open lines this УПД may be: of its invoice(s) or of its seller (see the module doc)."""
    numbers = {norm_doc_number(number) for number in header.invoice_numbers}
    hint = hint_supplier.strip().casefold()
    found: list[Candidate] = []
    for item in waiting:
        line = item.line
        if line.status not in DELIVERY_STATUSES or line.warehouse_event_emitted:
            continue
        if header.seller_known:
            same_supplier = book.agrees(line.supplier, header.supplier, header.inn)
        else:
            same_supplier = bool(hint) and line.supplier.strip().casefold() == hint
        by_invoice = False
        if numbers and item.invoice_number and norm_doc_number(item.invoice_number) in numbers:
            near = not (header.invoice_date and item.invoice_date
                        and abs((header.invoice_date - item.invoice_date).days) > INVOICE_DAYS)
            same_day = bool(header.invoice_date and header.invoice_date == item.invoice_date)
            # The number and the very date of the invoice: the supplier text of the order may be anything typed.
            seller_ok = same_supplier or same_day or not header.seller_known or not line.supplier.strip()
            by_invoice = near and seller_ok
        if by_invoice or same_supplier:
            found.append(candidate(item, by_invoice))
    return found


def _name_score(row: UpdRow, item: Candidate) -> float:
    best = 0.0
    for text in (row.name, row.upd_name):
        words = _name_words(text)
        if not words:
            continue
        for name in item.names:
            dice = 2 * len(words & name) / (len(words) + len(name))
            if name <= words:
                dice = max(dice, 0.6)  # every word of the order is in the row: the row only says more
            best = max(best, dice)
    return best


def comparable(row: UpdRow, unit: str) -> tuple[Decimal | None, bool]:
    """The row's quantity in `unit` and whether the units agree: the shelf figure, else the invoiced one; no unit on
    one side — the shelf figure, taken as agreeing."""
    wanted = unit_key(unit)
    if row.quantity is not None and unit_key(row.unit) == wanted:
        return row.quantity, True
    if row.invoice_quantity is not None and row.invoice_unit and unit_key(row.invoice_unit) == wanted:
        return row.invoice_quantity, True
    if row.quantity is None:
        return (row.invoice_quantity, not row.invoice_unit or not unit) if row.invoice_quantity is not None else (None, True)
    return row.quantity, not row.unit or not unit


def _arrival_order(item: Candidate) -> tuple:
    line = item.line
    return (not item.by_invoice, line.expected_arrival or date.max, line.order_number or "￿", line.created_at,
            line.legacy_id)


def _scored(row: UpdRow, pool: Sequence[Candidate]) -> list[tuple[int, float, Candidate]]:
    keys = _row_codes(row)
    own_codes = _codes(row.name, row.upd_name, row.article)
    scored = []
    for item in pool:
        name = _name_score(row, item)
        if keys & item.codes:
            tier = ARTICLE
        elif name >= 0.6:
            tier = NAME
        elif name >= 0.3:
            tier = WEAK
        else:
            continue
        if tier < ARTICLE and own_codes and item.codes and not (item.codes & keys):
            continue  # both carry part numbers and none agree: «XH-2P» is not «XH-3P», whatever the words
        scored.append((tier, name, item))
    return scored


def _quantity(value: Decimal, unit: str) -> str:
    return f"{number_text(value)} {unit}".strip()


def match(rows: Sequence[UpdRow], pool: Sequence[Candidate]) -> tuple[dict[UUID, LineProposal], dict[int, list[UUID]],
                                                                       dict[int, int]]:
    """Per line the rows it takes (with their share), per row the lines it went to, per row its tier."""
    groups: list[tuple[int, float, UpdRow, list[Candidate]]] = []
    for row in rows:
        scored = _scored(row, pool)
        if not scored:
            continue
        tier = max(item[0] for item in scored)
        best = max(item[1] for item in scored if item[0] == tier)
        group = [item for score_tier, name, item in scored
                 if score_tier == tier and (tier == ARTICLE or name >= best - 0.02)]
        groups.append((tier, best, row, group))
    proposals: dict[UUID, LineProposal] = {}
    row_lines: dict[int, list[UUID]] = {}
    row_tiers: dict[int, int] = {}
    owner: dict[UUID, int] = {}
    for tier, best, row, group in sorted(groups, key=lambda item: (-item[0], -item[1], item[2].index)):
        group = [item for item in group if owner.get(item.line.id, 0) <= tier]
        if not group:
            continue
        group.sort(key=_arrival_order)
        unit = unit_key(group[0].line.unit)
        group = [item for item in group if unit_key(item.line.unit) == unit]
        for item in group:
            owner[item.line.id] = max(owner.get(item.line.id, 0), tier)
        row_tiers[row.index] = tier
        quantity, unit_ok = comparable(row, group[0].line.unit)
        allotted = _share(row, quantity, group, proposals)
        row_lines[row.index] = [item.line.id for item, _ in allotted]
        for item, share in allotted:
            proposal = proposals.setdefault(item.line.id, LineProposal(candidate=item))
            proposal.rows.append((row.index, share))
            proposal.tier = max(proposal.tier, tier)
            if quantity is None:
                proposal.quantity_known = False
                _warn(proposal, f"Количество в УПД не прочитано (строка {row.index + 1}) — укажите по УПД")
            if not unit_ok:
                proposal.unit_ok = False
                _warn(proposal, f"В УПД {_quantity(quantity or Decimal(0), row.unit)}, в заказе {item.line.unit} — "
                                "проверьте количество")
    for proposal in proposals.values():
        quantity = proposal.document_quantity
        if quantity is not None and quantity > proposal.line.ordered_quantity:
            _warn(proposal, f"По УПД больше заказа на {_quantity(quantity - proposal.line.ordered_quantity, proposal.line.unit)}")
    return proposals, row_lines, row_tiers


def _warn(proposal: LineProposal, text: str) -> None:
    if text not in proposal.warnings:
        proposal.warnings.append(text)


def _share(row: UpdRow, quantity: Decimal | None, group: list[Candidate],
           proposals: Mapping[UUID, LineProposal]) -> list[tuple[Candidate, Decimal]]:
    """The row's quantity shared over the lines of the same item (see the module doc)."""
    if quantity is None:
        return [(group[0], Decimal("0"))]

    def remaining(item: Candidate) -> Decimal:
        taken = proposals.get(item.line.id)
        already = taken.document_quantity or Decimal("0") if taken else Decimal("0")
        return max(item.line.ordered_quantity - already, Decimal("0"))

    if len(group) > 1:
        exact = [item for item in group if remaining(item) == quantity]
        if len(exact) == 1 and sum((remaining(item) for item in group), Decimal("0")) != quantity:
            return [(exact[0], quantity)]
    left = quantity
    allotted: list[tuple[Candidate, Decimal]] = []
    for item in group:
        take = min(left, remaining(item))
        if take > 0:
            allotted.append((item, take))
            left -= take
        if left <= 0:
            break
    if left > 0:
        last = allotted[-1][0] if allotted else group[-1]
        if allotted and allotted[-1][0] is last:
            allotted[-1] = (last, allotted[-1][1] + left)
        else:
            allotted.append((last, left))
    return allotted


def propose(header: UpdHeader, rows: Sequence[UpdRow], waiting: Iterable[Waiting], book: SupplierBook, *,
            warehouse: str | None = None, hint_supplier: str = "") -> Proposal:
    """The delivery this УПД is: one warehouse (asked, else the one most rows went to), its matched lines ticked with
    their «По УПД», the other open lines of the same supplier unticked, the rows left over."""
    pool = candidates(header, waiting, book, hint_supplier=hint_supplier)
    rows = list(rows)[:ROWS_LIMIT]
    counts: dict[str, int] = {}
    everywhere, _, _ = match(rows, pool)
    for proposal in everywhere.values():
        counts[proposal.line.warehouse] = counts.get(proposal.line.warehouse, 0) + 1
    if not warehouse:
        present = counts or {item.line.warehouse: 0 for item in pool}
        warehouse = sorted(present, key=lambda name: (-present[name], name))[0] if present else ""
    local = [item for item in pool if item.line.warehouse == warehouse]
    proposals, row_lines, row_tiers = match(rows, local)
    ordered = sorted(local, key=lambda item: (item.line.id not in proposals, _first_row(proposals.get(item.line.id)),
                                              *_arrival_order(item)))
    return Proposal(
        warehouse=warehouse,
        lines=[proposals.get(item.line.id) or LineProposal(candidate=item) for item in ordered],
        rows=rows,
        row_lines=row_lines,
        row_tiers=row_tiers,
        other_warehouses={name: count for name, count in counts.items() if name != warehouse},
        invoice_numbers=header.invoice_numbers,
    )


def _first_row(proposal: LineProposal | None) -> int:
    return min(index for index, _ in proposal.rows) if proposal and proposal.rows else 1 << 30


def supplier_aliases(texts: Iterable[str], header: UpdHeader, book: SupplierBook) -> frozenset[str]:
    """The supplier texts (casefolded) of the lines one delivery may carry together: all of them the УПД's seller, or
    all one purchasing supplier. Otherwise none — `accept_delivery` keeps its one-supplier rule."""
    distinct = {text.strip().casefold(): text.strip() for text in texts}
    if len(distinct) < 2:
        return frozenset()
    if header.seller_known and all(book.agrees(text, header.supplier, header.inn) for text in distinct.values()):
        return frozenset(distinct)
    resolved = {book.resolve(text) for text in distinct.values()}
    if len(resolved) == 1 and None not in resolved:
        return frozenset(distinct)
    return frozenset()


# --- reading the registers ---------------------------------------------------------------------------------------


def supplier_book(purchasing_factory: Callable | None) -> SupplierBook:
    if purchasing_factory is None:
        return SupplierBook()
    with purchasing_factory() as uow:
        session = uow.session
        by_inn: dict[str, UUID] = {}
        by_key: dict[str, UUID] = {}
        for row in session.execute(select(SupplierRow.id, SupplierRow.name, SupplierRow.name_key, SupplierRow.inn)):
            if row.inn:
                by_inn.setdefault(row.inn, row.id)
            for key in (row.name_key, supplier_key(row.name)):
                if key:
                    by_key.setdefault(key, row.id)
        for row in session.execute(select(SupplierNameRow.name_key, SupplierNameRow.supplier_id)):
            by_key.setdefault(row.name_key, row.supplier_id)
    return SupplierBook(by_inn=by_inn, by_key=by_key)


def open_lines(receiving_factory: Callable) -> list[ReceivingLine]:
    with receiving_factory() as uow:
        lines = [line for status in sorted(DELIVERY_STATUSES, key=lambda item: item.value)
                 for line in uow.receiving.by_status(status, limit=WAITING_LIMIT)]
    return [line for line in lines if not line.warehouse_event_emitted]


def waiting_lines(receiving_factory: Callable, procurement_factory: Callable | None,
                  lines: Sequence[ReceivingLine] | None = None) -> list[Waiting]:
    """The open receiving lines with their order's invoice from procurement."""
    lines = list(open_lines(receiving_factory) if lines is None else lines)
    facts: dict[UUID, tuple] = {}
    ids = sorted({line.procurement_line_id for line in lines}, key=str)
    if procurement_factory is not None and ids:
        with procurement_factory() as uow:
            for start in range(0, len(ids), 500):
                for row in uow.session.execute(
                    select(PL.id, PL.invoice_number, PL.invoice_date, PL.invoice_item_name, PL.design_article)
                    .where(PL.id.in_(ids[start:start + 500]))
                ):
                    facts[row.id] = (row.invoice_number or "", row.invoice_date, row.invoice_item_name or "",
                                     row.design_article or "")
    return [Waiting(line, *facts.get(line.procurement_line_id, ("", None, "", ""))) for line in lines]


def header_from(recognised: Mapping[str, object]) -> UpdHeader:
    day = recognised.get("invoice_date")
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day[:10])
        except ValueError:
            day = None
    return UpdHeader(
        supplier=str(recognised.get("supplier") or "").strip(),
        inn=str(recognised.get("inn") or "").strip(),
        invoice_number=str(recognised.get("invoice_number") or "").strip(),
        invoice_date=day if isinstance(day, date) else None,
    )


def rows_from(lines: Sequence[Mapping[str, object]]) -> list[UpdRow]:
    return [
        UpdRow(
            index=index,
            article=str(line.get("article") or "").strip(),
            name=str(line.get("name") or "").strip(),
            upd_name=str(line.get("upd_name") or "").strip(),
            quantity=decimal_or_none(line.get("quantity")),
            unit=str(line.get("unit") or "").strip(),
            invoice_quantity=decimal_or_none(line.get("invoice_quantity")),
            invoice_unit=str(line.get("invoice_unit") or "").strip(),
            supplier_code=str(line.get("supplier_code") or "").strip(),
        )
        for index, line in enumerate(lines)
    ]


def proposal_view(proposal: Proposal, line_view: Callable[[ReceivingLine], dict]) -> dict:
    """The answer of «Принять по УПД»: the delivery pre-filled for the screen."""
    matched = proposal.matched
    return {
        "warehouse": proposal.warehouse,
        "other_warehouses": [{"warehouse": name, "matched": count}
                             for name, count in sorted(proposal.other_warehouses.items())],
        "invoice_numbers": proposal.invoice_numbers,
        "lines": [
            {
                "line": line_view(item.line),
                "ticked": item.ticked,
                "document_quantity": number_text(item.document_quantity) if item.rows else "",
                "confidence": item.confidence,
                "by_invoice": item.candidate.by_invoice,
                "rows": [{"index": index, "quantity": number_text(quantity)} for index, quantity in item.rows],
                "warnings": item.warnings,
            }
            for item in proposal.lines
        ],
        "rows": [
            {
                "index": row.index, "article": row.article, "name": row.name, "upd_name": row.upd_name,
                "quantity": number_text(row.quantity), "unit": row.unit,
                "invoice_quantity": number_text(row.invoice_quantity), "invoice_unit": row.invoice_unit,
                "lines": [str(line_id) for line_id in proposal.row_lines.get(row.index, [])],
                "confidence": CONFIDENCE.get(proposal.row_tiers.get(row.index, 0), ""),
            }
            for row in proposal.rows
        ],
        "unmatched": proposal.unmatched,
        "matched": sum(1 for item in matched if item.ticked),
        "orders": sorted({item.line.order_number for item in matched if item.line.order_number}),
    }


def upd_registry_header(recognised: Mapping[str, object], *, document_number: str, document_date: date | None,
                        warehouse: str) -> dict:
    """The header the УПД file goes to «Реестр УПД» under: what the storekeeper confirmed (№ / дата УПД) over what was
    recognised (seller, invoice, ПРД) — the columns `receipt_upd_registry.scan_document` reads."""

    def day(key: str) -> date | None:
        value = recognised.get(key)
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10]) if value else None
        except ValueError:
            return None

    return {
        "supplier": str(recognised.get("supplier") or "").strip(),
        "invoice_number": str(recognised.get("invoice_number") or "").strip(),
        "invoice_date": day("invoice_date"),
        "prd_number": str(recognised.get("prd_number") or "").strip(),
        "prd_date": day("prd_date"),
        "upd_number": document_number or str(recognised.get("upd_number") or "").strip(),
        "upd_date": document_date or day("upd_date"),
        "warehouse": warehouse,
    }
