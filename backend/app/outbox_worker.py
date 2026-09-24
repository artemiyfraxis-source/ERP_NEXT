"""Small polling worker that dispatches integration events from the ERP outbox."""

from __future__ import annotations

import argparse
import logging
import os
import time

from sqlalchemy.orm import Session, sessionmaker

from app.database import build_engine
from app.modules.procurement.application import (
    PoisonOutboxMessage,
    dispatch_next_procurement_requested,
)
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.receiving.application import (
    PoisonReceivingMessage,
    dispatch_next_receiving_expected,
)
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.modules.warehouse.application import PoisonWarehouseMessage, dispatch_next_warehouse_received
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.modules.operations.application import PoisonNotificationMessage, dispatch_next_notification
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.shared.clock import moscow_now


def dispatch_available(
    sessions: sessionmaker[Session],
    *,
    limit: int = 100,
) -> int:
    if limit < 1:
        raise ValueError("limit must be positive")
    dispatched = 0
    while dispatched < limit:
        progressed = False
        try:
            line = dispatch_next_procurement_requested(
                moscow_now(),
                SqlAlchemyProcurementUnitOfWork(sessions),
            )
        except PoisonOutboxMessage as error:
            logging.warning("Quarantined invalid outbox message %s", error.message_id)
            dispatched += 1
            continue
        if line is None:
            pass
        else:
            dispatched += 1
            progressed = True
        if dispatched >= limit:
            break
        try:
            receiving = dispatch_next_receiving_expected(
                moscow_now(),
                SqlAlchemyReceivingUnitOfWork(sessions),
            )
        except PoisonReceivingMessage as error:
            logging.warning("Quarantined invalid receiving message %s", error.message_id)
            dispatched += 1
            continue
        if receiving is not None:
            dispatched += 1
            progressed = True
        if dispatched >= limit:
            break
        try:
            warehouse = dispatch_next_warehouse_received(
                moscow_now(),
                SqlAlchemyWarehouseUnitOfWork(sessions),
            )
        except PoisonWarehouseMessage as error:
            logging.warning("Quarantined invalid warehouse message %s", error.message_id)
            dispatched += 1
            continue
        if warehouse is not None:
            dispatched += 1
            progressed = True
        if dispatched >= limit:
            break
        try:
            notifications = dispatch_next_notification(
                moscow_now(),
                SqlAlchemyOperationsUnitOfWork(sessions),
            )
        except PoisonNotificationMessage as error:
            logging.warning("Quarantined invalid notification message %s", error.message_id)
            dispatched += 1
            continue
        if notifications is not None:
            dispatched += 1
            progressed = True
        if not progressed:
            break
    return dispatched


def drain_available(sessions: sessionmaker[Session], *, limit: int = 100) -> int:
    total = 0
    while True:
        dispatched = dispatch_available(sessions, limit=limit)
        total += dispatched
        if dispatched < limit:
            return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch ERP outbox events")
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    database_url = os.getenv("ERP_DATABASE_URL")
    if not database_url:
        raise SystemExit("Set ERP_DATABASE_URL before starting the worker")

    engine = build_engine(database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        if args.once:
            drain_available(sessions, limit=args.limit)
            return
        while True:
            dispatch_available(sessions, limit=args.limit)
            time.sleep(max(args.interval, 0.1))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
