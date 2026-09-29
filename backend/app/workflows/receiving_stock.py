"""Receiving's StockBalanceLookup port over the Warehouse module's own lots.

RC-6: a supplier return writes the returned quantity off only when the lot of the line's ГРН + ПАКЕТ
really lies on stock (SupplierReturn.gs warehouseGrnInfo_ looks in ЦАР and ЖУК). Receiving never reads
Warehouse's tables, so this is a read-only port, the same shape as ProcurementLineStageLookup. The balance
it reports is what a write-off may take: ERP never writes off into the reserve (WH-2).
"""

from __future__ import annotations

from collections.abc import Callable

from app.modules.receiving.application import StockLotBalance
from app.modules.warehouse.application import UnitOfWork as WarehouseUnitOfWork

WAREHOUSES = ("ЦАР", "ЖУК")


class WarehouseStockBalanceLookup:
    def __init__(self, warehouse_uow_factory: Callable[[], WarehouseUnitOfWork]):
        self.warehouse_uow_factory = warehouse_uow_factory

    def lot_balance(self, grn: str, package: str) -> StockLotBalance | None:
        with self.warehouse_uow_factory() as uow:
            for warehouse in WAREHOUSES:
                lot = uow.warehouse.by_key(warehouse, grn, package)
                if lot is not None:
                    return StockLotBalance(warehouse=lot.warehouse, balance=lot.available, unit=lot.unit)
        return None
