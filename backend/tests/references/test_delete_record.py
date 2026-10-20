"""Удаление строки «Базы данных» (владелец 20.10.2026: слитые дубли БД.КОАКС висят на экране, удалить их нельзя):
одна транзакция — строка уходит, в журнал `references.record_deleted` идёт её полная старая запись; чужой записи нет —
отказ без записи в журнал."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.references.application import delete_record
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.shared.identity import Actor
from app.shared.persistence import AuditEventRow
from tests.references.test_load_mirror import sessions  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 20, 12, 0)
ACTOR = Actor(id=UUID(int=0), name="Тест", department="System")


def _coax(article: str, cable: str, **extra: str) -> TechOperationRecord:
    return TechOperationRecord.create(
        tab=TechOperationTab.COAX, display_text=f"SMA-female | {cable}", search_text="", source_sheet="БД.КОАКС",
        sort_key=article, fields={"Артикул": article, "Провод": cable, "coaxArticle": article, "coaxWire": cable, **extra},
        edited_in_erp=True)


def _save(sessions, *records):  # noqa: F811
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        for record in records:
            uow.tech_operations.save(record)
        uow.commit()


def _events(sessions):  # noqa: F811
    with sessions() as session:
        return session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "references.record_deleted")).all()


def test_the_row_goes_and_the_journal_keeps_the_whole_old_record(sessions):  # noqa: F811
    gone = _coax("SMA-female RG-174", "RG-174", **{"L1": "2,5", "Примечание": "дубль → S-211L"})
    kept = _coax("S-211L", "RG-174")
    _save(sessions, gone, kept)
    delete_record(gone.id, ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(sessions), reason="слитый дубль")
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        left = {record.id for record in uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100)}
    assert left == {kept.id}
    (event,) = _events(sessions)
    assert event.details["record_id"] == str(gone.id)
    assert event.details["fields"]["Артикул"] == "SMA-female RG-174" and event.details["fields"]["L1"] == "2,5"
    assert event.details["fields"]["Примечание"] == "дубль → S-211L"
    assert event.details["reason"] == "слитый дубль" and event.details["tab"] == "coax"


def test_an_unknown_row_is_refused_and_nothing_is_journaled(sessions):  # noqa: F811
    _save(sessions, _coax("S-211L", "RG-174"))
    with pytest.raises(LookupError):
        delete_record(uuid4(), ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(sessions))
    assert _events(sessions) == []
    with SqlAlchemyReferencesUnitOfWork(sessions) as uow:
        assert len(uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=100)) == 1


def test_a_view_only_table_is_refused(sessions):  # noqa: F811
    from app.modules.references.domain import EDITABLE_TABS, ReferenceValidationError
    view_only = next((tab for tab in TechOperationTab if tab not in EDITABLE_TABS), None)
    if view_only is None:
        pytest.skip("все таблицы редактируемые")
    record = TechOperationRecord.create(tab=view_only, display_text="X", search_text="", source_sheet="", sort_key="X",
                                        fields={"a": "b"}, edited_in_erp=False)
    _save(sessions, record)
    with pytest.raises(ReferenceValidationError):
        delete_record(record.id, ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(sessions))
    assert _events(sessions) == []
