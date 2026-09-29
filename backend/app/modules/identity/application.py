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
    AccessMode,
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


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    csrf_token: str
    principal: Principal


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
        self, user: IdentityUser, password_hash: str, role_access: dict[str, AccessMode]
    ) -> None: ...
    def update_user(self, user: IdentityUser) -> None: ...
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
) -> Principal:
    effective = effective_permissions(role_permissions(user.role, role_access), user_access)
    return Principal(
        actor=Actor(id=user.id, name=user.name or user.email, department=user.department),
        email=user.email,
        roles=frozenset({user.role}),
        permissions={section: mode.value for section, mode in effective.items()},
    )


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
        uow.identity.cleanup_security_state(
            now,
            THROTTLE_WINDOW,
            SECURITY_STATE_RETENTION,
        )
        uow.identity.lock_throttle_keys((login_key, client_key, GLOBAL_KEY))
        login_failures = uow.identity.failure_count(login_key, now, THROTTLE_WINDOW)
        client_failures = uow.identity.failure_count(client_key, now, THROTTLE_WINDOW)
        global_failures = uow.identity.failure_count(GLOBAL_KEY, now, THROTTLE_WINDOW)
        if (
            login_failures >= LOGIN_FAIL_LIMIT
            or global_failures >= LOGIN_FAIL_LIMIT_GLOBAL
            or client_failures >= CLIENT_FAIL_LIMIT
        ):
            if login_failures >= LOGIN_FAIL_LIMIT:
                limited_key, limited_by = login_key, "login"
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
                        "identifier_hash": secret_hash(normalized),
                        "client_key": client_key,
                        "limited_by": limited_by,
                    },
                )
                uow.commit()
            raise AuthenticationRateLimited(message)

        record = uow.identity.credentials_for_login(normalized)
        encoded = record[1] if record is not None and record[0].active else DUMMY_PASSWORD_HASH
        if not verify_password(password, encoded) or record is None or not record[0].active:
            uow.identity.increment_failure(login_key, now, THROTTLE_WINDOW)
            uow.identity.increment_failure(client_key, now, THROTTLE_WINDOW)
            uow.identity.increment_failure(GLOBAL_KEY, now, THROTTLE_WINDOW)
            attempt = login_failures + 1
            total = global_failures + 1
            # portalLoginFail_: every failure goes to the journal with its attempt number.
            _audit(
                uow,
                aggregate_id=record[0].id if record is not None else UUID(int=0),
                event_type="identity.login_failed",
                actor=SYSTEM_ACTOR,
                now=now,
                details={
                    "identifier_hash": secret_hash(normalized),
                    "client_key": client_key,
                    "attempt": str(attempt),
                    "total": str(total),
                },
            )
            # …and exactly at a threshold the owner is told (SUPPLYMAP e-mailed CONFIG.ERROR_ALERT_EMAIL; ERP puts it
            # into the administrators' notification feed).
            if attempt == LOGIN_FAIL_LIMIT or total == LOGIN_FAIL_LIMIT_GLOBAL:
                publish_notification(uow.outbox, notification_requested(
                    key=f"login-bruteforce:{secret_hash(normalized)}:{now.isoformat()}",
                    now=now,
                    title="Подбор пароля при входе",
                    body=(
                        f"Неудачных входов за 10 мин: по учётной записи «{normalized}» — {attempt}, всего — {total}. "
                        "Вход заблокирован до конца окна. Записи — в системном журнале."
                    ),
                    kind="security",
                    roles=("admin",),
                ))
            uow.commit()
            raise AuthenticationError("Неверный логин или пароль")

        user = record[0]
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
        uow.identity.reset_failures(login_key)
        uow.identity.add_session(session)
        principal = _principal(
            user,
            uow.identity.role_access(user.role),
            uow.identity.user_access(user.id),
        )
        _audit(
            uow,
            aggregate_id=user.id,
            event_type="identity.login_succeeded",
            actor=principal.actor,
            now=now,
            details={"client_key": client_key},
        )
        uow.commit()
        return LoginResult(
            session_token=session_token,
            csrf_token=csrf_token,
            principal=principal,
        )


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

        principal = _principal(
            user,
            uow.identity.role_access(user.role),
            uow.identity.user_access(user.id),
        )
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
