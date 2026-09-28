"""SQLAlchemy persistence for editable maps and technology snapshots."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, Uuid, delete, func, select
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
        return TechOperationRecord(row.id, TechOperationTab(row.tab), row.display_text, row.normalized_search, row.source_sheet, row.sort_key, row.fields)
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
    def replace(self, records: tuple[TechOperationRecord, ...], *, schema_version: int, source_updated_at: datetime | None, imported_at: datetime, keep_tabs: tuple[TechOperationTab, ...] = ()) -> None:
        statement = delete(TechOperationRecordRow)
        if keep_tabs: statement = statement.where(TechOperationRecordRow.tab.not_in([tab.value for tab in keep_tabs]))
        self.session.execute(statement)
        self.session.add_all(TechOperationRecordRow(
            id=record.id, tab=record.tab.value, display_text=record.display_text,
            normalized_search=record.normalized_search, source_sheet=record.source_sheet,
            sort_key=record.sort_key, fields=record.fields,
        ) for record in records)
        meta = self.session.get(TechOperationSnapshotRow, "current")
        if meta is None: meta = TechOperationSnapshotRow(name="current"); self.session.add(meta)
        meta.schema_version = schema_version; meta.source_updated_at = source_updated_at
        meta.imported_at = imported_at
        meta.record_count = len(records)
        self.session.flush()
        if keep_tabs:  # rows of БД.ПРОВ / БД.МАТ that this snapshot did not touch still count
            meta.record_count = self.session.scalar(select(func.count()).select_from(TechOperationRecordRow)) or 0
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
