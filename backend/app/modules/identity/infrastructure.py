"""SQLAlchemy adapters for users, access matrices, and server sessions."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.persistence import Base
from app.shared.persistence import SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository
from app.shared.clock import as_utc

from .application import IdentitySession, PinRecord
from .domain import ROLE_SEED, AccessMode, IdentityUser, normalize_login


class IdentityUserRow(Base):
    __tablename__ = "identity_users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    login: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    site: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # portal_seen_<email> (Portal.gs portalMarkSeen_): the first successful sign-in; closes «узнать PIN».
    seen_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))


class IdentityCredentialRow(Base):
    __tablename__ = "identity_credentials"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    # portal_pin_<email>: the PIN (6–8 digits) as an Argon2id hash; empty — no PIN assigned.
    pin_hash: Mapped[str | None] = mapped_column(String(512))


class IdentityIdentifierRow(Base):
    __tablename__ = "identity_identifiers"

    identifier: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class IdentityRoleAccessRow(Base):
    __tablename__ = "identity_role_access"

    role: Mapped[str] = mapped_column(String(64), primary_key=True)
    section: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)


class IdentityUserAccessRow(Base):
    __tablename__ = "identity_user_access"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    section: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)


class IdentitySessionRow(Base):
    __tablename__ = "identity_sessions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    absolute_expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), index=True)
    # portal_admin_<token>: «Режим админа» of this session is on until this moment.
    admin_mode_until: Mapped[Any | None] = mapped_column(DateTime(timezone=True))


class IdentityDepartmentRow(Base):
    """The department list of the «Сотрудники» screen (ID-8), in dropdown order. Empty until the first save:
    then the list is the departments people already have, as SUPPLYMAP staffDepts_ did."""

    __tablename__ = "identity_departments"

    position: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class IdentitySettingRow(Base):
    """Identity switches the owner flips at run time (SUPPLYMAP ScriptProperties), e.g. `pin_reveal`."""

    __tablename__ = "identity_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)


class IdentityLoginThrottleRow(Base):
    __tablename__ = "identity_login_throttles"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    window_started_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit_audited_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))


def _user_domain(row: IdentityUserRow) -> IdentityUser:
    return IdentityUser(
        id=row.id,
        login=row.login,
        email=row.email,
        name=row.name,
        department=row.department,
        role=row.role,
        site=row.site,
        active=row.active,
    )


def _session_domain(row: IdentitySessionRow) -> IdentitySession:
    return IdentitySession(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        csrf_hash=row.csrf_hash,
        created_at=as_utc(row.created_at),
        last_seen_at=as_utc(row.last_seen_at),
        expires_at=as_utc(row.expires_at),
        absolute_expires_at=as_utc(row.absolute_expires_at),
        revoked_at=as_utc(row.revoked_at) if row.revoked_at is not None else None,
        admin_mode_until=as_utc(row.admin_mode_until) if row.admin_mode_until is not None else None,
    )


class SqlAlchemyIdentityRepository:
    def __init__(self, session: Session):
        self.session = session

    def credentials_for_login(self, login: str) -> tuple[IdentityUser, str] | None:
        normalized = normalize_login(login)
        identifier = self.session.get(IdentityIdentifierRow, normalized)
        if identifier is None:
            return None
        user_row = self.session.get(IdentityUserRow, identifier.user_id)
        if user_row is None:
            return None
        credential = self.session.get(IdentityCredentialRow, user_row.id)
        if credential is None:
            return None
        return _user_domain(user_row), credential.password_hash

    def role_access(self, role: str) -> dict[str, AccessMode]:
        rows = self.session.scalars(
            select(IdentityRoleAccessRow).where(IdentityRoleAccessRow.role == role)
        )
        return {row.section: AccessMode(row.mode) for row in rows}

    def user_access(self, user_id: UUID) -> dict[str, AccessMode]:
        rows = self.session.scalars(
            select(IdentityUserAccessRow).where(IdentityUserAccessRow.user_id == user_id)
        )
        return {row.section: AccessMode(row.mode) for row in rows}

    def add_session(self, session: IdentitySession) -> None:
        self.session.add(
            IdentitySessionRow(
                id=session.id,
                user_id=session.user_id,
                token_hash=session.token_hash,
                csrf_hash=session.csrf_hash,
                created_at=session.created_at,
                last_seen_at=session.last_seen_at,
                expires_at=session.expires_at,
                absolute_expires_at=session.absolute_expires_at,
                revoked_at=session.revoked_at,
            )
        )

    def session_with_user(
        self, token_hash: str
    ) -> tuple[IdentitySession, IdentityUser] | None:
        session_row = self.session.scalar(
            select(IdentitySessionRow).where(IdentitySessionRow.token_hash == token_hash)
        )
        if session_row is None:
            return None
        user_row = self.session.get(IdentityUserRow, session_row.user_id)
        if user_row is None:
            return None
        return _session_domain(session_row), _user_domain(user_row)

    def update_session(self, session: IdentitySession) -> bool:
        result = self.session.execute(
            update(IdentitySessionRow)
            .where(
                IdentitySessionRow.id == session.id,
                IdentitySessionRow.revoked_at.is_(None),
                IdentitySessionRow.expires_at > session.last_seen_at,
                IdentitySessionRow.absolute_expires_at > session.last_seen_at,
            )
            .values(
                last_seen_at=session.last_seen_at,
                expires_at=session.expires_at,
            )
        )
        return result.rowcount == 1

    def revoke_session(self, token_hash: str, revoked_at) -> bool:
        row = self.session.scalar(
            select(IdentitySessionRow).where(IdentitySessionRow.token_hash == token_hash)
        )
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = revoked_at
        return True

    def _throttle(self, key: str) -> IdentityLoginThrottleRow | None:
        return self.session.get(IdentityLoginThrottleRow, key, with_for_update=True)

    def lock_throttle_keys(self, keys: tuple[str, ...]) -> None:
        if self.session.bind is None or self.session.bind.dialect.name != "postgresql":
            return
        for key in sorted(set(keys)):
            lock_id = int.from_bytes(
                hashlib.sha256(key.encode("utf-8")).digest()[:8],
                byteorder="big",
                signed=True,
            )
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )

    def cleanup_security_state(
        self,
        now,
        throttle_window: timedelta,
        session_retention: timedelta,
    ) -> None:
        self.session.execute(
            delete(IdentityLoginThrottleRow).where(
                IdentityLoginThrottleRow.window_started_at <= now - throttle_window
            )
        )
        cutoff = now - session_retention
        self.session.execute(
            delete(IdentitySessionRow).where(
                (IdentitySessionRow.absolute_expires_at <= cutoff)
                | (IdentitySessionRow.revoked_at <= cutoff)
            )
        )

    def failure_count(self, key: str, now, window: timedelta) -> int:
        row = self._throttle(key)
        if row is None or as_utc(row.window_started_at) <= as_utc(now) - window:
            return 0
        return row.failures

    def increment_failure(self, key: str, now, window: timedelta) -> None:
        row = self._throttle(key)
        if row is None:
            self.session.add(
                IdentityLoginThrottleRow(key=key, window_started_at=now, failures=1)
            )
            return
        if as_utc(row.window_started_at) <= as_utc(now) - window:
            row.window_started_at = now
            row.failures = 1
            return
        row.failures += 1

    def mark_rate_limit_audited(self, key: str, now) -> bool:
        row = self._throttle(key)
        if row is None or row.rate_limit_audited_at is not None:
            return False
        row.rate_limit_audited_at = now
        return True

    def reset_failures(self, key: str) -> None:
        row = self._throttle(key)
        if row is not None:
            self.session.delete(row)

    def deactivate_user(self, user_id: UUID) -> IdentityUser | None:
        row = self.session.get(IdentityUserRow, user_id, with_for_update=True)
        if row is None:
            return None
        row.active = False
        return _user_domain(row)

    def revoke_user_sessions(self, user_id: UUID, revoked_at) -> int:
        result = self.session.execute(
            update(IdentitySessionRow)
            .where(
                IdentitySessionRow.user_id == user_id,
                IdentitySessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        return result.rowcount

    # ── «Сотрудники» (ID-8) ──

    def all_users(self) -> list[IdentityUser]:
        return [_user_domain(row) for row in self.session.scalars(select(IdentityUserRow))]

    def user_for_update(self, user_id: UUID) -> IdentityUser | None:
        row = self.session.get(IdentityUserRow, user_id, with_for_update=True)
        return _user_domain(row) if row is not None else None

    def identifiers_taken(self, identifiers: set[str]) -> bool:
        return self.session.scalar(
            select(IdentityIdentifierRow.identifier)
            .where(IdentityIdentifierRow.identifier.in_(identifiers))
            .limit(1)
        ) is not None

    def add_user(
        self, user: IdentityUser, password_hash: str, role_access: dict[str, AccessMode], pin_hash: str | None = None,
    ) -> None:
        provision_user(self.session, user, password_hash, role_access=role_access, pin_hash=pin_hash)

    def update_user(self, user: IdentityUser) -> None:
        row = self.session.get(IdentityUserRow, user.id)
        if row is None:
            raise KeyError(f"Identity user not found: {user.id}")
        row.name = user.name
        row.department = user.department
        row.role = user.role
        row.site = user.site
        row.active = user.active

    def password_hash(self, user_id: UUID) -> str | None:
        row = self.session.get(IdentityCredentialRow, user_id, with_for_update=True)
        return None if row is None else row.password_hash

    def set_password(self, user_id: UUID, password_hash: str) -> None:
        row = self.session.get(IdentityCredentialRow, user_id)
        if row is None:
            self.session.add(IdentityCredentialRow(user_id=user_id, password_hash=password_hash))
        else:
            row.password_hash = password_hash

    def active_admin_ids(self) -> list[UUID]:
        # Locks the admin rows, so two admins cannot demote each other at the same time.
        return list(self.session.scalars(
            select(IdentityUserRow.id)
            .where(IdentityUserRow.role == "admin", IdentityUserRow.active.is_(True))
            .with_for_update()
        ))

    def saved_departments(self) -> list[str]:
        return list(self.session.scalars(
            select(IdentityDepartmentRow.name).order_by(IdentityDepartmentRow.position)
        ))

    def replace_departments(self, departments: list[str]) -> None:
        self.session.execute(delete(IdentityDepartmentRow))
        self.session.add_all(
            IdentityDepartmentRow(position=position, name=name)
            for position, name in enumerate(departments)
        )

    # ── «Доступы» (NAV-ACCESS) ──

    def all_role_access(self) -> dict[str, dict[str, AccessMode]]:
        result: dict[str, dict[str, AccessMode]] = {}
        for row in self.session.scalars(select(IdentityRoleAccessRow)):
            result.setdefault(row.role, {})[row.section] = AccessMode(row.mode)
        return result

    def all_user_access(self) -> dict[UUID, dict[str, AccessMode]]:
        result: dict[UUID, dict[str, AccessMode]] = {}
        for row in self.session.scalars(select(IdentityUserAccessRow)):
            result.setdefault(row.user_id, {})[row.section] = AccessMode(row.mode)
        return result

    def replace_role_access(self, role: str, overrides: dict[str, AccessMode]) -> None:
        self.session.execute(delete(IdentityRoleAccessRow).where(IdentityRoleAccessRow.role == role))
        self.session.add_all(
            IdentityRoleAccessRow(role=role, section=section, mode=mode.value)
            for section, mode in overrides.items()
        )

    def replace_user_access(self, user_id: UUID, overrides: dict[str, AccessMode]) -> None:
        self.session.execute(delete(IdentityUserAccessRow).where(IdentityUserAccessRow.user_id == user_id))
        self.session.add_all(
            IdentityUserAccessRow(user_id=user_id, section=section, mode=mode.value)
            for section, mode in overrides.items()
        )


    # ── PIN and «Режим админа» ──

    def pin_record(self, who: str) -> PinRecord | None:
        """`who` = the employee id from the login list, or an e-mail / login (portalLoginEmail_)."""
        value = str(who or "").strip()
        if not value:
            return None
        user_row = None
        try:
            user_row = self.session.get(IdentityUserRow, UUID(value))
        except ValueError:
            identifier = self.session.get(IdentityIdentifierRow, value.casefold())
            if identifier is not None:
                user_row = self.session.get(IdentityUserRow, identifier.user_id)
        if user_row is None:
            return None
        credential = self.session.get(IdentityCredentialRow, user_row.id)
        return PinRecord(
            user=_user_domain(user_row),
            pin_hash=credential.pin_hash if credential is not None else None,
            seen=user_row.seen_at is not None,
        )

    def set_pin(self, user_id: UUID, pin_hash: str) -> None:
        row = self.session.get(IdentityCredentialRow, user_id)
        if row is None:
            raise KeyError(f"Identity user not found: {user_id}")
        row.pin_hash = pin_hash

    def pin_set_ids(self) -> set[UUID]:
        return set(self.session.scalars(
            select(IdentityCredentialRow.user_id).where(IdentityCredentialRow.pin_hash.is_not(None))
        ))

    def mark_seen(self, user_id: UUID, now) -> None:
        row = self.session.get(IdentityUserRow, user_id)
        if row is not None and row.seen_at is None:
            row.seen_at = now

    def setting(self, key: str) -> str | None:
        row = self.session.get(IdentitySettingRow, key)
        return None if row is None else row.value

    def set_setting(self, key: str, value: str | None) -> None:
        row = self.session.get(IdentitySettingRow, key)
        if value is None:
            if row is not None:
                self.session.delete(row)
        elif row is None:
            self.session.add(IdentitySettingRow(key=key, value=value))
        else:
            row.value = value

    def set_admin_mode(self, session_id: UUID, until) -> bool:
        result = self.session.execute(
            update(IdentitySessionRow)
            .where(IdentitySessionRow.id == session_id, IdentitySessionRow.revoked_at.is_(None))
            .values(admin_mode_until=until)
        )
        return result.rowcount == 1


class SqlAlchemyIdentityUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self) -> SqlAlchemyIdentityUnitOfWork:
        self.committed = False
        self.session = self.session_factory()
        self.identity = SqlAlchemyIdentityRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        self.outbox = SqlAlchemyOutboxRepository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None:
            return
        if exc_type is not None or not self.committed:
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of Work has not been entered")
        self.session.commit()
        self.committed = True


def seed_role_matrix(session: Session) -> None:
    """Every role's ROLE_SEED as explicit matrix cells, where nothing is stored yet — what migration 0056 did, for a
    database built without migrations (tests, fixtures). Existing cells stay."""
    for role, row in ROLE_SEED.items():
        _seed_role_access(session, role, row)


def _seed_role_access(session: Session, role: str, defaults: dict[str, AccessMode]) -> None:
    """Explicit matrix rows for tests and fixtures; existing rows stay."""
    for section, mode in defaults.items():
        if session.get(IdentityRoleAccessRow, (role, section)) is None:
            session.add(IdentityRoleAccessRow(role=role, section=section, mode=mode.value))


def provision_user(
    session: Session,
    user: IdentityUser,
    password_hash: str,
    *,
    role_access: dict[str, AccessMode] | None = None,
    user_access: dict[str, AccessMode] | None = None,
    pin_hash: str | None = None,
) -> None:
    login = normalize_login(user.login)
    email = normalize_login(user.email)
    session.add(
        IdentityUserRow(
            id=user.id,
            login=login,
            email=email,
            name=user.name.strip(),
            department=user.department.strip(),
            role=user.role.strip(),
            site=user.site,
            active=user.active,
        )
    )
    for identifier in {login, email}:
        session.add(IdentityIdentifierRow(identifier=identifier, user_id=user.id))
    session.add(IdentityCredentialRow(user_id=user.id, password_hash=password_hash, pin_hash=pin_hash))
    _seed_role_access(session, user.role.strip(), role_access or {})
    for section, mode in (user_access or {}).items():
        session.add(IdentityUserAccessRow(user_id=user.id, section=section, mode=mode.value))
