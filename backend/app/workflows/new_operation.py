"""«Новая операция» (owner 14.10.2026): the operation reference («База данных» → операции) gains new operations.

An operation is two records joined by its code: the БД.ОП row (references: «Номер», «Название», time norms) and the
operation card template (techcards, БД.ТК: steps, tooling, defects, safety). Both are written in ONE transaction on
one SQLAlchemy session — blank, or a copy of an existing operation — so neither exists without the other. The code
is unique: it is checked against the rows and the templates under that same transaction, and a `FOR UPDATE` on the
library snapshot row serialises concurrent creations on PostgreSQL.

`SqlAlchemyReferencesOperationsUnitOfWork` is the references unit of work with the template codes: with it a rename
of «Номер» that would leave a card template without its row is refused (references `_check_operation_code`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.references.application import create_operation_row_in
from app.modules.references.domain import ReferenceValidationError, TechOperationRecord
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork, SqlAlchemyTechOperationRepository
from app.modules.techcards.application import NewOperation, create_operation_templates_in
from app.modules.techcards.infrastructure import OperationCardSnapshotRow, OperationCardTemplateRow
from app.modules.techcards.operation_cards import OperationCardTemplate
from app.modules.techcards.project_cards import TechcardValidationError
from app.shared.events import AuditEvent
from app.shared.identity import Actor

LIBRARY_AGGREGATE = UUID("00000000-0000-0000-0000-000000000018")


class SqlAlchemyOperationTemplateCodes:
    """references `OperationTemplateCodes` port over the techcards template table, on the caller's session."""

    def __init__(self, session: Session):
        self.session = session

    def codes(self) -> set[str]:
        return set(self.session.scalars(select(OperationCardTemplateRow.code)))


class SqlAlchemyReferencesOperationsUnitOfWork(SqlAlchemyReferencesUnitOfWork):
    """The references unit of work that also sees the card template codes (БД.ОП «Номер» rename guard)."""

    def __enter__(self):
        super().__enter__()
        self.operation_templates = SqlAlchemyOperationTemplateCodes(self.session)
        return self


class NewOperationUnitOfWork:
    """The techcards unit of work plus БД.ОП on its session: the row and its card commit or roll back together."""

    def __init__(self, techcards_uow):
        self.inner = techcards_uow
        self.session: Session | None = None

    def __enter__(self) -> NewOperationUnitOfWork:
        self.inner.__enter__()
        self.session = self.inner.session
        self.operation_cards = self.inner.operation_cards
        self.audit = self.inner.audit
        self.tech_operations = SqlAlchemyTechOperationRepository(self.session)
        self.operation_templates = SqlAlchemyOperationTemplateCodes(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self.inner.__exit__(exc_type, exc, traceback)

    def commit(self) -> None:
        self.inner.commit()

    def serialise(self) -> None:
        """PostgreSQL: one creation at a time (the code check and the insert stay atomic); SQLite ignores it."""
        assert self.session is not None
        self.session.get(OperationCardSnapshotRow, "current", with_for_update=True)


@dataclass(frozen=True)
class CreatedOperation:
    record: TechOperationRecord
    templates: tuple[OperationCardTemplate, ...]


def create_operation(operation: NewOperation, actor: Actor, now: datetime, uow: NewOperationUnitOfWork) -> CreatedOperation:
    """БД.ОП row + card template(s) of a new operation in one transaction; a taken code refuses both."""
    with uow:
        uow.serialise()
        templates = create_operation_templates_in(operation, actor, now, uow)
        try:
            record = create_operation_row_in(operation.code, operation.name, operation.copy_of, uow)
        except ReferenceValidationError as error:
            raise TechcardValidationError(str(error)) from error
        uow.audit.add(AuditEvent(
            id=uuid4(), aggregate_id=LIBRARY_AGGREGATE, event_type="techcards.operation_created",
            actor=actor, occurred_at=now,
            details={
                "code": operation.code, "name": operation.name, "copy_of": operation.copy_of,
                "record_id": str(record.id), "templates": "; ".join(card.source_id for card in templates),
            },
        ))
        uow.commit()
    return CreatedOperation(record=record, templates=tuple(templates))
