from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.equipment_setup.application import IssuedSetupOperation
from app.modules.equipment_setup.domain import IssuedOperationRef
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import TechOperationRecordRow
from app.workflows.equipment_setup_context import SqlAlchemySetupContext


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for row in (IdentityUserRow, TechOperationRecordRow):
        row.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_context_uses_real_person_activity_and_site(db):
    user_id = uuid4()
    db.add(IdentityUserRow(id=user_id, login="test", email="test@example.test", name="Сотрудник", department="Цех",
        role="admin", site="ЖУК", active=False, site_master=True))
    context = SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: None)
    person = context.person(user_id)
    assert person.actor.id == user_id
    assert person.site == "ЖУК"
    assert not person.active
    assert person.site_master
    assert context.person(uuid4()) is None


@pytest.mark.parametrize("site", ["ЦАР", "", "Неизвестно"])
def test_physical_machine_exact_identity_and_known_site_only(db, site):
    db.add(TechOperationRecordRow(id=uuid4(), tab=TechOperationTab.EQUIPMENT.value, display_text="Станок", normalized_search="станок",
        source_sheet="test", sort_key="1", fields={"Тип": "Оборудование", "Инв. номер": "ОБ.027.2",
        "Наименование": "Станок", "Расположение": site}))
    context = SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: None)
    assert (context.machine("ОБ.027.2") is not None) is (site == "ЦАР")
    assert context.machine("ОБ.027") is None


@pytest.mark.parametrize("kind", ["missing", "unknown_site", "wrong_revision"])
def test_unresolved_or_fallback_operation_rejected(db, kind):
    reference = IssuedOperationRef(uuid4(), 2, uuid4(), uuid4(), 1)
    value = None if kind == "missing" else IssuedSetupOperation(
        replace(reference, card_revision=3) if kind == "wrong_revision" else reference,
        "" if kind == "unknown_site" else "ЦАР", ("ОБ.027.2",))
    context = SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: value)
    assert context.issued_operation(reference) is None
