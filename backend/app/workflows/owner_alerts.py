"""Daily owner jobs of SUPPLYMAP OwnerTick.gs that only read business data and write to the inbox.

- OP-4 `dailyOverdueDigest_` (OwnerTick.gs:377-431): once a day, «что горит» — overdue purchase
  deadlines of requests plus receiving lines stuck in «Ожидание»/«Карантин».
- `quarantineEscalations_` (OwnerTick.gs:586-624): once a day, a line in «Карантин» for
  ≥ QUARANTINE_DAYS_DISPOSAL calendar days → one notification asking for the disposal decision.
- OP-5 `alertOnErrors_` (OwnerTick.gs:655-681): new background failures (dead-lettered outbox
  messages) → admins, deduplicated by signature with a 6 h cooldown; the first run only seeds.

The jobs read Requests, Procurement and Receiving tables read-only (a cross-module read model, like
the other adapters in `app/workflows`); they never change a business row. Their day gates and marks
live in `operations_job_marks` (SUPPLYMAP ScriptProperties).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.operations.infrastructure import SqlAlchemyJobMarks, SqlAlchemyOperationsRepository
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.receiving.domain import QUARANTINE_WORKDAYS, ReceivingStatus, workdays_between
from app.modules.receiving.infrastructure import ReceivingLineRow
from app.modules.requests.infrastructure import RequestRow
from app.shared.notifications import (
    SUPPLY_AUDIENCE,
    TECHNOLOGIST_AUDIENCE,
    notification_requested,
    publish_notification,
)
from app.shared.persistence import SqlAlchemyOutboxRepository

# Config.gs:228 QUAR_DAYS_DISPOSAL.
QUARANTINE_DAYS_DISPOSAL = 90
# Config.gs:231 QUAR_DISPOSAL_EMAILS = Виктор (снабжение) + Артемий («Технолог»).
DISPOSAL_AUDIENCES = (TECHNOLOGIST_AUDIENCE, SUPPLY_AUDIENCE)
# Config.gs:238 OVERDUE_DIGEST_EMAILS = [] and CONFIG.ERROR_ALERT_EMAIL = '' → the owner of the
# script. ERP has no «owner» account; the owner's alerts go to the superadministrators and administrators
# (the technologist audience). Решено агентом — проверить.
OWNER_AUDIENCES = (TECHNOLOGIST_AUDIENCE,)
# CONFIG.ERROR_ALERT_COOLDOWN_H || 6.
ERROR_ALERT_COOLDOWN = timedelta(hours=6)
DIGEST_LIMIT = 25
# overdueDigestText_: ['Закрыто', 'Отменено'] are not overdue.
_FINISHED_REQUEST_STATUSES = ("Закрыто", "Отменено")
# Config.gs PURCHASE_TO_REQUEST_STATUS: the request status SUPPLYMAP back-synced from its purchase row.
_PURCHASE_TO_REQUEST_STATUS = {
    "Отменено": "Отменено",
    "Готово": "Закрыто",
    "В пути": "В пути",
    "Согласование": "Согласование",
}

_DIGEST_GATE = "last_overdue_digest"
_ESCALATION_GATE = "last_quar_escalation"
_DISPOSAL_MARKS = "quarantine_notified"
_ERROR_MARKS = "error_alerted"


def disposal_label() -> str:
    """quarDisposalLabel_: «3 мес» from QUAR_DAYS_DISPOSAL."""
    return f"{round(QUARANTINE_DAYS_DISPOSAL / 30)} мес"


def _dmy(day: date) -> str:
    return day.strftime("%d.%m.%Y")


@dataclass(frozen=True)
class _Overdue:
    legacy_id: str
    name: str
    deadline: date
    days: int
    status: str


def _request_status(request: RequestRow, purchase_statuses: Sequence[str]) -> str:
    """syncRequestStatusFromPurchase_: a mapped purchase status wins, else the request's own.

    A split request has several purchase lines; their statuses are joined, and it is finished only
    when every part is «Закрыто»/«Отменено».
    """
    if not purchase_statuses:
        return request.status
    return " / ".join(dict.fromkeys(
        _PURCHASE_TO_REQUEST_STATUS.get(status, request.status) for status in purchase_statuses
    ))


def overdue_requests(session: Session, today: date) -> list[_Overdue]:
    requests = list(session.scalars(
        select(RequestRow).where(
            RequestRow.procurement_deadline < today,
            RequestRow.status.not_in(_FINISHED_REQUEST_STATUSES),
        )
    ))
    if not requests:
        return []
    purchase: dict[object, list[str]] = {}
    for request_id, status in session.execute(
        select(ProcurementLineRow.source_request_id, ProcurementLineRow.status)
        .where(ProcurementLineRow.source_request_id.in_([row.id for row in requests]))
        .order_by(ProcurementLineRow.created_at, ProcurementLineRow.id)
    ):
        purchase.setdefault(request_id, [])
        if status not in purchase[request_id]:
            purchase[request_id].append(status)
    found: list[_Overdue] = []
    for request in requests:
        status = _request_status(request, purchase.get(request.id, []))
        if all(part in _FINISHED_REQUEST_STATUSES for part in status.split(" / ")):
            continue
        found.append(_Overdue(
            legacy_id=request.legacy_id,
            name=request.name or request.designation or "",
            deadline=request.procurement_deadline,
            days=(today - request.procurement_deadline).days,
            status=status,
        ))
    # overdueRows_: the most overdue first.
    found.sort(key=lambda item: (-item.days, item.legacy_id))
    return found


# WAITLIST_TABS (Config.gs): «ОЖ.ЦАР» is read before «ОЖ.ЖУК».
_WAREHOUSE_ORDER = ("ЦАР", "ЖУК")


def _stuck_receiving(session: Session) -> list[ReceivingLineRow]:
    rows = list(session.scalars(
        select(ReceivingLineRow)
        .where(ReceivingLineRow.status.in_((ReceivingStatus.WAITING.value, ReceivingStatus.QUARANTINE.value)))
        .order_by(ReceivingLineRow.created_at, ReceivingLineRow.id)
    ))
    order = {code: index for index, code in enumerate(_WAREHOUSE_ORDER)}
    return sorted(rows, key=lambda row: order.get(row.warehouse, len(order)))


def overdue_digest_text(session: Session, today: date) -> str:
    """overdueDigestText_: '' when nothing is burning."""
    parts: list[str] = []
    overdue = overdue_requests(session, today)
    if overdue:
        lines = [f"ПРОСРОЧЕННЫЕ ДЕДЛАЙНЫ ({len(overdue)}):"]
        for item in overdue[:DIGEST_LIMIT]:
            lines.append(
                f"• {item.legacy_id} · {item.name or '—'} — дедлайн {_dmy(item.deadline)} "
                f"({item.days} дн. назад), статус: {item.status or '—'}"
            )
        if len(overdue) > DIGEST_LIMIT:
            lines.append(f"… и ещё {len(overdue) - DIGEST_LIMIT}")
        parts.append("\n".join(lines))
    quarantine: list[str] = []
    waiting: list[str] = []
    for line in _stuck_receiving(session):
        ref = line.inspected_on  # АКТ ВК ДАТА — the date the nonconformity was fixed
        days = (today - ref).days if ref else None
        text = f"• {line.legacy_id} · {line.name}"
        if line.status == ReceivingStatus.WAITING.value:
            wait = f" — ждёт решения {workdays_between(ref, today)} из {QUARANTINE_WORKDAYS} р.д." if ref else ""
            waiting.append(f"{text}{wait} ({line.warehouse})")
            continue
        flag = (
            f" · ≥{disposal_label().upper()} — НУЖНО РЕШЕНИЕ ОБ УТИЛИЗАЦИИ"
            if days is not None and days >= QUARANTINE_DAYS_DISPOSAL else ""
        )
        in_quarantine = f" — в Карантине {days} дн." if days is not None else ""
        quarantine.append(f"{text}{in_quarantine}{flag} ({line.warehouse})")
    if waiting:
        parts.append(f"ОЖИДАНИЕ РЕШЕНИЯ ПО НЕСООТВЕТСТВИЮ ({len(waiting)}):\n" + "\n".join(waiting))
    if quarantine:
        parts.append(f"ЗАВИСШИЙ КАРАНТИН ({len(quarantine)}):\n" + "\n".join(quarantine))
    return "\n\n".join(parts)


def daily_overdue_digest(now: datetime, sessions: sessionmaker[Session]) -> bool:
    """dailyOverdueDigest_: the day is marked BEFORE the digest, so a failure does not repeat it."""
    today = now.date()
    with sessions() as session:
        marks = SqlAlchemyJobMarks(session)
        if marks.get(_DIGEST_GATE) == today.isoformat():
            return False
        marks.put(_DIGEST_GATE, today.isoformat(), now)
        session.commit()
        text = overdue_digest_text(session, today)
        if not text:
            return False
        published = publish_notification(SqlAlchemyOutboxRepository(session), notification_requested(
            key=f"digest:{today.isoformat()}",
            now=now,
            title="Дайджест — просрочки и Карантин",
            body=text,
            kind="digest",
            audiences=OWNER_AUDIENCES,
        ))
        session.commit()
        return published


def _load_json(marks: SqlAlchemyJobMarks, key: str) -> dict | None:
    raw = marks.get(key)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def quarantine_escalations(now: datetime, sessions: sessionmaker[Session]) -> int:
    """quarantineEscalations_: one disposal notification per stay in «Карантин» ≥ 90 days."""
    today = now.date()
    sent = 0
    with sessions() as session:
        marks = SqlAlchemyJobMarks(session)
        if marks.get(_ESCALATION_GATE) == today.isoformat():
            return 0
        marks.put(_ESCALATION_GATE, today.isoformat(), now)
        notified = _load_json(marks, _DISPOSAL_MARKS) or {}
        outbox = SqlAlchemyOutboxRepository(session)
        seen: set[str] = set()
        changed = False
        for line in _stuck_receiving(session):
            if line.status != ReceivingStatus.QUARANTINE.value or line.inspected_on is None:
                continue
            key = str(line.id)
            seen.add(key)
            days = (today - line.inspected_on).days
            if notified.get(key) or days < QUARANTINE_DAYS_DISPOSAL:
                continue
            nature = ", ".join(line.nonconformities or [])
            publish_notification(outbox, notification_requested(
                key=f"receiving:{line.id}:disposal:{today.isoformat()}",
                now=now,
                title=f"Карантин ≥{disposal_label()} — нужно решение об утилизации — {line.legacy_id}",
                body=(
                    f"{line.legacy_id} · {line.name or '—'} ({line.warehouse})\n"
                    f"В Карантине {days} дн. (с {_dmy(line.inspected_on)})."
                    + (f"\nХарактер несоответствия: {nature}" if nature else "")
                    + f"\n\nРешение о выводе из карантина за {disposal_label()} не получено — по регламенту "
                    "требуется решение об утилизации."
                ),
                kind="disposal",
                reference=line.legacy_id,
                audiences=DISPOSAL_AUDIENCES,
            ))
            notified[key] = {"d": True}
            changed = True
            sent += 1
        # A line that left «Карантин» loses its mark: a new stay counts again.
        for key in [key for key in notified if key not in seen]:
            del notified[key]
            changed = True
        if changed:
            marks.put(_DISPOSAL_MARKS, json.dumps(notified, sort_keys=True), now)
        session.commit()
    return sent


def _signature(topic: str, error: str) -> str:
    return hashlib.sha1(f"{topic}|{error}".encode()).hexdigest()


def alert_on_dead_letters(now: datetime, sessions: sessionmaker[Session]) -> int:
    """alertOnErrors_: a digest of NEW failure signatures to admins, 6 h cooldown per signature.

    Every present signature is re-stamped on each run, so a failure that stays in the outbox is
    announced once; the first run only seeds the current ones (history is not sent).
    """
    stamp = now.timestamp()
    cooldown = ERROR_ALERT_COOLDOWN.total_seconds()
    with sessions() as session:
        marks = SqlAlchemyJobMarks(session)
        alerted = _load_json(marks, _ERROR_MARKS)
        first_run = alerted is None
        alerted = alerted or {}
        digest: list[str] = []
        for topic, error, count in SqlAlchemyOperationsRepository(session).dead_letter_signatures():
            signature = _signature(topic, error)
            if not first_run and stamp - float(alerted.get(signature, 0)) > cooldown:
                digest.append(f"• [{topic}]" + (f" (×{count})" if count > 1 else "") + f"\n  {error}")
            alerted[signature] = stamp
        alerted = {key: value for key, value in alerted.items() if stamp - float(value) <= cooldown * 4}
        marks.put(_ERROR_MARKS, json.dumps(alerted, sort_keys=True), now)
        if not first_run and digest:
            publish_notification(SqlAlchemyOutboxRepository(session), notification_requested(
                key=f"error-alert:{now.isoformat()}",
                now=now,
                title=f"Ошибки фоновых задач ({len(digest)})",
                body=(
                    "Новые ошибки фоновых задач (не доставлены после всех попыток):\n\n"
                    + "\n\n".join(digest)
                    + "\n\nПодробности — «Системный журнал»."
                ),
                kind="error",
                audiences=OWNER_AUDIENCES,
            ))
        session.commit()
        return 0 if first_run else len(digest)
