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
from app.modules.techcards.infrastructure import RefreshRequestRow
from app.workflows.equipment_setup_context import SqlAlchemySetupContext


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for row in (IdentityUserRow, TechOperationRecordRow, RefreshRequestRow):
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


def test_executor_choices_filter_activity_site_and_identity_before_page(db):
    ids = [uuid4() for _ in range(4)]
    for index, (site, active) in enumerate((("ЦАР", True), ("ЖУК", True), ("ЦАР", False), ("ЦАР", True))):
        db.add(IdentityUserRow(id=ids[index], login=f"test{index}", email=f"test{index}@example.test",
            name=f"Сотрудник {index}", department="Цех", role="staff", site=site, active=active))
    context = SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: None)
    result = context.executor_choices(site="ЦАР", limit=1, offset=1)
    assert [item.id for item in result] == [ids[3]]
    assert not hasattr(result[0], "email")
    assert context.executor_choices(site="", limit=100, offset=0) == ()
    assert context.executor_choices(site="ЦАР", own_id=ids[1], limit=100, offset=0) == ()


def test_machine_choices_filter_site_and_eligibility_before_page(db):
    for index, site in enumerate(("ЖУК", "", "ЦАР", "ЦАР"), start=1):
        db.add(TechOperationRecordRow(id=uuid4(), tab=TechOperationTab.EQUIPMENT.value, display_text="Станок", normalized_search="станок",
            source_sheet="test", sort_key=str(index), fields={"Тип": "Оборудование", "Инв. номер": f"ОБ.027.{index}",
            "Наименование": "Станок", "Расположение": site}))
    context = SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: None)
    assert [item.id for item in context.machine_choices(site="ЦАР", limit=1, offset=1)] == ["ОБ.027.4"]
    assert [item.id for item in context.machine_choices(site=None, eligible_ids=("ОБ.027.4",), limit=1, offset=0)] == ["ОБ.027.4"]
    assert len(context.machine_choices(site=None, limit=100, offset=0)) == 3
