"""Lines out of a request for quotes (port of Roller_Buyer rfq-withdraw.ts): back to the free lines («В новые») or to
«Перезапрос», with a note of who answered what — «Запрос КП КП-2610-001 от 13.10.2026: ФСТ — отказ; МПО — 23,18 ₽
(аналог: …); Меттатрон — не ответил».

ERP keeps the request line and its quotes (history; the procurement line is the entity, not the request line): the row
gets removed_at, why (`new` / `requote`), who and the note. A line with a chosen supplier is not taken out; a line with
prices goes only when the buyer confirms dropping them (409 `has_prices` first). The procurement line itself is not
touched — it is free for a new request at once."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.shared.identity import Actor

from . import settings as purchasing_settings
from .infrastructure import DecisionRow, QuoteRow, RfqLineRow, RfqSupplierRow, SupplierRow
from .offers import Offer, current_offers
from .rfq import Conflict, Refused, UnitOfWork, _audit, _live_lines, _open, _own, _rfq, _session, _today, _entries, asked_lines

WITHDRAW_TO = ("new", "requote")


class HasPrices(Conflict):
    """Suppliers priced these lines: the buyer confirms dropping the prices (they stay in the note)."""

    def __init__(self, names: Sequence[str]):
        super().__init__(f"У строк есть цены поставщиков: {', '.join(names)}. Убрать вместе с ценами?")
        self.payload = {"code": "has_prices", "message": str(self), "lines": list(names)}


def ru_money(value: Decimal) -> str:
    """22.33 → «22,33 ₽», 1500 → «1 500,00 ₽» (plain spaces: the note is read as text)."""
    whole, _, cents = f"{value.quantize(Decimal('0.01')):.2f}".partition(".")
    sign = "-" if whole.startswith("-") else ""
    digits = whole.lstrip("-")
    groups = []
    while digits:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    return f"{sign}{' '.join(groups)},{cents} ₽"


def withdraw_note(number: str, created_on, suppliers: Sequence[tuple[str, bool]], offers: dict[str, Offer | None],
                  analog_notes: dict[str, str | None]) -> str:
    """`suppliers` — (name, answered) of the suppliers asked for the line; `offers` — each one's current offer here."""
    parts: list[str] = []
    for name, answered in suppliers:
        offer = offers.get(name)
        if offer is None:
            parts.append(f"{name} — {'не предложил' if answered else 'не ответил'}")
        elif offer.declined:
            parts.append(f"{name} — отказ")
        else:
            price = ru_money(offer.gross_unit) if offer.gross_unit is not None else "цена"
            analog = ""
            if offer.analog:
                note = analog_notes.get(name)
                analog = f" (аналог{f': {note}' if note else ''})"
            parts.append(f"{name} — {price}{analog}")
    return f"Запрос КП {number} от {created_on:%d.%m.%Y}: {'; '.join(parts) if parts else 'поставщиков не было'}"


def withdraw_lines(
    uow: UnitOfWork, actor: Actor, now: datetime, rfq_id: UUID, rfq_line_ids: Sequence[UUID], to: str,
    drop_prices: bool, rfq_version: int | None = None,
) -> dict:
    if to not in WITHDRAW_TO:
        raise Refused("Куда убрать строку: «new» или «requote»")
    ids = list(dict.fromkeys(rfq_line_ids))
    if not ids:
        raise Refused("Выберите строки")
    with uow:
        session = _session(uow)
        rfq = _open(_own(_rfq(session, rfq_id), actor))
        if rfq_version is not None and rfq.version != rfq_version:
            raise Conflict("Запрос изменили — обновите и повторите")
        live = {line.id: line for line in _live_lines(session, rfq.id)}
        lines: list[RfqLineRow] = []
        for line_id in ids:
            line = live.get(line_id)
            if line is None:
                raise Refused("Строка не из этого запроса КП (или уже убрана)")
            lines.append(line)
        decided = set(session.scalars(select(DecisionRow.rfq_line_id).where(DecisionRow.rfq_line_id.in_(ids))))
        if decided:
            names = ", ".join(line.name for line in lines if line.id in decided)
            raise Refused(f"По строкам уже выбран поставщик — сначала отмените выбор: {names}")
        values = purchasing_settings.load(session)
        today = _today(now)
        entries = _entries(session, rfq.id, {line.id: line for line in lines}, int(values["quote_valid_days"]))
        current = {line.id: current_offers([entry.offer for entry in entries[line.id]], today) for line in lines}
        priced = [line.name for line in lines if any(not offer.declined for offer in current[line.id])]
        if priced and not drop_prices:
            raise HasPrices(priced)

        suppliers = list(session.execute(
            select(RfqSupplierRow, SupplierRow).join(SupplierRow, SupplierRow.id == RfqSupplierRow.supplier_id)
            .where(RfqSupplierRow.rfq_id == rfq.id)
        ).all())
        suppliers.sort(key=lambda pair: (not pair[1].is_key, pair[1].name.casefold()))
        asked = asked_lines(session, [row.id for row, _ in suppliers])
        answered = set(session.scalars(select(QuoteRow.supplier_id).where(QuoteRow.rfq_id == rfq.id)))
        for line in lines:
            here = [(row, supplier) for row, supplier in suppliers if row.id not in asked or line.id in asked[row.id]]
            by_supplier = {offer.supplier_id: offer for offer in current[line.id]}
            notes = {entry.quote.supplier_id: entry.line.analog_note for entry in entries[line.id]}
            note = withdraw_note(
                rfq.number, rfq.created_at,
                [(supplier.name, supplier.id in answered or row.declined_at is not None) for row, supplier in here],
                {supplier.name: by_supplier.get(supplier.id) for _, supplier in here},
                {supplier.name: notes.get(supplier.id) for _, supplier in here},
            )
            line.removed_at, line.removed_reason, line.removed_by, line.removed_note = now, to, actor.id, note
        rfq.version += 1
        rfq.updated_at, rfq.updated_by = now, actor.id
        _audit(uow, rfq.id, "purchasing.rfq_lines_withdrawn", actor, now, {
            "number": rfq.number, "to": to, "lines": str(len(lines)),
        })
        uow.commit()
        return {"withdrawn": len(lines)}
