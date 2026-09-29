"""Small polling worker that dispatches integration events from the ERP outbox."""

from __future__ import annotations

import argparse
import logging
import os
import time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.database import build_engine
from app.modules.procurement.application import (
    PoisonOutboxMessage,
    dispatch_next_backorder_requested,
    dispatch_next_procurement_requested,
    notify_initiators,
)
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.requests.application import PoisonRequestMessage, dispatch_next_reorder_requested
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork as SqlAlchemyRequestsUnitOfWork
from app.modules.receiving.application import (
    PoisonReceivingMessage,
    dispatch_next_receiving_expected,
    quarantine_overdue_waiting,
)
from app.modules.receiving.infrastructure import SqlAlchemyReceivingUnitOfWork
from app.modules.warehouse.application import (
    PoisonWarehouseMessage,
    dispatch_next_supplier_return,
    dispatch_next_warehouse_received,
)
from app.modules.warehouse.infrastructure import SqlAlchemyRackLayoutStore, SqlAlchemyWarehouseUnitOfWork
from app.modules.operations.application import PoisonNotificationMessage, dispatch_next_notification
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.clock import moscow_now
from app.workflows.supplier_directory import ReferencesSupplierDirectory
from app.shared.warehouse_location import configured_rack_layout, set_rack_layout_loader
from app.workflows.owner_alerts import alert_on_dead_letters, daily_overdue_digest, quarantine_escalations

# alertOnErrors_ ran once per owner tick (a minute); the worker loop turns every couple of seconds.
ERROR_ALERT_INTERVAL_SECONDS = 60.0
_last_error_alert = 0.0


def dispatch_available(
    sessions: sessionmaker[Session],
    *,
    limit: int = 100,
) -> int:
    if limit < 1:
        raise ValueError("limit must be positive")
    dispatched = 0
    suppliers = ReferencesSupplierDirectory(lambda: SqlAlchemyReferencesUnitOfWork(sessions))
    while dispatched < limit:
        progressed = False
        try:
            line = dispatch_next_procurement_requested(
                moscow_now(),
                SqlAlchemyProcurementUnitOfWork(sessions),
                suppliers,
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
            supplier_return = dispatch_next_supplier_return(
                moscow_now(),
                SqlAlchemyWarehouseUnitOfWork(sessions),
            )
        except PoisonWarehouseMessage as error:
            logging.warning("Quarantined invalid supplier return message %s", error.message_id)
            dispatched += 1
            continue
        if supplier_return is not None:
            dispatched += 1
            progressed = True
        if dispatched >= limit:
            break
        try:
            backorder = dispatch_next_backorder_requested(
                moscow_now(),
                SqlAlchemyProcurementUnitOfWork(sessions),
            )
        except PoisonOutboxMessage as error:
            logging.warning("Quarantined invalid backorder message %s", error.message_id)
            dispatched += 1
            continue
        if backorder is not None:
            dispatched += 1
            progressed = True
        if dispatched >= limit:
            break
        try:
            reorder = dispatch_next_reorder_requested(
                moscow_now(),
                SqlAlchemyRequestsUnitOfWork(sessions),
            )
        except PoisonRequestMessage as error:
            logging.warning("Quarantined invalid reorder message %s", error.message_id)
            dispatched += 1
            continue
        if reorder is not None:
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


def run_timers(sessions: sessionmaker[Session]) -> int:
    """Time-driven transitions that SUPPLYMAP ran from its owner tick."""
    done = 0
    try:
        done += len(quarantine_overdue_waiting(moscow_now(), SqlAlchemyReceivingUnitOfWork(sessions)))
    except (IntegrityError, StaleDataError):
        logging.warning("Receiving line changed during the quarantine timer; retrying next tick")
    # OwnerTick.gs notifyInitiators_: status changes of procurement lines → their initiators.
    try:
        done += notify_initiators(moscow_now(), SqlAlchemyProcurementUnitOfWork(sessions))
    except (IntegrityError, StaleDataError):
        logging.warning("Procurement line changed during the initiator notifications; retrying next tick")
    done += run_owner_jobs(sessions)
    return done


def run_owner_jobs(sessions: sessionmaker[Session]) -> int:
    """OP-4 daily digest, disposal escalation (day-gated) and OP-5 failure alert (once a minute).

    A failing job is logged and never stops the others (runTickStepsLocked_ swallowed step errors).
    """
    global _last_error_alert
    done = 0
    jobs = [("daily digest", daily_overdue_digest), ("quarantine escalation", quarantine_escalations)]
    if time.monotonic() - _last_error_alert >= ERROR_ALERT_INTERVAL_SECONDS:
        _last_error_alert = time.monotonic()
        jobs.append(("failure alert", alert_on_dead_letters))
    for name, job in jobs:
        try:
            done += int(job(moscow_now(), sessions))
        except Exception:  # noqa: BLE001 — one broken job must not stop the worker
            logging.exception("Owner job %s failed; retrying next tick", name)
    return done


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
    configured_rack_layout()  # a broken ERP_WAREHOUSE_RACK_LAYOUT stops the start, not a receipt

    engine = build_engine(database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    set_rack_layout_loader(SqlAlchemyRackLayoutStore(sessions).load)
    try:
        if args.once:
            run_timers(sessions)
            drain_available(sessions, limit=args.limit)
            return
        while True:
            run_timers(sessions)
            dispatch_available(sessions, limit=args.limit)
            time.sleep(max(args.interval, 0.1))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
