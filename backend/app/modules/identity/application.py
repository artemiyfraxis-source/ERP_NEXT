"""Identity use cases for login, session resolution, and revocation."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.shared.http_auth import Principal
from app.shared.identity import Actor
from app.shared.events import AuditEvent
from app.shared.clock import as_utc
from app.shared.notifications import notification_requested, publish_notification

from .domain import (
    ROLE_LABELS,
    AccessMode,
    can_admin_mode,
    FULL_ACCESS_ROLES,
    hash_pin,
    new_pin,
    IdentityUser,
    IdentityValidationError,
    validate_password,
    effective_permissions,
    hash_password,
    role_permissions,
    normalize_login,
    secret_hash,
    verify_password,
)


class AuthenticationError(ValueError):
    pass


class AuthenticationRateLimited(AuthenticationError):
    pass


@dataclass(frozen=True)
class IdentitySession:
    id: UUID
    user_id: UUID
    token_hash: str
    csrf_hash: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None
    # «Режим админа» (Portal.gs portalToggleAdmin): the role is admin until this moment (CacheService TTL, 6 h).
    admin_mode_until: datetime | None = None


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    csrf_token: str
    principal: Principal


@dataclass(frozen=True)
class PinReveal:
    """portalRevealPin_: the new PIN of an employee who has never signed in, shown once."""
    name: str
    pin: str


@dataclass(frozen=True)
class PinRecord:
    user: IdentityUser
    pin_hash: str | None
    seen: bool  # portal_seen_<email>: signed in at least once


class PinRefused(AuthenticationError):
    """Not a failed attempt (no counter moves): the PIN is not assigned, or the reveal door is closed."""


@dataclass(frozen=True)
class ResolvedSession:
    session: IdentitySession
    principal: Principal


class IdentityRepository(Protocol):
    def credentials_for_login(self, login: str) -> tuple[IdentityUser, str] | None: ...
    def role_access(self, role: str) -> dict[str, AccessMode]: ...
    def user_access(self, user_id: UUID) -> dict[str, AccessMode]: ...
    def add_session(self, session: IdentitySession) -> None: ...
    def session_with_user(self, token_hash: str) -> tuple[IdentitySession, IdentityUser] | None: ...
    def update_session(self, session: IdentitySession) -> bool: ...
    def revoke_session(self, token_hash: str, revoked_at: datetime) -> bool: ...
    def lock_throttle_keys(self, keys: tuple[str, ...]) -> None: ...
    def cleanup_security_state(
        self,
        now: datetime,
        throttle_window: timedelta,
        session_retention: timedelta,
    ) -> None: ...
    def failure_count(self, key: str, now: datetime, window: timedelta) -> int: ...
    def increment_failure(self, key: str, now: datetime, window: timedelta) -> None: ...
    def mark_rate_limit_audited(self, key: str, now: datetime) -> bool: ...
    def reset_failures(self, key: str) -> None: ...
    def deactivate_user(self, user_id: UUID) -> IdentityUser | None: ...
    def revoke_user_sessions(self, user_id: UUID, revoked_at: datetime) -> int: ...
    # «Сотрудники» (ID-8, staff.py)
    def all_users(self) -> list[IdentityUser]: ...
    def user_for_update(self, user_id: UUID) -> IdentityUser | None: ...
    def identifiers_taken(self, identifiers: set[str]) -> bool: ...
    def add_user(
        self, user: IdentityUser, password_hash: str, role_access: dict[str, AccessMode], pin_hash: str | None = None,
    ) -> None: ...
    def update_user(self, user: IdentityUser) -> None: ...
    def user_history(self, user_id: UUID) -> int: ...
    def delete_user(self, user_id: UUID) -> None: ...
    def password_hash(self, user_id: UUID) -> str | None: ...
    def set_password(self, user_id: UUID, password_hash: str) -> None: ...
    def active_admin_ids(self) -> list[UUID]: ...
    def saved_departments(self) -> list[str]: ...
    def replace_departments(self, departments: list[str]) -> None: ...
    # «Доступы» (NAV-ACCESS, access.py): the role matrix holds only changes on top of ROLE_DEFAULTS
    def all_role_access(self) -> dict[str, dict[str, AccessMode]]: ...
    def all_user_access(self) -> dict[UUID, dict[str, AccessMode]]: ...
    def replace_role_access(self, role: str, overrides: dict[str, AccessMode]) -> None: ...
    def replace_user_access(self, user_id: UUID, overrides: dict[str, AccessMode]) -> None: ...
    # PIN login (Portal.gs portalLogin / portalRevealPin_) and «Режим админа» (portalToggleAdmin)
    def pin_record(self, who: str) -> PinRecord | None: ...
    def set_pin(self, user_id: UUID, pin_hash: str) -> None: ...
    def pin_set_ids(self) -> set[UUID]: ...
    def mark_seen(self, user_id: UUID, now: datetime) -> None: ...
    def setting(self, key: str) -> str | None: ...
    def set_setting(self, key: str, value: str | None) -> None: ...
    def set_admin_mode(self, session_id: UUID, until: datetime | None) -> bool: ...


class UnitOfWork(Protocol):
    identity: IdentityRepository
    audit: object
    outbox: object  # notification.requested (ID-4: the brute-force alert)

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type, exc, traceback) -> bool | None: ...
    def commit(self) -> None: ...


SESSION_TTL = timedelta(days=14)
SESSION_ABSOLUTE_TTL = timedelta(days=30)
THROTTLE_WINDOW = timedelta(minutes=10)
# SUPPLYMAP Portal.gs:252-306 (ID-4): 10 failures per account and 30 over all accounts in 10 minutes close the login;
# the global counter stops a fan-out over many accounts. ERP keeps its per-client ceiling on top.
LOGIN_FAIL_LIMIT = 10
LOGIN_FAIL_LIMIT_GLOBAL = 30
CLIENT_FAIL_LIMIT = 300
GLOBAL_KEY = "global:login"
SECURITY_STATE_RETENTION = timedelta(days=7)
DUMMY_PASSWORD_HASH = hash_password("not-a-real-user-password", salt=b"\0" * 16)
SYSTEM_ACTOR = Actor(id=UUID(int=0), name="Identity", department="Система")


def _audit(
    uow: UnitOfWork,
    *,
    aggregate_id: UUID,
    event_type: str,
    actor: Actor,
    now: datetime,
    details: dict[str, str],
) -> None:
    uow.audit.add(
        AuditEvent(
            id=uuid4(),
            aggregate_id=aggregate_id,
            event_type=event_type,
            actor=actor,
            occurred_at=now,
            details=details,
        )
    )


def _principal(
    user: IdentityUser,
    role_access: dict[str, AccessMode],  # the stored matrix row of the role, ROLE_DEFAULTS are added here
    user_access: dict[str, AccessMode],
    *,
    admin_mode: bool = False,
) -> Principal:
    # portalSession_: in «Режим админа» the effective role is admin (its matrix row + the person's own exceptions,
    # portalAccessFor_('admin', email)); the stored role stays as it is.
    role = "admin" if admin_mode else user.role
    base = role_permissions(role, role_access)
    # A full-access role cannot be reduced: neither the matrix (already ignored in role_permissions) nor a personal
    # exception lowers it, so its personal overlay is skipped entirely (owner 2026-09-29).
    effective = base if role in FULL_ACCESS_ROLES else effective_permissions(base, user_access)
    return Principal(
        actor=Actor(id=user.id, name=user.name or user.email, department=user.department),
        email=user.email,
        roles=frozenset({role}),
        permissions={section: mode.value for section, mode in effective.items()},
        admin_mode=admin_mode,
        can_admin_mode=can_admin_mode(user.email),
    )


def _principal_for(uow: UnitOfWork, user: IdentityUser, *, admin_mode: bool = False) -> Principal:
    role = "admin" if admin_mode else user.role
    return _principal(user, uow.identity.role_access(role), uow.identity.user_access(user.id), admin_mode=admin_mode)


def role_label(principal: Principal) -> str:
    """portalState_ roleLabel: the effective role's label, «· режим» while the admin mode is on."""
    role = next(iter(principal.roles), "staff")
    return ROLE_LABELS.get(role, role) + (" · режим" if principal.admin_mode else "")


def _session_admin_mode(session: IdentitySession, user: IdentityUser, now: datetime) -> bool:
    """portalAdminMode_: the flag of this session and the right to it."""
    return (
        can_admin_mode(user.email)
        and session.admin_mode_until is not None
        and session.admin_mode_until > now
    )


def _rate_limited(
    uow: UnitOfWork, account_key: str, client_key: str, identifier: str, now: datetime,
) -> tuple[int, int]:
    """portalLoginBlocked_ (ID-4): 10 failures per account, 30 over all accounts, ERP's client ceiling. Raises when
    the login is closed; otherwise returns (failures of the account, failures overall) for the next attempt."""
    uow.identity.cleanup_security_state(now, THROTTLE_WINDOW, SECURITY_STATE_RETENTION)
    uow.identity.lock_throttle_keys((account_key, client_key, GLOBAL_KEY))
    login_failures = uow.identity.failure_count(account_key, now, THROTTLE_WINDOW)
    client_failures = uow.identity.failure_count(client_key, now, THROTTLE_WINDOW)
    global_failures = uow.identity.failure_count(GLOBAL_KEY, now, THROTTLE_WINDOW)
    if (
        login_failures >= LOGIN_FAIL_LIMIT
        or global_failures >= LOGIN_FAIL_LIMIT_GLOBAL
        or client_failures >= CLIENT_FAIL_LIMIT
    ):
        if login_failures >= LOGIN_FAIL_LIMIT:
            limited_key, limited_by = account_key, "login"
            message = "Слишком много попыток. Подождите 10 минут."
        elif global_failures >= LOGIN_FAIL_LIMIT_GLOBAL:
            limited_key, limited_by = GLOBAL_KEY, "global"
            message = "Вход временно закрыт (слишком много неудачных попыток). Подождите 10 минут."
        else:
            limited_key, limited_by = client_key, "client"
            message = "Слишком много попыток входа; попробуйте позже"
        if uow.identity.mark_rate_limit_audited(limited_key, now):
            _audit(
                uow,
                aggregate_id=UUID(int=0),
                event_type="identity.login_rate_limited",
                actor=SYSTEM_ACTOR,
                now=now,
                details={
                    "identifier_hash": secret_hash(identifier),
                    "client_key": client_key,
                    "limited_by": limited_by,
                },
            )
            uow.commit()
        raise AuthenticationRateLimited(message)
    return login_failures, global_failures


def _record_failure(
    uow: UnitOfWork,
    *,
    account_key: str,
    client_key: str,
    identifier: str,
    user_id: UUID | None,
    failures: tuple[int, int],
    now: datetime,
    method: str,
) -> None:
    """portalLoginFail_: both counters, the journal with the attempt number, the owner at a threshold."""
    uow.identity.increment_failure(account_key, now, THROTTLE_WINDOW)
    uow.identity.increment_failure(client_key, now, THROTTLE_WINDOW)
    uow.identity.increment_failure(GLOBAL_KEY, now, THROTTLE_WINDOW)
    attempt = failures[0] + 1
    total = failures[1] + 1
    details = {
        "identifier_hash": secret_hash(identifier),
        "client_key": client_key,
        "attempt": str(attempt),
        "total": str(total),
    }
    if method != "password":
        details["method"] = method
    _audit(
        uow,
        aggregate_id=user_id or UUID(int=0),
        event_type="identity.login_failed",
        actor=SYSTEM_ACTOR,
        now=now,
        details=details,
    )
    # …and exactly at a threshold the owner is told (SUPPLYMAP e-mailed CONFIG.ERROR_ALERT_EMAIL; ERP puts it
    # into the administrators' notification feed).
    if attempt == LOGIN_FAIL_LIMIT or total == LOGIN_FAIL_LIMIT_GLOBAL:
        publish_notification(uow.outbox, notification_requested(
            key=f"login-bruteforce:{secret_hash(identifier)}:{now.isoformat()}",
            now=now,
            title="Подбор пароля при входе",
            body=(
                f"Неудачных входов за 10 мин: по учётной записи «{identifier}» — {attempt}, всего — {total}. "
                "Вход заблокирован до конца окна. Записи — в системном журнале."
            ),
            kind="security",
            roles=("admin",),
        ))
    uow.commit()


def _open_session(
    uow: UnitOfWork, user: IdentityUser, account_key: str, client_key: str, now: datetime, method: str,
) -> LoginResult:
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session = IdentitySession(
        id=uuid4(),
        user_id=user.id,
        token_hash=secret_hash(session_token),
        csrf_hash=secret_hash(csrf_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_TTL,
        absolute_expires_at=now + SESSION_ABSOLUTE_TTL,
    )
    uow.identity.reset_failures(account_key)
    uow.identity.add_session(session)
    # portalMarkSeen_: the first successful sign-in closes the «узнать PIN» door for this employee.
    uow.identity.mark_seen(user.id, now)
    principal = _principal_for(uow, user)
    details = {"client_key": client_key}
    if method != "password":
        details["method"] = method
    _audit(
        uow,
        aggregate_id=user.id,
        event_type="identity.login_succeeded",
        actor=principal.actor,
        now=now,
        details=details,
    )
    uow.commit()
    return LoginResult(session_token=session_token, csrf_token=csrf_token, principal=principal)


def login(
    login: str,
    password: str,
    now: datetime,
    uow: UnitOfWork,
    *,
    client_key: str,
) -> LoginResult:
    now = as_utc(now)
    normalized = normalize_login(login)
    login_key = f"login:{normalized}"
    with uow:
        failures = _rate_limited(uow, login_key, client_key, normalized, now)
        record = uow.identity.credentials_for_login(normalized)
        encoded = record[1] if record is not None and record[0].active else DUMMY_PASSWORD_HASH
        if not verify_password(password, encoded) or record is None or not record[0].active:
            _record_failure(
                uow, account_key=login_key, client_key=client_key, identifier=normalized,
                user_id=record[0].id if record is not None else None, failures=failures, now=now, method="password",
            )
            raise AuthenticationError("Неверный логин или пароль")
        return _open_session(uow, record[0], login_key, client_key, now, "password")


# ── Вход по PIN (Portal.gs portalLogin / portalRevealPin_) ─────────────────────────────────────────────

PIN_BAD = "Неверный сотрудник или PIN."
PIN_NOT_ASSIGNED = "PIN ещё не назначен — обратись к администратору."
PIN_REVEAL_SETTING = "pin_reveal"  # PORTAL_REVEAL_PROP_: '1' — «узнать PIN при первом входе» is on


def login_with_pin(
    who: str,
    pin: str,
    now: datetime,
    uow: UnitOfWork,
    *,
    client_key: str,
) -> LoginResult | PinReveal:
    """portalLogin(who, pin): `who` is the employee picked from the list (their id) or an e-mail / login (the
    fallback path). An empty PIN is not a mistake but «я не знаю свой PIN» — portalRevealPin_. The same answer for an
    unknown employee and a wrong PIN, so the form is not an oracle; both count against the ID-4 limits."""
    now = as_utc(now)
    who = str(who or "").strip()
    pin = str(pin if pin is not None else "")
    with uow:
        record = uow.identity.pin_record(who)
        if record is not None and not record.user.active:
            record = None  # an employee without login rights is not in the directory
        identifier = record.user.login if record is not None else who.casefold()
        account_key = f"login:{identifier}"
        failures = _rate_limited(uow, account_key, client_key, identifier, now)
        if record is None:
            verify_password(pin, DUMMY_PASSWORD_HASH)  # the same time as a real check
            _record_failure(
                uow, account_key=account_key, client_key=client_key, identifier=identifier, user_id=None,
                failures=failures, now=now, method="pin",
            )
            raise AuthenticationError(PIN_BAD)
        if not pin:
            return _reveal_pin(uow, record, now)
        if not record.pin_hash:
            raise PinRefused(PIN_NOT_ASSIGNED)
        if not verify_password(pin, record.pin_hash):
            _record_failure(
                uow, account_key=account_key, client_key=client_key, identifier=identifier,
                user_id=record.user.id, failures=failures, now=now, method="pin",
            )
            raise AuthenticationError(PIN_BAD)
        return _open_session(uow, record.user, account_key, client_key, now, "pin")


def _reveal_pin(uow: UnitOfWork, record: PinRecord, now: datetime) -> PinReveal:
    """portalRevealPin_: the stored PIN is a hash, so «show» means issue a new one, store its hash, show it once.
    Only while the mode is on, never to an administrator, and only until the employee's first sign-in."""
    if uow.identity.setting(PIN_REVEAL_SETTING) != "1":
        raise PinRefused("Введи PIN.")
    # «уже входил» is worded apart, as in SUPPLYMAP; the mode being off and an administrator answer alike.
    if record.seen:
        raise PinRefused("Введи PIN. Пароль ты уже получал — если забыл, обратись к администратору.")
    if record.user.role == "admin":
        raise PinRefused("Введи PIN.")
    pin = new_pin()
    uow.identity.set_pin(record.user.id, hash_pin(pin))
    _audit(
        uow,
        aggregate_id=record.user.id,
        event_type="identity.pin_revealed",  # «Выдан пароль для первого входа»
        actor=SYSTEM_ACTOR,
        now=now,
        details={"name": record.user.name or record.user.email},
    )
    uow.commit()
    return PinReveal(name=record.user.name or record.user.email, pin=pin)


def pin_reveal_enabled(uow: UnitOfWork) -> bool:
    with uow:
        return uow.identity.setting(PIN_REVEAL_SETTING) == "1"


def set_pin_reveal(enabled: bool, uow: UnitOfWork) -> None:
    """pinRevealOn / pinRevealOff (the owner, from the CLI)."""
    with uow:
        uow.identity.set_setting(PIN_REVEAL_SETTING, "1" if enabled else None)
        uow.commit()


def login_staff(uow: UnitOfWork) -> list[IdentityUser]:
    """portalStaffLite_: the login list — active employees by name, no e-mails."""
    with uow:
        users = [user for user in uow.identity.all_users() if user.active]
    return sorted(users, key=lambda user: (user.name.casefold().replace("ё", "е"), user.login))


# ── «Режим админа» (Portal.gs portalToggleAdmin) ──────────────────────────────────────────────────────

ADMIN_MODE_TTL = timedelta(hours=6)  # PORTAL_SESSION_TTL_SEC_: the cache flag lives 6 hours


def toggle_admin_mode(
    session: IdentitySession, principal: Principal, on: bool, now: datetime, uow: UnitOfWork,
) -> Principal:
    """on=True lifts the role of this session to admin, False returns the own one. Only the account
    portalCanAdminMode_ allows; every switch goes to the journal."""
    if not principal.can_admin_mode:
        raise PermissionError("Режим админа недоступен для этого аккаунта.")
    now = as_utc(now)
    with uow:
        user = uow.identity.user_for_update(session.user_id)
        if user is None or not user.active or not can_admin_mode(user.email):
            raise PermissionError("Режим админа недоступен для этого аккаунта.")
        until = now + ADMIN_MODE_TTL if on else None
        if not uow.identity.set_admin_mode(session.id, until):
            raise AuthenticationError("Сессия истекла — войди заново.")
        _audit(
            uow,
            aggregate_id=user.id,
            event_type="identity.admin_mode_on" if on else "identity.admin_mode_off",
            actor=principal.actor,
            now=now,
            details={"event": "Вход в режим админа" if on else "Выход из режима админа"},
        )
        result = _principal_for(uow, user, admin_mode=on)
        uow.commit()
        return result


def resolve_session(token: str, now: datetime, uow: UnitOfWork) -> ResolvedSession | None:
    if not token:
        return None
    now = as_utc(now)
    with uow:
        record = uow.identity.session_with_user(secret_hash(token))
        if record is None:
            return None
        session, user = record
        if (
            session.revoked_at is not None
            or session.expires_at <= now
            or session.absolute_expires_at <= now
            or not user.active
        ):
            if session.revoked_at is None:
                uow.identity.revoke_session(session.token_hash, now)
                uow.commit()
            return None

        principal = _principal_for(uow, user, admin_mode=_session_admin_mode(session, user, now))
        if session.last_seen_at <= now - timedelta(minutes=15):
            session = replace(
                session,
                last_seen_at=now,
                expires_at=min(now + SESSION_TTL, session.absolute_expires_at),
            )
            if not uow.identity.update_session(session):
                return None
            uow.commit()
        return ResolvedSession(session=session, principal=principal)


def csrf_matches(resolved: ResolvedSession, csrf_token: str) -> bool:
    return bool(csrf_token) and secrets.compare_digest(
        resolved.session.csrf_hash,
        secret_hash(csrf_token),
    )


def logout(token: str, actor: Actor, now: datetime, uow: UnitOfWork) -> None:
    if not token:
        return
    now = as_utc(now)
    with uow:
        if uow.identity.revoke_session(secret_hash(token), now):
            _audit(
                uow,
                aggregate_id=actor.id or UUID(int=0),
                event_type="identity.logout",
                actor=actor,
                now=now,
                details={},
            )
            uow.commit()


def deactivate_user(
    user_id: UUID,
    actor: Actor,
    now: datetime,
    uow: UnitOfWork,
) -> IdentityUser:
    from .staff import ensure_admin_remains

    now = as_utc(now)
    with uow:
        ensure_admin_remains(user_id, uow)
        user = uow.identity.deactivate_user(user_id)
        if user is None:
            raise KeyError(f"Identity user not found: {user_id}")
        uow.identity.revoke_user_sessions(user_id, now)
        _audit(
            uow,
            aggregate_id=user_id,
            event_type="identity.user_deactivated",
            actor=actor,
            now=now,
            details={"target_login": user.login},
        )
        uow.commit()
        return user


# ── Own profile and password (SUPPLYMAP portalState_ shows role, department, site) ──────────────────


def own_profile(user_id: UUID, uow: UnitOfWork) -> IdentityUser:
    with uow:
        user = uow.identity.user_for_update(user_id)
        if user is None or not user.active:
            raise KeyError("Сотрудник не найден.")
        return user


def change_own_password(
    user_id: UUID, current: str, new: str, actor: Actor, now: datetime, uow: UnitOfWork,
) -> None:
    """The employee changes their own password: the current one must match. SUPPLYMAP had PINs issued by the
    administrator and no self-service change; ERP passwords are still issued by the administrator (no «узнать пароль
    при первом входе»), and the holder may replace theirs."""
    validate_password(new)
    if current == new:
        raise IdentityValidationError("Новый пароль совпадает с текущим.")
    now = as_utc(now)
    with uow:
        encoded = uow.identity.password_hash(user_id)
        if encoded is None or not verify_password(current, encoded):
            raise AuthenticationError("Текущий пароль указан неверно.")
        uow.identity.set_password(user_id, hash_password(new))
        _audit(
            uow,
            aggregate_id=user_id,
            event_type="identity.own_password_changed",
            actor=actor,
            now=now,
            details={},
        )
        uow.commit()
