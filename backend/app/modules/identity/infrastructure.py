"""SQLAlchemy adapters for users, access matrices, and server sessions."""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    delete,
    false,
    func,
    select,
    text,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.persistence import Base
from app.shared.persistence import SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository
from app.shared.clock import as_utc

from .application import ApiToken, IdentitySession, PinRecord
from .domain import FULL_ACCESS_LEVELS, LEVEL_SEED, AccessMode, IdentityUser, normalize_login
from .documents import Acknowledgement, Document, DocumentKind, DocumentScope, DocumentVersion
from .leave import LeaveConflict, LeaveKind, LeaveRequest, LeaveSignature, LeaveStatus, signature_order
from .level_migration import followed_flags, level_for_role, shop_worker_rule, site_master_rule


# A row inserted without a level (a fixture, an old caller that knows only the role) takes it from its role, and the
# flags from role / site / department.
def _default_level(context) -> str:
    return level_for_role(context.get_current_parameters().get("role"))


def _default_shop_worker(context) -> bool:
    params = context.get_current_parameters()
    return shop_worker_rule(params.get("role"), params.get("site"), params.get("department"))


def _default_site_master(context) -> bool:
    return site_master_rule(context.get_current_parameters().get("role"))


class IdentityUserRow(Base):
    __tablename__ = "identity_users"
    __table_args__ = (
        # Табельный номер is unique when set; NULL (not filled) may repeat.
        Index("ux_identity_users_personnel_number", "personnel_number", unique=True),
    )

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
    # «Личная карточка» (0085): кадровые поля.
    personnel_number: Mapped[str | None] = mapped_column(String(32))
    job_title: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    qualification: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    job_since: Mapped[date | None] = mapped_column(Date)
    manager_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="SET NULL", name="fk_identity_users_manager_id"),
        index=True,
    )
    # Уровень доступа и два флага (0086, docs/migration/access-levels-plan.md); `legacy_role` = the role at the
    # transfer, kept one release.
    access_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True, default=_default_level)
    shop_worker: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=_default_shop_worker, server_default=false(),
    )
    site_master: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=_default_site_master, server_default=false(),
    )
    legacy_role: Mapped[str | None] = mapped_column(String(64))


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


class IdentityLevelAccessRow(Base):
    """The level matrix (0086): level × section, an absent cell is closed. The superadmin/admin rows are edit everywhere;
    those levels are always full whatever is stored."""

    __tablename__ = "identity_level_access"

    level: Mapped[str] = mapped_column(String(16), primary_key=True)
    section: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)


class IdentityUserLevelAccessRow(Base):
    """Personal exceptions on top of the person's level row (0086), `none` closes; ignored for superadmin/admin.
    `identity_user_access` keeps the role-era exceptions (legacy, not read by the gates)."""

    __tablename__ = "identity_user_level_access"

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


class IdentityApiTokenRow(Base):
    """Токен API (0088): a named service credential with scopes. Only the sha256 of the secret is stored."""

    __tablename__ = "identity_api_tokens"
    __table_args__ = (Index("ux_identity_api_tokens_token_hash", "token_hash", unique=True),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list] = mapped_column(JSON, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="SET NULL", name="fk_identity_api_tokens_created_by"),
    )
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="SET NULL", name="fk_identity_api_tokens_revoked_by"),
    )
    revoked_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))


class IdentityLeaveRequestRow(Base):
    """Заявление на отпуск / отгул (0089, identity/leave.py). `version` is the row version every change checks."""

    __tablename__ = "identity_leave_requests"
    __table_args__ = (
        CheckConstraint("kind IN ('vacation', 'day_off')", name="ck_identity_leave_requests_kind"),
        CheckConstraint(
            "status IN ('draft', 'signed', 'approved', 'registered', 'rejected', 'withdrawn')",
            name="ck_identity_leave_requests_status",
        ),
        CheckConstraint("to_date >= from_date", name="ck_identity_leave_requests_period"),
        CheckConstraint("content_version >= 1", name="ck_identity_leave_requests_content_version"),
        CheckConstraint("version >= 1", name="ck_identity_leave_requests_version"),
        Index("ix_identity_leave_requests_employee_id", "employee_id"),
        Index("ix_identity_leave_requests_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    employee_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_identity_leave_requests_employee_id"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class IdentityLeaveSignatureRow(Base):
    """A signature on a leave request step (0089): who, when, how (PIN / password typed again — never stored)."""

    __tablename__ = "identity_leave_signatures"
    __table_args__ = (
        CheckConstraint("step IN ('employee', 'manager', 'hr', 'withdraw')", name="ck_identity_leave_signatures_step"),
        CheckConstraint(
            "decision IN ('signed', 'approved', 'rejected', 'registered', 'withdrawn')",
            name="ck_identity_leave_signatures_decision",
        ),
        CheckConstraint("method IN ('pin', 'password')", name="ck_identity_leave_signatures_method"),
        CheckConstraint("content_version >= 1", name="ck_identity_leave_signatures_content_version"),
        UniqueConstraint(
            "request_id", "step", "content_version", name="uq_identity_leave_signatures_request_step_version",
        ),
        Index("ix_identity_leave_signatures_signer_id", "signer_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_leave_requests.id", ondelete="CASCADE", name="fk_identity_leave_signatures_request_id"),
        nullable=False,
    )
    step: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    signer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_identity_leave_signatures_signer_id"),
        nullable=False,
    )
    signer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signed_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


# A SHA-256 in lowercase hex; trim(x, chars) is btrim on PostgreSQL and the two-argument trim on SQLite.
_HEX_SHA256 = "length({column}) = 64 AND trim({column}, '0123456789abcdef') = ''"


class IdentityDocumentRow(Base):
    """Документ с ознакомлением (0100, identity/documents.py): what it is and whom it is aimed at."""

    __tablename__ = "identity_documents"
    __table_args__ = (
        CheckConstraint("kind IN ('job_description', 'regulation', 'other')", name="ck_identity_documents_kind"),
        CheckConstraint("scope IN ('all', 'department', 'job_title')", name="ck_identity_documents_scope"),
        CheckConstraint(
            "(scope = 'all' AND scope_value = '') OR (scope <> 'all' AND scope_value <> '')",
            name="ck_identity_documents_scope_value",
        ),
        CheckConstraint("title <> ''", name="ck_identity_documents_title"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_value: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_identity_documents_created_by"),
        nullable=False,
    )
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityDocumentVersionRow(Base):
    """A version (редакция) of a document: the PDF itself, kept in the database like procurement's files."""

    __tablename__ = "identity_document_versions"
    __table_args__ = (
        CheckConstraint("version_no >= 1", name="ck_identity_document_versions_version_no"),
        CheckConstraint("size > 0 AND size <= 20971520", name="ck_identity_document_versions_size"),
        CheckConstraint(_HEX_SHA256.format(column="sha256"), name="ck_identity_document_versions_sha256"),
        UniqueConstraint("document_id", "version_no", name="uq_identity_document_versions_document_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_documents.id", ondelete="RESTRICT", name="fk_identity_document_versions_document_id"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    uploaded_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_identity_document_versions_uploaded_by"),
        nullable=False,
    )
    uploaded_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityDocumentAckRow(Base):
    """«Ознакомлен»: one signature per person and version (PIN / password typed again — never stored)."""

    __tablename__ = "identity_document_acks"
    __table_args__ = (
        CheckConstraint("method IN ('pin', 'password')", name="ck_identity_document_acks_method"),
        CheckConstraint(_HEX_SHA256.format(column="sha256"), name="ck_identity_document_acks_sha256"),
        UniqueConstraint("version_id", "user_id", name="uq_identity_document_acks_version_user"),
        Index("ix_identity_document_acks_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_document_versions.id", ondelete="RESTRICT", name="fk_identity_document_acks_version_id"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_identity_document_acks_user_id"),
        nullable=False,
    )
    signed_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class IdentityDepartmentRow(Base):
    """The department list of the «Сотрудники» screen (ID-8), in dropdown order. Empty until the first save:
    then the list is the departments people already have, as SUPPLYMAP staffDepts_ did."""

    __tablename__ = "identity_departments"

    position: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Руководитель отдела (0085), optional.
    head_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="SET NULL", name="fk_identity_departments_head_user_id"),
    )


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


# Identity tables that are history all the same: a leave request and its signatures (0089), the documents, their
# versions and «Ознакомлен» signatures (0100) — all RESTRICT to the person.
_IDENTITY_HISTORY_TABLES = frozenset({
    "identity_leave_requests", "identity_leave_signatures",
    "identity_documents", "identity_document_versions", "identity_document_acks",
})
# Audit rows the employee leaves just by signing in; they stay in the log but do not make the employee «used».
_OWN_SESSION_EVENTS = (
    "identity.login_succeeded", "identity.logout", "identity.own_password_changed", "identity.pin_revealed",
    "identity.admin_mode_off",
)
_PERSON_COLUMN = re.compile(
    r"(^|_)(actor|operator|inspector|user|recipient|requester|buyer|initiator|manager|author|assignee|employee|worker)_id$"
)


def _references_user(column) -> list:
    return [key for key in column.foreign_keys if key.column.table.name == IdentityUserRow.__tablename__]


def _cascades_from_user(column) -> bool:
    return any((key.ondelete or "").upper() == "CASCADE" for key in _references_user(column))


def _points_at_person(column) -> bool:
    return bool(_references_user(column)) or (isinstance(column.type, Uuid) and bool(_PERSON_COLUMN.search(column.name)))


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
        personnel_number=row.personnel_number or "",
        job_title=row.job_title or "",
        qualification=row.qualification or "",
        job_since=row.job_since,
        manager_id=row.manager_id,
        access_level=row.access_level or "",
        shop_worker=bool(row.shop_worker),
        site_master=bool(row.site_master),
    )


def _write_personnel(row: IdentityUserRow, user: IdentityUser) -> None:
    row.personnel_number = user.personnel_number or None  # NULL, not "", so the unique index lets blanks repeat
    row.job_title = user.job_title
    row.qualification = user.qualification
    row.job_since = user.job_since
    row.manager_id = user.manager_id


def _follow_role(row: IdentityUserRow, user: IdentityUser, flags_as_given: bool) -> None:
    """The level is what the gates read: written as the user carries it; a user without one (an old caller that knows
    only the role) gets it from the legacy role when that changes. The two flags: `flags_as_given` — the caller decided
    them itself («Сотрудники», T3) and they are written as carried; otherwise they follow the role, site and department
    unless set against the rule by hand (`followed_flags`)."""
    if user.access_level:
        row.access_level = user.access_level
    elif row.role != user.role:
        row.access_level = level_for_role(user.role)
    if flags_as_given:
        row.shop_worker, row.site_master = user.shop_worker, user.site_master
    else:
        row.shop_worker, row.site_master = followed_flags(row, user.role, user.site, user.department)


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

    def level_access(self, level: str) -> dict[str, AccessMode]:
        rows = self.session.scalars(select(IdentityLevelAccessRow).where(IdentityLevelAccessRow.level == level))
        return {row.section: AccessMode(row.mode) for row in rows}

    def user_level_access(self, user_id: UUID) -> dict[str, AccessMode]:
        rows = self.session.scalars(
            select(IdentityUserLevelAccessRow).where(IdentityUserLevelAccessRow.user_id == user_id)
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

    def release_throttles(self, keys: tuple[str, ...] | None) -> int:
        statement = delete(IdentityLoginThrottleRow)
        if keys is not None:
            statement = statement.where(IdentityLoginThrottleRow.key.in_(keys))
        return int(self.session.execute(statement).rowcount or 0)

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

    def user_history(self, user_id: UUID) -> int:
        """Rows anywhere in the database that record what the employee did or was given: a RESTRICT foreign key to
        the user, or a UUID column named like a person (actor_id, operator_id, buyer_id, manager_id …). The employee's own
        settings (CASCADE: credentials, sessions, access, skills, absences) and their own sign-ins are not history."""
        total = 0
        for table in Base.metadata.sorted_tables:
            if table.name.startswith("identity_") and table.name not in _IDENTITY_HISTORY_TABLES:
                continue
            for column in table.columns:
                if _cascades_from_user(column) or not _points_at_person(column):
                    continue
                query = select(func.count()).select_from(table).where(column == user_id)
                if table.name == "audit_events":
                    query = query.where(table.c.event_type.not_in(_OWN_SESSION_EVENTS))
                total += self.session.scalar(query) or 0
        return total

    def delete_user(self, user_id: UUID) -> None:
        """Erases an employee without history. Dependent CASCADE rows go first, explicitly: SQLite (tests, preview)
        does not enforce foreign keys, so ON DELETE CASCADE alone would leave orphans there."""
        for table in reversed(Base.metadata.sorted_tables):
            for column in table.columns:
                if _cascades_from_user(column):
                    self.session.execute(table.delete().where(column == user_id))
        # ON DELETE SET NULL, done by hand for the same reason: subordinates and departments lose this person as head.
        self.session.execute(
            update(IdentityUserRow).where(IdentityUserRow.manager_id == user_id).values(manager_id=None)
        )
        self.session.execute(
            update(IdentityDepartmentRow).where(IdentityDepartmentRow.head_user_id == user_id).values(head_user_id=None)
        )
        # API tokens keep their row; a token whose creator is gone stops working (resolve_api_token).
        for column in (IdentityApiTokenRow.created_by, IdentityApiTokenRow.revoked_by):
            self.session.execute(update(IdentityApiTokenRow).where(column == user_id).values({column.key: None}))
        self.session.execute(delete(IdentityUserRow).where(IdentityUserRow.id == user_id))

    def update_user(self, user: IdentityUser, *, flags_as_given: bool = False) -> None:
        row = self.session.get(IdentityUserRow, user.id)
        if row is None:
            raise KeyError(f"Identity user not found: {user.id}")
        _follow_role(row, user, flags_as_given)
        row.name = user.name
        row.department = user.department
        row.role = user.role
        row.site = user.site
        row.active = user.active
        _write_personnel(row, user)

    def personnel_number_owner(self, number: str) -> UUID | None:
        return self.session.scalar(
            select(IdentityUserRow.id).where(IdentityUserRow.personnel_number == number).limit(1)
        )

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
        # Active superadmins and admins. Locks their rows, so two administrators cannot demote each other at once.
        return list(self.session.scalars(
            select(IdentityUserRow.id)
            .where(IdentityUserRow.access_level.in_(FULL_ACCESS_LEVELS), IdentityUserRow.active.is_(True))
            .with_for_update()
        ))

    def saved_departments(self) -> list[str]:
        return list(self.session.scalars(
            select(IdentityDepartmentRow.name).order_by(IdentityDepartmentRow.position)
        ))

    def department_heads(self) -> dict[str, UUID]:
        """Saved department name → its head, for the departments that have one."""
        return {
            row.name: row.head_user_id
            for row in self.session.scalars(
                select(IdentityDepartmentRow).where(IdentityDepartmentRow.head_user_id.is_not(None))
            )
        }

    def replace_departments(self, departments: list[str], heads: dict[str, UUID] | None = None) -> None:
        """The list in dropdown order. `heads` (name → head, matched case-insensitively) replaces the heads; without
        it every department keeps the head it had under the same name."""
        kept = self.department_heads() if heads is None else heads
        by_name = {name.casefold(): head for name, head in kept.items()}
        self.session.execute(delete(IdentityDepartmentRow))
        self.session.add_all(
            IdentityDepartmentRow(position=position, name=name, head_user_id=by_name.get(name.casefold()))
            for position, name in enumerate(departments)
        )

    # ── «Доступы» (NAV-ACCESS): the level matrix and the personal level exceptions ──

    def all_level_access(self) -> dict[str, dict[str, AccessMode]]:
        result: dict[str, dict[str, AccessMode]] = {}
        for row in self.session.scalars(select(IdentityLevelAccessRow)):
            result.setdefault(row.level, {})[row.section] = AccessMode(row.mode)
        return result

    def all_user_level_access(self) -> dict[UUID, dict[str, AccessMode]]:
        result: dict[UUID, dict[str, AccessMode]] = {}
        for row in self.session.scalars(select(IdentityUserLevelAccessRow)):
            result.setdefault(row.user_id, {})[row.section] = AccessMode(row.mode)
        return result

    def replace_level_access(self, level: str, cells: dict[str, AccessMode]) -> None:
        self.session.execute(delete(IdentityLevelAccessRow).where(IdentityLevelAccessRow.level == level))
        self.session.add_all(
            IdentityLevelAccessRow(level=level, section=section, mode=mode.value) for section, mode in cells.items()
        )

    def replace_user_level_access(self, user_id: UUID, overrides: dict[str, AccessMode]) -> None:
        self.session.execute(
            delete(IdentityUserLevelAccessRow).where(IdentityUserLevelAccessRow.user_id == user_id)
        )
        self.session.add_all(
            IdentityUserLevelAccessRow(user_id=user_id, section=section, mode=mode.value)
            for section, mode in overrides.items()
        )

    # The role tables (`identity_role_access`, `identity_user_access`) are legacy: nothing reads or writes them at run
    # time any more; the «было / стало» report reads them over SQL.

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

    # ── Токены API ──

    def _user_names(self, ids: set[UUID]) -> dict[UUID, str]:
        if not ids:
            return {}
        rows = self.session.execute(
            select(IdentityUserRow.id, IdentityUserRow.name, IdentityUserRow.email).where(IdentityUserRow.id.in_(ids))
        )
        return {row.id: row.name or row.email for row in rows}

    def _api_token(self, row: IdentityApiTokenRow, names: dict[UUID, str]) -> ApiToken:
        return ApiToken(
            id=row.id,
            name=row.name,
            token_hash=row.token_hash,
            scopes=frozenset(str(scope) for scope in row.scopes or ()),
            created_by=row.created_by,
            created_at=as_utc(row.created_at),
            expires_at=as_utc(row.expires_at),
            last_used_at=as_utc(row.last_used_at) if row.last_used_at is not None else None,
            revoked_by=row.revoked_by,
            revoked_at=as_utc(row.revoked_at) if row.revoked_at is not None else None,
            created_by_name=names.get(row.created_by, "") if row.created_by else "",
            revoked_by_name=names.get(row.revoked_by, "") if row.revoked_by else "",
        )

    def add_api_token(self, token: ApiToken) -> None:
        self.session.add(
            IdentityApiTokenRow(
                id=token.id,
                name=token.name,
                token_hash=token.token_hash,
                scopes=sorted(token.scopes),
                created_by=token.created_by,
                created_at=token.created_at,
                expires_at=token.expires_at,
            )
        )

    def api_tokens(self) -> list[ApiToken]:
        rows = list(self.session.scalars(select(IdentityApiTokenRow)))
        names = self._user_names({uid for row in rows for uid in (row.created_by, row.revoked_by) if uid})
        return [self._api_token(row, names) for row in rows]

    def api_token_for_update(self, token_id: UUID) -> ApiToken | None:
        row = self.session.get(IdentityApiTokenRow, token_id, with_for_update=True)
        if row is None:
            return None
        return self._api_token(row, self._user_names({uid for uid in (row.created_by, row.revoked_by) if uid}))

    def revoke_api_token(self, token_id: UUID, revoked_by: UUID, revoked_at) -> bool:
        result = self.session.execute(
            update(IdentityApiTokenRow)
            .where(IdentityApiTokenRow.id == token_id, IdentityApiTokenRow.revoked_at.is_(None))
            .values(revoked_by=revoked_by, revoked_at=revoked_at)
        )
        return result.rowcount == 1

    def api_token_by_hash(self, token_hash: str) -> tuple[ApiToken, bool] | None:
        row = self.session.scalar(select(IdentityApiTokenRow).where(IdentityApiTokenRow.token_hash == token_hash))
        if row is None:
            return None
        creator = self.session.get(IdentityUserRow, row.created_by) if row.created_by else None
        return self._api_token(row, {}), bool(creator is not None and creator.active)

    def touch_api_token(self, token_id: UUID, now) -> None:
        self.session.execute(
            update(IdentityApiTokenRow).where(IdentityApiTokenRow.id == token_id).values(last_used_at=now)
        )


def _leave_domain(row: IdentityLeaveRequestRow, signatures: list[IdentityLeaveSignatureRow]) -> LeaveRequest:
    return LeaveRequest(
        id=row.id, employee_id=row.employee_id, kind=LeaveKind(row.kind), from_date=row.from_date,
        to_date=row.to_date, comment=row.comment or "", status=LeaveStatus(row.status),
        content_version=row.content_version, content_sha256=row.content_sha256, version=row.version,
        created_at=as_utc(row.created_at), updated_at=as_utc(row.updated_at),
        signatures=tuple(sorted((LeaveSignature(
            id=item.id, request_id=item.request_id, step=item.step, decision=item.decision, signer_id=item.signer_id,
            signer_name=item.signer_name, signed_at=as_utc(item.signed_at), method=item.method,
            content_version=item.content_version, content_sha256=item.content_sha256, comment=item.comment or "",
        ) for item in signatures), key=signature_order)),
    )


class SqlAlchemyLeaveRepository:
    """Leave requests (identity/leave.py). `save` writes over the row only from the version the caller saw: the
    mapper's version column makes a concurrent change fail the flush (StaleDataError → conflict)."""

    def __init__(self, session: Session):
        self.session = session

    def _many(self, rows: list[IdentityLeaveRequestRow]) -> list[LeaveRequest]:
        ids = [row.id for row in rows]
        signatures: dict[UUID, list[IdentityLeaveSignatureRow]] = {}
        if ids:
            for item in self.session.scalars(
                select(IdentityLeaveSignatureRow).where(IdentityLeaveSignatureRow.request_id.in_(ids))
                .order_by(IdentityLeaveSignatureRow.signed_at, IdentityLeaveSignatureRow.id)
            ):
                signatures.setdefault(item.request_id, []).append(item)
        return [_leave_domain(row, signatures.get(row.id, [])) for row in rows]

    def get(self, request_id: UUID) -> LeaveRequest | None:
        row = self.session.get(IdentityLeaveRequestRow, request_id, with_for_update=True)
        return None if row is None else self._many([row])[0]

    def for_employee(self, employee_id: UUID) -> list[LeaveRequest]:
        rows = list(self.session.scalars(
            select(IdentityLeaveRequestRow).where(IdentityLeaveRequestRow.employee_id == employee_id)
            .order_by(IdentityLeaveRequestRow.from_date, IdentityLeaveRequestRow.id)
        ))
        return self._many(rows)

    def with_status(self, statuses) -> list[LeaveRequest]:
        values = sorted(LeaveStatus(status).value for status in statuses)
        rows = list(self.session.scalars(
            select(IdentityLeaveRequestRow).where(IdentityLeaveRequestRow.status.in_(values))
            .order_by(IdentityLeaveRequestRow.from_date, IdentityLeaveRequestRow.id)
        ))
        return self._many(rows)

    def add(self, request: LeaveRequest) -> None:
        self.session.add(IdentityLeaveRequestRow(
            id=request.id, employee_id=request.employee_id, kind=request.kind.value, from_date=request.from_date,
            to_date=request.to_date, comment=request.comment, status=request.status.value,
            content_version=request.content_version, content_sha256=request.content_sha256, version=request.version,
            created_at=request.created_at, updated_at=request.updated_at,
        ))
        self.session.flush()

    def save(self, request: LeaveRequest, expected_version: int) -> None:
        row = self.session.get(IdentityLeaveRequestRow, request.id)
        if row is None or row.version != expected_version or request.version != expected_version + 1:
            raise LeaveConflict("Заявление уже изменилось — обновите страницу.")
        row.kind = request.kind.value
        row.from_date = request.from_date
        row.to_date = request.to_date
        row.comment = request.comment
        row.status = request.status.value
        row.content_version = request.content_version
        row.content_sha256 = request.content_sha256
        row.updated_at = request.updated_at
        row.version = request.version
        self.session.flush()

    def add_signature(self, signature: LeaveSignature) -> None:
        self.session.add(IdentityLeaveSignatureRow(
            id=signature.id, request_id=signature.request_id, step=signature.step, decision=signature.decision,
            signer_id=signature.signer_id, signer_name=signature.signer_name, signed_at=signature.signed_at,
            method=signature.method, content_version=signature.content_version,
            content_sha256=signature.content_sha256, comment=signature.comment,
        ))
        self.session.flush()


def _document_version(row: IdentityDocumentVersionRow) -> DocumentVersion:
    return DocumentVersion(
        id=row.id, document_id=row.document_id, version_no=row.version_no, file_name=row.file_name, size=row.size,
        sha256=row.sha256, uploaded_by=row.uploaded_by, uploaded_at=as_utc(row.uploaded_at),
    )


def _ack(row: IdentityDocumentAckRow) -> Acknowledgement:
    return Acknowledgement(
        id=row.id, version_id=row.version_id, user_id=row.user_id, signed_at=as_utc(row.signed_at), method=row.method,
        sha256=row.sha256,
    )


class SqlAlchemyDocumentRepository:
    """Documents with acknowledgement (identity/documents.py). The PDF bytes load only through `content`."""

    def __init__(self, session: Session):
        self.session = session

    def _many(self, rows: list[IdentityDocumentRow]) -> list[Document]:
        ids = [row.id for row in rows]
        versions: dict[UUID, list[DocumentVersion]] = {}
        if ids:
            for item in self.session.scalars(
                select(IdentityDocumentVersionRow).where(IdentityDocumentVersionRow.document_id.in_(ids))
                .order_by(IdentityDocumentVersionRow.version_no)
            ):
                versions.setdefault(item.document_id, []).append(_document_version(item))
        return [Document(
            id=row.id, title=row.title, kind=DocumentKind(row.kind), scope=DocumentScope(row.scope),
            scope_value=row.scope_value or "", archived=bool(row.archived), created_by=row.created_by,
            created_at=as_utc(row.created_at), versions=tuple(versions.get(row.id, ())),
        ) for row in rows]

    def all(self) -> list[Document]:
        return self._many(list(self.session.scalars(
            select(IdentityDocumentRow).order_by(IdentityDocumentRow.created_at, IdentityDocumentRow.id)
        )))

    def get(self, document_id: UUID, *, for_update: bool = False) -> Document | None:
        row = self.session.get(IdentityDocumentRow, document_id, with_for_update=for_update or None)
        return None if row is None else self._many([row])[0]

    def add(self, document: Document) -> None:
        self.session.add(IdentityDocumentRow(
            id=document.id, title=document.title, kind=document.kind.value, scope=document.scope.value,
            scope_value=document.scope_value, archived=document.archived, created_by=document.created_by,
            created_at=document.created_at,
        ))
        self.session.flush()

    def set_archived(self, document_id: UUID, archived: bool) -> None:
        self.session.execute(
            update(IdentityDocumentRow).where(IdentityDocumentRow.id == document_id).values(archived=archived)
        )

    def add_version(self, version: DocumentVersion, content: bytes) -> None:
        self.session.add(IdentityDocumentVersionRow(
            id=version.id, document_id=version.document_id, version_no=version.version_no, file_name=version.file_name,
            size=version.size, sha256=version.sha256, content=content, uploaded_by=version.uploaded_by,
            uploaded_at=version.uploaded_at,
        ))
        self.session.flush()

    def version(self, version_id: UUID) -> DocumentVersion | None:
        row = self.session.get(IdentityDocumentVersionRow, version_id)
        return None if row is None else _document_version(row)

    def content(self, version_id: UUID) -> bytes | None:
        return self.session.scalar(
            select(IdentityDocumentVersionRow.content).where(IdentityDocumentVersionRow.id == version_id)
        )

    def delete_version(self, version_id: UUID) -> None:
        """The caller has checked nobody signed it; the FK (RESTRICT) refuses otherwise. The only version takes the
        document with it."""
        row = self.session.get(IdentityDocumentVersionRow, version_id)
        if row is None:
            return
        document_id = row.document_id
        self.session.delete(row)
        self.session.flush()
        left = self.session.scalar(
            select(func.count()).select_from(IdentityDocumentVersionRow)
            .where(IdentityDocumentVersionRow.document_id == document_id)
        )
        if not left:
            self.session.execute(delete(IdentityDocumentRow).where(IdentityDocumentRow.id == document_id))

    def acks_of_versions(self, version_ids) -> list[Acknowledgement]:
        ids = list(version_ids)
        if not ids:
            return []
        return [_ack(row) for row in self.session.scalars(
            select(IdentityDocumentAckRow).where(IdentityDocumentAckRow.version_id.in_(ids))
            .order_by(IdentityDocumentAckRow.signed_at, IdentityDocumentAckRow.id)
        )]

    def acks_of_user(self, user_id: UUID) -> list[Acknowledgement]:
        return [_ack(row) for row in self.session.scalars(
            select(IdentityDocumentAckRow).where(IdentityDocumentAckRow.user_id == user_id)
            .order_by(IdentityDocumentAckRow.signed_at, IdentityDocumentAckRow.id)
        )]

    def add_ack(self, ack: Acknowledgement) -> None:
        self.session.add(IdentityDocumentAckRow(
            id=ack.id, version_id=ack.version_id, user_id=ack.user_id, signed_at=ack.signed_at, method=ack.method,
            sha256=ack.sha256,
        ))
        self.session.flush()


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
        self.leave = SqlAlchemyLeaveRepository(self.session)
        self.documents = SqlAlchemyDocumentRepository(self.session)
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


def seed_level_matrix(session: Session, rows: dict[str, dict[str, AccessMode]] | None = None) -> None:
    """The level matrix of a database built without migrations (tests, fixtures): LEVEL_SEED (or `rows`) for every
    level that has no stored cell yet — what migration 0086 writes for a level nobody holds. superadmin/admin need none:
    they are full whatever is stored."""
    session.flush()
    stored = set(session.scalars(select(IdentityLevelAccessRow.level).distinct()))
    for level, row in (LEVEL_SEED if rows is None else rows).items():
        if level in stored or level in FULL_ACCESS_LEVELS:
            continue
        session.add_all(
            IdentityLevelAccessRow(level=level, section=section, mode=mode.value) for section, mode in row.items()
        )


def _seed_role_access(session: Session, role: str, defaults: dict[str, AccessMode]) -> None:
    """Legacy role-matrix rows for fixtures of the role-era data; existing rows stay."""
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
    level_access: dict[str, AccessMode] | None = None,
) -> None:
    """A user row with its login, e-mail and credential. `level_access` = the person's level exceptions (what the gates
    read); `role_access` / `user_access` write the legacy role tables (fixtures of the role-era data)."""
    login = normalize_login(user.login)
    email = normalize_login(user.email)
    # A user that carries its level writes it; otherwise the column defaults take it from the role.
    level = (
        {"access_level": user.access_level, "shop_worker": user.shop_worker, "site_master": user.site_master}
        if user.access_level else {}
    )
    row = IdentityUserRow(
        id=user.id,
        login=login,
        email=email,
        name=user.name.strip(),
        department=user.department.strip(),
        role=user.role.strip(),
        site=user.site,
        active=user.active,
        **level,
    )
    _write_personnel(row, user)
    session.add(row)
    # The dependent rows below reference the user by FK only in the schema (no ORM relationship), so the unit
    # of work cannot order them: PostgreSQL checks the FK per statement and refuses them before the user.
    session.flush()
    for identifier in {login, email}:
        session.add(IdentityIdentifierRow(identifier=identifier, user_id=user.id))
    session.add(IdentityCredentialRow(user_id=user.id, password_hash=password_hash, pin_hash=pin_hash))
    _seed_role_access(session, user.role.strip(), role_access or {})
    for section, mode in (user_access or {}).items():
        session.add(IdentityUserAccessRow(user_id=user.id, section=section, mode=mode.value))
    for section, mode in (level_access or {}).items():
        session.add(IdentityUserLevelAccessRow(user_id=user.id, section=section, mode=mode.value))
