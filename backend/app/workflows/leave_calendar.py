"""Зарегистрированное заявление на отпуск / отгул → календарь цеха (cabinet plan T6).

Identity owns the requests (`identity/leave.py`), Production owns the calendar the planner, the dispatcher and
«Загрузка по людям» read (`production_absences`). The bridge copies every registered request into the calendar as an
absence tied to it (`leave_request_id`) and removes the copy of a request that is no longer registered (withdrawn).
The copy is idempotent — the calendar's leave rows become exactly the registered set — so it runs right after a
decision or a withdrawal and again from the outbox worker's timer, which repairs a copy that failed in between. The
master's own absences are never touched, and the master's calendar save never touches these.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session, sessionmaker

from app.modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork
from app.modules.identity.leave import registered_leaves
from app.modules.production.application import Absence, SyncLeaveAbsencesCommand, sync_leave_absences
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.shared.identity import Actor

SYNC_ACTOR = Actor(id=UUID(int=0), name="Заявления на отпуск", department="Кадры")


def absence_id(request_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"fraxis-erp:leave-absence:{request_id}")


def sync_leave_calendar(
    identity: Callable[[], object], production: Callable[[], object], now: datetime, actor: Actor = SYNC_ACTOR,
) -> int:
    """Registered requests → the calendar's leave rows. Returns the rows changed."""
    absences = tuple(
        Absence(
            absence_id(leave.request_id), leave.employee_id, leave.from_date, leave.to_date, leave.kind_label,
            leave.request_id,
        )
        for leave in registered_leaves(identity())
    )
    return sync_leave_absences(SyncLeaveAbsencesCommand(absences=absences, actor=actor, now=now), production())


def sync_after_decision(
    identity: Callable[[], object] | None, production: Callable[[], object] | None, now: datetime, actor: Actor,
) -> bool:
    """Right after a leave transition: a failure is logged, not raised — the decision is already saved and the worker's
    timer repeats the copy."""
    if identity is None or production is None:
        return False
    try:
        sync_leave_calendar(identity, production, now, actor)
    except Exception:  # noqa: BLE001 — the request's own transaction is committed; the timer repairs the calendar
        logging.exception("Leave calendar sync failed; the worker retries it")
        return False
    return True


def leave_calendar_tick(now: datetime, sessions: sessionmaker[Session]) -> int:
    """The outbox worker's repair pass."""
    return sync_leave_calendar(
        lambda: SqlAlchemyIdentityUnitOfWork(sessions), lambda: SqlAlchemyProductionUnitOfWork(sessions), now,
    )
