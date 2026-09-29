"""SQLAlchemy persistence for editable maps and technology snapshots."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, Text, Uuid, delete, false as sa_false, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.persistence import Base, SqlAlchemyAuditRepository

from .domain import ReferenceEntry, ReferenceKind, TechOperationRecord, TechOperationTab


class ReferenceEntryRow(Base):
    __tablename__ = "reference_entries"
    __table_args__ = (
        Index("ux_reference_entries_kind_key", "kind", "normalized_key", unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(512), nullable=False)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class TechOperationRecordRow(Base):
    __tablename__ = "tech_operation_records"
    __table_args__ = (
        Index("ix_tech_operation_records_tab_sort", "tab", "sort_key"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    tab: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    display_text: Mapped[str] = mapped_column(String(2000), nullable=False)
    normalized_search: Mapped[str] = mapped_column(Text(), nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_key: Mapped[str] = mapped_column(String(2000), nullable=False)
    fields: Mapped[dict] = mapped_column(JSON, nullable=False)
    mirror_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    edited_in_erp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())


class TechOperationSnapshotRow(Base):
    __tablename__ = "tech_operation_snapshots"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)


class SqlAlchemyReferenceRepository:
    def __init__(self, session: Session): self.session = session
    def list(self) -> list[ReferenceEntry]:
        rows = self.session.scalars(select(ReferenceEntryRow).order_by(ReferenceEntryRow.kind, ReferenceEntryRow.position, ReferenceEntryRow.id))
        return [ReferenceEntry(row.id, ReferenceKind(row.kind), row.key, row.normalized_key, row.value, row.position) for row in rows]
    def replace(self, entries: tuple[ReferenceEntry, ...]) -> None:
        self.session.execute(delete(ReferenceEntryRow))
        self.session.add_all(ReferenceEntryRow(
            id=entry.id, kind=entry.kind.value, key=entry.key, normalized_key=entry.normalized_key,
            value=entry.value, position=entry.position,
        ) for entry in entries)


class SqlAlchemyTechOperationRepository:
    def __init__(self, session: Session): self.session = session
    @staticmethod
    def _domain(row: TechOperationRecordRow) -> TechOperationRecord:
        return TechOperationRecord(row.id, TechOperationTab(row.tab), row.display_text, row.normalized_search, row.source_sheet, row.sort_key, row.fields, row.mirror_key, row.edited_in_erp)
    def list(self, *, tab: TechOperationTab | None, query: str, limit: int) -> list[TechOperationRecord]:
        statement = select(TechOperationRecordRow)
        if tab is not None: statement = statement.where(TechOperationRecordRow.tab == tab.value)
        normalized = " ".join(str(query or "").casefold().split())
        if normalized: statement = statement.where(func.lower(TechOperationRecordRow.normalized_search).contains(normalized))
        rows = self.session.scalars(statement.order_by(TechOperationRecordRow.tab, TechOperationRecordRow.sort_key, TechOperationRecordRow.id).limit(limit))
        return [self._domain(row) for row in rows]
    def get(self, record_id: UUID) -> TechOperationRecord | None:
        row = self.session.get(TechOperationRecordRow, record_id)
        return None if row is None else self._domain(row)
    def save(self, record: TechOperationRecord) -> None:
        row = self.session.get(TechOperationRecordRow, record.id)
        if row is None: row = TechOperationRecordRow(id=record.id); self.session.add(row)
        row.tab = record.tab.value; row.display_text = record.display_text
        row.normalized_search = record.normalized_search; row.source_sheet = record.source_sheet
        row.sort_key = record.sort_key; row.fields = dict(record.fields)
        row.mirror_key = record.mirror_key; row.edited_in_erp = record.edited_in_erp
    def replace(self, records: tuple[TechOperationRecord, ...], *, schema_version: int, source_updated_at: datetime | None, imported_at: datetime, keep_tabs: tuple[TechOperationTab, ...] = (), preserve_erp_edits: bool = False) -> dict[str, int]:
        """Replaces the records of every tab not in `keep_tabs`. With `preserve_erp_edits` the rows edited/created in
        ERP stay untouched and a mirror record whose `mirror_key` belongs to an edited row is not inserted again;
        returns {tab: skipped mirror records}."""
        skipped: dict[str, int] = {}
        scope = TechOperationRecordRow.tab.not_in([tab.value for tab in keep_tabs]) if keep_tabs else None
        statement = delete(TechOperationRecordRow)
        if scope is not None: statement = statement.where(scope)
        if preserve_erp_edits:
            statement = statement.where(TechOperationRecordRow.edited_in_erp.is_(False))
            protected = {(row.tab, row.mirror_key) for row in self.session.execute(
                select(TechOperationRecordRow.tab, TechOperationRecordRow.mirror_key).where(TechOperationRecordRow.edited_in_erp.is_(True), TechOperationRecordRow.mirror_key != ""))}
            kept = []
            for record in records:
                if (record.tab.value, record.mirror_key) in protected:
                    skipped[record.tab.value] = skipped.get(record.tab.value, 0) + 1
                else:
                    kept.append(record)
            records = tuple(kept)
        self.session.execute(statement)
        self.session.add_all(TechOperationRecordRow(
            id=record.id, tab=record.tab.value, display_text=record.display_text,
            normalized_search=record.normalized_search, source_sheet=record.source_sheet,
            sort_key=record.sort_key, fields=record.fields, mirror_key=record.mirror_key, edited_in_erp=False,
        ) for record in records)
        meta = self.session.get(TechOperationSnapshotRow, "current")
        if meta is None: meta = TechOperationSnapshotRow(name="current"); self.session.add(meta)
        meta.schema_version = schema_version; meta.source_updated_at = source_updated_at
        meta.imported_at = imported_at
        meta.record_count = len(records)
        self.session.flush()
        if keep_tabs or preserve_erp_edits:  # rows this snapshot did not touch still count
            meta.record_count = self.session.scalar(select(func.count()).select_from(TechOperationRecordRow)) or 0
        return skipped
    def metadata(self) -> dict | None:
        row = self.session.get(TechOperationSnapshotRow, "current")
        if row is None: return None
        return {"schema_version": row.schema_version, "source_updated_at": row.source_updated_at, "imported_at": row.imported_at, "record_count": row.record_count}


class SqlAlchemyReferencesUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory; self.session: Session | None = None; self.committed = False
    def __enter__(self):
        self.committed = False; self.session = self.session_factory()
        self.references = SqlAlchemyReferenceRepository(self.session)
        self.tech_operations = SqlAlchemyTechOperationRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
        return self
    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.session is None: return
        if exc_type is not None or not self.committed: self.session.rollback()
        self.session.close()
    def commit(self) -> None:
        if self.session is None: raise RuntimeError("Unit of Work has not been entered")
        self.session.commit(); self.committed = True
