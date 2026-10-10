"""SQLAlchemy persistence for project techcards and immutable revisions."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, Uuid, delete, or_, select
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker, validates

from app.shared.persistence import Base, SqlAlchemyAuditRepository
from app.shared.project_number import normalize_project_number

from .harness_infrastructure import SqlAlchemyHarnessComponentRepository, SqlAlchemyHarnessRevisionRepository
from .kitset_infrastructure import SqlAlchemyKitsetRepository
from .project_cards import OperationSketch, ProjectTechcard, TechcardRevision, TechcardStatus
from .operation_cards import OperationCardTemplate, OperationRecordEdit, parse_operation_template
from .template_sheets import TemplateDraft


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
    # № проекта для сравнения (`normalize_project_number` ≡ `kitsets.project_key`): карты проекта — по индексу.
    # Ставится при записи `project`; NULL — строка мимо ORM (читается и сверяется в Python).
    project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    @validates("project")
    def _keep_project_key(self, _name: str, value: str) -> str:
        self.project_key = normalize_project_number(value)
        return value


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
    # Шаблон-лист, сохранённый в ERP из черновика (SUPPLYMAP _TC_LIBRARY: описание, updatedAt).
    description: Mapped[str] = mapped_column(Text(), nullable=False, default="", server_default="")
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    saved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OperationCardDraftRow(Base):
    """Черновик шаблона-листа (SUPPLYMAP лист «Черновик — <название>» в файле-хранилище)."""

    __tablename__ = "techcard_operation_template_drafts"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(600), nullable=False, unique=True)
    existing_template_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    rows: Mapped[list] = mapped_column(JSON, nullable=False)
    images: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperationCardSnapshotRow(Base):
    __tablename__ = "techcard_operation_template_snapshots"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    template_count: Mapped[int] = mapped_column(Integer, nullable=False)


class OperationSketchRow(Base):
    """A picture the technologist attached to one ETK operation (project card + operation key)."""

    __tablename__ = "techcard_operation_sketches"
    __table_args__ = (
        Index("ux_techcard_operation_sketches_card_key", "project_card_id", "operation_key", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_card_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("techcard_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operation_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    # The card lists sketches without the bytes; they are read only when the picture is shown.
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _sketch(row: OperationSketchRow) -> OperationSketch:
    return OperationSketch(
        operation_key=row.operation_key, file_name=row.file_name, content_type=row.content_type,
        size=row.size, uploaded_by=row.uploaded_by, uploaded_at=row.uploaded_at,
    )


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

    def for_project(self, project: str) -> list[ProjectTechcard]:
        """Карты одного проекта точно по ключу (по индексу `project_key`), в порядке `list()`."""
        key = normalize_project_number(project)
        if not key:
            return []
        rows = self.session.scalars(
            select(ProjectTechcardRow)
            .where(or_(ProjectTechcardRow.project_key == key, ProjectTechcardRow.project_key.is_(None)))
            .order_by(
                ProjectTechcardRow.deadline.is_(None),
                ProjectTechcardRow.deadline,
                ProjectTechcardRow.project,
                ProjectTechcardRow.item_index,
            )
        )
        return [self._domain(row) for row in rows if normalize_project_number(row.project) == key]

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
        self.session.execute(delete(OperationSketchRow).where(OperationSketchRow.project_card_id.in_(ids)))
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

    def sketches(self, card_id: UUID) -> list[OperationSketch]:
        rows = self.session.scalars(
            select(OperationSketchRow).where(OperationSketchRow.project_card_id == card_id)
            .order_by(OperationSketchRow.operation_key)
        )
        return [_sketch(row) for row in rows]

    def sketch_content(self, card_id: UUID, operation_key: str) -> tuple[OperationSketch, bytes] | None:
        row = self._sketch_row(card_id, operation_key)
        return (_sketch(row), row.content) if row is not None else None

    def put_sketch(self, card_id: UUID, sketch: OperationSketch, content: bytes) -> None:
        """One picture per operation: a new upload replaces the old one."""
        row = self._sketch_row(card_id, sketch.operation_key)
        if row is None:
            row = OperationSketchRow(id=uuid4(), project_card_id=card_id, operation_key=sketch.operation_key)
            self.session.add(row)
        row.file_name, row.content_type, row.size = sketch.file_name, sketch.content_type, sketch.size
        row.content, row.uploaded_by, row.uploaded_at = content, sketch.uploaded_by, sketch.uploaded_at

    def delete_sketch(self, card_id: UUID, operation_key: str) -> int:
        return self.session.execute(delete(OperationSketchRow).where(
            OperationSketchRow.project_card_id == card_id, OperationSketchRow.operation_key == operation_key,
        )).rowcount or 0

    def _sketch_row(self, card_id: UUID, operation_key: str) -> OperationSketchRow | None:
        return self.session.scalar(select(OperationSketchRow).where(
            OperationSketchRow.project_card_id == card_id, OperationSketchRow.operation_key == operation_key,
        ))


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

    def edited_records(self) -> list[str]:
        """SUPPLYMAP tcRecordsSync report.editedInPortal: «КОД (ИЗМЕНЕНО, КЕМ)» for every record edited in the portal,
        one per operation code."""
        rows = self.session.scalars(
            select(OperationCardTemplateRow).where(OperationCardTemplateRow.edited_at.is_not(None))
            .order_by(OperationCardTemplateRow.code, OperationCardTemplateRow.side, OperationCardTemplateRow.id)
        )
        found: dict[str, str] = {}
        for row in rows:
            found.setdefault(row.code, f"{row.code} ({row.edited_at.strftime('%d.%m.%Y %H:%M')}, {row.edited_by or ''})")
        return list(found.values())

    def replace(
        self, cards: tuple[OperationCardTemplate, ...], *, source_commit: str, imported_at: datetime,
    ) -> None:
        """Replace the snapshot whole (SUPPLYMAP tcRecordsSync rewrites БД.ОП-ТК with an empty ИЗМЕНЕНО): edits made
        in ERP are dropped. Refusing when there are edits is the application's job."""
        # Шаблон-лист, сохранённый в ERP из черновика, снимок не затирает: ERP-версия остаётся.
        saved = set(self.session.scalars(
            select(OperationCardTemplateRow.id).where(OperationCardTemplateRow.saved_at.is_not(None))
        ))
        self.session.execute(delete(OperationCardTemplateRow).where(OperationCardTemplateRow.saved_at.is_(None)))
        self.session.flush()
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
        ) for card in cards if card.source_id not in saved)
        snapshot = self.session.get(OperationCardSnapshotRow, "current")
        if snapshot is None:
            snapshot = OperationCardSnapshotRow(name="current")
            self.session.add(snapshot)
        snapshot.source_commit = source_commit
        snapshot.imported_at = imported_at
        snapshot.template_count = len(cards)

    # ── Шаблоны-листы: каталог и черновики (SUPPLYMAP TcTemplates.gs) ──

    def sheets(self) -> list[OperationCardTemplateRow]:
        """Каталог шаблонов-листов (SUPPLYMAP readCatalog_ → tcTemplateCatalogForPortal)."""
        return list(self.session.scalars(select(OperationCardTemplateRow).order_by(
            OperationCardTemplateRow.category, OperationCardTemplateRow.title, OperationCardTemplateRow.id,
        )))

    def sheet(self, source_id: str) -> OperationCardTemplateRow | None:
        return self.session.get(OperationCardTemplateRow, source_id)

    def save_sheet(
        self, card: OperationCardTemplate, *, description: str, saved_at: datetime, saved_by: str,
    ) -> bool:
        """SUPPLYMAP upsertCatalogRecord_ + writeRangeToStore_: блок и строка каталога пишутся целиком.
        Правка записи (ИЗМЕНЕНО) шаблона не трогается — пересохранение листа её не сбрасывает.
        Returns True when the template was created."""
        row = self.session.get(OperationCardTemplateRow, card.source_id)
        created = row is None
        if row is None:
            snapshot = self.session.get(OperationCardSnapshotRow, "current")
            sibling = self.session.scalars(select(OperationCardTemplateRow).where(
                OperationCardTemplateRow.code == card.code, OperationCardTemplateRow.edited_at.is_not(None),
            )).first()
            row = OperationCardTemplateRow(
                id=card.source_id,
                source_commit=snapshot.source_commit if snapshot is not None else "",
                imported_at=saved_at,
                # Одна запись на код (tcRecordsByCode_): новый блок кода с правленой записью получает ту же правку.
                edited=sibling.edited if sibling is not None else None,
                edited_at=sibling.edited_at if sibling is not None else None,
                edited_by=sibling.edited_by if sibling is not None else None,
            )
            self.session.add(row)
        row.code = card.code
        row.side = card.side
        row.title = card.title
        row.name = card.name
        row.category = card.category
        row.description = description
        row.raw_rows = [list(item) for item in card.raw_rows]
        row.images = [
            {"row": image.row, "col": image.col, "driveFileId": image.drive_file_id,
             "width": image.width, "height": image.height}
            for image in card.images
        ]
        row.saved_at = saved_at
        row.saved_by = saved_by
        domain = self._domain(row)
        row.name = domain.name
        row.document = domain.document()
        return created

    def delete_sheet(self, source_id: str) -> OperationCardTemplateRow | None:
        """SUPPLYMAP deleteTemplateImpl_: строка каталога и блок уходят целиком."""
        row = self.session.get(OperationCardTemplateRow, source_id)
        if row is not None:
            self.session.delete(row)
        return row

    @staticmethod
    def _draft(row: OperationCardDraftRow) -> TemplateDraft:
        return TemplateDraft(
            id=row.id, name=row.name, existing_template_id=row.existing_template_id or "",
            title=row.title, category=row.category, description=row.description,
            rows=tuple(tuple(str(cell) for cell in item) for item in row.rows),
            images=tuple(dict(item) for item in row.images),
            created_at=row.created_at, created_by=row.created_by, updated_at=row.updated_at,
        )

    def drafts(self) -> list[TemplateDraft]:
        return [self._draft(row) for row in self.session.scalars(
            select(OperationCardDraftRow).order_by(OperationCardDraftRow.created_at, OperationCardDraftRow.name)
        )]

    def draft(self, draft_id: UUID) -> TemplateDraft | None:
        row = self.session.get(OperationCardDraftRow, draft_id)
        return self._draft(row) if row is not None else None

    def draft_by_name(self, name: str) -> TemplateDraft | None:
        row = self.session.scalars(select(OperationCardDraftRow).where(OperationCardDraftRow.name == name)).first()
        return self._draft(row) if row is not None else None

    def put_draft(self, draft: TemplateDraft) -> None:
        row = self.session.get(OperationCardDraftRow, draft.id)
        if row is None:
            row = OperationCardDraftRow(id=draft.id)
            self.session.add(row)
        row.name = draft.name
        row.existing_template_id = draft.existing_template_id or None
        row.title = draft.title
        row.category = draft.category
        row.description = draft.description
        row.rows = [list(item) for item in draft.rows]
        row.images = [dict(item) for item in draft.images]
        row.created_at = draft.created_at
        row.created_by = draft.created_by
        row.updated_at = draft.updated_at

    def delete_draft(self, draft_id: UUID) -> bool:
        row = self.session.get(OperationCardDraftRow, draft_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

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
        self.harness_revisions = SqlAlchemyHarnessRevisionRepository(self.session)
        self.harness_components = SqlAlchemyHarnessComponentRepository(self.session)
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
