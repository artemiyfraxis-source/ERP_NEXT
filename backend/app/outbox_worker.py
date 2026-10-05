"""Small polling worker that dispatches integration events from the ERP outbox."""

from __future__ import annotations

import argparse
import logging
import os
import threading
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
    sync_receipt_stages,
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
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.operations.application import PoisonNotificationMessage, dispatch_next_notification
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.clock import moscow_now
from app.shared.stop_file import watch_stop_file
from app.workflows.supplier_directory import ReferencesSupplierDirectory
from app.shared.warehouse_location import configured_rack_layout, set_rack_layout_loader
from app.workflows.arbiter import PoisonTriggerMessage, close_followed_up, dispatch_next_trigger, feed_triggers
from app.workflows.arrival_kits import PoisonArrivalMessage, ReceivingArrivalLookup, dispatch_next_lot_arrived
from app.workflows.kitset_names import kitset_names_tick
from app.workflows.procurement_autofill import AUTOFILL_TICK_CAP, procurement_autofill_tick
from app.workflows.receipt_stage import ReceivingStatusLookup
from app.workflows.owner_alerts import alert_on_dead_letters, daily_overdue_digest, quarantine_escalations

# alertOnErrors_ ran once per owner tick (a minute); the worker loop turns every couple of seconds.
ERROR_ALERT_INTERVAL_SECONDS = 60.0
_last_error_alert = 0.0
# tcKitNamesTick_ ran in the owner tail tick (ownerTailTick, once a minute) because it goes to supplier sites.
# Here it gets its own thread for the same reason: 15 page fetches must not hold the outbox dispatch.
TAIL_INTERVAL_SECONDS = 60.0
_last_tail = float("-inf")
# The arbiter's auto-close reads production tasks and shipments; a task waits for a launch or a shipment for hours.
ARBITER_CLOSE_INTERVAL_SECONDS = 30.0
_last_arbiter_close = float("-inf")
_tail_thread: threading.Thread | None = None
# «Взять в работу» → name / manufacturer / country from the supplier's page (Guards.gs inWorkSideEffects_) run on their
# own thread too, but on a short beat: the buyer is looking at the line while it fills, so the minute of the tail
# group would be too slow, and a dead supplier site (15 s timeout) must not hold the outbox dispatch either.
AUTOFILL_INTERVAL_SECONDS = 3.0
_last_autofill = float("-inf")
_autofill_thread: threading.Thread | None = None


def dispatch_available(
    sessions: sessionmaker[Session],
    *,
    limit: int = 100,
) -> int:
    if limit < 1:
        raise ValueError("limit must be positive")
    dispatched = 0
    suppliers = ReferencesSupplierDirectory(lambda: SqlAlchemyReferencesUnitOfWork(sessions))
    arrivals = ReceivingArrivalLookup(
        lambda: SqlAlchemyReceivingUnitOfWork(sessions),
        lambda: SqlAlchemyProcurementUnitOfWork(sessions),
    )
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
            arrival = dispatch_next_lot_arrived(
                moscow_now(),
                lambda: SqlAlchemyFulfillmentUnitOfWork(sessions),
                arrivals,
            )
        except PoisonArrivalMessage as error:
            logging.warning("Quarantined invalid lot arrival message %s", error.message_id)
            dispatched += 1
            continue
        except (IntegrityError, StaleDataError):
            # A kit request or the lot changed under the binding (the storekeeper on the screen): retried next pass.
            logging.warning("Kit request changed during the arrival binding; retrying next tick")
            arrival = None
        if arrival is not None:
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
        if dispatched >= limit:
            break
        try:
            trigger = dispatch_next_trigger(moscow_now(), sessions)
        except PoisonTriggerMessage as error:
            logging.warning("Quarantined invalid arbiter trigger %s", error.message_id)
            dispatched += 1
            continue
        if trigger is not None:
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
    # OwnerTick.gs syncPurchaseStatusFromWaitlist_: receiving moves paid lines to «Карантин»/«Готово». Before
    # the notifications, so the initiator hears about the new stage in the same tick.
    try:
        done += sync_receipt_stages(
            moscow_now(),
            SqlAlchemyProcurementUnitOfWork(sessions),
            ReceivingStatusLookup(lambda: SqlAlchemyReceivingUnitOfWork(sessions)),
        )
    except (IntegrityError, StaleDataError):
        logging.warning("Procurement line changed during the receipt stage sync; retrying next tick")
    # OwnerTick.gs notifyInitiators_: status changes of procurement lines → their initiators.
    try:
        done += notify_initiators(moscow_now(), SqlAlchemyProcurementUnitOfWork(sessions))
    except (IntegrityError, StaleDataError):
        logging.warning("Procurement line changed during the initiator notifications; retrying next tick")
    done += run_owner_jobs(sessions)
    done += run_arbiter(sessions)
    return done


def run_arbiter(sessions: sessionmaker[Session]) -> int:
    """ARCH-TRACK-3 arbiter: new module trigger events → its outbox topic (dispatched in `dispatch_available`) every
    loop; open project tasks whose follow-up already happened → closed, at most every ARBITER_CLOSE_INTERVAL_SECONDS
    (it reads production tasks and shipments). A failure is logged, never raised."""
    global _last_arbiter_close
    done = 0
    jobs = [("trigger feed", feed_triggers)]
    if time.monotonic() - _last_arbiter_close >= ARBITER_CLOSE_INTERVAL_SECONDS:
        _last_arbiter_close = time.monotonic()
        jobs.append(("task auto-close", close_followed_up))
    for name, job in jobs:
        try:
            done += int(job(moscow_now(), sessions))
        except Exception:  # noqa: BLE001 — one broken job must not stop the worker
            logging.exception("Arbiter job %s failed; retrying next tick", name)
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


def run_tail_jobs(sessions: sessionmaker[Session]) -> int:
    """OwnerTick.gs tail group: the kitset name fill from supplier sites. A failure is logged, never raised."""
    try:
        return int(kitset_names_tick(moscow_now(), sessions))
    except Exception:  # noqa: BLE001 — one broken job must not stop the worker
        logging.exception("Tail job kitset names failed; retrying next tick")
        return 0


def start_tail_jobs(sessions: sessionmaker[Session]) -> bool:
    """Start the tail jobs on a background thread, at most once a minute and never two at a time."""
    global _last_tail, _tail_thread
    if _tail_thread is not None and _tail_thread.is_alive():
        return False
    if time.monotonic() - _last_tail < TAIL_INTERVAL_SECONDS:
        return False
    _last_tail = time.monotonic()
    _tail_thread = threading.Thread(target=run_tail_jobs, args=(sessions,), name="owner-tail", daemon=True)
    _tail_thread.start()
    return True


def run_autofill_job(sessions: sessionmaker[Session], cap: int = AUTOFILL_TICK_CAP) -> int:
    """Procurement lines taken into work: the page-dependent fields fill themselves. A failure is logged, never raised."""
    try:
        return int(procurement_autofill_tick(sessions, cap=cap))
    except Exception:  # noqa: BLE001 — one broken job must not stop the worker
        logging.exception("Autofill job failed; retrying next tick")
        return 0


def start_autofill_job(sessions: sessionmaker[Session]) -> bool:
    """Start the autofill on a background thread, at most every AUTOFILL_INTERVAL_SECONDS and never two at a time."""
    global _last_autofill, _autofill_thread
    if _autofill_thread is not None and _autofill_thread.is_alive():
        return False
    if time.monotonic() - _last_autofill < AUTOFILL_INTERVAL_SECONDS:
        return False
    _last_autofill = time.monotonic()
    _autofill_thread = threading.Thread(target=run_autofill_job, args=(sessions,), name="procurement-autofill", daemon=True)
    _autofill_thread.start()
    return True


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
    watch_stop_file()  # local PC copy: the updater stops the worker with a stop file (scripts/update-local.ps1)

    engine = build_engine(database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    set_rack_layout_loader(SqlAlchemyRackLayoutStore(sessions).load)
    try:
        if args.once:
            run_timers(sessions)
            run_tail_jobs(sessions)
            run_autofill_job(sessions, cap=args.limit)
            drain_available(sessions, limit=args.limit)
            return
        while True:
            run_timers(sessions)
            start_tail_jobs(sessions)
            start_autofill_job(sessions)
            dispatch_available(sessions, limit=args.limit)
            time.sleep(max(args.interval, 0.1))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
