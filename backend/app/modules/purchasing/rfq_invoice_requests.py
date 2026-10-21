"""«Запросить счета» (port of Roller_Buyer packages/shared/src/invoice-request.ts): the lines chosen in a request for
quotes, grouped by supplier, as a letter asking each one for an invoice — quantities as they will be bought (the MOQ,
whole packs), prices of the supplier's own quote at that quantity's break in the quote's VAT mode, the totals, the
delivery, our requisites and the delivery address of the lines' «ТОЧКА ОТГРУЗКИ». The ERP sends no mail: the text is
copied or opened in the mail program.

Lines whose procurement line already has an invoice (past «Жду счет») are left out — the invoice is there."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select

from app.modules.procurement.domain import delivery_receiver
from app.shared.identity import Actor

from . import settings as purchasing_settings
from .infrastructure import DecisionRow, QuoteLineRow, QuoteRow, SupplierRow
from .offers import read_tiers
from .rfq import ProcurementLines, UnitOfWork, _live_lines, _rfq, _session, first_email, num
from .rfq_withdraw import ru_money

# Statuses where the invoice is still to be asked for: the line is on the buyer's desk or waits for the invoice.
ASK_STATUSES = ("В работе", "Готов к запросу", "Жду счет")
CANCELLED = "Отменено"


@dataclass(frozen=True)
class RequestLine:
    rfq_line_id: UUID
    name: str
    article: str | None
    unit: str
    quantity: Decimal  # what will be bought: the chosen offer's cover quantity
    unit_price: Decimal  # the quote's base price, its own VAT mode
    tiers: tuple  # offers.Tier
    pack: Decimal | None
    vat_included: bool
    delivery_point: str = ""
    legacy_id: str = ""


@dataclass
class Letter:
    supplier_id: UUID
    supplier_name: str
    email: str | None
    quote_number: str
    quote_date: date
    delivery_cost: Decimal | None
    lines: list[dict] = field(default_factory=list)
    total: Decimal = Decimal(0)
    points: list[str] = field(default_factory=list)


def _qty(value: Decimal) -> str:
    """1500 → «1 500», 87.5 → «87,5» (plain spaces: the letter goes into any mail program)."""
    text = format(value.normalize(), "f")
    whole, _, frac = text.partition(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return " ".join(groups) + (f",{frac}" if frac else "")


def _day(value: date) -> str:
    return f"{value:%d.%m.%Y}"


def line_name(name: str, article: str | None) -> str:
    return f"{name} ({article})" if article and article not in name else name


def invoice_letter_line(line: RequestLine, index: int) -> dict:
    """The quantity bought, its packs, the price at that quantity's break (the quote's VAT mode), the sum."""
    # The break this quantity falls into, in the quote's own VAT mode; packs only at the base price (a break often
    # means a bigger reel the quote does not name).
    applied = next((tier for tier in reversed(line.tiers) if tier.from_quantity <= line.quantity), None)
    price = applied.price if applied else line.unit_price
    packs = ""
    if applied is None and line.pack and line.pack > 0:
        count = line.quantity / line.pack
        if count == count.to_integral_value() and count > 1:
            packs = f"{_qty(count)} × {_qty(line.pack)} {line.unit}"
    total = (price * line.quantity).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return {
        "rfq_line_id": str(line.rfq_line_id), "legacy_id": line.legacy_id, "name": line_name(line.name, line.article),
        "article": line.article or "", "qty": num(line.quantity), "unit": line.unit, "packs": packs, "price": num(price),
        "sum": num(total), "vat_included": line.vat_included, "_total": total, "_index": index,
    }


def compose(letters: Sequence[Letter], requisites: str) -> list[dict]:
    """Subject, body and the website cart of each supplier's letter, dearest first (RB invoiceRequests)."""
    out: list[dict] = []
    for letter in sorted(letters, key=lambda item: -item.total):
        # A number is a number when it has digits; «сайт Продиэлком» is a note about where the prices came from.
        numbered = bool(re.search(r"\d", letter.quote_number))
        note = f" ({letter.quote_number})" if not numbered and letter.quote_number else ""
        ref = f"КП № {letter.quote_number} от {_day(letter.quote_date)}" if numbered else f"предложение от {_day(letter.quote_date)}{note}"
        all_gross = all(line["vat_included"] for line in letter.lines)
        all_net = all(not line["vat_included"] for line in letter.lines)

        def vat(gross: bool) -> str:
            return "с НДС" if gross else "без НДС"

        rows = []
        for index, line in enumerate(letter.lines, start=1):
            packs = f" ({line['packs']})" if line["packs"] else ""
            mode = "" if all_gross or all_net else f" {vat(line['vat_included'])}"
            rows.append(f"{index}. {line['name']} — {_qty(Decimal(line['qty']))} {line['unit']}{packs} × "
                        f"{ru_money(Decimal(line['price']))}{mode} = {ru_money(line['_total'])}")
        points = [point for point in dict.fromkeys(letter.points) if point]
        receivers = [delivery_receiver(point) for point in points] or [delivery_receiver("")]
        addresses = [receiver.address or point for receiver, point in zip(receivers, points or [""])]
        address = "; ".join(item for item in addresses if item) or "уточним при оплате"
        if letter.delivery_cost is not None:
            delivery = (f"Доставка — бесплатно, как в предложении, по адресу: {address}." if letter.delivery_cost == 0
                        else f"Доставка — {ru_money(letter.delivery_cost)}, как в предложении, по адресу: {address}.")
        else:
            delivery = f"Доставка по адресу: {address} — просим включить её стоимость в счёт."
        contact = receivers[0]
        subject = f"Запрос счёта — {ref}, {len(letter.lines)} поз."
        body = "\n".join([
            "Добрый день!",
            "",
            f"Просим выставить счёт на оплату по {f'вашему {ref}' if numbered else f'вашему предложению от {_day(letter.quote_date)}{note}'} на следующие позиции:",
            "",
            *rows,
            "",
            f"Итого по ценам предложения: {ru_money(letter.total)}{' с НДС' if all_gross else ' без НДС' if all_net else ''}.",
            "",
            delivery,
            f"Контактное лицо для доставки: {contact.name}, тел. {contact.phone}.",
            *(["", "Покупатель:", *[row for row in requisites.splitlines() if row.strip()]] if requisites.strip() else []),
            "",
            "В счёте просим указать срок поставки. Если цена, наличие или срок изменились — сообщите, пожалуйста, до выставления счёта.",
            "",
            "Заранее спасибо!",
        ])
        cart_rows = []
        for line in letter.lines:
            amount = line["packs"] or f"{_qty(Decimal(line['qty']))} {line['unit']}"
            cart_rows.append(f"{line['article'] or line['name']}\t{amount}")
        cart = "\n".join(cart_rows)
        out.append({
            "supplier_id": str(letter.supplier_id), "supplier_name": letter.supplier_name, "email": letter.email,
            "quote_number": letter.quote_number, "quote_date": letter.quote_date.isoformat(),
            "delivery_cost": num(letter.delivery_cost), "total": num(letter.total),
            "lines": [{key: value for key, value in line.items() if not key.startswith("_")} for line in letter.lines],
            "subject": subject, "body": body, "cart": cart,
        })
    return out


def invoice_requests(uow: UnitOfWork, lines: ProcurementLines, actor: Actor, rfq_id: UUID) -> dict:
    """The letters for the request's decided lines that have no invoice yet."""
    with uow:
        session = _session(uow)
        rfq = _rfq(session, rfq_id)
        requisites = str(purchasing_settings.load(session)["letter_requisites"])
        rfq_lines = {line.id: line for line in _live_lines(session, rfq.id)}
        decisions = list(session.scalars(select(DecisionRow).where(DecisionRow.rfq_line_id.in_(list(rfq_lines))))) if rfq_lines else []
        views = lines.get_many([rfq_lines[decision.rfq_line_id].procurement_line_id for decision in decisions])
        letters: dict[UUID, Letter] = {}
        waiting = 0
        for decision in decisions:
            rfq_line = rfq_lines[decision.rfq_line_id]
            view = views.get(rfq_line.procurement_line_id)
            if view is None or view.status not in ASK_STATUSES:
                continue
            quote_line = session.get(QuoteLineRow, decision.quote_line_id)
            quote = session.get(QuoteRow, quote_line.quote_id) if quote_line else None
            if quote_line is None or quote is None or quote_line.unit_price is None:
                continue
            supplier = session.get(SupplierRow, quote.supplier_id)
            latest = session.scalars(select(QuoteRow).where(QuoteRow.rfq_id == rfq.id, QuoteRow.supplier_id == quote.supplier_id)
                                     .order_by(QuoteRow.received_on.desc(), QuoteRow.created_at.desc())).first()
            letter = letters.setdefault(quote.supplier_id, Letter(
                supplier_id=quote.supplier_id, supplier_name=supplier.name if supplier else "",
                email=first_email(supplier.email) if supplier else None, quote_number=quote.number or "",
                quote_date=quote.received_on, delivery_cost=latest.delivery_cost if latest else None,
            ))
            entry = invoice_letter_line(RequestLine(
                rfq_line_id=rfq_line.id, name=rfq_line.name, article=rfq_line.article, unit=rfq_line.unit,
                quantity=Decimal(decision.cover_quantity), unit_price=Decimal(quote_line.unit_price),
                tiers=read_tiers(quote_line.tiers), pack=quote_line.pack, vat_included=quote_line.vat_included,
                delivery_point=view.delivery_point or "", legacy_id=view.legacy_id,
            ), len(letter.lines))
            letter.lines.append(entry)
            letter.total += entry["_total"]
            letter.points.append(view.delivery_point or "")
            waiting += view.status == "Готов к запросу"
        return {"requests": compose(list(letters.values()), requisites), "ready_for_request": waiting}
