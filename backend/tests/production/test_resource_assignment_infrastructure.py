from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.application import AssignProductionCommand, SetProductionCancelledCommand, assign_production, set_production_cancelled
from app.modules.production.execution import ManualOutputCommand, SessionCommand, StartWorkCommand, SubmitWorkCommand, cancel_work, pause_work, record_manual_output, resume_work, start_work, submit_work
from app.modules.production.infrastructure import ProductionWorkSessionRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.resource_assignments import MachineBinding, PhysicalAllocation
from app.modules.production.resource_assignment_infrastructure import ResourceAllocationRow, ResourceSessionMemberRow, SqlAlchemyProductionResources
from app.modules.production.resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceOwner, ResourceWindow
from app.modules.production.resource_infrastructure import ActiveResourceRow, ResourceGenerationRow, SqlAlchemyResourceClaims
from app.shared.clock import MOSCOW
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.production.test_execution_persistence import execution_setup
from app.workflows.stage_cascade import cascade_uow_factory


class Resolver:
    def validate(self, task, allocation):
        pass

    def assert_legacy_available(self, resource, window, *, excluding=()):
        pass


@pytest.fixture
def setup():
    engine, sessions, old_factory, master, operator, task, source, now = execution_setup()
    with sessions() as session:
        session.add(ResourceGenerationRow(id=1, version=0))
        session.commit()
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions, lambda session: SqlAlchemyProductionResources(session, Resolver()))
    yield sessions, old_factory, factory, master, operator, task, now
    engine.dispose()


def assign(factory, master, task, people, now):
    return assign_production(AssignProductionCommand(task.id, task.version, task.operations[0].id,
        tuple(people), now, master, now, machine_bindings=(MachineBinding(1, "ОБ.027.2"),)), factory())[0]


def begin(factory, operator, task, now):
    return start_work(StartWorkCommand(task_id=task.id, operation_id=task.operations[0].id,
        actor=operator, now=now, box_numbers=()), factory())


def test_start_pause_resume_cancel_updates_claims_in_existing_transaction(setup):
    sessions, _, factory, master, operator, task, now = setup
    task = assign(factory, master, task, [operator.id], now)
    work = begin(factory, operator, task, now)
    pause_work(SessionCommand(work.id, operator, now + timedelta(minutes=1)), factory())
    with sessions() as session:
        assert len(list(session.scalars(select(ActiveResourceRow)))) == 2
    resume_work(SessionCommand(work.id, operator, now + timedelta(hours=3)), factory())
    with sessions() as session:
        assert len(list(session.scalars(select(ActiveResourceRow)))) == 2
    cancel_work(SessionCommand(work.id, operator, now + timedelta(hours=4)), factory())
    with sessions() as session:
        assert list(session.scalars(select(ActiveResourceRow))) == []
        assert session.get(ResourceSessionMemberRow, work.id).closed


def test_last_crew_member_releases_machine_not_first(setup):
    sessions, _, factory, master, operator, task, now = setup
    second = Actor(id=uuid4(), name="Второй", department="Производство")
    with sessions() as session:
        session.add(IdentityUserRow(id=second.id, login="second", email="second@example.test", name=second.name,
            department=second.department, role="staff", site="ЦАР", active=True, shop_worker=True))
        session.commit()
    task = assign(factory, master, task, [operator.id, second.id], now)
    one = begin(factory, operator, task, now)
    two = begin(factory, second, task, now)
    cancel_work(SessionCommand(one.id, operator, now + timedelta(minutes=1)), factory())
    with sessions() as session:
        assert session.get(ActiveResourceRow, ("machine", "ОБ.027.2")) is not None
        assert session.get(ActiveResourceRow, ("person", str(operator.id))) is None
    cancel_work(SessionCommand(two.id, second, now + timedelta(minutes=2)), factory())
    with sessions() as session:
        assert list(session.scalars(select(ActiveResourceRow))) == []


def test_unresolved_start_rolls_back_work_and_legacy_completion_remains_allowed(setup):
    sessions, old_factory, factory, master, operator, task, now = setup
    with pytest.raises(ResourceConflict, match="явно назначьте"):
        begin(factory, operator, task, now)
    with sessions() as session:
        assert list(session.scalars(select(ProductionWorkSessionRow))) == []
    old_work = begin(old_factory, operator, task, now)
    cancel_work(SessionCommand(old_work.id, operator, now + timedelta(minutes=1)), factory())
    with sessions() as session:
        assert list(session.scalars(select(ActiveResourceRow))) == []


def test_machine_change_during_active_execution_rolls_back(setup):
    sessions, _, factory, master, operator, task, now = setup
    task = assign(factory, master, task, [operator.id], now)
    begin(factory, operator, task, now)
    with factory() as uow:
        current = uow.production.get(task.id)
    with pytest.raises(ResourceConflict, match="завершите"):
        assign_production(AssignProductionCommand(current.id, current.version, current.operations[0].id,
            (operator.id,), now, master, now, machine_bindings=(MachineBinding(1, "ОБ.027.3"),)), factory())
    with factory() as uow:
        assert uow.production.get(task.id).version == current.version
        assert uow.session.get(ActiveResourceRow, ("machine", "ОБ.027.2")) is not None


@pytest.mark.parametrize("manual", [False, True])
def test_completion_and_manual_close_release_actual_holds(setup, manual):
    sessions, _, factory, master, operator, task, now = setup
    task = assign(factory, master, task, [operator.id], now)
    work = begin(factory, operator, task, now)
    if manual:
        record_manual_output(ManualOutputCommand(task.id, task.operations[0].id, Decimal(10), master,
            now + timedelta(minutes=2), performer=operator), factory())
    else:
        submit_work(SubmitWorkCommand(session_id=work.id, actor=operator, now=now + timedelta(minutes=2),
            good_quantity=Decimal(10), defect_quantity=Decimal(0)), factory())
    with sessions() as session:
        assert list(session.scalars(select(ActiveResourceRow))) == []
        assert session.get(ResourceSessionMemberRow, work.id).closed


def test_cancelling_plan_does_not_release_actual_execution(setup):
    sessions, _, factory, master, operator, task, now = setup
    task = assign(factory, master, task, [operator.id], now)
    work = begin(factory, operator, task, now)
    with factory() as uow:
        current = uow.production.get(task.id)
    set_production_cancelled(SetProductionCancelledCommand(current.id, current.version, True, master, now), factory())
    with sessions() as session:
        assert len(list(session.scalars(select(ActiveResourceRow)))) == 2
    cancel_work(SessionCommand(work.id, operator, now + timedelta(minutes=1)), factory())
    with sessions() as session:
        assert list(session.scalars(select(ActiveResourceRow))) == []


def test_stage_cascade_propagates_same_transaction_resource_factory(setup):
    sessions, _, factory, master, operator, task, now = setup
    task = assign(factory, master, task, [operator.id], now)
    cascade = cascade_uow_factory(factory)
    with cascade() as uow:
        assert uow.resources is not None
        uow.production.add(task.set_cancelled(True))
        uow.commit()
    from app.modules.production.resource_infrastructure import ResourceReservationRow
    with sessions() as session:
        assert list(session.scalars(select(ResourceReservationRow))) == []


def test_start_conflict_rolls_back_session_task_and_audit(setup):
    sessions, _, factory, master, operator, task, now = setup
    task = assign(factory, master, task, [operator.id], now)
    later = (now + timedelta(hours=5)).replace(tzinfo=MOSCOW)
    with sessions() as session:
        claims = SqlAlchemyResourceClaims(session)
        owner = ResourceOwner("setup", uuid4())
        machine = ResourceKey(ResourceKind.MACHINE, "ОБ.027.2")
        claims.replace_plan(owner, (machine,), ResourceWindow(later, later + timedelta(hours=1)))
        claims.activate(owner, owner.id, (machine,), later)
        session.commit()
        audit_before = set(session.scalars(select(AuditEventRow.id)))
    with pytest.raises(ResourceConflict):
        begin(factory, operator, task, later)
    with factory() as uow:
        assert uow.production.get(task.id).version == task.version
        assert list(uow.session.scalars(select(ProductionWorkSessionRow))) == []
        assert set(uow.session.scalars(select(AuditEventRow.id))) == audit_before


def test_separate_allocations_on_same_operation_are_not_inferred_as_crew(setup):
    sessions, old_factory, factory, master, operator, task, now = setup
    second_id = uuid4()
    with sessions() as session:
        session.add(IdentityUserRow(id=second_id, login="independent", email="independent@example.test", name="Другой",
            department="Производство", role="staff", site="ЦАР", active=True, shop_worker=True))
        session.commit()
    task = assign_production(AssignProductionCommand(task.id, task.version, task.operations[0].id,
        (operator.id, second_id), now, master, now), old_factory())[0]
    with pytest.raises(ResourceConflict, match="пересекаются"):
        with factory() as uow:
            for identifier in (operator.id, second_id):
                uow.resources.configure(task, PhysicalAllocation(task.id, task.operations[0].id, (identifier,),
                    (MachineBinding(1, "ОБ.027.2"),)))
            uow.commit()
    with sessions() as session:
        assert list(session.scalars(select(ResourceAllocationRow))) == []


def test_actual_output_survives_conflicting_best_effort_reflow(setup, monkeypatch):
    from app.modules.production import application
    from app.modules.production.infrastructure import ProductionOutputRow, ProductionWorkTimeLogRow
    from app.modules.production.resource_infrastructure import ResourceReservationRow

    sessions, _, factory, master, operator, task, now = setup
    task = assign(factory, master, task, [operator.id], now)
    task = assign_production(AssignProductionCommand(task.id, task.version, task.operations[1].id,
        (operator.id,), now + timedelta(hours=1), master, now,
        machine_bindings=(MachineBinding(2, "ОБ.027.3"),)), factory())[0]
    work = begin(factory, operator, task, now)
    later = now + timedelta(hours=5)
    with sessions() as session:
        claims = SqlAlchemyResourceClaims(session)
        claims.replace_plan(ResourceOwner("setup", uuid4()), (ResourceKey(ResourceKind.MACHINE, "ОБ.027.3"),),
            ResourceWindow(later.replace(tzinfo=MOSCOW), (later + timedelta(hours=1)).replace(tzinfo=MOSCOW)))
        session.commit()
    with factory() as uow:
        before = uow.production.get(task.id)
        plans_before = {(r.owner_id, r.resource_kind, r.resource_key, r.start, r.finish) for r in uow.session.scalars(select(ResourceReservationRow))}
        logs_before = set(uow.session.scalars(select(ProductionWorkTimeLogRow.id)))
    original = application._record_fact

    def conflicting_reflow(uow, *args):
        original(uow, *args)
        current = uow.production.get(task.id)
        operation = current.operations[1]
        shifted = operation.with_shares(tuple(replace(share, planned_start=later,
            planned_finish=later + timedelta(minutes=10)) for share in operation.shares), operation.assigned_by)
        uow.production.add(current.with_operations((shifted,)))

    monkeypatch.setattr(application, "_record_fact", conflicting_reflow)
    completed, _, output, _ = submit_work(SubmitWorkCommand(session_id=work.id, actor=operator,
        now=now + timedelta(minutes=2), good_quantity=Decimal(10), defect_quantity=Decimal(0)), factory())
    with factory() as uow:
        assert uow.production.get(task.id) == before
        assert uow.session.get(ProductionOutputRow, output.id) is not None
        assert uow.session.get(ProductionWorkSessionRow, work.id).status == completed.status.value
        assert set(uow.session.scalars(select(ProductionWorkTimeLogRow.id))) > logs_before
        assert {(r.owner_id, r.resource_kind, r.resource_key, r.start, r.finish) for r in uow.session.scalars(select(ResourceReservationRow))} == plans_before
        assert list(uow.session.scalars(select(ActiveResourceRow))) == []
        assert uow.session.get(ResourceSessionMemberRow, work.id).closed
