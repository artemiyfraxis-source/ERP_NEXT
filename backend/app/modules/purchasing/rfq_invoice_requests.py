"""«Запросить счета» (port of Roller_Buyer packages/shared/src/invoice-request.ts): the lines chosen in a request for
quotes, grouped by supplier, as a letter asking each one for an invoice — quantities as they will be bought (the MOQ,
whole packs), prices of the supplier's own quote at that quantity's break in the quote's VAT mode, the totals, the
delivery, our requisites and the delivery address of the lines' «ТОЧКА ОТГРУЗКИ». The ERP sends no mail: the text is
copied or opened in the mail program.

Lines whose procurement line already has an invoice (past «Жду счет») are left out — the invoice is there.

`purchase_letters` is the «Закупка» variant (owner 23.10): the same lines, quantities and prices, but the letter states
the intent to buy by the supplier's КП (every КП the decisions were taken on) and names our order once the request is
formed."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select

from app.modules.procurement.domain import delivery_receiver
from app.shared.identity import Actor

from . import settings as purchasing_settings
from .infrastructure import DecisionRow, QuoteLineRow, QuoteRow, RfqLineRow, SupplierRow
from .offers import read_tiers
from .rfq import ProcurementLines, UnitOfWork, _live_lines, _rfq, _session, first_email, num
from .rfq_withdraw import ru_money

# Statuses where the invoice is still to be asked for: the line is on the buyer's desk or waits for the invoice.
ASK_STATUSES = ("В работе", "Готов к запросу", "Жду счет")
# «Закупка»: the letter goes for a decided line before the request is formed and while the invoice is awaited.
INTENT_STATUSES = ("Готов к запросу", "Жду счет")
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
    quotes: list[tuple[str, date]] = field(default_factory=list)  # every КП the lines were decided on («Закупка»)
    orders: list[str] = field(default_factory=list)  # our № ЗАКАЗА once the request is formed («Закупка»)


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


def quote_ref(number: str, day: date) -> tuple[str, bool]:
    """«КП № ДЛТ/1990 от 14.09.2026», or «предложение от 14.09.2026 (сайт Продиэлком)»: a number is a number when it
    has digits, «сайт Продиэлком» is a note about where the prices came from. Second — whether it is numbered."""
    numbered = bool(re.search(r"\d", number))
    note = f" ({number})" if not numbered and number else ""
    return (f"КП № {number} от {_day(day)}" if numbered else f"предложение от {_day(day)}{note}"), numbered


@dataclass(frozen=True)
class _Parts:
    """What both letters share: the numbered rows, the VAT tail of the total, delivery, contact, requisites, cart."""

    rows: list[str]
    vat_tail: str
    delivery: str
    contact: str
    buyer: list[str]
    cart: str


def _parts(letter: Letter, requisites: str) -> _Parts:
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
    cart_rows = []
    for line in letter.lines:
        amount = line["packs"] or f"{_qty(Decimal(line['qty']))} {line['unit']}"
        cart_rows.append(f"{line['article'] or line['name']}\t{amount}")
    return _Parts(
        rows=rows,
        vat_tail=" с НДС" if all_gross else " без НДС" if all_net else "",
        delivery=delivery,
        contact=f"Контактное лицо для доставки: {contact.name}, тел. {contact.phone}.",
        buyer=["", "Покупатель:", *[row for row in requisites.splitlines() if row.strip()]] if requisites.strip() else [],
        cart="\n".join(cart_rows),
    )


def _result(letter: Letter, subject: str, body: str, cart: str) -> dict:
    return {
        "supplier_id": str(letter.supplier_id), "supplier_name": letter.supplier_name, "email": letter.email,
        "quote_number": letter.quote_number, "quote_date": letter.quote_date.isoformat(),
        "delivery_cost": num(letter.delivery_cost), "total": num(letter.total),
        "lines": [{key: value for key, value in line.items() if not key.startswith("_")} for line in letter.lines],
        "subject": subject, "body": body, "cart": cart,
    }


def compose(letters: Sequence[Letter], requisites: str) -> list[dict]:
    """Subject, body and the website cart of each supplier's letter, dearest first (RB invoiceRequests)."""
    out: list[dict] = []
    for letter in sorted(letters, key=lambda item: -item.total):
        ref, numbered = quote_ref(letter.quote_number, letter.quote_date)
        parts = _parts(letter, requisites)
        subject = f"Запрос счёта — {ref}, {len(letter.lines)} поз."
        # «по вашему КП № … от …» / «по вашему предложению от … (note)» — the ref already reads that way.
        body = "\n".join([
            "Добрый день!",
            "",
            f"Просим выставить счёт на оплату по вашему {ref.replace('предложение от', 'предложению от', 1) if not numbered else ref} на следующие позиции:",
            "",
            *parts.rows,
            "",
            f"Итого по ценам предложения: {ru_money(letter.total)}{parts.vat_tail}.",
            "",
            parts.delivery,
            parts.contact,
            *parts.buyer,
            "",
            "В счёте просим указать срок поставки. Если цена, наличие или срок изменились — сообщите, пожалуйста, до выставления счёта.",
            "",
            "Заранее спасибо!",
        ])
        out.append(_result(letter, subject, body, parts.cart))
    return out


def compose_intent(letters: Sequence[Letter], requisites: str) -> list[dict]:
    """«Закупка»: the letter says we are buying by the supplier's КП and asks for the invoice; once the request is
    formed it names our order, so the invoice can carry it. Dearest first, like `compose`."""
    out: list[dict] = []
    for letter in sorted(letters, key=lambda item: -item.total):
        quotes = list(dict.fromkeys(letter.quotes or [(letter.quote_number, letter.quote_date)]))
        refs = [quote_ref(number, day)[0] for number, day in quotes]
        by_ref = f"вашему {refs[0]}" if len(refs) == 1 else f"вашим предложениям ({'; '.join(refs)})"
        by_ref = by_ref.replace("вашему предложение от", "вашему предложению от", 1)
        short = refs[0] if len(refs) == 1 else f"{refs[0]} и ещё {len(refs) - 1}"
        orders = [number for number in dict.fromkeys(letter.orders) if number]
        order_text = f"заказ № {', '.join(orders)}" if orders else ""
        parts = _parts(letter, requisites)
        subject = f"Закупка по {short.replace('предложение от', 'предложению от', 1)} — просим счёт" \
                  f"{f', {order_text}' if orders else ''}, {len(letter.lines)} поз."
        body = "\n".join([
            "Добрый день!",
            "",
            f"По {by_ref} мы приняли решение о закупке и готовы оплатить следующие позиции:",
            "",
            *parts.rows,
            "",
            f"Итого по ценам КП: {ru_money(letter.total)}{parts.vat_tail}.",
            "",
            f"Просим выставить счёт на оплату{f' на наш {order_text}' if orders else ''} со ссылкой на КП.",
            parts.delivery,
            parts.contact,
            *parts.buyer,
            "",
            "В счёте просим указать срок поставки. Если цена, наличие или срок изменились — сообщите, пожалуйста, до выставления счёта.",
            "",
            "Заранее спасибо!",
        ])
        result = _result(letter, subject, body, parts.cart)
        result["quotes"] = [{"number": number, "date": day.isoformat()} for number, day in quotes]
        result["orders"] = orders
        out.append(result)
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


def purchase_letters(uow: UnitOfWork, lines: ProcurementLines, line_ids: Iterable[UUID]) -> dict:
    """«Закупка» → «Запросить счёт»: one intent-to-buy letter per supplier for these procurement lines — those with
    an applied request-for-quotes decision, «Готов к запросу» or «Жду счет». The КП of every decision is named; the
    delivery of a single КП is quoted, several КП ask for it in the invoice. `skipped` — the lines given that have no
    such decision or are past «Жду счет»."""
    wanted = list(dict.fromkeys(line_ids))
    with uow:
        session = _session(uow)
        requisites = str(purchasing_settings.load(session)["letter_requisites"])
        rows = session.execute(
            select(DecisionRow, RfqLineRow, QuoteLineRow, QuoteRow)
            .join(RfqLineRow, RfqLineRow.id == DecisionRow.rfq_line_id)
            .join(QuoteLineRow, QuoteLineRow.id == DecisionRow.quote_line_id)
            .join(QuoteRow, QuoteRow.id == QuoteLineRow.quote_id)
            .where(
                RfqLineRow.procurement_line_id.in_(wanted), RfqLineRow.removed_at.is_(None),
                DecisionRow.applied_at.is_not(None),
            )
        ).all() if wanted else []
        views = lines.get_many([rfq_line.procurement_line_id for _, rfq_line, _, _ in rows])
        letters: dict[UUID, Letter] = {}
        used: set[UUID] = set()
        for decision, rfq_line, quote_line, quote in sorted(
            rows, key=lambda row: (views[row[1].procurement_line_id].legacy_id if row[1].procurement_line_id in views else "")
        ):
            view = views.get(rfq_line.procurement_line_id)
            if view is None or view.status not in INTENT_STATUSES or quote_line.unit_price is None:
                continue
            supplier = session.get(SupplierRow, quote.supplier_id)
            letter = letters.setdefault(quote.supplier_id, Letter(
                supplier_id=quote.supplier_id, supplier_name=supplier.name if supplier else "",
                email=first_email(supplier.email) if supplier else None, quote_number=quote.number or "",
                quote_date=quote.received_on, delivery_cost=quote.delivery_cost,
            ))
            if (quote.number or "", quote.received_on) not in letter.quotes:
                letter.quotes.append((quote.number or "", quote.received_on))
            if view.order_number:
                letter.orders.append(view.order_number)
            entry = invoice_letter_line(RequestLine(
                rfq_line_id=rfq_line.id, name=rfq_line.name, article=rfq_line.article, unit=rfq_line.unit,
                quantity=Decimal(decision.cover_quantity), unit_price=Decimal(quote_line.unit_price),
                tiers=read_tiers(quote_line.tiers), pack=quote_line.pack, vat_included=quote_line.vat_included,
                delivery_point=view.delivery_point or "", legacy_id=view.legacy_id,
            ), len(letter.lines))
            entry["line_id"] = str(view.id)
            letter.lines.append(entry)
            letter.total += entry["_total"]
            letter.points.append(view.delivery_point or "")
            used.add(view.id)
        for letter in letters.values():
            if len(letter.quotes) > 1:
                letter.delivery_cost = None  # several КП, several deliveries: the supplier puts the one he ships in
        return {
            "letters": compose_intent(list(letters.values()), requisites),
            "skipped": [str(line_id) for line_id in wanted if line_id not in used],
        }
