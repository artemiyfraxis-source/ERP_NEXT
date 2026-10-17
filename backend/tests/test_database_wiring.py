"""Production database wiring for cross-module workflow adapters."""

from types import SimpleNamespace

from app.database import configure_database
from app.shared.warehouse_location import set_rack_layout_loader
from app.workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from app.workflows.request_purpose import RequestPurposeLookup


def test_configure_database_wires_procurement_adapters(tmp_path):
    holder = SimpleNamespace(state=SimpleNamespace())
    engine = configure_database(holder, f"sqlite+pysqlite:///{tmp_path / 'erp.db'}")
    try:
        assert isinstance(holder.state.request_purpose_lookup, RequestPurposeLookup)
        assert isinstance(
            holder.state.procurement_stock_uow_factory(),
            SqlAlchemyProcurementStockUnitOfWork,
        )
    finally:
        set_rack_layout_loader(None)
        engine.dispose()
