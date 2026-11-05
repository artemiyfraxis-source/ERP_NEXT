from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.production.resource_assignments import MachineBinding, PhysicalAllocation
from app.modules.production.resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceWindow
from app.modules.references.equipment import PhysicalEquipment
from app.modules.techcards.project_cards import TechcardStatus
from app.shared.clock import MOSCOW
from app.workflows.production_resource_context import SqlAlchemyProductionResourceContext
from tests.production.test_execution_persistence import execution_setup
from tests.production.test_machine_load import etk, make_task, single
from app.modules.production.execution import StartWorkCommand, start_work


class Context(SqlAlchemyProductionResourceContext):
    def __init__(self, document):
        self.document = document

    def _document(self, task):
        return self.document

    def site(self, task, operators, machine_sites=()):
        return "ЦАР"

    def catalog(self):
        return (PhysicalEquipment("ОБ.027.2", "ОБ.027.2", "Станок", "Модель", "ЦАР"),
            PhysicalEquipment("ОБ.027.3", "ОБ.027.3", "Станок", "Модель", "ЖУК"))


def data(machine="ОБ.027"):
    operation = single(1, "OP", "Резка")
    task = make_task(1, [operation])
    return task, operation, etk({"code": "OP", "stage": "Резка", "machine": machine})


def test_matching_family_is_not_a_license_to_choose_first_or_other_site():
    task, operation, document = data()
    context = Context(document)
    def allocation(identifier):
        return PhysicalAllocation(task.id, operation.id, (uuid4(),), (MachineBinding(1, identifier),))
    context.validate(task, allocation("ОБ.027.2"))
    for identifier in ("ОБ.027", "ОБ.027.3", "ОБ.098"):
        with pytest.raises(ResourceConflict):
            context.validate(task, allocation(identifier))


def test_all_equipment_rows_are_considered_not_only_first():
    task, operation, document = data()
    document["operations"][0]["card"]["tooling"].append({"section": "Оборудование", "inventory_number": "ОБ.098"})
    with pytest.raises(ResourceConflict, match="несколько типов"):
        Context(document).requirements(task, operation)


def test_missing_tooling_is_unknown_not_manual():
    task, operation, document = data()
    document["operations"][0]["card"] = {}
    with pytest.raises(ResourceConflict, match="не предполагается"):
        Context(document).requirements(task, operation)
    document["operations"][0]["card"]["tooling"] = []
    assert Context(document).requirements(task, operation) == {1: None}


def test_missing_pinned_revision_never_falls_forward_to_current():
    task, _, document = data()
    task = replace(task, techcard_id=uuid4(), techcard_revision=2)
    card = SimpleNamespace(status=TechcardStatus.ISSUED.value, revision=3, document=document)
    session = SimpleNamespace(get=lambda *args: card, scalar=lambda *args: None)
    with pytest.raises(ResourceConflict, match="не подставляется"):
        SqlAlchemyProductionResourceContext(session)._document(task)
    card.revision = 2
    assert SqlAlchemyProductionResourceContext(session)._document(task) is document


def test_unknown_legacy_machine_blocks_but_completed_interval_does_not():
    engine, sessions, _, _, operator, task, _, now = execution_setup()
    try:
        with sessions() as session:
            context = SqlAlchemyProductionResourceContext(session)
            begin = now.replace(tzinfo=MOSCOW)
            machine = ResourceKey(ResourceKind.MACHINE, "ОБ.027.2")
            with pytest.raises(ResourceConflict, match="неуточнённым"):
                context.assert_legacy_available(machine, ResourceWindow(begin, begin + timedelta(hours=1)))
            context.assert_legacy_available(machine, ResourceWindow(begin + timedelta(days=20), begin + timedelta(days=21)))
    finally:
        engine.dispose()


def test_legacy_active_work_without_plan_still_blocks_resources_after_plan_end():
    engine, sessions, factory, _, operator, task, _, now = execution_setup()
    try:
        start_work(StartWorkCommand(task.id, task.operations[0].id, (), operator, now), factory())
        with factory() as uow:
            current = uow.production.get(task.id)
            uow.production.add(current.with_operations((current.operations[0].with_shares((), "Мастер"),)))
            uow.commit()
        with sessions() as session:
            context = SqlAlchemyProductionResourceContext(session)
            begin = (now + timedelta(days=20)).replace(tzinfo=MOSCOW)
            for resource in (ResourceKey(ResourceKind.MACHINE, "ОБ.027.2"), ResourceKey(ResourceKind.PERSON, str(operator.id))):
                with pytest.raises(ResourceConflict):
                    context.assert_legacy_available(resource, ResourceWindow(begin, begin + timedelta(hours=1)))
    finally:
        engine.dispose()
