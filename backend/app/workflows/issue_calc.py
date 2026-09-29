"""«Заявка по расчёту» — SUPPLYMAP IssueCalc.html, Issue.gs `issueCalcPreview` / `issueCalcMatch_` / `issueStock_`.

The calculation is a Google Sheets link: its ЗКП sheet is read by the Requests module (`extract_from_calc`, the same
reader «Из расчёта» uses); every position is matched against the chosen warehouse by `isSameItem_` and picked FIFO up
to its quantity. The request itself is created by the ordinary fulfillment path (picked lots + shortNote).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.fulfillment.application import UnitOfWork as FulfillmentUnitOfWork
from app.modules.requests.application import CalcSpreadsheetReader, extract_from_calc
from app.modules.warehouse.domain import WAREHOUSES, StockLot


class IssueCalcError(ValueError):
    """The preview cannot be built; the message is shown as is."""


def _norm_alnum(value: object) -> str:
    """Schema.gs normAlnum_."""
    return re.sub(r"[^0-9a-zа-яё]+", "", str(value or "").lower())


def is_same_item(article: object, site_name: object) -> bool:
    """Schema.gs isSameItem_: every token (≥ 2 chars) of the calculation name occurs in the stock name. An empty stock
    name or a name without tokens counts as the same item — SUPPLYMAP's rule, kept as is."""
    site = _norm_alnum(site_name)
    if not site:
        return True
    tokens = [token for token in re.split(r"[^0-9a-zа-яё]+", str(article or "").lower()) if len(token) >= 2]
    return all(token in site for token in tokens)


def calc_number(value: object) -> Decimal:
    """icrNum_: «12,5» → 12.5; empty or garbage → 0."""
    text = re.sub(r"\s+", "", str(value or "")).replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return Decimal("0")
    return number if number.is_finite() else Decimal("0")


def fifo_key(lot: StockLot) -> tuple[int, int, str, str]:
    """The FIFO order of lots. ERP keeps no receipt date on a lot; the ГРН is issued in order («FRX.26.0000012»: year,
    then number), so it stands in for the order of SUPPLYMAP sheet rows (receipts are appended at the bottom)."""
    match = re.search(r"(\d{2})\.(\d+)$", lot.grn)
    year, number = (int(match.group(1)), int(match.group(2))) if match else (10**9, 0)
    return year, number, lot.grn, lot.package


def stock_name(lot: StockLot) -> str:
    """issueStock_: the stock row is known by its АРТИКУЛ, by НАИМЕНОВАНИЕ where the article is empty."""
    return lot.article or lot.name


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def fifo_pick(rows: Sequence[StockLot], need: Decimal) -> tuple[list[tuple[StockLot, Decimal]], Decimal]:
    """Issue.gs fifoPick_: from the first delivery to the last until the nominal quantity is reached."""
    rest = need
    picks: list[tuple[StockLot, Decimal]] = []
    if need <= 0:
        return picks, Decimal("0")
    for lot in rows:
        if rest <= Decimal("0.0001"):
            break
        if lot.available <= 0:
            continue
        take = min(lot.available, rest)
        picks.append((lot, _round2(take)))
        rest = _round2(rest - take)
    return picks, max(rest, Decimal("0"))


@dataclass(frozen=True)
class CalcPosition:
    name: str
    quantity: Decimal
    unit: str


def calc_match(items: Iterable[CalcPosition], stock: Sequence[StockLot]) -> list[dict[str, Any]]:
    """Issue.gs issueCalcMatch_ → [{name, qty, unit, picks, short, found}] (found=false — nothing on the warehouse)."""
    ordered = sorted(stock, key=fifo_key)
    result = []
    for item in items:
        rows = [lot for lot in ordered if is_same_item(item.name, stock_name(lot))]
        picks, short = fifo_pick(rows, item.quantity)
        result.append({
            "name": item.name,
            "qty": _text(item.quantity),
            "unit": item.unit,
            "found": bool(rows),
            "short": _text(short if rows else item.quantity),
            "picks": [{
                "lot_id": str(lot.id),
                "warehouse": lot.warehouse,
                "grn": lot.grn,
                "package": lot.package,
                "article": lot.article,
                "name": lot.name,
                "unit": lot.unit,
                "storage_location": lot.storage_location,
                "qty": _text(qty),
            } for lot, qty in picks],
        })
    return result


def issue_calc_preview(
    url: str,
    warehouse: str,
    reader: CalcSpreadsheetReader,
    fulfillment_uow: Callable[[], FulfillmentUnitOfWork],
) -> dict[str, Any]:
    """Issue.gs issueCalcPreview: the calculation by link × the warehouse snapshot → {project, lines}."""
    if warehouse not in WAREHOUSES:
        raise IssueCalcError(f"Неизвестный склад: {warehouse}")
    calc = extract_from_calc(url, reader)
    if not calc.items:
        raise IssueCalcError("В расчёте не найдено позиций.")
    with fulfillment_uow() as uow:
        stock = [
            lot for lot in uow.warehouse.list(limit=5000, warehouse=warehouse, include_zero=False)
            if lot.available > 0
        ]
    positions = [CalcPosition(item.name, calc_number(item.quantity), item.unit) for item in calc.items]
    return {"project": calc.project, "lines": calc_match(positions, stock)}


def calc_reader(state: Any) -> CalcSpreadsheetReader:
    """Tests put a fake on ``app.state.calc_spreadsheet_reader`` (the one «Из расчёта» uses); otherwise the
    service-account adapter from env. Raises `CalcSheetError` (kind ``config``) without a key."""
    reader = getattr(state, "calc_spreadsheet_reader", None)
    if reader is not None:
        return reader
    from app.modules.requests.google_sheets import reader_from_env

    return reader_from_env()
