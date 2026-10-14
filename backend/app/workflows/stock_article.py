"""Port: one warehouse lot as the references journal keeps it («склад → база данных», T14, 13.10.2026).

The references module writes the journal of stock articles tied to or made into its records; what a lot is
(склад, ГРН, пакет, артикул, наименование) belongs to the warehouse, so it is read here and handed over as a snapshot.
Read-only: the warehouse is never changed by a link."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.modules.references.domain import StockArticle
from app.modules.warehouse.application import UnitOfWork as WarehouseUnitOfWork


def read_stock_article(warehouse_uow_factory: Callable[[], WarehouseUnitOfWork], lot_id: UUID) -> StockArticle | None:
    with warehouse_uow_factory() as uow:
        lot = uow.warehouse.get(lot_id)
    if lot is None:
        return None
    return StockArticle(lot_id=lot.id, warehouse=lot.warehouse, grn=lot.grn, package=lot.package,
                        article=lot.article, name=lot.name)
