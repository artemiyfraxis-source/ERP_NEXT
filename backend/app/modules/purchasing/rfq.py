"""«Запросы КП» (as Roller_Buyer routes/rfqs.ts, decisions.ts, rfq-matrix.ts): the buyer's procurement lines go to
several suppliers, their quotes land in a matrix compared with VAT by the cost of covering the need, and the choice —
with the reasons the gate asks for — is written back into the procurement line.

Procurement lines are seen only through `ProcurementLines` (app/workflows/purchasing_procurement_bridge.py). Only the
line's own buyer puts it into a request or decides on it; every write to a request is its buyer's. Results are plain
JSON-ready dicts (Decimals as strings)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.workflows.article_key import code_key
from app.shared.events import AuditEvent
from app.shared.identity import Actor

from . import settings as purchasing_settings
from .suppliers import supplier_key
from .infrastructure import (
    ITEM_CATEGORIES,
    RFQ_SUPPLIER_REASONS,
    DecisionRow,
    ItemRow,
    LineItemRow,
    QuoteLineRow,
    QuoteRow,
    RfqLineRow,
    RfqRow,
    RfqSupplierRow,
    SupplierNameRow,
    SupplierRow,
)
from .offers import (
    Offer,
    Reasons,
    Violation,
    _arrival,
    baseline,
    check_decision,
    cheapest,
    cover,
    current_offers,
    delta_pct,
    gate_status,
    normalize_decision,
    read_tiers,
)

# ── errors ──────────────────────────────────────────────────────────────────────────────────────────────────────


class RfqError(Exception):
    status = 422

    def __init__(self, message: str, violations: Sequence[Violation] = ()):
        super().__init__(message)
        self.violations = tuple(violations)


class NotFound(RfqError):
    status = 404


class Forbidden(RfqError):
    status = 403


class Refused(RfqError):
    status = 422


class Conflict(RfqError):
    status = 409


class LineChanged(Conflict):
    """The procurement line changed since it was read (its version moved on)."""


class LineRefused(Refused):
    """Procurement refused the facts (not the buyer's line, the stage moved on, …)."""


# ── the procurement port ────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LineView:
    id: UUID
    legacy_id: str
    status: str
    version: int
    buyer_id: UUID | None
    editable: bool  # «В работе» / «Готов к запросу»: facts (and so a decision) may still be written
    name: str
    article: str | None
    to_buy: Decimal
    unit: str
    need_by: date | None
    budget_unit_gross: Decimal | None
    supplier: str | None
    hint: str = ""  # ТИП / КАТЕГОРИЯ / name — for the category guess
    actual_quantity: Decimal | None = None
    actual_unit: str | None = None


class ProcurementLines(Protocol):
    def mine(self, actor: Actor) -> list[LineView]: ...
    def get_many(self, line_ids: Iterable[UUID]) -> dict[UUID, LineView]: ...
    def apply_choice(
        self, line_id: UUID, *, supplier: str, quantity: Decimal, unit: str, expected_version: int, actor: Actor,
        now: datetime,
    ) -> LineView: ...


class UnitOfWork(Protocol):
    session: Session | None

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, *args) -> None: ...
    def commit(self) -> None: ...


# ── small helpers ───────────────────────────────────────────────────────────────────────────────────────────────


def num(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    return text


def name_key(name: str) -> str:
    """The supplier directory's key (legal form, quotes and case folded away), so every module finds the same entry."""
    return supplier_key(name)


def _session(uow: UnitOfWork) -> Session:
    assert uow.session is not None
    return uow.session


def _audit(uow: Any, aggregate_id: UUID, event_type: str, actor: Actor, now: datetime, details: dict) -> None:
    audit = getattr(uow, "audit", None)
    if audit is not None:
        audit.add(AuditEvent(id=uuid4(), aggregate_id=aggregate_id, event_type=event_type, actor=actor,
                             occurred_at=now, details=details))


def _rfq(session: Session, rfq_id: UUID) -> RfqRow:
    rfq = session.get(RfqRow, rfq_id)
    if rfq is None:
        raise NotFound("Запрос КП не найден")
    return rfq


def _own(rfq: RfqRow, actor: Actor) -> RfqRow:
    if rfq.buyer_id != actor.id:
        raise Forbidden("Это запрос КП другого закупщика")
    return rfq


def _open(rfq: RfqRow) -> RfqRow:
    if rfq.closed_at is not None:
        raise Refused(f"Запрос {rfq.number} закрыт")
    return rfq


def _live_lines(session: Session, rfq_id: UUID) -> list[RfqLineRow]:
    return list(session.scalars(
        select(RfqLineRow)
        .where(RfqLineRow.rfq_id == rfq_id, RfqLineRow.removed_at.is_(None))
        .order_by(RfqLineRow.created_at, RfqLineRow.name, RfqLineRow.id)
    ))


def _today(now: datetime) -> date:
    return now.date()


# ── candidates and a new request ────────────────────────────────────────────────────────────────────────────────


def _in_live_rfq(session: Session, line_ids: Sequence[UUID]) -> dict[UUID, str]:
    if not line_ids:
        return {}
    rows = session.execute(
        select(RfqLineRow.procurement_line_id, RfqRow.number)
        .join(RfqRow, RfqRow.id == RfqLineRow.rfq_id)
        .where(RfqLineRow.procurement_line_id.in_(list(line_ids)), RfqLineRow.removed_at.is_(None))
    )
    return {line_id: number for line_id, number in rows}


def _line_result(view: LineView) -> dict:
    return {
        "id": str(view.id),
        "legacy_id": view.legacy_id,
        "name": view.name,
        "article": view.article,
        "quantity": num(view.to_buy),
        "unit": view.unit,
        "supplier": view.supplier,
        "budget_unit_gross": num(view.budget_unit_gross),
        "need_by": view.need_by.isoformat() if view.need_by else None,
        "status": view.status,
        "version": view.version,
    }


def candidates(uow: UnitOfWork, lines: ProcurementLines, actor: Actor) -> list[dict]:
    """My lines «В работе»/«Готов к запросу» with something to buy and in no live request."""
    mine = [view for view in lines.mine(actor) if view.to_buy > 0]
    with uow:
        taken = _in_live_rfq(_session(uow), [view.id for view in mine])
    return [_line_result(view) for view in mine if view.id not in taken]


def next_number(session: Session, now: datetime) -> str:
    prefix = f"КП-{now:%y%m}-"
    numbers = session.scalars(select(RfqRow.number).where(RfqRow.number.like(f"{prefix}%")))
    used = [int(number[len(prefix):]) for number in numbers if number[len(prefix):].isdigit()]
    return f"{prefix}{max(used, default=0) + 1:03d}"


def create_rfq(
    uow: UnitOfWork, lines: ProcurementLines, actor: Actor, now: datetime, line_ids: Sequence[UUID], title: str | None,
) -> dict:
    ids = list(dict.fromkeys(line_ids))
    if not ids:
        raise Refused("Выберите строки для запроса")
    views = lines.get_many(ids)
    for line_id in ids:
        view = views.get(line_id)
        if view is None:
            raise NotFound("Строка закупки не найдена")
        if view.buyer_id != actor.id:
            raise Forbidden(f"{view.legacy_id}: в запрос КП строку берёт только её закупщик")
        if not view.editable:
            raise Refused(f"{view.legacy_id}: в запрос КП — только строки «В работе» и «Готов к запросу»")
        if view.to_buy <= 0:
            raise Refused(f"{view.legacy_id}: всё покрыто со склада — покупать нечего")
    with uow:
        session = _session(uow)
        taken = _in_live_rfq(session, ids)
        if taken:
            line_id, number = next(iter(taken.items()))
            raise Conflict(f"{views[line_id].legacy_id} уже в запросе {number}")
        items = dict(session.execute(
            select(LineItemRow.procurement_line_id, LineItemRow.item_id).where(LineItemRow.procurement_line_id.in_(ids))
        ).all())
        rfq = RfqRow(
            id=uuid4(), number=next_number(session, now), title=(title or "").strip() or None, buyer_id=actor.id,
            created_at=now, created_by=actor.id, version=1,
        )
        session.add(rfq)
        session.flush()
        for view in (views[line_id] for line_id in ids):
            session.add(RfqLineRow(
                id=uuid4(), rfq_id=rfq.id, procurement_line_id=view.id, item_id=items.get(view.id),
                name=view.name[:512], article=(view.article or None) and view.article[:255], quantity=view.to_buy,
                unit=view.unit or "шт", need_by=view.need_by, budget_unit_gross=view.budget_unit_gross, created_at=now,
            ))
        _audit(uow, rfq.id, "purchasing.rfq_created", actor, now, {"number": rfq.number, "lines": str(len(ids))})
        uow.commit()
        return {"id": str(rfq.id), "number": rfq.number}


def list_rfqs(uow: UnitOfWork, actor: Actor, all_buyers: bool = False) -> list[dict]:
    """Open first, then the newest."""
    with uow:
        session = _session(uow)
        statement = select(RfqRow)
        if not all_buyers:
            statement = statement.where(RfqRow.buyer_id == actor.id)
        rfqs = list(session.scalars(statement))
        ids = [rfq.id for rfq in rfqs]
        if not ids:
            return []
        lines = dict(session.execute(
            select(RfqLineRow.rfq_id, func.count()).where(RfqLineRow.rfq_id.in_(ids), RfqLineRow.removed_at.is_(None))
            .group_by(RfqLineRow.rfq_id)
        ).all())
        suppliers: dict[UUID, tuple[int, int]] = {}
        for rfq_id, total, sent in session.execute(
            select(RfqSupplierRow.rfq_id, func.count(), func.count(RfqSupplierRow.sent_at))
            .where(RfqSupplierRow.rfq_id.in_(ids)).group_by(RfqSupplierRow.rfq_id)
        ):
            suppliers[rfq_id] = (total, sent)
        quotes = dict(session.execute(
            select(QuoteRow.rfq_id, func.count()).where(QuoteRow.rfq_id.in_(ids)).group_by(QuoteRow.rfq_id)
        ).all())
        decided = dict(session.execute(
            select(RfqLineRow.rfq_id, func.count()).join(DecisionRow, DecisionRow.rfq_line_id == RfqLineRow.id)
            .where(RfqLineRow.rfq_id.in_(ids)).group_by(RfqLineRow.rfq_id)
        ).all())
        rfqs.sort(key=lambda rfq: (rfq.closed_at is not None, -rfq.created_at.timestamp()))
        return [
            {
                "id": str(rfq.id), "number": rfq.number, "title": rfq.title,
                "created_at": rfq.created_at.isoformat(), "closed_at": rfq.closed_at.isoformat() if rfq.closed_at else None,
                "mine": rfq.buyer_id == actor.id,
                "lines": lines.get(rfq.id, 0), "suppliers": suppliers.get(rfq.id, (0, 0))[0],
                "sent": suppliers.get(rfq.id, (0, 0))[1], "quotes": quotes.get(rfq.id, 0), "decided": decided.get(rfq.id, 0),
            }
            for rfq in rfqs
        ]


# ── suppliers of a request ──────────────────────────────────────────────────────────────────────────────────────

_CATEGORY_GUESS = (
    ("cable", re.compile(r"кабел|провод|шнур|жгут|cable|wire", re.I)),
    ("connector", re.compile(r"разъ[её]м|разъем|клемм|коннект|вилк|розетк|гнезд|наконечн|connector|terminal", re.I)),
    ("ekb", re.compile(r"резист|конденс|микросх|диод|транзист|дроссел|индукт|кварц|светодиод|реле|предохр|чип", re.I)),
)


def guess_category(text: str) -> str:
    for category, pattern in _CATEGORY_GUESS:
        if pattern.search(text or ""):
            return category
    return "other"


def category_code(value: object) -> str:
    """A supplier's category as stored (code or Russian label) → the item category code."""
    text = str(value or "").strip().casefold()
    if text in ITEM_CATEGORIES:
        return text
    for prefix, code in (("кабел", "cable"), ("провод", "cable"), ("разъ", "connector"), ("коннект", "connector"),
                         ("экб", "ekb"), ("электрон", "ekb"), ("проч", "other"), ("друг", "other")):
        if text.startswith(prefix):
            return code
    return text


def _supplier_by_name(session: Session, text: str | None) -> SupplierRow | None:
    key = name_key(text or "")
    if not key:
        return None
    row = session.scalar(select(SupplierRow).where(SupplierRow.name_key == key))
    if row is None:
        alias = session.get(SupplierNameRow, key)
        row = session.get(SupplierRow, alias.supplier_id) if alias is not None else None
    return row if row is not None and row.archived_at is None else None


def _supplier_brief(row: SupplierRow) -> dict:
    return {"supplier_id": str(row.id), "name": row.name, "is_key": row.is_key, "categories": list(row.categories or [])}


def directory(uow: UnitOfWork) -> list[dict]:
    """Every supplier not archived — for «добавить поставщика» (key ones first)."""
    with uow:
        rows = list(_session(uow).scalars(select(SupplierRow).where(SupplierRow.archived_at.is_(None))))
        rows.sort(key=lambda row: (not row.is_key, row.name.casefold()))
        return [_supplier_brief(row) for row in rows]


def suggest_suppliers(uow: UnitOfWork, lines: ProcurementLines, actor: Actor, rfq_id: UUID) -> dict:
    """Whom to ask (Roller rotation.ts): key suppliers of the lines' categories, `rotation_non_key` non-key ones asked
    longest ago (never asked first), the supplier already written in a line («plan»); the rest of the category is
    offered unticked."""
    with uow:
        session = _session(uow)
        rfq = _own(_rfq(session, rfq_id), actor)
        rfq_lines = _live_lines(session, rfq.id)
        views = lines.get_many([line.procurement_line_id for line in rfq_lines])
        item_categories = dict(session.execute(
            select(ItemRow.id, ItemRow.category).where(ItemRow.id.in_([l.item_id for l in rfq_lines if l.item_id]))
        ).all()) if any(line.item_id for line in rfq_lines) else {}
        categories = set()
        for line in rfq_lines:
            if line.item_id in item_categories:
                categories.add(item_categories[line.item_id])
            else:
                view = views.get(line.procurement_line_id)
                categories.add(guess_category(f"{line.name} {view.hint if view else ''}"))
        values = purchasing_settings.load(session)
        suppliers = list(session.scalars(select(SupplierRow).where(SupplierRow.archived_at.is_(None))))
        in_rfq = set(session.scalars(select(RfqSupplierRow.supplier_id).where(RfqSupplierRow.rfq_id == rfq.id)))
        last_asked = dict(session.execute(
            select(RfqSupplierRow.supplier_id, func.max(RfqSupplierRow.sent_at))
            .where(RfqSupplierRow.sent_at.is_not(None)).group_by(RfqSupplierRow.supplier_id)
        ).all())
        planned: dict[UUID, SupplierRow] = {}
        unmapped: list[str] = []
        for view in views.values():
            text = (view.supplier or "").strip()
            if not text:
                continue
            row = _supplier_by_name(session, text)
            if row is not None:
                planned[row.id] = row
            elif text not in unmapped:
                unmapped.append(text)

        def matches(row: SupplierRow) -> bool:
            return any(category_code(value) in categories for value in (row.categories or []))

        relevant = sorted((row for row in suppliers if matches(row)), key=lambda row: row.name.casefold())
        non_key = sorted(
            (row for row in relevant if not row.is_key),
            key=lambda row: (last_asked.get(row.id) is not None, last_asked.get(row.id) or datetime.min, row.name.casefold()),
        )
        rotation = non_key[: int(values["rotation_non_key"])]
        picked: dict[UUID, tuple[SupplierRow, str, bool]] = {}
        for row in planned.values():
            picked[row.id] = (row, "plan", True)
        for row in relevant:
            if row.is_key and row.id not in picked:
                picked[row.id] = (row, "key", True)
        for row in rotation:
            picked.setdefault(row.id, (row, "rotation", True))
        for row in non_key:
            picked.setdefault(row.id, (row, "category", False))
        return {
            "categories": sorted(categories),
            "suggestions": [
                {
                    **_supplier_brief(row), "reason": reason, "preselect": preselect and row.id not in in_rfq,
                    "in_rfq": row.id in in_rfq,
                    "last_asked_at": last_asked[row.id].isoformat() if last_asked.get(row.id) else None,
                }
                for row, reason, preselect in picked.values()
            ],
            "unmapped_plan": unmapped,
        }


def add_supplier(
    uow: UnitOfWork, actor: Actor, now: datetime, rfq_id: UUID, *, supplier_id: UUID | None, name: str | None,
    reason: str,
) -> dict:
    """Adds a supplier to the request; by name — finds it in the directory or quick-adds it (just a name)."""
    if reason not in RFQ_SUPPLIER_REASONS:
        raise Refused("Неизвестная причина выбора поставщика")
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        if supplier_id is not None:
            supplier = session.get(SupplierRow, supplier_id)
            if supplier is None:
                raise NotFound("Поставщик не найден")
            if supplier.archived_at is not None:
                raise Refused(f"Поставщик «{supplier.name}» в архиве")
        else:
            clean = " ".join((name or "").split())
            if not clean:
                raise Refused("Укажите поставщика")
            if len(clean) > 255:
                raise Refused("Название поставщика длиннее 255 знаков")
            supplier = _supplier_by_name(session, clean)
            if supplier is None:
                supplier = SupplierRow(
                    id=uuid4(), name=clean, name_key=name_key(clean), categories=[], is_key=False, domains=[],
                    created_at=now, created_by=actor.id, version=1,
                )
                session.add(supplier)
                session.flush()
                _audit(uow, supplier.id, "purchasing.supplier_quick_added", actor, now, {"name": clean})
        existing = session.scalar(select(RfqSupplierRow).where(
            RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.supplier_id == supplier.id))
        if existing is None:
            session.add(RfqSupplierRow(id=uuid4(), rfq_id=rfq.id, supplier_id=supplier.id, reason=reason, created_at=now))
        result = _supplier_brief(supplier)
        uow.commit()
        return result


def _rfq_supplier(session: Session, rfq: RfqRow, supplier_id: UUID) -> RfqSupplierRow:
    row = session.scalar(select(RfqSupplierRow).where(
        RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.supplier_id == supplier_id))
    if row is None:
        raise NotFound("Поставщика нет в этом запросе")
    return row


def mark_sent(uow: UnitOfWork, actor: Actor, now: datetime, rfq_id: UUID, supplier_id: UUID) -> None:
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        row = _rfq_supplier(session, rfq, supplier_id)
        if row.sent_at is None:
            row.sent_at = now
        uow.commit()


def mark_declined(
    uow: UnitOfWork, actor: Actor, now: datetime, rfq_id: UUID, supplier_id: UUID, note: str | None, declined: bool,
) -> None:
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        row = _rfq_supplier(session, rfq, supplier_id)
        row.declined_at = now if declined else None
        row.decline_note = ((note or "").strip()[:512] or None) if declined else None
        uow.commit()


def remove_supplier(uow: UnitOfWork, actor: Actor, rfq_id: UUID, supplier_id: UUID) -> None:
    """Only while the supplier has no quote in this request."""
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        row = _rfq_supplier(session, rfq, supplier_id)
        if session.scalar(select(QuoteRow.id).where(QuoteRow.rfq_id == rfq.id, QuoteRow.supplier_id == supplier_id)):
            raise Conflict("У поставщика уже есть КП в этом запросе — сначала удалите КП")
        session.delete(row)
        uow.commit()


# ── offers and the matrix ───────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Entry:
    offer: Offer
    line: QuoteLineRow
    quote: QuoteRow
    cover_quantity: Decimal | None


def _entries(session: Session, rfq_id: UUID, lines: dict[UUID, RfqLineRow], valid_days: int) -> dict[UUID, list[_Entry]]:
    by_line: dict[UUID, list[_Entry]] = {line_id: [] for line_id in lines}
    rows = session.execute(
        select(QuoteLineRow, QuoteRow).join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id).where(QuoteRow.rfq_id == rfq_id)
    )
    for quote_line, quote in rows:
        rfq_line = lines.get(quote_line.rfq_line_id)
        if rfq_line is None:
            continue
        covered = None if quote_line.declined else cover(
            Decimal(rfq_line.quantity), price=quote_line.unit_price, vat_included=quote_line.vat_included,
            vat_rate=Decimal(quote_line.vat_rate), moq=quote_line.moq, pack=quote_line.pack,
            tiers=read_tiers(quote_line.tiers),
        )
        offer = Offer(
            quote_line_id=quote_line.id, supplier_id=quote.supplier_id, is_key=quote.supplier_is_key,
            received_on=quote.received_on, gross_unit=covered.unit_gross if covered else None,
            total=covered.total_gross if covered else None, declined=quote_line.declined, analog=quote_line.analog,
            valid_until=quote.valid_until or quote.received_on + timedelta(days=valid_days), entered_at=quote.created_at,
        )
        by_line[rfq_line.id].append(_Entry(offer, quote_line, quote, covered.quantity if covered else None))
    return by_line


def line_offers(session: Session, rfq_id: UUID, lines: dict[UUID, RfqLineRow], valid_days: int) -> dict[UUID, list[Offer]]:
    """Every offer of each live request line (current or expired): the read model of the «Сегодня» follow-ups."""
    return {line_id: [entry.offer for entry in found] for line_id, found in _entries(session, rfq_id, lines, valid_days).items()}


def _cell(entry: _Entry, *, current: bool, best: Offer | None, base: Offer | None, decision: DecisionRow | None,
          requires: list[str]) -> dict:
    line, quote, offer = entry.line, entry.quote, entry.offer
    return {
        "quote_line_id": str(line.id), "quote_id": str(quote.id),
        "received_on": quote.received_on.isoformat(),
        "valid_until": offer.valid_until.isoformat() if offer.valid_until else None,
        "unit_price": num(line.unit_price), "vat_included": line.vat_included, "vat_rate": num(Decimal(line.vat_rate)),
        "gross_unit": num(offer.gross_unit), "cover_qty": num(entry.cover_quantity), "total": num(offer.total),
        "moq": num(line.moq), "pack": num(line.pack), "tiers": list(line.tiers or []),
        "lead_days": line.lead_days, "in_stock": line.in_stock, "analog": line.analog, "analog_note": line.analog_note,
        "declined": line.declined, "expired": not current,
        "cheapest": current and best is not None and best.quote_line_id == offer.quote_line_id,
        "baseline": current and base is not None and base.quote_line_id == offer.quote_line_id,
        "decided": decision is not None and decision.quote_line_id == line.id,
        "requires": requires,
    }


def _decision_result(decision: DecisionRow, supplier_id: UUID | None) -> dict:
    return {
        "id": str(decision.id), "quote_line_id": str(decision.quote_line_id),
        "supplier_id": str(supplier_id) if supplier_id else None,
        "cover_quantity": num(decision.cover_quantity), "unit_gross": num(decision.unit_gross),
        "total_gross": num(decision.total_gross), "baseline_unit_gross": num(decision.baseline_unit_gross),
        "cheapest_unit_gross": num(decision.cheapest_unit_gross), "priced_quotes": decision.priced_quotes,
        "gate_override_reason": decision.gate_override_reason, "not_cheapest_reason": decision.not_cheapest_reason,
        "analog_approved_by": decision.analog_approved_by,
        "decided_at": decision.decided_at.isoformat(), "applied_at": decision.applied_at.isoformat() if decision.applied_at else None,
    }


def matrix(uow: UnitOfWork, lines: ProcurementLines, actor: Actor, rfq_id: UUID, today: date) -> dict:
    """Lines × suppliers: each supplier's latest valid offer (an expired one is shown greyed), cheapest by the cost of
    covering the need, the gate per line, the budget Δ, and per supplier the whole basket bought from him."""
    with uow:
        session = _session(uow)
        rfq = _rfq(session, rfq_id)
        values = purchasing_settings.load(session)
        min_quotes, require_non_key = int(values["gate_min_quotes"]), bool(values["gate_require_non_key"])
        rfq_lines = _live_lines(session, rfq.id)
        by_id = {line.id: line for line in rfq_lines}
        suppliers = list(session.execute(
            select(RfqSupplierRow, SupplierRow).join(SupplierRow, SupplierRow.id == RfqSupplierRow.supplier_id)
            .where(RfqSupplierRow.rfq_id == rfq.id)
        ).all())
        suppliers.sort(key=lambda pair: (not pair[1].is_key, pair[1].name.casefold()))
        quotes = list(session.scalars(
            select(QuoteRow).where(QuoteRow.rfq_id == rfq.id).order_by(QuoteRow.received_on, QuoteRow.created_at)
        ))
        entries = _entries(session, rfq.id, by_id, int(values["quote_valid_days"]))
        decisions = {
            row.rfq_line_id: row
            for row in session.scalars(select(DecisionRow).where(DecisionRow.rfq_line_id.in_(list(by_id))))
        } if by_id else {}
        views = lines.get_many([line.procurement_line_id for line in rfq_lines])
        supplier_of_quote_line = {entry.line.id: entry.quote.supplier_id for found in entries.values() for entry in found}
        used_quotes = {
            entry.quote.id for line_id, found in entries.items() for entry in found
            if decisions.get(line_id) is not None and decisions[line_id].quote_line_id == entry.line.id
        }

        line_results: list[dict] = []
        current_by_line: dict[UUID, list[Offer]] = {}
        for line in rfq_lines:
            found = entries[line.id]
            offers = [entry.offer for entry in found]
            current = current_offers(offers, today)
            current_by_line[line.id] = current
            current_ids = {offer.quote_line_id for offer in current}
            best, base = cheapest(current), baseline(current)
            gate = gate_status(current, min_quotes, require_non_key)
            decision = decisions.get(line.id)
            cells: dict[str, dict] = {}
            for _, supplier in suppliers:
                own = [entry for entry in found if entry.quote.supplier_id == supplier.id]
                if not own:
                    continue
                live = next((entry for entry in own if entry.offer.quote_line_id in current_ids), None)
                shown = live or max(own, key=lambda entry: _arrival(entry.offer))
                if decision is not None and live is None:
                    chosen = next((entry for entry in own if entry.line.id == decision.quote_line_id), None)
                    shown = chosen or shown
                requires = [
                    violation.field for violation in check_decision(
                        offers, shown.offer.quote_line_id, today=today, min_quotes=min_quotes,
                        require_non_key=require_non_key, reasons=Reasons(),
                    )
                ] if live is not None and live.offer.priced else []
                cells[str(supplier.id)] = _cell(shown, current=live is not None, best=best, base=base, decision=decision,
                                                requires=requires)
            view = views.get(line.procurement_line_id)
            price = decision.unit_gross if decision is not None else (best.gross_unit if best else None)
            line_results.append({
                "id": str(line.id), "procurement_line_id": str(line.procurement_line_id),
                "legacy_id": view.legacy_id if view else None, "status": view.status if view else None,
                "version": view.version if view else None, "editable": bool(view and view.editable),
                "name": line.name, "article": line.article, "quantity": num(Decimal(line.quantity)), "unit": line.unit,
                "need_by": line.need_by.isoformat() if line.need_by else None,
                "budget_unit_gross": num(line.budget_unit_gross),
                "delta_pct": num(delta_pct(price, line.budget_unit_gross)),
                "gate": {"priced": gate.priced, "required": gate.required, "has_non_key": gate.has_non_key,
                         "require_non_key": gate.require_non_key, "passes": gate.passes},
                "cheapest_quote_line_id": str(best.quote_line_id) if best else None,
                "baseline_quote_line_id": str(base.quote_line_id) if base else None,
                "decision": _decision_result(decision, supplier_of_quote_line.get(decision.quote_line_id)) if decision else None,
                "cells": cells,
            })

        latest_terms: dict[UUID, QuoteRow] = {}
        for quote in quotes:
            latest_terms[quote.supplier_id] = quote
        supplier_results: list[dict] = []
        for rfq_supplier, supplier in suppliers:
            totals = [
                offer.total for current in current_by_line.values() for offer in current
                if offer.supplier_id == supplier.id and offer.priced and offer.total is not None
            ]
            terms = latest_terms.get(supplier.id)
            delivery = terms.delivery_cost if terms else None
            minimum = terms.min_order_sum if terms else None
            basket = sum(totals, Decimal(0)) + (delivery or 0) if totals else None
            chosen = sum(
                (decision.total_gross for decision in decisions.values()
                 if supplier_of_quote_line.get(decision.quote_line_id) == supplier.id),
                Decimal(0),
            )
            warning = None
            if minimum:
                if chosen and chosen < minimum:
                    warning = f"Выбрано на {num(chosen)} ₽ — меньше минимального заказа {num(minimum)} ₽"
                elif totals and sum(totals, Decimal(0)) < minimum:
                    warning = f"Даже всё у него — меньше минимального заказа {num(minimum)} ₽"
            supplier_results.append({
                "supplier_id": str(supplier.id), "name": supplier.name, "is_key": supplier.is_key,
                "reason": rfq_supplier.reason,
                "sent_at": rfq_supplier.sent_at.isoformat() if rfq_supplier.sent_at else None,
                "declined_at": rfq_supplier.declined_at.isoformat() if rfq_supplier.declined_at else None,
                "decline_note": rfq_supplier.decline_note,
                "answered": terms is not None,
                "delivery_cost": num(delivery), "min_order_sum": num(minimum),
                "basket_total": num(basket), "basket_lines": len(totals), "chosen_total": num(chosen) if chosen else None,
                "min_order_warning": warning,
            })
        return {
            "rfq": {
                "id": str(rfq.id), "number": rfq.number, "title": rfq.title, "created_at": rfq.created_at.isoformat(),
                "closed_at": rfq.closed_at.isoformat() if rfq.closed_at else None, "mine": rfq.buyer_id == actor.id,
            },
            "settings": {"gate_min_quotes": min_quotes, "gate_require_non_key": require_non_key,
                         "default_vat_rate": num(Decimal(values["default_vat_rate"]))},
            "suppliers": supplier_results,
            "lines": line_results,
            "quotes": [
                {
                    "id": str(quote.id), "supplier_id": str(quote.supplier_id), "supplier_is_key": quote.supplier_is_key,
                    "received_on": quote.received_on.isoformat(),
                    "valid_until": quote.valid_until.isoformat() if quote.valid_until else None,
                    "delivery_cost": num(quote.delivery_cost), "min_order_sum": num(quote.min_order_sum),
                    "source": quote.source, "used": quote.id in used_quotes,
                }
                for quote in quotes
            ],
        }


# ── quotes ──────────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QuoteLineInput:
    rfq_line_id: UUID
    unit_price: Decimal | None = None
    vat_included: bool = True
    vat_rate: Decimal | None = None
    moq: Decimal | None = None
    pack: Decimal | None = None
    tiers: tuple[tuple[Decimal, Decimal], ...] = ()
    lead_days: int | None = None
    in_stock: bool = False
    declined: bool = False
    analog: bool = False
    analog_note: str | None = None


@dataclass(frozen=True)
class QuoteInput:
    supplier_id: UUID
    lines: tuple[QuoteLineInput, ...]
    received_on: date | None = None
    valid_until: date | None = None
    delivery_cost: Decimal | None = None
    min_order_sum: Decimal | None = None
    source: str = "manual"
    note: str | None = None


def _positive(value: Decimal | None, label: str) -> Decimal | None:
    if value is not None and value <= 0:
        raise Refused(f"{label} должно быть больше нуля")
    return value


def add_quote(uow: UnitOfWork, actor: Actor, now: datetime, rfq_id: UUID, data: QuoteInput) -> dict:
    """A quote is never edited: a re-quote is a new quote and supersedes the supplier's earlier one per line.
    `supplier_is_key` is the supplier's flag at entry."""
    if data.source not in ("manual", "paste", "file"):
        raise Refused("Неизвестный источник КП")
    if not data.lines:
        raise Refused("В КП нет ни одной строки с ценой или отказом")
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        supplier = session.get(SupplierRow, data.supplier_id)
        if supplier is None:
            raise NotFound("Поставщик не найден")
        live = {line.id: line for line in _live_lines(session, rfq.id)}
        default_vat = Decimal(purchasing_settings.load(session)["default_vat_rate"])
        received_on = data.received_on or _today(now)
        if data.valid_until is not None and data.valid_until < received_on:
            raise Refused("КП действует до даты раньше даты получения")
        for value, label in ((data.delivery_cost, "Доставка"), (data.min_order_sum, "Минимальный заказ")):
            if value is not None and value < 0:
                raise Refused(f"{label} не может быть меньше нуля")
        seen: set[UUID] = set()
        rows: list[QuoteLineRow] = []
        quote_id = uuid4()
        for item in data.lines:
            line = live.get(item.rfq_line_id)
            if line is None:
                raise Refused("Строка КП не из этого запроса")
            if item.rfq_line_id in seen:
                raise Refused(f"«{line.name}» дважды в одном КП")
            seen.add(item.rfq_line_id)
            vat = default_vat if item.vat_rate is None else item.vat_rate
            if vat < 0 or vat >= 100:
                raise Refused(f"«{line.name}»: НДС от 0 до 99 %")
            if not item.declined and (item.unit_price is None or item.unit_price <= 0):
                raise Refused(f"«{line.name}»: укажите цену или отказ")
            if item.lead_days is not None and item.lead_days < 0:
                raise Refused(f"«{line.name}»: срок не может быть меньше нуля")
            tiers = []
            for start, price in item.tiers:
                if start <= 0 or price <= 0:
                    raise Refused(f"«{line.name}»: ступень цены — количество и цена больше нуля")
                tiers.append({"from": num(start), "price": num(price)})
            rows.append(QuoteLineRow(
                id=uuid4(), quote_id=quote_id, rfq_line_id=line.id,
                unit_price=None if item.declined else item.unit_price, vat_included=item.vat_included, vat_rate=vat,
                moq=_positive(item.moq, f"«{line.name}»: минимальная партия"),
                pack=_positive(item.pack, f"«{line.name}»: упаковка"), tiers=tiers, lead_days=item.lead_days,
                in_stock=item.in_stock, declined=item.declined, analog=item.analog,
                analog_note=((item.analog_note or "").strip()[:512] or None) if item.analog else None,
            ))
        if session.scalar(select(RfqSupplierRow.id).where(
                RfqSupplierRow.rfq_id == rfq.id, RfqSupplierRow.supplier_id == supplier.id)) is None:
            session.add(RfqSupplierRow(id=uuid4(), rfq_id=rfq.id, supplier_id=supplier.id, reason="manual", created_at=now))
        session.add(QuoteRow(
            id=quote_id, rfq_id=rfq.id, supplier_id=supplier.id, supplier_is_key=supplier.is_key,
            received_on=received_on, valid_until=data.valid_until, currency="RUB", delivery_cost=data.delivery_cost,
            min_order_sum=data.min_order_sum, source=data.source, note=(data.note or "").strip() or None,
            created_at=now, created_by=actor.id,
        ))
        session.flush()
        session.add_all(rows)
        _audit(uow, rfq.id, "purchasing.quote_added", actor, now,
               {"number": rfq.number, "supplier": supplier.name, "lines": str(len(rows))})
        uow.commit()
        return {"id": str(quote_id)}


def delete_quote(uow: UnitOfWork, actor: Actor, now: datetime, quote_id: UUID) -> None:
    with uow:
        session = _session(uow)
        quote = session.get(QuoteRow, quote_id)
        if quote is None:
            raise NotFound("КП не найдено")
        rfq = _open(_own(_rfq(session, quote.rfq_id), actor))
        line_ids = list(session.scalars(select(QuoteLineRow.id).where(QuoteLineRow.quote_id == quote.id)))
        if line_ids and session.scalar(select(DecisionRow.id).where(DecisionRow.quote_line_id.in_(line_ids))):
            raise Conflict("По этому КП уже принято решение — сначала отмените решение")
        for row in session.scalars(select(QuoteLineRow).where(QuoteLineRow.quote_id == quote.id)):
            session.delete(row)
        session.flush()
        session.delete(quote)
        _audit(uow, rfq.id, "purchasing.quote_deleted", actor, now, {"number": rfq.number, "quote": str(quote.id)})
        uow.commit()


# ── paste from Excel (port of Roller shared/quote-import.ts) ───────────────────────────────────────────────────

COLUMN_KEYS = ("name", "article", "qty", "unit", "price", "vat", "lead")
_HINTS = {
    "name": re.compile(r"наимен|номенклат|товар|описан|позиц|name|description", re.I),
    "article": re.compile(r"артик|парт|код|обознач|p/n|part|art", re.I),
    "qty": re.compile(r"кол|к-во|qty|количеств", re.I),
    "unit": re.compile(r"^ед|ед\.|изм|unit", re.I),
    "price": re.compile(r"цена|стоим|price|руб", re.I),
    "vat": re.compile(r"ндс|nds|vat", re.I),
    "lead": re.compile(r"срок|постав|налич|lead|дн", re.I),
}
_NOT_VAT = re.compile(r"сумм|итог|стоим|цена|total", re.I)
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def parse_ru_number(text: str) -> Decimal | None:
    """«1 234,56», «1234.56 руб.» → 1234.56; otherwise None."""
    cleaned = re.sub(r"[\s ]", "", text or "")
    cleaned = re.sub(r"руб\.?|р\.|₽", "", cleaned, flags=re.I).replace(",", ".")
    if not _NUMBER.match(cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_lead(text: str) -> tuple[int | None, bool, bool]:
    """«10 дн», «2-3 недели», «в наличии», «под заказ 30 раб. дней» → (days, in stock, declined)."""
    t = (text or "").strip().lower()
    if not t:
        return None, False, False
    if re.search(r"нет в налич|не поставл|отказ|снят|не можем", t):
        return None, False, True
    if re.search(r"в налич|склад|stock|сразу", t):
        return 0, True, False
    numbers = [Decimal(m.replace(",", ".")) for m in re.findall(r"\d+(?:[.,]\d+)?", t)]
    if not numbers:
        return None, False, False
    days = max(numbers)
    if re.search(r"нед|week", t):
        days *= 7
    elif re.search(r"мес|month", t):
        days *= 30
    elif "раб" in t:
        days = (days * Decimal("1.4")).to_integral_value(rounding="ROUND_CEILING")
    return int(days.to_integral_value()), False, False


def parse_vat(text: str) -> Decimal | None:
    """«22%», «20», «без НДС» → the rate; anything else → None (the default applies)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if "без" in t:
        return Decimal(0)
    value = parse_ru_number(t.replace("%", ""))
    return value if value is not None and 0 <= value < 100 else None


def parse_pasted_table(text: str) -> tuple[list[str] | None, list[list[str]]]:
    """Tab-separated text as Excel puts it on the clipboard → header (when recognisable) + rows."""
    lines = [
        [cell.strip() for cell in line.split("\t")]
        for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    lines = [cells for cells in lines if any(cells)]
    if not lines:
        return None, []
    first = lines[0]
    hits = sum(1 for cell in first if any(pattern.search(cell) for pattern in _HINTS.values()))
    looks_like_header = hits >= 2 and all(cell == "" or parse_ru_number(cell) is None for cell in first)
    return (first, lines[1:]) if looks_like_header else (None, lines)


def guess_columns(header: list[str] | None, rows: list[list[str]]) -> dict[str, int]:
    """Header words first («Цена» before «Код»: a weaker hint); without a header the longest text column is the
    name and the numeric ones after it are quantity, then price."""
    columns = {key: -1 for key in COLUMN_KEYS}
    width = max([len(header or [])] + [len(row) for row in rows] or [0])
    if header:
        for key in ("name", "price", "qty", "lead", "unit", "vat", "article"):
            for index, title in enumerate(header):
                if index in columns.values() or not _HINTS[key].search(title):
                    continue
                if key == "vat" and _NOT_VAT.search(title):
                    continue
                columns[key] = index
                break
        if columns["name"] != -1:
            return columns
    sample = rows[:30]

    def average_length(index: int) -> float:
        return sum(len(row[index]) if index < len(row) else 0 for row in sample) / max(len(sample), 1)

    def numeric_share(index: int) -> float:
        hits = sum(1 for row in sample if index < len(row) and row[index] and parse_ru_number(row[index]) is not None)
        return hits / max(len(sample), 1)

    best = -1
    for index in range(width):
        if numeric_share(index) < 0.5 and (best == -1 or average_length(index) > average_length(best)):
            best = index
    columns["name"] = best
    numeric = [index for index in range(width) if index != best and numeric_share(index) >= 0.8]
    if len(numeric) >= 2:
        columns["qty"], columns["price"] = numeric[0], numeric[1]
    elif len(numeric) == 1:
        columns["price"] = numeric[0]
    return columns


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-zа-яё0-9]+", (text or "").lower()) if len(token) >= 2}


@dataclass
class PastedRow:
    source_name: str
    source_article: str
    qty: Decimal | None
    unit: str
    price: Decimal | None
    vat_rate: Decimal | None
    lead_days: int | None
    in_stock: bool
    declined: bool
    analog: bool
    rfq_line_id: UUID | None = None
    matched_by: str | None = None
    extra: dict = field(default_factory=dict)


def paste_rows(rows: list[list[str]], columns: dict[str, int]) -> list[PastedRow]:
    def cell(row: list[str], key: str) -> str:
        index = columns.get(key, -1)
        return row[index].strip() if 0 <= index < len(row) else ""

    result: list[PastedRow] = []
    for row in rows:
        days, in_stock, declined = parse_lead(cell(row, "lead"))
        price_text = cell(row, "price")
        price = parse_ru_number(price_text)
        if price is not None and price <= 0:
            price = None
        item = PastedRow(
            source_name=cell(row, "name"), source_article=cell(row, "article"), qty=parse_ru_number(cell(row, "qty")),
            unit=cell(row, "unit"), price=price, vat_rate=parse_vat(cell(row, "vat")), lead_days=days,
            in_stock=in_stock, declined=declined or (price is None and bool(re.search(r"нет|отказ", price_text, re.I))),
            analog=bool(re.search(r"аналог", cell(row, "name"), re.I)),
        )
        if item.source_name or item.price is not None:
            result.append(item)
    return result


@dataclass(frozen=True)
class MatchTarget:
    rfq_line_id: UUID
    name: str
    article: str | None


def match_rows(rows: list[PastedRow], targets: Sequence[MatchTarget]) -> list[PastedRow]:
    """Each pasted row → one RFQ line: the part number first (code_key, exact or inside the row's text, the longest
    wins), then the name words (≥ 60 % in common). One RFQ line takes at most one row; the rest stay for the buyer."""
    taken: set[UUID] = set()

    def claim(row: PastedRow, line_id: UUID, by: str) -> None:
        if line_id not in taken and row.rfq_line_id is None:
            row.rfq_line_id, row.matched_by = line_id, by
            taken.add(line_id)

    coded = sorted(
        ((target, code_key(target.article)) for target in targets if len(code_key(target.article)) >= 3),
        key=lambda pair: -len(pair[1]),
    )
    for row in rows:
        own_key = code_key(row.source_article)
        text = code_key(f"{row.source_article} {row.source_name}")
        for target, key in coded:
            if target.rfq_line_id in taken:
                continue
            if (own_key and own_key == key) or key in text:
                claim(row, target.rfq_line_id, "article")
                break
    for row in rows:
        if row.rfq_line_id is not None:
            continue
        mine = _tokens(f"{row.source_name} {row.source_article}")
        best: tuple[float, UUID] | None = None
        for target in targets:
            if target.rfq_line_id in taken:
                continue
            theirs = _tokens(f"{target.name} {target.article or ''}")
            common = len(mine & theirs)
            score = common / max(1, min(len(mine), len(theirs)))
            if score >= 0.6 and (best is None or score > best[0]):
                best = (score, target.rfq_line_id)
        if best is not None:
            claim(row, best[1], "name")
    return rows


def paste_preview(uow: UnitOfWork, actor: Actor, rfq_id: UUID, text: str) -> dict:
    """Parsed rows matched to the request's lines; nothing is saved — the client confirms through add_quote."""
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        targets = [MatchTarget(line.id, line.name, line.article) for line in _live_lines(session, rfq.id)]
        default_vat = Decimal(purchasing_settings.load(session)["default_vat_rate"])
    header, rows = parse_pasted_table(text)
    if not rows:
        raise Refused("Во вставке нет строк")
    columns = guess_columns(header, rows)
    if columns["name"] < 0 and columns["article"] < 0:
        raise Refused("Не нашёл столбец с наименованием")
    price_title = header[columns["price"]] if header and columns["price"] >= 0 else ""
    vat_included = not re.search(r"без\s*ндс", price_title, re.I)
    matched = match_rows(paste_rows(rows, columns), targets)
    return {
        "header": header,
        "columns": columns,
        "vat_included": vat_included,
        "default_vat_rate": num(default_vat),
        "rows": [
            {
                "source_name": row.source_name, "source_article": row.source_article, "qty": num(row.qty),
                "unit": row.unit, "price": num(row.price), "vat_rate": num(row.vat_rate), "lead_days": row.lead_days,
                "in_stock": row.in_stock, "declined": row.declined, "analog": row.analog,
                "rfq_line_id": str(row.rfq_line_id) if row.rfq_line_id else None, "matched_by": row.matched_by,
            }
            for row in matched[:200]
        ],
    }


# ── decisions ───────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionInput:
    rfq_line_id: UUID
    quote_line_id: UUID
    expected_version: int | None = None
    gate_override_reason: str | None = None
    not_cheapest_reason: str | None = None
    analog_approved_by: str | None = None


def decide(
    uow: UnitOfWork, lines: ProcurementLines, actor: Actor, now: datetime, rfq_id: UUID, data: DecisionInput,
) -> dict:
    """check_decision → the decision with its snapshots (baseline, cheapest, priced quotes) → the procurement line's
    ПОСТАВЩИК / КОЛ-ВО / ЕД.ИЗМ (факт) through the bridge, with the line's version → applied_at.

    Nothing is written to the purchasing tables before procurement accepts the facts; if the decision then fails to
    save, the facts stay — a retry of the same choice writes nothing new there (the save is idempotent)."""
    today = _today(now)
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        line = session.get(RfqLineRow, data.rfq_line_id)
        if line is None or line.rfq_id != rfq.id or line.removed_at is not None:
            raise NotFound("Строки нет в этом запросе")
        quote_line = session.get(QuoteLineRow, data.quote_line_id)
        if quote_line is None or quote_line.rfq_line_id != line.id:
            raise Refused("Это КП не относится к строке запроса")
        if session.scalar(select(DecisionRow.id).where(DecisionRow.rfq_line_id == line.id)) is not None:
            raise Conflict("По строке уже есть решение — сначала отмените его")
        view = lines.get_many([line.procurement_line_id]).get(line.procurement_line_id)
        if view is None:
            raise Refused("Строка закупки не найдена")
        if view.buyer_id != actor.id:
            raise Forbidden("Поставщика выбирает только закупщик строки")
        if not view.editable:
            raise Refused(f"{view.legacy_id} уже «{view.status}» — поставщик в строку не пишется")
        values = purchasing_settings.load(session)
        rules = {"today": today, "min_quotes": int(values["gate_min_quotes"]),
                 "require_non_key": bool(values["gate_require_non_key"])}
        entries = _entries(session, rfq.id, {line.id: line}, int(values["quote_valid_days"]))[line.id]
        offers = [entry.offer for entry in entries]
        asked = Reasons(data.gate_override_reason, data.not_cheapest_reason, data.analog_approved_by)
        violations = check_decision(offers, quote_line.id, reasons=asked, **rules)
        if violations:
            raise Refused(" ".join(violation.message for violation in violations), violations)
        reasons = normalize_decision(offers, quote_line.id, reasons=asked, **rules)
        current = current_offers(offers, today)
        best, base = cheapest(current), baseline(current)
        gate = gate_status(current, rules["min_quotes"], rules["require_non_key"])
        chosen = next(entry for entry in entries if entry.line.id == quote_line.id)
        supplier = session.get(SupplierRow, chosen.quote.supplier_id)
        assert supplier is not None and chosen.cover_quantity is not None and chosen.offer.total is not None
        lines.apply_choice(
            line.procurement_line_id, supplier=supplier.name, quantity=chosen.cover_quantity, unit=line.unit,
            expected_version=data.expected_version or view.version, actor=actor, now=now,
        )
        decision = DecisionRow(
            id=uuid4(), rfq_line_id=line.id, quote_line_id=quote_line.id, cover_quantity=chosen.cover_quantity,
            unit_gross=chosen.offer.gross_unit, total_gross=chosen.offer.total,
            baseline_unit_gross=base.gross_unit if base else None, cheapest_unit_gross=best.gross_unit if best else None,
            priced_quotes=gate.priced, gate_override_reason=reasons.gate_override_reason,
            not_cheapest_reason=reasons.not_cheapest_reason, analog_approved_by=reasons.analog_approved_by,
            decided_at=now, decided_by=actor.id, applied_at=now,
        )
        session.add(decision)
        _audit(uow, rfq.id, "purchasing.decision_made", actor, now, {
            "number": rfq.number, "line": view.legacy_id, "supplier": supplier.name,
            "quantity": num(chosen.cover_quantity), "total": num(chosen.offer.total),
        })
        uow.commit()
        return _decision_result(decision, supplier.id)


def undo_decision(uow: UnitOfWork, lines: ProcurementLines, actor: Actor, now: datetime, rfq_id: UUID,
                  decision_id: UUID) -> None:
    """Only while the procurement line is still «В работе»/«Готов к запросу». The line's facts stay as they are."""
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        decision = session.get(DecisionRow, decision_id)
        line = session.get(RfqLineRow, decision.rfq_line_id) if decision is not None else None
        if decision is None or line is None or line.rfq_id != rfq.id:
            raise NotFound("Решение не найдено")
        view = lines.get_many([line.procurement_line_id]).get(line.procurement_line_id)
        if view is not None and not view.editable:
            raise Refused(f"{view.legacy_id} уже «{view.status}» — решение не отменить")
        session.delete(decision)
        _audit(uow, rfq.id, "purchasing.decision_undone", actor, now, {"number": rfq.number, "line": line.name})
        uow.commit()


def close_rfq(uow: UnitOfWork, actor: Actor, now: datetime, rfq_id: UUID) -> None:
    """Lines without a decision leave the request (removed_at) so they can go into a new one."""
    with uow:
        session = _session(uow)
        rfq = _own(_rfq(session, rfq_id), actor)
        if rfq.closed_at is not None:
            return
        decided = set(session.scalars(
            select(DecisionRow.rfq_line_id).join(RfqLineRow, RfqLineRow.id == DecisionRow.rfq_line_id)
            .where(RfqLineRow.rfq_id == rfq.id)
        ))
        for line in _live_lines(session, rfq.id):
            if line.id not in decided:
                line.removed_at = now
        rfq.closed_at, rfq.updated_at, rfq.updated_by = now, now, actor.id
        rfq.version += 1
        _audit(uow, rfq.id, "purchasing.rfq_closed", actor, now, {"number": rfq.number})
        uow.commit()
