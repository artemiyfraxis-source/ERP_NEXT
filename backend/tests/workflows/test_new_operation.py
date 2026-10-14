"""app/workflows/new_operation.py: the БД.ОП row and the card template of a new operation share one transaction."""

from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.references.application import CreateRecordCommand, EditRecordCommand, FieldEdit, create_record, edit_record
from app.modules.references.domain import ReferenceValidationError, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.application import new_operation
from app.modules.techcards.infrastructure import OperationCardTemplateRow, SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import TechcardValidationError
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows.new_operation import (
    NewOperationUnitOfWork, SqlAlchemyReferencesOperationsUnitOfWork, create_operation,
)

ACTOR = Actor(id=UUID("40000000-0000-0000-0000-000000000001"), name="Технолог", department="Производство")
NOW = datetime(2026, 10, 14, 11, 0)


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    engine.dispose()


def _create(sessions, code, name="Операция", copy_of=""):
    return create_operation(new_operation(code, name, copy_of), ACTOR, NOW,
                            NewOperationUnitOfWork(SqlAlchemyTechcardsUnitOfWork(sessions)))


def _row(sessions, number):
    return create_record(CreateRecordCommand(TechOperationTab.OPERATION, {"opNumber": number, "opName": "x"}, ACTOR, NOW),
                         SqlAlchemyReferencesUnitOfWork(sessions))


def test_creates_row_card_and_one_audit_event_together(sessions):
    created = _create(sessions, "NEW_OP", "Новая")

    with sessions() as session:
        templates = session.scalars(select(OperationCardTemplateRow)).all()
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "techcards.operation_created")).all()
    assert [(row.code, row.saved_by, row.edited_at) for row in templates] == [("NEW_OP", "Технолог", None)]
    assert created.record.fields == {"opNumber": "NEW_OP", "opName": "Новая"}
    assert [event.details["code"] for event in events] == ["NEW_OP"]
    assert events[0].details["record_id"] == str(created.record.id)


def test_a_row_taking_the_code_rolls_the_written_card_back(sessions):
    _row(sessions, "ROW_ONLY")

    with pytest.raises(TechcardValidationError, match="«ROW_ONLY» уже есть"):
        _create(sessions, "ROW_ONLY")

    with sessions() as session:
        assert session.scalars(select(OperationCardTemplateRow)).all() == []
        assert session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "techcards.operation_created")).all() == []


def test_rename_guard_sees_the_card_codes_only_on_the_workflow_unit_of_work(sessions):
    record = _create(sessions, "NEW_OP").record
    edit = EditRecordCommand(record.id, (FieldEdit("opNumber", "NEW_OP", "OTHER"),), ACTOR, NOW)

    with pytest.raises(ReferenceValidationError, match="карточка операции"):
        edit_record(edit, SqlAlchemyReferencesOperationsUnitOfWork(sessions))
    # the plain references unit of work has no template port: it checks the rows only
    assert edit_record(edit, SqlAlchemyReferencesUnitOfWork(sessions)).fields["opNumber"] == "OTHER"
