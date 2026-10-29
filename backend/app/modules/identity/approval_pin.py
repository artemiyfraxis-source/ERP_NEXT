"""Разрешительный ПИН (владелец 29.10.2026): «ПИН того же, что для входа — нет; давай какой-то другой: в профиле будет
как разрешительный ПИН, может где-то ещё будем такое использовать».

A second personal PIN that permits an action on someone else's device (today: «Отгрузить без УПД»,
workflows/ship_no_upd.py). It is never the login PIN:
- only the person sets or changes it, on their own card, after typing their current password or login PIN (as a leave
  signature does: the same `login:<логин>` counters, a mismatch is a failed sign-in attempt);
- the policy is the login PIN's (6–8 digits), kept as an Argon2id hash with the same parameters, and it must differ
  from the person's login PIN;
- an administrator (full level) only resets it (clears it) from «Сотрудники» — never sees or sets it; a full-level
  holder's PIN is reset only by a superadministrator;
- `confirm_with_approval_pin` checks it under the PIN-login limits: one account counter per person for every purpose
  (`approval:<логин>` — a counter per purpose would multiply the guesses), the login's client and overall counters; a
  wrong PIN is a failed attempt journalled with the purpose, a series locks for 10 minutes.
Journal: identity.approval_pin_set / _changed / _reset; failures and locks — identity.login_failed /
login_rate_limited with `method` = the purpose.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.shared.clock import as_utc
from app.shared.identity import Actor

from .application import (
    APPROVER_REFUSED, ApproverRefused, AuthenticationError, PinRefused, UnitOfWork, _audit, _rate_limited,
    _record_failure, _section_edit, user_level,
)
from .domain import FULL_ACCESS_LEVELS, PIN_RE, IdentityUser, IdentityValidationError, hash_pin, verify_password

APPROVAL_PIN_POLICY = "Разрешительный ПИН — 6–8 цифр."
APPROVAL_PIN_SAME_AS_LOGIN = "Разрешительный ПИН должен отличаться от PIN входа."
APPROVAL_PIN_UNCHANGED = "Новый разрешительный ПИН совпадает с текущим."
APPROVAL_CURRENT_BAD = "Текущий пароль или PIN входа указан неверно."
APPROVAL_PIN_MISSING = "У сотрудника не задан разрешительный ПИН — его задают в личном кабинете."
APPROVAL_PIN_BAD = "Неверный разрешительный ПИН."
APPROVAL_RESET_FULL_ONLY = "Сбросить разрешительный ПИН может только администратор."
APPROVAL_RESET_SUPERADMIN = "Разрешительный ПИН администратора сбрасывает только суперадминистратор."


class ApprovalPinForbidden(PermissionError):
    pass


def _validate(pin: str) -> str:
    value = str(pin if pin is not None else "").strip()
    if not PIN_RE.fullmatch(value):
        raise IdentityValidationError(APPROVAL_PIN_POLICY)
    return value


def approval_pin_set_at(user_id: UUID, uow: UnitOfWork) -> datetime | None:
    """When the person's approval PIN was set; None — not set (or no such person)."""
    with uow:
        credentials = uow.identity.approval_credentials(user_id)
        return credentials.approval_pin_set_at if credentials is not None else None


def approval_pin_dates(uow: UnitOfWork) -> dict[UUID, datetime]:
    with uow:
        return uow.identity.approval_pin_dates()


def set_own_approval_pin(
    user_id: UUID, current: str, pin: str, actor: Actor, now: datetime, uow: UnitOfWork, *, client_key: str,
) -> datetime:
    """The person sets (or changes) their own approval PIN. `current` is their account password or login PIN, checked
    like a sign-in under the login's counters before anything else is said. Returns the moment it was set."""
    value = _validate(pin)
    now = as_utc(now)
    with uow:
        user = uow.identity.user_for_update(user_id)
        credentials = uow.identity.approval_credentials(user_id)
        if user is None or not user.active or credentials is None:
            raise KeyError("Сотрудник не найден.")
        _check_current(uow, user, credentials.password_hash, credentials.pin_hash, current, now, client_key)
        if credentials.pin_hash and verify_password(value, credentials.pin_hash):
            raise IdentityValidationError(APPROVAL_PIN_SAME_AS_LOGIN)
        changed = credentials.approval_pin_hash is not None
        if changed and verify_password(value, credentials.approval_pin_hash):
            raise IdentityValidationError(APPROVAL_PIN_UNCHANGED)
        uow.identity.set_approval_pin(user_id, hash_pin(value), now)
        _audit(
            uow,
            aggregate_id=user_id,
            event_type="identity.approval_pin_changed" if changed else "identity.approval_pin_set",
            actor=actor,
            now=now,
            details={"login": user.login, "name": user.name or user.email},
        )
        uow.commit()
        return now


def _check_current(
    uow: UnitOfWork, user: IdentityUser, password_hash: str, pin_hash: str | None, current: str, now: datetime,
    client_key: str,
) -> None:
    """The current password or login PIN, as a leave signature checks it: the `login:<логин>` counters, a mismatch is
    a failed attempt (committed) and refused; success clears nothing (the person is already signed in)."""
    account_key = f"login:{user.login}"
    failures = _rate_limited(uow, account_key, client_key, user.login, now, method="approval_pin_set")
    secret = str(current or "")
    value = secret.strip()
    if value and pin_hash and PIN_RE.fullmatch(value) and verify_password(value, pin_hash):
        return
    if secret and verify_password(secret, password_hash):
        return
    _record_failure(
        uow, account_key=account_key, client_key=client_key, identifier=user.login, user_id=user.id,
        failures=failures, now=now, method="approval_pin_set",
    )
    raise AuthenticationError(APPROVAL_CURRENT_BAD)


def reset_approval_pin(
    user_id: UUID, actor: Actor, now: datetime, uow: UnitOfWork, *, actor_level: str,
) -> bool:
    """An administrator clears a person's approval PIN (forgotten, or the person left the role); the person sets a new
    one themselves. True — there was one to clear; journalled either way."""
    if actor_level not in FULL_ACCESS_LEVELS:
        raise ApprovalPinForbidden(APPROVAL_RESET_FULL_ONLY)
    now = as_utc(now)
    with uow:
        user = uow.identity.user_for_update(user_id)
        credentials = uow.identity.approval_credentials(user_id)
        if user is None or credentials is None:
            raise KeyError("Сотрудник не найден.")
        if user_level(user) in FULL_ACCESS_LEVELS and actor_level != "superadmin" and user.id != actor.id:
            raise ApprovalPinForbidden(APPROVAL_RESET_SUPERADMIN)
        was_set = credentials.approval_pin_hash is not None
        if was_set:
            uow.identity.set_approval_pin(user_id, None, None)
        _audit(
            uow,
            aggregate_id=user_id,
            event_type="identity.approval_pin_reset",
            actor=actor,
            now=now,
            details={"login": user.login, "name": user.name or user.email, "was": "задан" if was_set else "не задан"},
        )
        uow.commit()
        return was_set


def confirm_with_approval_pin(
    user_id: UUID, pin: str, purpose: str, now: datetime, uow: UnitOfWork, *, client_key: str,
    section: str | None = None,
) -> IdentityUser:
    """The person `user_id` permits an action (`purpose`, e.g. «noupd») with their approval PIN, typed on someone else's
    device. They must be active and, when `section` is given, hold it on edit — refused before any PIN is checked (no
    counter moves). Then the PIN-login limits: the account counter `approval:<логин>`, the login's client and overall
    counters; «не задан» is said as such (a PinRefused, no counter moves), a wrong PIN is a failed attempt journalled
    with the purpose, a lock answers AuthenticationRateLimited."""
    now = as_utc(now)
    with uow:
        user = uow.identity.user_by_id(user_id)
        if user is None or not user.active:
            raise ApproverRefused(APPROVER_REFUSED)
        if section is not None and not _section_edit(
            user, section, uow.identity.level_access(user_level(user)), uow.identity.user_level_access(user.id),
        ):
            raise ApproverRefused(APPROVER_REFUSED)
        identifier = user.login or str(user.id)
        account_key = f"approval:{identifier}"
        failures = _rate_limited(uow, account_key, client_key, identifier, now, method=purpose)
        credentials = uow.identity.approval_credentials(user.id)
        if credentials is None or not credentials.approval_pin_hash:
            raise PinRefused(APPROVAL_PIN_MISSING)
        if not verify_password(str(pin or "").strip(), credentials.approval_pin_hash):
            _record_failure(
                uow, account_key=account_key, client_key=client_key, identifier=identifier, user_id=user.id,
                failures=failures, now=now, method=purpose,
            )
            raise AuthenticationError(APPROVAL_PIN_BAD)
        uow.identity.reset_failures(account_key)
        uow.commit()
        return user
