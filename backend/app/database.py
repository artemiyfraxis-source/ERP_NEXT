"""Shared database wiring; domain modules keep ownership of their tables."""

from collections.abc import Callable
import os

from fastapi import FastAPI
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .modules.requests.application import UnitOfWork
from .modules.requests.infrastructure import SqlAlchemyUnitOfWork
from .modules.procurement.application import (
    StockCoverUnitOfWork,
    UnitOfWork as ProcurementUnitOfWork,
)
from .modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from .modules.identity.application import UnitOfWork as IdentityUnitOfWork
from .modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork
from .modules.receiving.application import UnitOfWork as ReceivingUnitOfWork
from .modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from .modules.warehouse.application import UnitOfWork as WarehouseUnitOfWork
from .modules.warehouse.infrastructure import SqlAlchemyRackLayoutStore, SqlAlchemyWarehouseUnitOfWork
from .modules.fulfillment.application import UnitOfWork as FulfillmentUnitOfWork
from .modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from .modules.production.application import UnitOfWork as ProductionUnitOfWork
from .modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from .modules.shipping.application import UnitOfWork as ShippingUnitOfWork
from .modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from .modules.references.application import UnitOfWork as ReferencesUnitOfWork
from .modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from .modules.operations.application import UnitOfWork as OperationsUnitOfWork
from .modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from .modules.techcards.application import UnitOfWork as TechcardsUnitOfWork
from .modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from .modules.purchasing.infrastructure import SqlAlchemyPurchasingUnitOfWork
from .workflows import etk_refresh  # noqa: F401 — registers the refresh-request trigger on every session
from .workflows.kitset_names import KitsetNameQueue
from .workflows.new_operation import SqlAlchemyReferencesOperationsUnitOfWork
from .workflows.project_registry import InternalAwareProjectRegistry, PlanSnapshotProjectRegistry
from .workflows.receiving_stock import WarehouseStockBalanceLookup
from .workflows.procurement_stage import ProcurementLineStageLookup
from .workflows.procurement_stock import SqlAlchemyProcurementStockUnitOfWork
from .workflows.request_purpose import RequestPurposeLookup
from .workflows.supplier_directory import ReferencesSupplierDirectory
from .shared.warehouse_location import set_rack_layout_loader


# Connections per process: pool + overflow. Production runs ERP_API_WORKERS API processes, each with its own pool, so
# PostgreSQL's max_connections must cover workers × (pool + overflow) + the outbox worker + the CLIs
# (deploy/docker-compose.yml; tests/test_deploy_capacity.py holds the sum). Unset → SQLAlchemy's 5 + 10.
POOL_ENV = {"ERP_DB_POOL_SIZE": "pool_size", "ERP_DB_MAX_OVERFLOW": "max_overflow"}


def pool_options() -> dict[str, int]:
    options: dict[str, int] = {}
    for env, option in POOL_ENV.items():
        raw = os.getenv(env, "").strip()
        if not raw:
            continue
        if not raw.isdigit() or (option == "pool_size" and int(raw) < 1):
            raise RuntimeError(f"{env}={raw!r}: нужно целое число (размер пула соединений с базой)")
        options[option] = int(raw)
    return options


def build_engine(database_url: str) -> Engine:
    options: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    else:
        options.update(pool_options())
    return create_engine(database_url, **options)


def build_uow_factory(engine: Engine) -> Callable[[], UnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyUnitOfWork(sessions)


def build_procurement_uow_factory(
    engine: Engine,
) -> Callable[[], ProcurementUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyProcurementUnitOfWork(sessions)


def build_procurement_stock_uow_factory(
    engine: Engine,
) -> Callable[[], StockCoverUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyProcurementStockUnitOfWork(sessions)


def build_identity_uow_factory(engine: Engine) -> Callable[[], IdentityUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyIdentityUnitOfWork(sessions)


def build_receiving_uow_factory(engine: Engine) -> Callable[[], ReceivingUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyReceivingUnitOfWork(sessions)


def build_warehouse_uow_factory(engine: Engine) -> Callable[[], WarehouseUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyWarehouseUnitOfWork(sessions)


def build_fulfillment_uow_factory(engine: Engine) -> Callable[[], FulfillmentUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)


def build_production_uow_factory(engine: Engine) -> Callable[[], ProductionUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyProductionUnitOfWork(sessions)


def build_shipping_uow_factory(engine: Engine) -> Callable[[], ShippingUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyShippingUnitOfWork(sessions)


def build_references_uow_factory(engine: Engine) -> Callable[[], ReferencesUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    # The references UoW that also sees operation cards (БД.ТК): renaming «Номер» can't orphan a card (owner 14.10).
    return lambda: SqlAlchemyReferencesOperationsUnitOfWork(sessions)


def build_operations_uow_factory(engine: Engine) -> Callable[[], OperationsUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyOperationsUnitOfWork(sessions)


def build_techcards_uow_factory(engine: Engine) -> Callable[[], TechcardsUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyTechcardsUnitOfWork(sessions)


def build_purchasing_uow_factory(engine: Engine) -> Callable[[], SqlAlchemyPurchasingUnitOfWork]:
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return lambda: SqlAlchemyPurchasingUnitOfWork(sessions)


def configure_database(app: FastAPI, database_url: str) -> Engine:
    engine = build_engine(database_url)
    app.state.database_engine = engine
    app.state.uow_factory = build_uow_factory(engine)
    app.state.procurement_uow_factory = build_procurement_uow_factory(engine)
    app.state.procurement_stock_uow_factory = build_procurement_stock_uow_factory(engine)
    app.state.identity_uow_factory = build_identity_uow_factory(engine)
    app.state.receiving_uow_factory = build_receiving_uow_factory(engine)
    app.state.warehouse_uow_factory = build_warehouse_uow_factory(engine)
    app.state.fulfillment_uow_factory = build_fulfillment_uow_factory(engine)
    app.state.production_uow_factory = build_production_uow_factory(engine)
    app.state.shipping_uow_factory = build_shipping_uow_factory(engine)
    app.state.references_uow_factory = build_references_uow_factory(engine)
    app.state.operations_uow_factory = build_operations_uow_factory(engine)
    app.state.techcards_uow_factory = build_techcards_uow_factory(engine)
    app.state.purchasing_uow_factory = build_purchasing_uow_factory(engine)
    app.state.project_registry = InternalAwareProjectRegistry(
        PlanSnapshotProjectRegistry(app.state.production_uow_factory), app.state.purchasing_uow_factory)
    app.state.procurement_stage_lookup = ProcurementLineStageLookup(app.state.procurement_uow_factory)
    app.state.request_purpose_lookup = RequestPurposeLookup(app.state.uow_factory)
    app.state.procurement_supplier_directory = ReferencesSupplierDirectory(app.state.references_uow_factory)
    app.state.receiving_stock_lookup = WarehouseStockBalanceLookup(app.state.warehouse_uow_factory)
    app.state.kitset_name_queue = KitsetNameQueue(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))
    app.state.rack_layout_store = SqlAlchemyRackLayoutStore(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))
    set_rack_layout_loader(app.state.rack_layout_store.load)
    return engine
