"""SQLAlchemy persistence adapters for Requests, audit, and outbox."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker, validates

from app.shared.identity import Actor
from app.shared.project_number import normalize_project_number, project_from_purpose
from app.shared.persistence import (
    AuditEventRow,
    Base,
    OutboxMessageRow,
    SqlAlchemyAuditRepository,
    SqlAlchemyOutboxRepository,
    create_schema,
)

from .domain import Request, RequestStatus


class RequestRow(Base):
    __tablename__ = "requests"
    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "creation_key",
            "creation_index",
            name="uq_requests_actor_creation_item",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    legacy_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    creation_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    creation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    creation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    designation: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_department: Mapped[str] = mapped_column(String(255), nullable=False)
    procurement_actor_name: Mapped[str | None] = mapped_column(String(255))
    procurement_actor_department: Mapped[str | None] = mapped_column(String(255))
    procurement_actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(64), nullable=False)
    line_sum: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    project_reference: Mapped[str | None] = mapped_column(String(128), index=True)
    delivery_point: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    item_type: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    design_article: Mapped[str | None] = mapped_column(String(512))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    link: Mapped[str | None] = mapped_column(Text)
    quality_document: Mapped[str] = mapped_column(String(8), nullable=False)
    procurement_deadline: Mapped[Any] = mapped_column(Date, nullable=False)
    planned_date: Mapped[Any] = mapped_column(Date, nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    price_with_vat: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    comment: Mapped[str | None] = mapped_column(Text)
    # Проект заявки для сравнения — из трёх мест, как `request_matches_project`: № ПРОЕКТА (`normalize_project_number`),
    # ЦЕЛЬ и ОБОЗНАЧЕНИЕ «проект.NNN» (`project_from_purpose`); «» — проекта там нет. Заявки проекта — по индексам.
    # Ставятся при записи своих полей; NULL — строка мимо ORM (читается и сверяется в Python).
    project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    purpose_project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    designation_project_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    __mapper_args__ = {
        "version_id_col": version,
        "version_id_generator": False,
    }

    @validates("project_reference", "purpose", "designation")
    def _keep_project_keys(self, name: str, value: str | None) -> str | None:
        if name == "project_reference":
            self.project_key = normalize_project_number(value) if value else ""
        elif name == "purpose":
            self.purpose_project_key = project_from_purpose(value)
        else:
            self.designation_project_key = project_from_purpose(value)
        return value


def request_project_keys(project_reference: str | None, purpose: str | None, designation: str | None) -> set[str]:
    """Проекты заявки — те же три места, что у ключей RequestRow (и `request_matches_project`)."""
    return {
        normalize_project_number(project_reference) if project_reference else "",
        project_from_purpose(purpose), project_from_purpose(designation),
    } - {""}


class RequestDesignationRow(Base):
    __tablename__ = "request_designations"

    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    creation_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(128), primary_key=True)
    designation: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)


def _request_row(request: Request) -> RequestRow:
    procurement_actor = request.procurement_actor
    return RequestRow(
        id=request.id,
        legacy_id=request.legacy_id,
        creation_key=request.creation_key,
        creation_index=request.creation_index,
        creation_fingerprint=request.creation_fingerprint,
        designation=request.designation,
        status=request.status.value,
        version=request.version,
        created_at=request.created_at,
        actor_id=request.actor.id,
        actor_name=request.actor.name,
        actor_department=request.actor.department,
        procurement_actor_name=procurement_actor.name if procurement_actor else None,
        procurement_actor_department=procurement_actor.department if procurement_actor else None,
        procurement_actor_id=procurement_actor.id if procurement_actor else None,
        task_type=request.task_type,
        priority=request.priority,
        line_sum=request.line_sum,
        purpose=request.purpose,
        project_reference=request.project_reference,
        delivery_point=request.delivery_point,
        name=request.name,
        item_type=request.item_type,
        category=request.category,
        design_article=request.design_article,
        quantity=request.quantity,
        unit=request.unit,
        link=request.link,
        quality_document=request.quality_document,
        procurement_deadline=request.procurement_deadline,
        planned_date=request.planned_date,
        price=request.price,
        price_with_vat=request.price_with_vat,
        comment=request.comment,
    )


def _request_domain(row: RequestRow) -> Request:
    procurement_actor = None
    if row.procurement_actor_name:
        procurement_actor = Actor(
            id=row.procurement_actor_id,
            name=row.procurement_actor_name,
            department=row.procurement_actor_department or "",
        )
    return Request(
        id=row.id,
        legacy_id=row.legacy_id,
        designation=row.designation,
        status=RequestStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        actor=Actor(id=row.actor_id, name=row.actor_name, department=row.actor_department),
        task_type=row.task_type,
        priority=row.priority,
        line_sum=row.line_sum,
        purpose=row.purpose,
        project_reference=row.project_reference,
        delivery_point=row.delivery_point,
        name=row.name,
        item_type=row.item_type,
        category=row.category,
        design_article=row.design_article,
        quantity=row.quantity,
        unit=row.unit,
        link=row.link,
        quality_document=row.quality_document,
        procurement_deadline=row.procurement_deadline,
        planned_date=row.planned_date,
        price=row.price,
        price_with_vat=row.price_with_vat,
        comment=row.comment,
        procurement_actor=procurement_actor,
        creation_key=row.creation_key,
        creation_index=row.creation_index,
        creation_fingerprint=row.creation_fingerprint,
    )


_SEARCH_COLUMNS = (
    RequestRow.legacy_id,
    RequestRow.designation,
    RequestRow.status,
    RequestRow.actor_name,
    RequestRow.purpose,
    RequestRow.project_reference,
    RequestRow.delivery_point,
    RequestRow.name,
    RequestRow.item_type,
    RequestRow.category,
    RequestRow.design_article,
    RequestRow.priority,
    RequestRow.comment,
)


def _search_filter(search: str) -> list:
    """Portal search box (`renderView`): a substring anywhere in the row, case-insensitive.

    ILIKE folds Cyrillic on PostgreSQL; SQLite (tests/preview) folds ASCII only.
    """
    text = search.strip()
    if not text:
        return []
    pattern = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return [or_(*(column.ilike(pattern, escape="\\") for column in _SEARCH_COLUMNS))]


class SqlAlchemyRequestRepository:
    def __init__(self, session: Session):
        self.session = session

    def by_creation_key(self, actor_id: UUID, creation_key: str) -> list[Request]:
        statement = (
            select(RequestRow)
            .where(
                RequestRow.actor_id == actor_id,
                RequestRow.creation_key == creation_key,
            )
            .order_by(RequestRow.creation_index)
        )
        return [_request_domain(row) for row in self.session.scalars(statement)]

    def all_legacy_ids(self) -> list[str]:
        return list(self.session.scalars(select(RequestRow.legacy_id)))

    def all_designations(self) -> list[str]:
        return list(self.session.scalars(select(RequestDesignationRow.designation)))

    def add(self, request: Request) -> None:
        row = _request_row(request)
        stored = self.session.get(RequestRow, request.id)
        if stored is None:
            self.session.add(row)
            allocation = self.session.get(
                RequestDesignationRow,
                (request.actor.id, request.creation_key, request.purpose),
            )
            if allocation is None:
                self.session.add(
                    RequestDesignationRow(
                        actor_id=request.actor.id,
                        creation_key=request.creation_key,
                        purpose=request.purpose,
                        designation=request.designation,
                    )
                )
            return
        for column in RequestRow.__table__.columns:
            if column.name != "id":
                setattr(stored, column.name, getattr(row, column.name))

    def get(self, request_id: UUID) -> Request | None:
        row = self.session.get(RequestRow, request_id)
        return _request_domain(row) if row is not None else None

    def purposes_by_ids(self, request_ids: Sequence[UUID]) -> dict[UUID, str]:
        """ЦЕЛЬ заявок одним запросом (для строк листа закупки)."""
        ids = list(request_ids)
        if not ids:
            return {}
        rows = self.session.execute(select(RequestRow.id, RequestRow.purpose).where(RequestRow.id.in_(ids)))
        return {row_id: purpose for row_id, purpose in rows}

    def delete(self, request_id: UUID) -> None:
        """Заявка, ещё не взятая в закупку; выданное ей обозначение остаётся занятым (request_designations)."""
        row = self.session.get(RequestRow, request_id)
        if row is not None:
            self.session.delete(row)
            self.session.flush()

    def recent(self, limit: int, search: str = "") -> list[Request]:
        statement = (
            select(RequestRow)
            .where(*_search_filter(search))
            .order_by(RequestRow.created_at.desc(), RequestRow.creation_index)
            .limit(limit)
        )
        return [_request_domain(row) for row in self.session.scalars(statement)]

    def for_project(self, project: str) -> list[Request]:
        """Заявки одного проекта — по № ПРОЕКТА, ЦЕЛИ или ОБОЗНАЧЕНИЮ, точно по ключу (индексы трёх ключей), без
        общего лимита ленты; свежие первыми, как `recent`."""
        key = normalize_project_number(project)
        if not key:
            return []
        statement = (
            select(RequestRow)
            .where(or_(
                RequestRow.project_key == key, RequestRow.purpose_project_key == key,
                RequestRow.designation_project_key == key,
                RequestRow.project_key.is_(None), RequestRow.purpose_project_key.is_(None),
                RequestRow.designation_project_key.is_(None),
            ))
            .order_by(RequestRow.created_at.desc(), RequestRow.creation_index)
        )
        return [
            _request_domain(row) for row in self.session.scalars(statement)
            if key in request_project_keys(row.project_reference, row.purpose, row.designation)
        ]

    def by_initiator(
        self, actor_id: UUID, limit: int, offset: int, search: str = ""
    ) -> list[Request]:
        statement = (
            select(RequestRow)
            .where(RequestRow.actor_id == actor_id, *_search_filter(search))
            .order_by(RequestRow.created_at.desc(), RequestRow.creation_index.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_request_domain(row) for row in self.session.scalars(statement)]

    def count_by_initiator(self, actor_id: UUID, search: str = "") -> int:
        statement = (
            select(func.count())
            .select_from(RequestRow)
            .where(RequestRow.actor_id == actor_id, *_search_filter(search))
        )
        return int(self.session.scalar(statement) or 0)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory
        self.session: Session | None = None
        self.committed = False

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        self.committed = False
        self.session = self.session_factory()
        self.requests = SqlAlchemyRequestRepository(self.session)
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
