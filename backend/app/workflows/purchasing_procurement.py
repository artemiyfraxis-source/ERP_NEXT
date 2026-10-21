"""Port of the purchasing hybrid to procurement: reads the supplier facts of procurement lines.

purchasing/ never imports the procurement module; this workflow is the only place the two meet (read-only)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date, datetime

from sqlalchemy import select

from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.purchasing.suppliers import LineFacts


def _day(value) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def supplier_lines(procurement_uow_factory: Callable) -> list[LineFacts]:
    """Every procurement line that has a supplier text, as plain facts. Only the columns used are selected: hydrating
    whole 80-column rows made the directory and the card cost ~0.45 s on 5 000 lines."""
    R = ProcurementLineRow
    columns = (
        R.id, R.supplier, R.status, R.invoice_item_name, R.calculated_name, R.design_article, R.actual_quantity,
        R.calculated_quantity, R.actual_unit, R.calculated_unit, R.actual_price_with_vat, R.actual_sum, R.order_number,
        R.order_date, R.paid_at, R.expected_arrival, R.actual_arrival,
    )
    with procurement_uow_factory() as uow:
        rows = uow.session.execute(select(*columns).where(R.supplier.is_not(None))).all()
        return [
            LineFacts(
                id=str(row.id), supplier=row.supplier or "", status=row.status,
                name=row.invoice_item_name or row.calculated_name, article=row.design_article,
                quantity=row.actual_quantity if row.actual_quantity is not None else row.calculated_quantity,
                unit=row.actual_unit or row.calculated_unit,
                unit_price_with_vat=row.actual_price_with_vat, actual_sum=row.actual_sum,
                order_number=row.order_number, order_date=_day(row.order_date), paid_at=_day(row.paid_at),
                expected_arrival=_day(row.expected_arrival), actual_arrival=_day(row.actual_arrival),
            )
            for row in rows
            if (row.supplier or "").strip()
        ]


def supplier_texts(lines: list[LineFacts]) -> dict[str, int]:
    return dict(Counter(" ".join(line.supplier.split()) for line in lines))


def domain_map(directory) -> dict[str, str]:
    """«домен → поставщик» of the References module; empty when the directory is not wired."""
    return dict(directory.supplier_map()) if directory is not None else {}


