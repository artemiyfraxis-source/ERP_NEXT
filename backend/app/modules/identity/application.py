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

from .domain import (
    AccessMode,
    IdentityUser,
    effective_permissions,
    hash_password,
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


class UnitOfWork(Protocol):
    identity: IdentityRepository
    audit: object

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type, exc, traceback) -> bool | None: ...
    def commit(self) -> None: ...


SESSION_TTL = timedelta(days=14)
SESSION_ABSOLUTE_TTL = timedelta(days=30)
THROTTLE_WINDOW = timedelta(minutes=10)
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
    role_access: dict[str, AccessMode],
    user_access: dict[str, AccessMode],
) -> Principal:
    effective = effective_permissions(role_access, user_access)
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
        uow.identity.lock_throttle_keys((login_key, client_key))
        login_failures = uow.identity.failure_count(login_key, now, THROTTLE_WINDOW)
        client_failures = uow.identity.failure_count(client_key, now, THROTTLE_WINDOW)
        if login_failures >= 10 or client_failures >= 300:
            limited_key = client_key if client_failures >= 300 else login_key
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
                        "limited_by": "client" if limited_key == client_key else "login",
                    },
                )
                uow.commit()
            raise AuthenticationRateLimited("Слишком много попыток входа; попробуйте позже")

        record = uow.identity.credentials_for_login(normalized)
        encoded = record[1] if record is not None and record[0].active else DUMMY_PASSWORD_HASH
        if not verify_password(password, encoded) or record is None or not record[0].active:
            uow.identity.increment_failure(login_key, now, THROTTLE_WINDOW)
            uow.identity.increment_failure(client_key, now, THROTTLE_WINDOW)
            _audit(
                uow,
                aggregate_id=record[0].id if record is not None else UUID(int=0),
                event_type="identity.login_failed",
                actor=SYSTEM_ACTOR,
                now=now,
                details={
                    "identifier_hash": secret_hash(normalized),
                    "client_key": client_key,
                },
            )
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
    now = as_utc(now)
    with uow:
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
