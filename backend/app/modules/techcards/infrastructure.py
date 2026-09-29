"""SQLAlchemy persistence for project techcards and immutable revisions."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from app.shared.persistence import Base, SqlAlchemyAuditRepository

from .kitset_infrastructure import SqlAlchemyKitsetRepository
from .project_cards import ProjectTechcard, TechcardRevision, TechcardStatus
from .operation_cards import OperationCardTemplate, OperationRecordEdit, parse_operation_template


class ProjectTechcardRow(Base):
    __tablename__ = "techcard_projects"
    __table_args__ = (
        Index("ux_techcard_projects_project_item", "project", "item_index", unique=True),
        Index("ix_techcard_projects_status_updated", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item_index: Mapped[str] = mapped_column(String(255), nullable=False)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False)
    source_calculation: Mapped[str] = mapped_column(String(512), nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    manual_operation_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class TechcardRevisionRow(Base):
    __tablename__ = "techcard_revisions"
    __table_args__ = (
        Index("ux_techcard_revisions_card_revision", "project_card_id", "revision", unique=True),
        Index("ix_techcard_revisions_card_time", "project_card_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_card_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("techcard_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text(), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)


class OperationCardTemplateRow(Base):
    __tablename__ = "techcard_operation_templates"
    __table_args__ = (Index("ix_techcard_operation_templates_code_side", "code", "side"),)

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    code: Mapped[str] = mapped_column(String(255), nullable=False)
    side: Mapped[str] = mapped_column(String(1), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    raw_rows: Mapped[list] = mapped_column(JSON, nullable=False)
    images: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Правка записи в ERP поверх снимка (SUPPLYMAP БД.ОП-ТК: ИЗМЕНЕНО / КЕМ); переживает замену библиотеки.
    edited: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edited_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OperationCardSnapshotRow(Base):
    __tablename__ = "techcard_operation_template_snapshots"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    template_count: Mapped[int] = mapped_column(Integer, nullable=False)


class SqlAlchemyTechcardRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: ProjectTechcardRow) -> ProjectTechcard:
        return ProjectTechcard(
            id=row.id,
            version=row.version,
            project=row.project,
            item_index=row.item_index,
            product_name=row.product_name,
            source_calculation=row.source_calculation,
            deadline=row.deadline,
            status=TechcardStatus(row.status),
            revision=row.revision,
            document=row.document,
            manual_operation_keys=tuple(row.manual_operation_keys),
            created_at=row.created_at,
            updated_at=row.updated_at,
            issued_at=row.issued_at,
        )

    def get(self, card_id: UUID) -> ProjectTechcard | None:
        row = self.session.get(ProjectTechcardRow, card_id)
        return self._domain(row) if row is not None else None

    def by_key(self, project: str, item_index: str) -> ProjectTechcard | None:
        row = self.session.scalar(
            select(ProjectTechcardRow).where(
                ProjectTechcardRow.project == project,
                ProjectTechcardRow.item_index == item_index,
            )
        )
        return self._domain(row) if row is not None else None

    def list(self) -> list[ProjectTechcard]:
        rows = self.session.scalars(
            select(ProjectTechcardRow).order_by(
                ProjectTechcardRow.deadline.is_(None),
                ProjectTechcardRow.deadline,
                ProjectTechcardRow.project,
                ProjectTechcardRow.item_index,
            )
        )
        return [self._domain(row) for row in rows]

    def save(self, card: ProjectTechcard) -> None:
        row = self.session.get(ProjectTechcardRow, card.id)
        if row is None:
            row = ProjectTechcardRow(id=card.id)
            self.session.add(row)
        for field in (
            "version",
            "project",
            "item_index",
            "product_name",
            "source_calculation",
            "deadline",
            "revision",
            "document",
            "created_at",
            "updated_at",
            "issued_at",
        ):
            setattr(row, field, getattr(card, field))
        row.status = card.status.value
        row.manual_operation_keys = list(card.manual_operation_keys)

    def add_revision(self, revision: TechcardRevision) -> None:
        self.session.add(
            TechcardRevisionRow(
                id=revision.id,
                project_card_id=revision.project_card_id,
                revision=revision.revision,
                reason=revision.reason,
                actor_name=revision.actor_name,
                occurred_at=revision.occurred_at,
                document=revision.document,
            )
        )

    def delete_project(self, project: str) -> tuple[int, int]:
        """`erpProdDropCards_`: the project's cards go together with their revisions. → (cards, revisions)."""
        ids = list(self.session.scalars(select(ProjectTechcardRow.id).where(ProjectTechcardRow.project == project)))
        if not ids:
            return 0, 0
        revisions = self.session.execute(
            delete(TechcardRevisionRow).where(TechcardRevisionRow.project_card_id.in_(ids))
        ).rowcount or 0
        cards = self.session.execute(delete(ProjectTechcardRow).where(ProjectTechcardRow.id.in_(ids))).rowcount or 0
        return cards, revisions

    def revisions(self, card_id: UUID) -> list[TechcardRevision]:
        rows = self.session.scalars(
            select(TechcardRevisionRow)
            .where(TechcardRevisionRow.project_card_id == card_id)
            .order_by(TechcardRevisionRow.revision)
        )
        return [
            TechcardRevision(
                id=row.id,
                project_card_id=row.project_card_id,
                revision=row.revision,
                reason=row.reason,
                actor_name=row.actor_name,
                occurred_at=row.occurred_at,
                document=row.document,
            )
            for row in rows
        ]


class SqlAlchemyOperationCardRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _domain(row: OperationCardTemplateRow) -> OperationCardTemplate:
        card = parse_operation_template(
            source_id=row.id,
            title=row.title,
            category=row.category,
            rows=row.raw_rows,
            images=row.images,
        )
        if row.edited and row.edited_at is not None:
            card = card.with_edit(
                OperationRecordEdit.from_json(row.edited),
                changed_at=row.edited_at, changed_by=row.edited_by or "",
            )
        return card

    def list(self) -> list[OperationCardTemplate]:
        rows = self.session.scalars(
            select(OperationCardTemplateRow).order_by(
                OperationCardTemplateRow.category,
                OperationCardTemplateRow.code,
                OperationCardTemplateRow.side,
                OperationCardTemplateRow.id,
            )
        )
        return [self._domain(row) for row in rows]

    def get(self, source_id: str) -> OperationCardTemplate | None:
        row = self.session.get(OperationCardTemplateRow, source_id)
        return self._domain(row) if row is not None else None

    def edit(self, code: str, edit: OperationRecordEdit, *, changed_at: datetime, changed_by: str) -> int:
        """One record = one operation code (SUPPLYMAP tcRecordsByCode_): the edit lands on every (A)/(B) block of the
        code. Returns how many templates were changed (0 — no such code)."""
        rows = list(self.session.scalars(
            select(OperationCardTemplateRow).where(OperationCardTemplateRow.code == code)
        ))
        for row in rows:
            row.edited = edit.as_json()
            row.edited_at = changed_at
            row.edited_by = changed_by
            row.name = edit.name
            row.document = self._domain(row).document()
        return len(rows)

    def replace(
        self, cards: tuple[OperationCardTemplate, ...], *, source_commit: str, imported_at: datetime,
    ) -> tuple[str, ...]:
        """Replace the snapshot whole, but keep ERP edits: a code edited here keeps its edit over the fresh snapshot
        (SUPPLYMAP tcRecordsSync / tcRecordResyncFromTemplate refuse to overwrite a record with ИЗМЕНЕНО).
        Returns the codes whose edits were kept."""
        kept = {
            row.code: (row.edited, row.edited_at, row.edited_by)
            for row in self.session.scalars(
                select(OperationCardTemplateRow).where(OperationCardTemplateRow.edited_at.is_not(None))
            )
        }
        self.session.execute(delete(OperationCardTemplateRow))
        self.session.add_all(OperationCardTemplateRow(
            id=card.source_id,
            code=card.code,
            side=card.side,
            title=card.title,
            name=card.name,
            category=card.category,
            document=card.document(),
            raw_rows=[list(row) for row in card.raw_rows],
            images=[
                {
                    "row": image.row, "col": image.col, "driveFileId": image.drive_file_id,
                    "width": image.width, "height": image.height,
                }
                for image in card.images
            ],
            source_commit=source_commit,
            imported_at=imported_at,
            edited=kept[card.code][0] if card.code in kept else None,
            edited_at=kept[card.code][1] if card.code in kept else None,
            edited_by=kept[card.code][2] if card.code in kept else None,
        ) for card in cards)
        snapshot = self.session.get(OperationCardSnapshotRow, "current")
        if snapshot is None:
            snapshot = OperationCardSnapshotRow(name="current")
            self.session.add(snapshot)
        snapshot.source_commit = source_commit
        snapshot.imported_at = imported_at
        snapshot.template_count = len(cards)
        return tuple(sorted(code for code in kept if any(card.code == code for card in cards)))

    def metadata(self) -> dict | None:
        row = self.session.get(OperationCardSnapshotRow, "current")
        if row is None:
            return None
        return {
            "source_commit": row.source_commit,
            "imported_at": row.imported_at,
            "template_count": row.template_count,
        }


class SqlAlchemyTechcardsUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self):
        self.committed = False
        self.session = self.session_factory()
        self.techcards = SqlAlchemyTechcardRepository(self.session)
        self.operation_cards = SqlAlchemyOperationCardRepository(self.session)
        self.kitsets = SqlAlchemyKitsetRepository(self.session)
        self.audit = SqlAlchemyAuditRepository(self.session)
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
