"""the buyer's indicators over a period, computed on read.

Pure functions over line objects; money stays Decimal and is rounded only in the output.
Population: lines paid in the period (`paid_at` in Moscow time). Dropped metric: «взято → счёт» — a line stores no
«taken» timestamp (only `created_at` of the request line), so the duration cannot be measured honestly.
«В срок» is measured against `expected_arrival`, which the buyer may move until the goods are in (no date fixed at
payment is stored), hence the label «к ожидаемой дате». «Получено» = `actual_arrival` is set (latest inspection)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.shared.clock import MOSCOW, as_utc

from .domain import ProcurementLine

TOTAL = "Итого"
NO_BUYER = "Без закупщика"


def paid_day(line: ProcurementLine) -> date | None:
    if line.paid_at is None:
        return None
    return as_utc(line.paid_at).astimezone(MOSCOW).date()


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _average(values: list[int]) -> dict:
    if not values:
        return {"days": None, "n": 0}
    mean = (Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return {"days": str(mean), "n": len(values)}


def _saving(line: ProcurementLine) -> Decimal | None:
    """(calculated gross unit price − actual gross unit price) × actual quantity; None when not comparable."""
    calculated = line.calculated_price_with_vat
    actual = line.money.price_with_vat
    if calculated is None or actual is None or line.actual_quantity is None:
        return None
    if not line.actual_unit or line.actual_unit != line.calculated_unit:
        return None
    return (calculated - actual) * line.actual_quantity


def _row(buyer: str, lines: list[ProcurementLine]) -> dict:
    paid_sum = sum((line.money.sum for line in lines if line.money.sum is not None), Decimal("0"))
    received = [line for line in lines if line.actual_arrival is not None]
    timed = [line for line in received if line.expected_arrival is not None]
    on_time = sum(1 for line in timed if line.actual_arrival <= line.expected_arrival)
    invoice_to_paid = [
        (paid_day(line) - line.invoice_date).days for line in lines if line.invoice_date is not None
    ]
    paid_to_received = [(line.actual_arrival - paid_day(line)).days for line in received]
    savings = [(line, _saving(line)) for line in lines]
    comparable = [value for _, value in savings if value is not None]
    return {
        "buyer": buyer,
        "lines": len(lines),
        # СУММА (факт): unit price with VAT × quantity, summed over the lines that have it.
        "paid_sum": _money(paid_sum),
        "received": len(received),
        "on_time": {
            "n": len(timed),
            "ok": on_time,
            "percent": str((Decimal(on_time) * 100 / Decimal(len(timed))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
            if timed
            else None,
        },
        "invoice_to_paid": _average(invoice_to_paid),
        "paid_to_received": _average(paid_to_received),
        "savings": {
            "sum": _money(sum(comparable, Decimal("0"))),
            "n": len(comparable),
            "excluded": len(lines) - len(comparable),
        },
    }


def build_kpi(lines: Iterable[ProcurementLine], start: date, end: date, buyer: str | None = None) -> dict:
    """Rows per buyer (by name) plus the total row, for lines paid within [start, end]."""
    groups: dict[str, list[ProcurementLine]] = defaultdict(list)
    for line in lines:
        day = paid_day(line)
        if day is None or not (start <= day <= end):
            continue
        name = line.buyer.name if line.buyer else NO_BUYER
        if buyer and name != buyer:
            continue
        groups[name].append(line)
    rows = [_row(name, groups[name]) for name in sorted(groups, key=str.casefold)]
    everyone = [line for name in groups for line in groups[name]]
    return {"from": start.isoformat(), "to": end.isoformat(), "rows": rows, "total": _row(TOTAL, everyone)}
