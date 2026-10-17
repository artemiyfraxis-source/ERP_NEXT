"""Заявления на отпуск и отгул — «Личная карточка», фаза 2 (docs/migration/cabinet-plan.md T4/T6; native, no SUPPLYMAP
source).

Route: Сотрудник → Руководитель → Кадры. The employee drafts a request in «Моя карточка» and signs it; the manager
approves or rejects it; «Кадры» (section `hr`, edit) registers or rejects it. A registered request becomes an absence
in the shop calendar (app/workflows/leave_calendar.py), a withdrawal takes it away.

Statuses: draft → signed → approved → registered | rejected | withdrawn. Rules:
- every step is a signature: the signer types their PIN or password again (the login check, the same brute-force
  counters); the secret is never stored — the signature keeps who, when, how, the content version and its SHA-256;
- an edit of a draft or of a signed request (by its author, before the manager's decision) raises the content version
  and returns it to draft: the old signature no longer covers it;
- nobody approves their own request; the manager step is the direct manager or, as substitute, the manager's manager;
  when neither is there, anyone with «Кадры» edit;
- every change sends the row version it saw; another version → conflict (two approvers cannot both act);
- a vacation fits the year's norm (VACATION_NORM_DAYS calendar days a year, no carry-over); open requests of one
  employee never overlap; a request does not start in the past.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Protocol
from uuid import UUID, uuid4

from app.shared.clock import as_utc
from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.notifications import notification_requested, publish_notification

from .application import UnitOfWork, _rate_limited, _record_failure, user_level
from .audiences import EDIT, holds
from .domain import FULL_ACCESS_LEVELS, PIN_RE, AccessMode, IdentityUser, level_permissions, verify_password

# The year's paid vacation, calendar days (ТК РФ ст. 115); one norm for everyone until the card stores its own.
VACATION_NORM_DAYS = 28
COMMENT_LIMIT = 1000
LONGEST_REQUEST_DAYS = 366
HR_SECTION = "hr"


class LeaveKind(str, Enum):
    VACATION = "vacation"
    DAY_OFF = "day_off"


# The same words as the shop calendar's absence kinds (production «Отпуск» / «Отгул»).
KIND_LABELS: dict[LeaveKind, str] = {LeaveKind.VACATION: "Отпуск", LeaveKind.DAY_OFF: "Отгул"}


class LeaveStatus(str, Enum):
    DRAFT = "draft"
    SIGNED = "signed"
    APPROVED = "approved"
    REGISTERED = "registered"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


STATUS_LABELS: dict[LeaveStatus, str] = {
    LeaveStatus.DRAFT: "Черновик",
    LeaveStatus.SIGNED: "На согласовании у руководителя",
    LeaveStatus.APPROVED: "Согласовано руководителем",
    LeaveStatus.REGISTERED: "Зарегистрировано кадрами",
    LeaveStatus.REJECTED: "Отклонено",
    LeaveStatus.WITHDRAWN: "Отозвано",
}
# A request in these takes its period: another one of the same employee may not overlap it.
OPEN_STATUSES = frozenset({LeaveStatus.DRAFT, LeaveStatus.SIGNED, LeaveStatus.APPROVED, LeaveStatus.REGISTERED})

STEP_LABELS = {"employee": "Сотрудник", "manager": "Руководитель", "hr": "Кадры", "withdraw": "Отзыв"}
STEP_ORDER = {step: position for position, step in enumerate(STEP_LABELS)}


def signature_order(signature: "LeaveSignature") -> tuple:
    """Signatures in the order they were given (steps of one moment in route order)."""
    return (signature.signed_at, signature.content_version, STEP_ORDER.get(signature.step, len(STEP_ORDER)))
DECISION_LABELS = {
    "signed": "подписал", "approved": "согласовал", "rejected": "отклонил", "registered": "зарегистрировал",
    "withdrawn": "отозвал",
}
METHOD_LABELS = {"pin": "PIN", "password": "пароль"}


class LeaveError(ValueError):
    """A rule of the request is broken (422)."""


class LeaveForbidden(PermissionError):
    """The person may not take this step (403)."""


class LeaveConflict(RuntimeError):
    """The request changed or is no longer at this step (409)."""


class LeaveNotFound(LookupError):
    pass


class SignatureRefused(LeaveError):
    """The PIN / password did not match (422, counted like a failed login)."""


@dataclass(frozen=True)
class LeaveSignature:
    id: UUID
    request_id: UUID
    step: str
    decision: str
    signer_id: UUID
    signer_name: str
    signed_at: datetime
    method: str
    content_version: int
    content_sha256: str
    comment: str = ""


@dataclass(frozen=True)
class LeaveRequest:
    id: UUID
    employee_id: UUID
    kind: LeaveKind
    from_date: date
    to_date: date
    comment: str
    status: LeaveStatus
    content_version: int
    content_sha256: str
    version: int
    created_at: datetime
    updated_at: datetime
    signatures: tuple[LeaveSignature, ...] = ()

    @property
    def days(self) -> int:
        return (self.to_date - self.from_date).days + 1

    def overlaps(self, from_date: date, to_date: date) -> bool:
        return self.from_date <= to_date and from_date <= self.to_date


class LeaveRepository(Protocol):
    def get(self, request_id: UUID) -> LeaveRequest | None: ...
    def for_employee(self, employee_id: UUID) -> list[LeaveRequest]: ...
    def with_status(self, statuses: Iterable[LeaveStatus]) -> list[LeaveRequest]: ...
    def add(self, request: LeaveRequest) -> None: ...
    def save(self, request: LeaveRequest, expected_version: int) -> None: ...
    def add_signature(self, signature: LeaveSignature) -> None: ...


def content_sha256(
    employee_id: UUID, kind: LeaveKind, from_date: date, to_date: date, comment: str, content_version: int,
) -> str:
    """What a signature covers: the employee, kind, period, comment and the content version, canonical JSON."""
    content = {
        "employee_id": str(employee_id), "kind": kind.value, "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(), "comment": comment, "content_version": content_version,
    }
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# ── Pure rules ───────────────────────────────────────────────────────────────────────────────────────


def normalize_kind(value: str) -> LeaveKind:
    try:
        return LeaveKind(str(value or "").strip())
    except ValueError as error:
        raise LeaveError("Вид заявления — отпуск или отгул.") from error


def normalize_comment(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) > COMMENT_LIMIT:
        raise LeaveError(f"Комментарий длиннее {COMMENT_LIMIT} символов.")
    return text


def days_in_year(from_date: date, to_date: date, year: int) -> int:
    start, finish = max(from_date, date(year, 1, 1)), min(to_date, date(year, 12, 31))
    return max((finish - start).days + 1, 0)


def check_period(
    kind: LeaveKind, from_date: date, to_date: date, today: date, others: Sequence[LeaveRequest],
) -> None:
    """The period of a new or edited request against the employee's other requests (`others` without this one)."""
    if to_date < from_date:
        raise LeaveError("Конец периода раньше начала.")
    if from_date < today:
        raise LeaveError("Заявление не подаётся задним числом: начало — сегодня или позже.")
    if (to_date - from_date).days + 1 > LONGEST_REQUEST_DAYS:
        raise LeaveError("Период длиннее года.")
    for other in others:
        if other.status in OPEN_STATUSES and other.overlaps(from_date, to_date):
            raise LeaveError(
                f"Период пересекается с заявлением «{KIND_LABELS[other.kind]}» "
                f"{other.from_date:%d.%m.%Y}–{other.to_date:%d.%m.%Y} ({STATUS_LABELS[other.status].lower()})."
            )
    if kind is not LeaveKind.VACATION:
        return
    for year in range(from_date.year, to_date.year + 1):
        taken = sum(
            days_in_year(other.from_date, other.to_date, year) for other in others
            if other.kind is LeaveKind.VACATION and other.status in OPEN_STATUSES
        )
        wanted = days_in_year(from_date, to_date, year)
        if taken + wanted > VACATION_NORM_DAYS:
            raise LeaveError(
                f"Отпуск {year} года: норма {VACATION_NORM_DAYS} дн., уже в заявлениях {taken} дн., "
                f"в этом — {wanted} дн. Осталось {max(VACATION_NORM_DAYS - taken, 0)} дн."
            )


@dataclass(frozen=True)
class Staff:
    """Who is who for the route: everyone by id and who holds «Кадры» edit."""
    users: dict[UUID, IdentityUser]
    hr_ids: frozenset[UUID]  # «Кадры» edit, superadmin/admin included (may act)
    hr_notify_ids: tuple[UUID, ...]  # who hears about a request waiting for «Кадры»

    def name(self, user_id: UUID | None) -> str:
        user = self.users.get(user_id) if user_id is not None else None
        return user.name if user is not None else "сотрудник удалён"

    def manager_approvers(self, employee_id: UUID) -> tuple[UUID, ...]:
        """The direct manager and the manager's manager (substitute), active and not the employee; nobody of them →
        everyone with «Кадры» edit except the employee."""
        chain: list[UUID] = []
        employee = self.users.get(employee_id)
        current = employee.manager_id if employee is not None else None
        seen: set[UUID] = set()
        while current is not None and len(seen) < 2 and current not in seen:
            seen.add(current)
            person = self.users.get(current)
            if person is None:
                break
            if person.active and person.id != employee_id:
                chain.append(person.id)
            current = person.manager_id
        if chain:
            return tuple(chain)
        return tuple(sorted((user_id for user_id in self.hr_ids if user_id != employee_id), key=str))

    def may_act(self, request: LeaveRequest, actor_id: UUID) -> bool:
        if actor_id == request.employee_id:
            return False
        if request.status is LeaveStatus.SIGNED:
            return actor_id in self.manager_approvers(request.employee_id)
        if request.status is LeaveStatus.APPROVED:
            return actor_id in self.hr_ids
        return False


def staff_of(uow: UnitOfWork) -> Staff:
    users = {user.id: user for user in uow.identity.all_users()}
    matrix = uow.identity.all_level_access()
    exceptions = uow.identity.all_user_level_access()
    hr_ids: set[UUID] = set()
    explicit: list[UUID] = []
    full: list[UUID] = []
    for user in users.values():
        if not user.active:
            continue
        level = user_level(user)
        personal = exceptions.get(user.id, {})
        permissions = level_permissions(level, matrix.get(level, {}), personal)
        if permissions.get(HR_SECTION) is AccessMode.EDIT:
            hr_ids.add(user.id)
        # The notification audience rule (identity/audiences.py): a superadmin/admin hears only with an explicit grant.
        held = personal.get(HR_SECTION) if level in FULL_ACCESS_LEVELS else personal.get(HR_SECTION, matrix.get(level, {}).get(HR_SECTION))
        if holds(held.value if held is not None else None, EDIT):
            explicit.append(user.id)
        elif level in FULL_ACCESS_LEVELS:
            full.append(user.id)
    return Staff(users=users, hr_ids=frozenset(hr_ids), hr_notify_ids=tuple(sorted(explicit or full, key=str)))


# ── Signature ────────────────────────────────────────────────────────────────────────────────────────


def _verify_signer(uow: UnitOfWork, user: IdentityUser, secret: str, now: datetime, client_key: str) -> str:
    """The PIN or the password typed again, checked like a login (same per-account and global counters). Returns the
    method; a mismatch is recorded as a failed attempt (committed) and refused."""
    account_key = f"login:{user.login}"
    failures = _rate_limited(uow, account_key, client_key, user.login, now)
    value = str(secret or "").strip()
    record = uow.identity.pin_record(str(user.id))
    pin_hash = record.pin_hash if record is not None else None
    if value and pin_hash and PIN_RE.fullmatch(value) and verify_password(value, pin_hash):
        return "pin"
    password_hash = uow.identity.password_hash(user.id)
    if value and password_hash and verify_password(str(secret or ""), password_hash):
        return "password"
    _record_failure(
        uow, account_key=account_key, client_key=client_key, identifier=user.login, user_id=user.id,
        failures=failures, now=now, method="signature",
    )
    raise SignatureRefused("Неверный PIN или пароль — подпись не принята.")


def _signer(uow: UnitOfWork, actor: Actor) -> IdentityUser:
    user = uow.identity.user_for_update(actor.id)
    if user is None or not user.active:
        raise LeaveForbidden("Подписать может только действующий сотрудник.")
    return user


def _signature(
    request: LeaveRequest, step: str, decision: str, signer: IdentityUser, method: str, now: datetime, comment: str = "",
) -> LeaveSignature:
    return LeaveSignature(
        id=uuid4(), request_id=request.id, step=step, decision=decision, signer_id=signer.id, signer_name=signer.name,
        signed_at=now, method=method, content_version=request.content_version,
        content_sha256=request.content_sha256, comment=comment,
    )


# ── Use cases ────────────────────────────────────────────────────────────────────────────────────────


def _audit(uow: UnitOfWork, request: LeaveRequest, event: str, actor: Actor, now: datetime, **details: str) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=request.id, event_type=f"identity.leave_{event}", actor=actor, occurred_at=now,
        details={
            "employee_id": str(request.employee_id), "kind": request.kind.value,
            "from_date": request.from_date.isoformat(), "to_date": request.to_date.isoformat(),
            "status": request.status.value, "content_version": str(request.content_version), **details,
        },
    ))


def _notify(
    uow: UnitOfWork, request: LeaveRequest, to: str, user_ids: Iterable[UUID], title: str, body: str, now: datetime,
) -> None:
    """`to` names the addressee group (employee · manager · hr): one message per group and request version."""
    publish_notification(uow.outbox, notification_requested(
        key=f"leave:{request.id}:{request.version}:{to}", now=now, title=title, body=body,
        kind="leave", reference=f"{KIND_LABELS[request.kind]} {request.from_date:%d.%m}–{request.to_date:%d.%m}",
        user_ids=user_ids,
    ))


def _period_text(request: LeaveRequest) -> str:
    return (
        f"{KIND_LABELS[request.kind].lower()} {request.from_date:%d.%m.%Y}–{request.to_date:%d.%m.%Y} "
        f"({request.days} дн.)"
    )


def _load(uow: UnitOfWork, request_id: UUID, expected_version: int) -> LeaveRequest:
    request = uow.leave.get(request_id)
    if request is None:
        raise LeaveNotFound("Заявление не найдено.")
    if request.version != expected_version:
        raise LeaveConflict("Заявление уже изменилось — обновите страницу.")
    return request


def create_leave(
    actor: Actor, kind: str, from_date: date, to_date: date, comment: str | None, now: datetime, uow: UnitOfWork,
) -> LeaveRequest:
    """A draft of the signed-in employee (any active employee files their own)."""
    today = now.date()
    stamp = as_utc(now)
    leave_kind, text = normalize_kind(kind), normalize_comment(comment)
    with uow:
        user = uow.identity.user_for_update(actor.id)
        if user is None or not user.active:
            raise LeaveForbidden("Заявление подаёт действующий сотрудник.")
        check_period(leave_kind, from_date, to_date, today, uow.leave.for_employee(user.id))
        request = LeaveRequest(
            id=uuid4(), employee_id=user.id, kind=leave_kind, from_date=from_date, to_date=to_date, comment=text,
            status=LeaveStatus.DRAFT, content_version=1,
            content_sha256=content_sha256(user.id, leave_kind, from_date, to_date, text, 1),
            version=1, created_at=stamp, updated_at=stamp,
        )
        uow.leave.add(request)
        _audit(uow, request, "created", actor, stamp)
        uow.commit()
        return request


def update_leave(
    request_id: UUID, expected_version: int, actor: Actor, kind: str, from_date: date, to_date: date,
    comment: str | None, now: datetime, uow: UnitOfWork,
) -> LeaveRequest:
    """The author edits a draft, or a signed request the manager has not decided yet: a changed content raises its
    version and returns the request to draft (sign again)."""
    stamp = as_utc(now)
    leave_kind, text = normalize_kind(kind), normalize_comment(comment)
    with uow:
        request = _load(uow, request_id, expected_version)
        if request.employee_id != actor.id:
            raise LeaveForbidden("Править заявление может только его автор.")
        if request.status not in (LeaveStatus.DRAFT, LeaveStatus.SIGNED):
            raise LeaveConflict("Заявление уже согласовано или закрыто — правка невозможна, его можно отозвать.")
        same = (leave_kind, from_date, to_date, text) == (request.kind, request.from_date, request.to_date, request.comment)
        if same:
            return request
        others = [other for other in uow.leave.for_employee(request.employee_id) if other.id != request.id]
        check_period(leave_kind, from_date, to_date, now.date(), others)
        content_version = request.content_version + 1
        updated = replace(
            request, kind=leave_kind, from_date=from_date, to_date=to_date, comment=text, status=LeaveStatus.DRAFT,
            content_version=content_version,
            content_sha256=content_sha256(request.employee_id, leave_kind, from_date, to_date, text, content_version),
            version=request.version + 1, updated_at=stamp,
        )
        uow.leave.save(updated, expected_version)
        _audit(uow, updated, "edited", actor, stamp, previous_status=request.status.value)
        uow.commit()
        return updated


def sign_leave(
    request_id: UUID, expected_version: int, actor: Actor, secret: str, now: datetime, uow: UnitOfWork,
    *, client_key: str,
) -> LeaveRequest:
    """The employee signs their draft: it goes to the manager (or the substitute)."""
    stamp = as_utc(now)
    with uow:
        request = _load(uow, request_id, expected_version)
        if request.employee_id != actor.id:
            raise LeaveForbidden("Подписать заявление может только его автор.")
        if request.status is not LeaveStatus.DRAFT:
            raise LeaveConflict("Заявление уже подписано.")
        if request.from_date < now.date():
            raise LeaveError("Начало периода уже прошло — измените даты.")
        signer = _signer(uow, actor)
        method = _verify_signer(uow, signer, secret, stamp, client_key)
        signed = replace(request, status=LeaveStatus.SIGNED, version=request.version + 1, updated_at=stamp)
        uow.leave.save(signed, expected_version)
        signature = _signature(signed, "employee", "signed", signer, method, stamp)
        uow.leave.add_signature(signature)
        signed = replace(signed, signatures=(*signed.signatures, signature))
        _audit(uow, signed, "signed", actor, stamp, method=method)
        staff = staff_of(uow)
        _notify(
            uow, signed, "manager", staff.manager_approvers(signed.employee_id), "Заявление на согласование",
            f"{signer.name}: {_period_text(signed)}. Согласуйте или отклоните в «Моей карточке».", stamp,
        )
        uow.commit()
        return signed


def decide_leave(
    request_id: UUID, expected_version: int, actor: Actor, approve: bool, comment: str | None, secret: str,
    now: datetime, uow: UnitOfWork, *, client_key: str,
) -> LeaveRequest:
    """The manager's step (signed → approved | rejected) or the «Кадры» step (approved → registered | rejected), each
    signed. A rejection says why."""
    stamp = as_utc(now)
    text = normalize_comment(comment)
    if not approve and not text:
        raise LeaveError("Укажите причину отказа.")
    with uow:
        request = _load(uow, request_id, expected_version)
        if request.status not in (LeaveStatus.SIGNED, LeaveStatus.APPROVED):
            raise LeaveConflict("Заявление уже не ждёт решения.")
        if request.employee_id == actor.id:
            raise LeaveForbidden("Своё заявление не согласуют.")
        staff = staff_of(uow)
        if not staff.may_act(request, actor.id):
            raise LeaveForbidden(
                "Согласует руководитель сотрудника." if request.status is LeaveStatus.SIGNED
                else "Регистрирует сотрудник с доступом «Кадры»."
            )
        signer = _signer(uow, actor)
        method = _verify_signer(uow, signer, secret, stamp, client_key)
        manager_step = request.status is LeaveStatus.SIGNED
        if approve:
            status, decision = (
                (LeaveStatus.APPROVED, "approved") if manager_step else (LeaveStatus.REGISTERED, "registered")
            )
        else:
            status, decision = LeaveStatus.REJECTED, "rejected"
        decided = replace(request, status=status, version=request.version + 1, updated_at=stamp)
        uow.leave.save(decided, expected_version)
        signature = _signature(decided, "manager" if manager_step else "hr", decision, signer, method, stamp, text)
        uow.leave.add_signature(signature)
        decided = replace(decided, signatures=(*decided.signatures, signature))
        _audit(uow, decided, decision, actor, stamp, method=method, comment=text)
        employee_name = staff.name(decided.employee_id)
        period = _period_text(decided)
        if status is LeaveStatus.APPROVED:
            _notify(uow, decided, "hr", staff.hr_notify_ids, "Заявление ждёт регистрации",
                    f"{employee_name}: {period} — согласовано руководителем ({signer.name}).", stamp)
        explained = f" Причина: {text}" if text else ""
        _notify(uow, decided, "employee", (decided.employee_id,), f"Заявление: {STATUS_LABELS[status].lower()}",
                f"{period} — {DECISION_LABELS[decision]} {signer.name}.{explained}", stamp)
        uow.commit()
        return decided


def withdraw_leave(
    request_id: UUID, expected_version: int, actor: Actor, secret: str, comment: str | None, now: datetime,
    uow: UnitOfWork, *, client_key: str,
) -> LeaveRequest:
    """The author withdraws a request until it starts (a draft — without a signature); «Кадры» cancels a registered one
    at any time. A registered request leaves the shop calendar with it."""
    stamp = as_utc(now)
    text = normalize_comment(comment)
    with uow:
        request = _load(uow, request_id, expected_version)
        if request.status not in OPEN_STATUSES:
            raise LeaveConflict("Заявление уже закрыто.")
        own = request.employee_id == actor.id
        staff = staff_of(uow)
        if own:
            if request.status is LeaveStatus.REGISTERED and request.from_date <= now.date():
                raise LeaveForbidden("Отпуск уже начался — отозвать его может только «Кадры».")
        elif not (request.status is LeaveStatus.REGISTERED and actor.id in staff.hr_ids):
            raise LeaveForbidden("Отозвать заявление может его автор, зарегистрированное — «Кадры».")
        method = ""
        signer = None
        if request.status is not LeaveStatus.DRAFT:
            signer = _signer(uow, actor)
            method = _verify_signer(uow, signer, secret, stamp, client_key)
        withdrawn = replace(request, status=LeaveStatus.WITHDRAWN, version=request.version + 1, updated_at=stamp)
        uow.leave.save(withdrawn, expected_version)
        if signer is not None:
            signature = _signature(withdrawn, "withdraw", "withdrawn", signer, method, stamp, text)
            uow.leave.add_signature(signature)
            withdrawn = replace(withdrawn, signatures=(*withdrawn.signatures, signature))
        _audit(uow, withdrawn, "withdrawn", actor, stamp, method=method, comment=text,
               previous_status=request.status.value)
        period = _period_text(withdrawn)
        if not own:
            _notify(uow, withdrawn, "employee", (withdrawn.employee_id,), "Заявление отозвано кадрами",
                    f"{period} — отозвал {staff.name(actor.id)}." + (f" Причина: {text}" if text else ""), stamp)
        elif request.status is LeaveStatus.SIGNED:
            _notify(uow, withdrawn, "manager", staff.manager_approvers(withdrawn.employee_id), "Заявление отозвано",
                    f"{staff.name(withdrawn.employee_id)}: {period} — отозвано автором.", stamp)
        elif request.status in (LeaveStatus.APPROVED, LeaveStatus.REGISTERED):
            _notify(uow, withdrawn, "hr", staff.hr_notify_ids, "Заявление отозвано",
                    f"{staff.name(withdrawn.employee_id)}: {period} — отозвано автором.", stamp)
        uow.commit()
        return withdrawn


# ── Reading ──────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LeaveOverview:
    own: list[LeaveRequest]
    inbox: list[LeaveRequest]
    all: list[LeaveRequest] | None  # «Кадры»: every request but drafts; None — not theirs to see
    staff: Staff


def leave_overview(user_id: UUID, uow: UnitOfWork) -> LeaveOverview:
    """The person's own requests, the requests waiting for their decision and, with «Кадры» edit, the whole journal."""
    with uow:
        staff = staff_of(uow)
        own = uow.leave.for_employee(user_id)
        waiting = uow.leave.with_status((LeaveStatus.SIGNED, LeaveStatus.APPROVED))
        inbox = sorted(
            (request for request in waiting if staff.may_act(request, user_id)),
            key=lambda request: (request.from_date, request.created_at),
        )
        journal = None
        if user_id in staff.hr_ids:
            journal = uow.leave.with_status(set(LeaveStatus) - {LeaveStatus.DRAFT})
            journal.sort(key=lambda request: request.updated_at, reverse=True)
        own.sort(key=lambda request: (request.from_date, request.created_at), reverse=True)
        return LeaveOverview(own=own, inbox=inbox, all=journal, staff=staff)


def waiting_count(user_id: UUID, uow: UnitOfWork) -> int:
    with uow:
        staff = staff_of(uow)
        waiting = uow.leave.with_status((LeaveStatus.SIGNED, LeaveStatus.APPROVED))
        return sum(1 for request in waiting if staff.may_act(request, user_id))


@dataclass(frozen=True)
class RegisteredLeave:
    request_id: UUID
    employee_id: UUID
    from_date: date
    to_date: date
    kind_label: str


def registered_leaves(uow: UnitOfWork) -> list[RegisteredLeave]:
    """Every registered request — what the shop calendar must hold as absences."""
    with uow:
        return [
            RegisteredLeave(request.id, request.employee_id, request.from_date, request.to_date, KIND_LABELS[request.kind])
            for request in uow.leave.with_status((LeaveStatus.REGISTERED,))
        ]


def year_balance(requests: Sequence[LeaveRequest], year: int) -> dict[str, int]:
    """The year's vacation: the norm, registered days, days in requests on the way (draft · signed · approved)."""
    registered = sum(
        days_in_year(request.from_date, request.to_date, year) for request in requests
        if request.kind is LeaveKind.VACATION and request.status is LeaveStatus.REGISTERED
    )
    pending = sum(
        days_in_year(request.from_date, request.to_date, year) for request in requests
        if request.kind is LeaveKind.VACATION
        and request.status in (LeaveStatus.DRAFT, LeaveStatus.SIGNED, LeaveStatus.APPROVED)
    )
    return {
        "year": year, "norm": VACATION_NORM_DAYS, "registered": registered, "pending": pending,
        "left": max(VACATION_NORM_DAYS - registered - pending, 0),
    }


def leave_view(request: LeaveRequest, staff: Staff, viewer_id: UUID, today: date) -> dict:
    """One request for the screens: Russian labels, who is next, the signatures and what the viewer may do."""
    if request.status is LeaveStatus.SIGNED:
        approvers = staff.manager_approvers(request.employee_id)
        waiting_for = ", ".join(staff.name(user_id) for user_id in approvers) or "нет согласующего — назначьте руководителя"
    elif request.status is LeaveStatus.APPROVED:
        waiting_for = "Кадры"
    else:
        waiting_for = None
    own = request.employee_id == viewer_id
    can_withdraw = request.status in OPEN_STATUSES and (
        (own and not (request.status is LeaveStatus.REGISTERED and request.from_date <= today))
        or (not own and request.status is LeaveStatus.REGISTERED and viewer_id in staff.hr_ids)
    )
    return {
        "id": str(request.id),
        "employee_id": str(request.employee_id),
        "employee_name": staff.name(request.employee_id),
        "kind": request.kind.value,
        "kind_label": KIND_LABELS[request.kind],
        "from_date": request.from_date.isoformat(),
        "to_date": request.to_date.isoformat(),
        "days": request.days,
        "comment": request.comment,
        "status": request.status.value,
        "status_label": STATUS_LABELS[request.status],
        "waiting_for": waiting_for,
        "content_version": request.content_version,
        "content_sha256": request.content_sha256,
        "version": request.version,
        "created_at": request.created_at.isoformat(),
        "updated_at": request.updated_at.isoformat(),
        "signatures": [{
            "step": signature.step,
            "step_label": STEP_LABELS.get(signature.step, signature.step),
            "decision": signature.decision,
            "decision_label": DECISION_LABELS.get(signature.decision, signature.decision),
            "signer_name": signature.signer_name,
            "signed_at": signature.signed_at.isoformat(),
            "method_label": METHOD_LABELS.get(signature.method, signature.method),
            "content_version": signature.content_version,
            "content_sha256": signature.content_sha256,
            "comment": signature.comment,
        } for signature in sorted(request.signatures, key=signature_order)],
        "actions": {
            "edit": own and request.status in (LeaveStatus.DRAFT, LeaveStatus.SIGNED),
            "sign": own and request.status is LeaveStatus.DRAFT,
            "decide": staff.may_act(request, viewer_id),
            "withdraw": can_withdraw,
        },
    }

