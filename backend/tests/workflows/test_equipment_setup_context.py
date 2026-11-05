from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.equipment_setup.application import IssuedSetupOperation, SetupOperationUnavailable
from app.modules.equipment_setup.domain import IssuedOperationRef
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.references.domain import TechOperationTab
from app.modules.references.infrastructure import TechOperationRecordRow
from app.modules.techcards.infrastructure import RefreshRequestRow
from app.modules.production.resource_claims import ResourceConflict
from app.modules.references.equipment import PhysicalEquipment
from app.workflows.equipment_setup_context import SqlAlchemySetupContext
from app.workflows.production_resource_context import SqlAlchemyProductionResourceContext
from tests.production.test_machine_load import etk, make_task, single


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
        "Неизвестно" if kind == "unknown_site" else "ЦАР", ("ОБ.027.2",))
    context = SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: value)
    assert context.issued_operation(reference) is None


def test_operation_without_fixed_site_passes_through_for_the_machine_to_decide(db):
    reference = IssuedOperationRef(uuid4(), 2, uuid4(), uuid4(), 1)
    value = IssuedSetupOperation(reference, "", ("ОБ.027.2", "ОБ.027.3"))
    assert SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: value).issued_operation(reference) == value


class Resolver(SqlAlchemyProductionResourceContext):
    """Real resolution rules over an in-memory task, ETK document and catalog; executors come from the db."""

    def __init__(self, session, task, document):
        super().__init__(session)
        self.task, self.document = task, document

    def _task(self, task_id):
        return self.task if task_id == self.task.id else None

    def _document(self, task):
        if self.document is None:
            raise ResourceConflict("Точная ревизия ЭТК не найдена; текущая ревизия не подставляется")
        return self.document

    def catalog(self):
        return (PhysicalEquipment("ОБ.027.2", "ОБ.027.2", "Автомат резки", "EW", "ЦАР"),
            PhysicalEquipment("ОБ.027.3", "ОБ.027.3", "Автомат резки", "EW", "ЖУК"),
            PhysicalEquipment("ОБ.098.1", "ОБ.098.1", "Пресс", "P", "ЦАР"))


def issued(machine="ОБ.027", **changes):
    task = replace(make_task(1, [single(1, "OP", "Резка")]), techcard_id=uuid4(), techcard_revision=2, **changes)
    reference = IssuedOperationRef(task.techcard_id, 2, task.id, task.operations[0].id, 1)
    return task, reference, etk({"code": "OP", "stage": "Резка", "machine": machine})


def test_operation_without_executor_or_handed_to_resolves_and_machine_decides_site(db):
    task, reference, document = issued()
    assert not task.operations[0].operator_ids and not task.handed_to
    result = Resolver(db, task, document).issued_operation(reference)
    assert result == IssuedSetupOperation(reference, "", ("ОБ.027.2", "ОБ.027.3"))
    assert SqlAlchemySetupContext(db, resolve_issued_operation=lambda reference: result).issued_operation(reference) == result


def test_site_order_handed_to_then_executors(db):
    task, reference, document = issued(handed_to="ЖУК")
    assert Resolver(db, task, document).issued_operation(reference).eligible_machine_ids == ("ОБ.027.3",)
    person = uuid4()
    db.add(IdentityUserRow(id=person, login="op", email="op@example.test", name="Оператор", department="Цех",
        role="staff", site="ЦАР", active=True))
    task, reference, document = issued()
    task = task.with_operations((replace(task.operations[0], operator_ids=(person,), shares=()),))
    result = Resolver(db, task, document).issued_operation(reference)
    assert (result.site, result.eligible_machine_ids) == ("ЦАР", ("ОБ.027.2",))


@pytest.mark.parametrize("kind, message", [
    ("revision", "Ревизия ЭТК задания изменилась — обновите список операций"),
    ("no_equipment", "В ЭТК нет оборудования для операции"),
    ("unknown_executor", "Уточните площадку исполнителей операции"),
])
def test_existing_operation_failure_names_its_own_reason(db, kind, message):
    task, reference, document = issued(machine="" if kind == "no_equipment" else "ОБ.027")
    if kind == "revision":
        reference = replace(reference, card_revision=1)
    if kind == "unknown_executor":
        task = task.with_operations((replace(task.operations[0], operator_ids=(uuid4(),), shares=()),))
    with pytest.raises(SetupOperationUnavailable) as error:
        Resolver(db, task, document).issued_operation(reference)
    assert str(error.value) == message


@pytest.mark.parametrize("kind", ["task", "card", "operation", "position", "revision_document"])
def test_not_found_only_when_task_or_revision_really_missing(db, kind):
    task, reference, document = issued()
    reference = {
        "task": replace(reference, production_task_id=uuid4()), "card": replace(reference, card_id=uuid4()),
        "operation": replace(reference, production_operation_id=uuid4()), "position": replace(reference, etk_position=5),
    }.get(kind, reference)
    assert Resolver(db, task, None if kind == "revision_document" else document).issued_operation(reference) is None


def test_production_site_falls_back_to_chosen_machine_only_without_executors(db):
    task, _, document = issued()
    resolver = Resolver(db, task, document)
    assert resolver.site(task, (), {"ЖУК"}) == "ЖУК"
    for machines in ((), {"ЦАР", "ЖУК"}):
        with pytest.raises(ResourceConflict, match="^Площадка не определена — назначьте исполнителя или выберите станок$"):
            resolver.site(task, (), machines)


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
