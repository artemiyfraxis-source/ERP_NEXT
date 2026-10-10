"""Persistence of the harness drawing history (infrastructure): `techcard_harness_revisions`, migration 0090.

The drawing itself is the КД register row of its position (`kitset_infrastructure.KitDocumentRow`, current bytes);
each save appends one revision here with the same bytes. The database holds the rules: a revision belongs to an
existing КД row (FK, RESTRICT — a drawing with history is never deleted), its numbers are positive and unique per
drawing (two saves made on the same revision cannot both land — the second gets HarnessWriteConflict → 409), the hash
is a full sha256, a restore names an earlier row.

The history is thinned (`harness.application.save`): an autosave folded into its author's open «saved» row rewrites
that row's bytes, `revision` and `saved_at` — only that row, only from the revision it holds (`fold`); every other row
never changes again. Nothing is deleted."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint, Uuid, false, func,
    select, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base

from . import kitset_infrastructure as _kitsets  # noqa: F401  (the FK target in the same metadata)
from .harness.schema import REVISION_KINDS, HarnessRevision, HarnessWriteConflict, content_sha256

_KINDS = ", ".join(f"'{kind}'" for kind in REVISION_KINDS)


class HarnessRevisionRow(Base):
    __tablename__ = "techcard_harness_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision", name="uq_techcard_harness_revisions_document_revision"),
        UniqueConstraint("document_id", "first_revision", name="uq_techcard_harness_revisions_document_first"),
        CheckConstraint("revision > 0", name="ck_techcard_harness_revisions_revision_positive"),
        CheckConstraint("first_revision > 0 AND first_revision <= revision",
                        name="ck_techcard_harness_revisions_first_revision"),
        CheckConstraint("size >= 0", name="ck_techcard_harness_revisions_size"),
        CheckConstraint("length(content_sha256) = 64", name="ck_techcard_harness_revisions_sha256"),
        CheckConstraint(f"kind IN ({_KINDS})", name="ck_techcard_harness_revisions_kind"),
        CheckConstraint(
            "restored_from IS NULL OR (kind = 'restored' AND restored_from > 0 AND restored_from < first_revision)",
            name="ck_techcard_harness_revisions_restored_from",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("techcard_kitset_documents.id", ondelete="RESTRICT",
                   name="fk_techcard_harness_revisions_document_id"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    restored_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The revision this row began at and when; `revision` / `saved_at` — the last save folded into it.
    first_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # «Сохранить версию»: no later save folds into a closed row.
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    saved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _revision(row: HarnessRevisionRow) -> HarnessRevision:
    return HarnessRevision(
        document_id=row.document_id, revision=row.revision, content_sha256=row.content_sha256, size=row.size,
        saved_by=row.saved_by, saved_at=row.saved_at, kind=row.kind, restored_from=row.restored_from,
        first_revision=row.first_revision, started_at=row.started_at, closed=row.closed,
    )


class SqlAlchemyHarnessRevisionRepository:
    def __init__(self, session: Session):
        self.session = session

    def latest(self, document_id: UUID) -> HarnessRevision | None:
        row = self.session.scalar(
            select(HarnessRevisionRow).where(HarnessRevisionRow.document_id == document_id)
            .order_by(HarnessRevisionRow.revision.desc()).limit(1)
        )
        return None if row is None else _revision(row)

    def list(self, document_id: UUID, *, limit: int) -> list[HarnessRevision]:
        rows = self.session.scalars(
            select(HarnessRevisionRow).where(HarnessRevisionRow.document_id == document_id)
            .order_by(HarnessRevisionRow.revision.desc()).limit(limit)
        )
        return [_revision(row) for row in rows]

    def count(self, document_id: UUID) -> int:
        return self.session.scalar(
            select(func.count()).select_from(HarnessRevisionRow).where(HarnessRevisionRow.document_id == document_id)
        ) or 0

    def get(self, document_id: UUID, revision: int) -> tuple[HarnessRevision, bytes] | None:
        row = self.session.scalar(
            select(HarnessRevisionRow).where(
                HarnessRevisionRow.document_id == document_id, HarnessRevisionRow.revision == revision,
            )
        )
        return None if row is None else (_revision(row), row.content)

    def append(self, document_id: UUID, revision: int, content: bytes, *, kind: str, saved_by: str,
               saved_at: datetime, restored_from: int | None = None, closed: bool = False) -> HarnessRevision:
        """Writes at once, so a revision number taken by a concurrent save — or a position linked by a concurrent first
        save (migration 0089) — fails here as HarnessWriteConflict. A КД row added in this transaction goes first (no
        ORM relationship orders the two inserts)."""
        try:
            self.session.flush()
        except IntegrityError as error:
            raise HarnessWriteConflict(str(error.orig)) from error
        row = HarnessRevisionRow(
            id=uuid4(), document_id=document_id, revision=revision, content=content,
            content_sha256=content_sha256(content), size=len(content), kind=kind, restored_from=restored_from,
            first_revision=revision, closed=closed, saved_by=saved_by, started_at=saved_at, saved_at=saved_at,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as error:
            raise HarnessWriteConflict(str(error.orig)) from error
        return _revision(row)

    def fold(self, document_id: UUID, held: int, revision: int, content: bytes, *, saved_at: datetime) -> bool:
        """An autosave into the open «saved» row holding revision `held`: its bytes, number and time move on. False —
        the row moved on or was closed meanwhile (a concurrent save): the caller reports a conflict."""
        try:
            result = self.session.execute(
                update(HarnessRevisionRow)
                .where(HarnessRevisionRow.document_id == document_id, HarnessRevisionRow.revision == held,
                       HarnessRevisionRow.kind == "saved", HarnessRevisionRow.closed.is_(False))
                .values(revision=revision, content=content, content_sha256=content_sha256(content), size=len(content),
                        saved_at=saved_at)
                .execution_options(synchronize_session=False)
            )
        except IntegrityError as error:
            raise HarnessWriteConflict(str(error.orig)) from error
        return bool(result.rowcount)

    def close(self, document_id: UUID, revision: int) -> bool:
        """«Сохранить версию»: the row holding `revision` takes no more autosaves. False — no such open row."""
        result = self.session.execute(
            update(HarnessRevisionRow)
            .where(HarnessRevisionRow.document_id == document_id, HarnessRevisionRow.revision == revision,
                   HarnessRevisionRow.closed.is_(False))
            .values(closed=True)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)
