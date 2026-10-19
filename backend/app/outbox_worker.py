"""Small polling worker that dispatches integration events from the ERP outbox."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
import threading
import time

from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
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
from app.modules.operations.infrastructure import SqlAlchemyOperationsUnitOfWork, SqlAlchemyWorkerHeartbeats
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.clock import as_utc, moscow_now
from app.shared.notifications import NOTIFICATION_TOPIC
from app.shared.persistence import SqlAlchemyOutboxRepository
from app.shared.stop_file import watch_stop_file
from app.workflows.supplier_directory import ReferencesSupplierDirectory
from app.shared.warehouse_location import configured_rack_layout, set_rack_layout_loader
from app.workflows.arbiter import TOPIC as ARBITER_TOPIC
from app.workflows.arbiter import PoisonTriggerMessage, close_followed_up, dispatch_next_trigger, feed_triggers
from app.workflows.arrival_kits import (
    LOT_ARRIVED_TOPIC,
    PoisonArrivalMessage,
    ReceivingArrivalLookup,
    dispatch_next_lot_arrived,
)
from app.workflows.etk_refresh import refresh_tick
from app.workflows.kitset_names import kitset_names_tick
from app.workflows.leave_calendar import leave_calendar_tick
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
# Registered leave requests → the shop calendar: copied right after each decision; this pass repairs a copy that failed.
LEAVE_CALENDAR_INTERVAL_SECONDS = 60.0
_last_leave_calendar = float("-inf")
# «Взять в работу» → name / manufacturer / country from the supplier's page (Guards.gs inWorkSideEffects_) run on their
# own thread too, but on a short beat: the buyer is looking at the line while it fills, so the minute of the tail
# group would be too slow, and a dead supplier site (15 s timeout) must not hold the outbox dispatch either.
AUTOFILL_INTERVAL_SECONDS = 3.0
_last_autofill = float("-inf")
_autofill_thread: threading.Thread | None = None


class _GroupRun:
    """One run of a job group (audit U08): times it and stamps the group's heartbeat when it ends — a success, or the
    errors its jobs reported (each job still logs and swallows its own; one broken job never stops the rest)."""

    def __init__(self, sessions: sessionmaker[Session], group: str):
        self.sessions = sessions
        self.group = group
        self.errors: list[str] = []

    def failed(self, job: str, error: BaseException) -> None:
        self.errors.append(f"{job}: {type(error).__name__}: {error}"[:300])

    def __enter__(self) -> _GroupRun:
        self.started_at = as_utc(moscow_now())
        self._t0 = time.perf_counter()  # not time.monotonic: the beat gates own that clock
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is not None:
            self.failed("run", exc)
        if isinstance(self.sessions, sessionmaker):
            duration_ms = int((time.perf_counter() - self._t0) * 1000)
            SqlAlchemyWorkerHeartbeats(self.sessions).record(
                self.group, self.started_at, duration_ms, "; ".join(self.errors) or None,
            )
        return False


# Errors that mean «the database is not answering», not «this message is bad»: the message stays as it is and the next
# pass retries it; the group's heartbeat records the error. A concurrent edit (IntegrityError / StaleDataError — a kit
# request or a lot changed under the binding) is an attempt on the message like any crash: retried after the backoff,
# dead-lettered only if it fails 5 times in a row.
_TRANSIENT = (OperationalError, InterfaceError)


@dataclass(frozen=True)
class _Topic:
    name: str  # the outbox topic, for quarantining its head message
    label: str
    dispatch: Callable[[], object | None]
    poison: tuple[type[Exception], ...]


def _topics(sessions: sessionmaker[Session]) -> tuple[_Topic, ...]:
    suppliers = ReferencesSupplierDirectory(lambda: SqlAlchemyReferencesUnitOfWork(sessions))
    arrivals = ReceivingArrivalLookup(
        lambda: SqlAlchemyReceivingUnitOfWork(sessions),
        lambda: SqlAlchemyProcurementUnitOfWork(sessions),
    )
    return (
        _Topic("procurement.requested", "outbox", lambda: dispatch_next_procurement_requested(
            moscow_now(), SqlAlchemyProcurementUnitOfWork(sessions), suppliers,
        ), (PoisonOutboxMessage,)),
        _Topic("receiving.expected", "receiving", lambda: dispatch_next_receiving_expected(
            moscow_now(), SqlAlchemyReceivingUnitOfWork(sessions),
        ), (PoisonReceivingMessage,)),
        _Topic("warehouse.received", "warehouse", lambda: dispatch_next_warehouse_received(
            moscow_now(), SqlAlchemyWarehouseUnitOfWork(sessions),
        ), (PoisonWarehouseMessage,)),
        _Topic(LOT_ARRIVED_TOPIC, "lot arrival", lambda: dispatch_next_lot_arrived(
            moscow_now(), lambda: SqlAlchemyFulfillmentUnitOfWork(sessions), arrivals,
        ), (PoisonArrivalMessage,)),
        _Topic("warehouse.supplier_return_requested", "supplier return", lambda: dispatch_next_supplier_return(
            moscow_now(), SqlAlchemyWarehouseUnitOfWork(sessions),
        ), (PoisonWarehouseMessage,)),
        _Topic("procurement.backorder_requested", "backorder", lambda: dispatch_next_backorder_requested(
            moscow_now(), SqlAlchemyProcurementUnitOfWork(sessions),
        ), (PoisonOutboxMessage,)),
        _Topic("requests.reorder_requested", "reorder", lambda: dispatch_next_reorder_requested(
            moscow_now(), SqlAlchemyRequestsUnitOfWork(sessions),
        ), (PoisonRequestMessage,)),
        _Topic(NOTIFICATION_TOPIC, "notification", lambda: dispatch_next_notification(
            moscow_now(), SqlAlchemyOperationsUnitOfWork(sessions),
        ), (PoisonNotificationMessage,)),
        _Topic(ARBITER_TOPIC, "arbiter trigger", lambda: dispatch_next_trigger(moscow_now(), sessions), (PoisonTriggerMessage,)),
    )


def _quarantine_head(sessions: sessionmaker[Session], topic: str, error: BaseException) -> bool:
    """A handler crashed on an error nobody expected: the message it had claimed (the oldest due one of the topic — the
    crash rolled the claim back) gets a failed attempt with the backoff, so it neither blocks its topic nor crashes the
    worker again at once, and after the usual 5 attempts it is dead-lettered. The attempt count lives in the database,
    so a restart does not reset it. False — the attempt could not be recorded either (the next pass tries again)."""
    try:
        with sessions() as session:
            outbox = SqlAlchemyOutboxRepository(session)
            now = moscow_now()
            head = outbox.next_unpublished(topic, now)
            if head is None:
                return False
            outbox.mark_failed(head.id, now, f"{type(error).__name__}: {error}")
            session.commit()
            return True
    except Exception:  # noqa: BLE001 — the database itself is failing; the heartbeat shows it
        logging.exception("Cannot record the failure of a %s message", topic)
        return False


def dispatch_available(
    sessions: sessionmaker[Session],
    *,
    limit: int = 100,
) -> int:
    """Work the module topics off in turns, up to `limit` messages. Every topic is isolated: a bad message (its
    module's Poison* error, or any unexpected crash) is recorded on the message and the turn goes on to the next topic,
    so one poison message never stalls the healthy topics or the worker."""
    if limit < 1:
        raise ValueError("limit must be positive")
    dispatched = 0
    with _GroupRun(sessions, "dispatch") as run:
        topics = _topics(sessions)
        while dispatched < limit:
            progressed = False
            for topic in topics:
                if dispatched >= limit:
                    break
                try:
                    result = topic.dispatch()
                except topic.poison as error:
                    logging.warning("Quarantined invalid %s message %s", topic.label, getattr(error, "message_id", ""))
                    dispatched += 1
                    progressed = True
                    continue
                except _TRANSIENT as error:
                    logging.warning("%s dispatch hit %s; retrying next tick", topic.label, type(error).__name__)
                    run.failed(topic.name, error)
                    continue
                except Exception as error:  # noqa: BLE001 — a crash in one topic must not stop the others
                    if isinstance(error, (IntegrityError, StaleDataError)):
                        logging.warning("%s changed during the dispatch; retrying after the backoff", topic.label)
                    else:
                        logging.exception("%s dispatch crashed", topic.label)
                    if _quarantine_head(sessions, topic.name, error):
                        dispatched += 1
                        progressed = True
                    else:
                        run.failed(topic.name, error)
                    continue
                if result is not None:
                    dispatched += 1
                    progressed = True
            if not progressed:
                break
    return dispatched


def _log_job_error(error: Exception, conflict_message: str, job: str) -> None:
    if isinstance(error, (IntegrityError, StaleDataError)):
        logging.warning(conflict_message)
    else:
        logging.exception("Timer %s failed; retrying next tick", job)


def run_timers(sessions: sessionmaker[Session]) -> int:
    """Time-driven transitions that SUPPLYMAP ran from its owner tick."""
    done = 0
    with _GroupRun(sessions, "timers") as run:
        try:
            done += len(quarantine_overdue_waiting(moscow_now(), SqlAlchemyReceivingUnitOfWork(sessions)))
        except Exception as error:  # noqa: BLE001 — one broken timer must not stop the others or the loop
            _log_job_error(error, "Receiving line changed during the quarantine timer; retrying next tick", "quarantine timer")
            run.failed("quarantine timer", error)
        # OwnerTick.gs syncPurchaseStatusFromWaitlist_: receiving moves paid lines to «Карантин»/«Готово». Before
        # the notifications, so the initiator hears about the new stage in the same tick.
        try:
            done += sync_receipt_stages(
                moscow_now(),
                SqlAlchemyProcurementUnitOfWork(sessions),
                ReceivingStatusLookup(lambda: SqlAlchemyReceivingUnitOfWork(sessions)),
            )
        except Exception as error:  # noqa: BLE001 — one broken timer must not stop the others or the loop
            _log_job_error(error, "Procurement line changed during the receipt stage sync; retrying next tick", "receipt stage sync")
            run.failed("receipt stage sync", error)
        # OwnerTick.gs notifyInitiators_: status changes of procurement lines → their initiators.
        try:
            done += notify_initiators(moscow_now(), SqlAlchemyProcurementUnitOfWork(sessions))
        except Exception as error:  # noqa: BLE001 — one broken timer must not stop the others or the loop
            _log_job_error(error, "Procurement line changed during the initiator notifications; retrying next tick", "initiator notifications")
            run.failed("initiator notifications", error)
    done += run_owner_jobs(sessions)
    done += run_arbiter(sessions)
    done += run_leave_calendar(sessions)
    return done


def run_leave_calendar(sessions: sessionmaker[Session]) -> int:
    """The leave-calendar repair pass (workflows/leave_calendar.py), at most every LEAVE_CALENDAR_INTERVAL_SECONDS.
    A failure is logged, never raised."""
    global _last_leave_calendar
    if time.monotonic() - _last_leave_calendar < LEAVE_CALENDAR_INTERVAL_SECONDS:
        return 0
    _last_leave_calendar = time.monotonic()
    with _GroupRun(sessions, "leave_calendar") as run:
        try:
            return int(leave_calendar_tick(moscow_now(), sessions))
        except Exception as error:  # noqa: BLE001 — one broken job must not stop the worker
            logging.exception("Leave calendar sync failed; retrying next pass")
            run.failed("leave calendar", error)
            return 0


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
    with _GroupRun(sessions, "arbiter") as run:
        for name, job in jobs:
            try:
                done += int(job(moscow_now(), sessions))
            except Exception as error:  # noqa: BLE001 — one broken job must not stop the worker
                logging.exception("Arbiter job %s failed; retrying next tick", name)
                run.failed(name, error)
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
    with _GroupRun(sessions, "owner") as run:
        for name, job in jobs:
            try:
                done += int(job(moscow_now(), sessions))
            except Exception as error:  # noqa: BLE001 — one broken job must not stop the worker
                logging.exception("Owner job %s failed; retrying next tick", name)
                run.failed(name, error)
    return done


def run_tail_jobs(sessions: sessionmaker[Session]) -> int:
    """OwnerTick.gs tail group: the kitset name fill from supplier sites, then the ETK refresh of pending reference
    changes. A failure is logged, never raised."""
    with _GroupRun(sessions, "kitset_names") as run:
        try:
            done = int(kitset_names_tick(moscow_now(), sessions))
        except Exception as error:  # noqa: BLE001 — one broken job must not stop the worker
            logging.exception("Tail job kitset names failed; retrying next tick")
            run.failed("kitset names", error)
            done = 0
        try:  # pending reference/template changes become ETK revisions (only with ETK_AUTO_REVISION=1)
            done += int(refresh_tick(sessions))
        except Exception as error:  # noqa: BLE001 — one broken job must not stop the worker
            logging.exception("Tail job ETK refresh failed; retrying next tick")
            run.failed("etk refresh", error)
    return done


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
    with _GroupRun(sessions, "autofill") as run:
        try:
            return int(procurement_autofill_tick(sessions, cap=cap))
        except Exception as error:  # noqa: BLE001 — one broken job must not stop the worker
            logging.exception("Autofill job failed; retrying next tick")
            run.failed("autofill", error)
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
            try:
                run_timers(sessions)
                start_tail_jobs(sessions)
                start_autofill_job(sessions)
                dispatch_available(sessions, limit=args.limit)
            except Exception:  # noqa: BLE001 — every group stamps its own failure; the loop itself keeps turning
                logging.exception("Worker pass failed; next pass in %.1f s", args.interval)
            time.sleep(max(args.interval, 0.1))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
