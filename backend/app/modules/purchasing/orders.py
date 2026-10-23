"""«Счета и поставки» (ported from Roller_Buyer OrdersPage / OrderBoard / orders.ts).

A card is an ERP supplier order: the procurement lines sharing one № ЗАКАЗА (they arrive here as plain facts from
app/workflows/purchasing_orders.py). Its column is computed on read from the stage of its EARLIEST line and the order's
own facts (purchasing_order_facts):

  «Ждём счёт» (a line «Жду счет») → «Не оплачен» («В оплату», not handed to the accountant) → «В оплате» («В оплату»
  and sent to pay) → «Ждём отгрузку» («В пути», the supplier has not shipped) → «В доставке» («В пути» and shipped, or
  part of the order already received) → «Приёмка» («Карантин») → «На складе» (every line «Готово»).

`-Д` backorder lines are not a stage (they repeat a paid line's remainder: same invoice, same money) — the card shows
them apart; cancelled lines are not read at all. Only the facts procurement has no place for are written here, by the
order's own buyer, with an optimistic version; every change is audited."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select

from app.modules.procurement.domain import is_kaiten_invoice_url
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.workflows.purchasing_orders import OrderLineFacts

from .infrastructure import ORDER_NOTE_MAX, SHIPMENT_NOTE_MAX, OrderFactRow

AWAITING, TO_PAY, IN_TRANSIT, QUARANTINE, DONE = "Жду счет", "В оплату", "В пути", "Карантин", "Готово"
_RANK = {AWAITING: 0, TO_PAY: 1, IN_TRANSIT: 2, QUARANTINE: 3, DONE: 4}

# Board columns left to right (keys are the contract's `&filter=` words where they coincide).
COLUMNS = (
    ("awaiting", "Ждём счёт"),
    ("unpaid", "Не оплачен"),
    ("to_pay", "В оплате"),
    ("waiting", "Ждём отгрузку"),
    ("transit", "В доставке"),
    ("receiving", "Приёмка"),
    ("stocked", "На складе"),
)
COLUMN_KEYS = tuple(key for key, _ in COLUMNS)
_NAMESPACE = UUID("6f1d3c1e-9a52-4f4b-9e63-0c2d5b0e2a17")
FACTS_EVENT = "purchasing.order_facts_saved"
FACT_LABELS = {
    "sent_to_pay_on": "Передан в оплату",
    "shipped_on": "Отгрузка",
    "shipment_note": "Транспорт, трек-номер",
    "delivery_cost": "Доставка",
    "note": "Заметка",
}


class OrderError(Exception):
    def __init__(self, message: str, status: int = 422, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.field = field


def order_aggregate_id(order_number: str) -> UUID:
    """A stable audit aggregate for an order (the order has no id of its own, only its №)."""
    return uuid5(_NAMESPACE, f"purchasing.order:{order_number}")


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _num(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def column_of(lines: Sequence[OrderLineFacts], facts: OrderFactRow | None) -> str | None:
    """The board column of an order (module doc); None — the order has no stage line (only backorders)."""
    staged = [line for line in lines if not line.is_backorder and line.status in _RANK]
    if not staged:
        return None
    earliest = min(_RANK[line.status] for line in staged)
    if earliest == 0:
        return "awaiting"
    if earliest == 1:
        return "to_pay" if facts is not None and facts.sent_to_pay_on else "unpaid"
    if earliest == 2:
        received = any(_RANK[line.status] > 2 or line.receipt.accepted > 0 for line in staged)
        return "transit" if received or (facts is not None and facts.shipped_on) else "waiting"
    if earliest == 3:
        return "receiving"
    return "stocked"


def _sum(lines: Iterable[OrderLineFacts]) -> tuple[Decimal, bool]:
    """Σ СУММА (факт), a line without it counts with its calculated sum; True when any line is only calculated."""
    total, estimated = Decimal("0"), False
    for line in lines:
        if line.actual_sum is not None:
            total += line.actual_sum
        elif line.calculated_sum is not None:
            total += line.calculated_sum
            estimated = True
        else:
            estimated = True
    return total, estimated


def _late_days(lines: Iterable[OrderLineFacts], today: date) -> int:
    """Most days overdue among the lines not yet with receiving's result (RB `lateDays`: expected date passed)."""
    days = [
        (today - line.expected_arrival).days for line in lines
        if line.status in (AWAITING, TO_PAY, IN_TRANSIT) and line.expected_arrival and line.expected_arrival < today
    ]
    return max(days, default=0)


def line_json(line: OrderLineFacts) -> dict:
    return {
        "id": str(line.id), "legacy_id": line.legacy_id, "version": line.version, "status": line.status,
        "name": line.name, "calculated_name": line.calculated_name, "invoice_item_name": line.invoice_item_name,
        "article": line.article, "project": line.project, "quantity": _num(line.quantity), "unit": line.unit,
        "price_with_vat": _num(line.price_with_vat), "actual_sum": _num(line.actual_sum),
        "calculated_sum": _num(line.calculated_sum), "currency": line.currency, "source_currency": line.source_currency,
        "source_sum": _num(line.source_sum), "expected_arrival": _iso(line.expected_arrival),
        "promised": _iso(line.promised), "invoice_number": line.invoice_number, "invoice_date": _iso(line.invoice_date),
        "invoice_url": line.invoice_url, "paid_on": _iso(line.paid_on), "actual_arrival": _iso(line.actual_arrival),
        "note": line.note, "is_backorder": line.is_backorder, "pay_missing": list(line.pay_missing),
        "accepted_quantity": _num(line.receipt.accepted) if line.receipt.rows else None,
        "counted_quantity": _num(line.receipt.counted) if line.receipt.rows else None,
        "inspected_on": _iso(line.receipt.inspected_on),
    }


def facts_json(facts: OrderFactRow | None) -> dict:
    return {
        "sent_to_pay_on": _iso(facts.sent_to_pay_on) if facts else None,
        "shipped_on": _iso(facts.shipped_on) if facts else None,
        "shipment_note": facts.shipment_note if facts else "",
        "delivery_cost": _num(facts.delivery_cost) if facts else None,
        "note": facts.note if facts else "",
        "facts_version": facts.version if facts else 0,
        "facts_updated_by": facts.updated_by_name if facts else None,
        "facts_updated_at": _iso(facts.updated_at) if facts else None,
    }


def order_json(number: str, lines: Sequence[OrderLineFacts], facts: OrderFactRow | None, today: date,
               viewer: UUID | None, detail: bool = True) -> dict | None:
    """The card of one order; None when it has no stage line (only `-Д` backorders left). Without `detail` (the board,
    the table) the lines are left out — the card reads them — and a backorder is only its № and stage."""
    column = column_of(lines, facts)
    if column is None:
        return None
    staged = sorted((line for line in lines if not line.is_backorder), key=lambda line: line.legacy_id)
    backorders = sorted((line for line in lines if line.is_backorder), key=lambda line: line.legacy_id)
    total, estimated = _sum(staged)
    foreign: dict[str, Decimal] = {}
    for line in staged:
        if line.source_currency and line.source_sum is not None:
            foreign[line.source_currency] = foreign.get(line.source_currency, Decimal("0")) + line.source_sum
    invoices: dict[tuple, dict] = {}
    for line in staged:
        if line.invoice_url or line.invoice_number:
            key = (line.invoice_url or "", line.invoice_number or "")
            invoices.setdefault(key, {
                "number": line.invoice_number, "date": _iso(line.invoice_date), "url": line.invoice_url,
                # The Roller_Buyer import's stand-in for the file: a Kaiten invoice of «Счета» (the link opens its PDF
                # once the registry has it, else «файл не приложен»).
                "kaiten": is_kaiten_invoice_url(line.invoice_url),
            })
    open_lines = [line for line in staged if line.status in (AWAITING, TO_PAY, IN_TRANSIT, QUARANTINE)]
    expected = sorted({line.expected_arrival for line in open_lines if line.expected_arrival})
    promised = [line.promised for line in staged if line.promised]
    paid = [line.paid_on for line in staged if line.paid_on]
    received = [line.actual_arrival or line.receipt.inspected_on for line in staged if line.status == DONE]
    buyer = next((line for line in staged if line.buyer_name), staged[0])
    supplier = next((line.supplier for line in staged if line.supplier), "")
    stages = Counter(line.status for line in staged)
    paid_count = sum(1 for line in staged if _RANK[line.status] >= 2)
    received_count = sum(1 for line in staged if line.status == DONE or line.receipt.accepted > 0)
    return {
        "order_number": number,
        "supplier": supplier,
        "buyer": buyer.buyer_name,
        "mine": viewer is not None and all(line.buyer_id == viewer for line in staged),
        "order_date": _iso(min((line.order_date for line in staged if line.order_date), default=None)),
        "column": column,
        "stages": dict(stages),
        "lines_count": len(staged),
        "paid_count": paid_count,
        "received_count": received_count,
        "invoiced_count": sum(1 for line in staged if _RANK[line.status] >= 1),
        "quarantine_count": stages.get(QUARANTINE, 0),
        "invoices": list(invoices.values()),
        "invoice_date": _iso(min((line.invoice_date for line in staged if line.invoice_date), default=None)),
        "expected": _iso(expected[0]) if expected else None,
        "expected_last": _iso(expected[-1]) if expected else None,
        "promised": _iso(max(promised)) if promised else None,
        "paid_on": _iso(max(paid)) if paid else None,
        "stocked_on": _iso(max(day for day in received if day)) if any(received) else None,
        "late_days": _late_days(staged, today),
        "sum": _num(total),
        "sum_estimated": estimated,
        "foreign": [{"currency": currency, "sum": _num(value)} for currency, value in sorted(foreign.items())],
        "projects": sorted({line.project for line in staged if line.project}),
        **({"lines": [line_json(line) for line in staged]} if detail else {}),
        "backorders": [line_json(line) if detail else {"legacy_id": line.legacy_id, "status": line.status} for line in backorders],
        **facts_json(facts),
    }


def load_facts(session, numbers: Iterable[str]) -> dict[str, OrderFactRow]:
    wanted = sorted(set(numbers))
    out: dict[str, OrderFactRow] = {}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start:start + 500]
        out.update({row.order_number: row for row in session.scalars(
            select(OrderFactRow).where(OrderFactRow.order_number.in_(chunk))
        )})
    return out


def build_orders(lines: Iterable[OrderLineFacts], facts: Mapping[str, OrderFactRow], today: date,
                 viewer: UUID | None) -> list[dict]:
    grouped: dict[str, list[OrderLineFacts]] = {}
    for line in lines:
        grouped.setdefault(line.order_number, []).append(line)
    orders = [order_json(number, group, facts.get(number), today, viewer, detail=False) for number, group in grouped.items()]
    return sorted(
        (order for order in orders if order is not None),
        key=lambda order: (order["order_date"] or "", order["order_number"]), reverse=True,
    )


# ---- writes --------------------------------------------------------------------------------------------------------

_UNSET = object()


@dataclass(frozen=True)
class FactsChange:
    """A PATCH of the order's facts: only the fields given change; None clears a date or the delivery cost."""

    version: int
    sent_to_pay_on: object = _UNSET
    shipped_on: object = _UNSET
    shipment_note: object = _UNSET
    delivery_cost: object = _UNSET
    note: object = _UNSET

    def given(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in FACT_LABELS if getattr(self, name) is not _UNSET}


def own_order_lines(lines: Sequence[OrderLineFacts], number: str, actor: Actor) -> list[OrderLineFacts]:
    """The order's stage lines, the actor's own; 404 without lines, 403 when they are another buyer's."""
    staged = [line for line in lines if not line.is_backorder]
    if not staged:
        raise OrderError(f"Заказ {number} не найден", 404)
    if any(line.buyer_id != actor.id for line in staged):
        raise OrderError(f"Заказ {number} — другого закупщика: его факты меняет только он", 403)
    return staged


def _check_date(label: str, value: date | None, lines: Sequence[OrderLineFacts], today: date, field: str) -> None:
    """Roller_Buyer: paperwork cannot happen before the invoice or in the future."""
    if value is None:
        return
    if value > today:
        raise OrderError(f"{label} не может быть в будущем", field=field)
    floor = min((line.invoice_date for line in lines if line.invoice_date), default=None)
    floor = floor or min((line.order_date for line in lines if line.order_date), default=None)
    if floor is not None and value < floor:
        raise OrderError(f"{label} не может быть раньше даты счёта ({floor:%d.%m.%Y})", field=field)


def _shown(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _num(value) or ""
    return str(value)


def save_facts(uow, number: str, lines: Sequence[OrderLineFacts], change: FactsChange, actor: Actor,
               now: datetime) -> OrderFactRow | None:
    """Write the given facts of an order (all or nothing). Version 0 = the order has no facts yet."""
    staged = own_order_lines(lines, number, actor)
    today = now.date()
    given = change.given()
    invoiced = any(_RANK.get(line.status, 0) >= 1 for line in staged)
    if not invoiced and any(given.get(name) for name in ("sent_to_pay_on", "shipped_on")):
        raise OrderError(f"У заказа {number} ещё нет счёта — передавать в оплату и отгружать нечего")
    if "sent_to_pay_on" in given:
        _check_date("Передача в оплату", given["sent_to_pay_on"], staged, today, "sent_to_pay_on")
    if "shipped_on" in given:
        _check_date("Отгрузка", given["shipped_on"], staged, today, "shipped_on")
    if "shipment_note" in given:
        text = " ".join(str(given["shipment_note"] or "").split())
        if len(text) > SHIPMENT_NOTE_MAX:
            raise OrderError(f"Транспорт и трек-номер — не длиннее {SHIPMENT_NOTE_MAX} знаков", field="shipment_note")
        given["shipment_note"] = text
    if "note" in given:
        text = str(given["note"] or "").strip()
        if len(text) > ORDER_NOTE_MAX:
            raise OrderError(f"Заметка — не длиннее {ORDER_NOTE_MAX} знаков", field="note")
        given["note"] = text
    if "delivery_cost" in given and given["delivery_cost"] is not None:
        cost = Decimal(str(given["delivery_cost"]))
        if cost < 0:
            raise OrderError("Доставка не может быть меньше нуля", field="delivery_cost")
        given["delivery_cost"] = cost.quantize(Decimal("0.01"))
    with uow:
        session = uow.session
        row = session.get(OrderFactRow, number)
        current = row.version if row is not None else 0
        if change.version != current:
            raise OrderError("Заказ изменили — обновите и повторите", 409)
        before = {name: getattr(row, name) if row is not None else None for name in FACT_LABELS}
        changed = {name: value for name, value in given.items() if (before[name] or None) != (value or None)}
        if not changed:
            return row
        if row is None:
            row = OrderFactRow(
                order_number=number, shipment_note="", note="", created_at=now, created_by=actor.id,
                updated_at=now, updated_by=actor.id, updated_by_name=actor.name, version=1,
            )
            for name, value in changed.items():
                setattr(row, name, value)
            session.add(row)
        else:
            for name, value in changed.items():
                setattr(row, name, value)
            row.updated_at, row.updated_by, row.updated_by_name = now, actor.id, actor.name
            row.version = current + 1
        uow.audit.add(AuditEvent(
            id=uuid4(),
            aggregate_id=order_aggregate_id(number),
            event_type=FACTS_EVENT,
            actor=actor,
            occurred_at=now,
            details={
                "order_number": number,
                "fields": ", ".join(FACT_LABELS[name] for name in changed),
                **{name: _shown(value) for name, value in changed.items()},
                **{f"previous_{name}": _shown(before[name]) for name in changed},
            },
        ))
        uow.commit()
        return row
