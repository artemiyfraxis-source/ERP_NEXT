from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.production.application import (
    Absence,
    AssignProductionCommand,
    LaunchProductionCommand,
    Operator,
    SetProductionCancelledCommand,
    assign_production,
    launch_production,
    set_production_cancelled,
)
from app.modules.production.domain import ProductionOperation, ProductionStatus, ProductionValidationError
from tests.factories import Actor


OPERATOR = UUID("10000000-0000-0000-0000-000000000001")


class ProductionRepo:
    def __init__(self):
        self.items = {}
        self.sequence = 0

    def get(self, task_id): return self.items.get(task_id)
    def by_idempotency_key(self, key): return next((item for item in self.items.values() if item.source_idempotency_key == key), None)
    def list(self, limit=200, after_id=None): return list(self.items.values())[:limit]
    def reserve_number(self):
        self.sequence += 1
        return f"ПЗ-{self.sequence:04d}"
    def add(self, task): self.items[task.id] = task


class Operators:
    def __init__(self): self.items = {OPERATOR: Operator(OPERATOR, "Оператор", "ЦАР", True)}
    def get(self, operator_id): return self.items.get(operator_id)
    def list(self): return list(self.items.values())


class Calendar:
    def holidays(self): return frozenset({date(2026, 9, 23)})
    def absences(self): return [Absence(UUID("60000000-0000-0000-0000-000000000001"), OPERATOR, date(2026, 9, 24), date(2026, 9, 24), "Отпуск")]
    def replace(self, holidays, absences): pass


class Audit:
    def __init__(self): self.items = []
    def add(self, item): self.items.append(item)


class Serials:
    def __init__(self): self.tasks = {}
    def ensure_task(self, task_id, task_number, quantity):
        changed = task_id not in self.tasks
        self.tasks[task_id] = (task_number, quantity)
        return changed


class Uow:
    def __init__(self):
        self.production = ProductionRepo()
        self.operators = Operators()
        self.calendar = Calendar()
        self.audit = Audit()
        self.serials = Serials()
        self.commits = 0
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def commit(self): self.commits += 1


def route(quantity=10):
    return (ProductionOperation.create(
        position=1, code="OP-01", stage="Разделка", description="",
        seconds_per_unit=Decimal("60"), setup_seconds=Decimal("120"), quantity=quantity,
    ),)


def launch(uow, actor, now, key="task-1"):
    return launch_production(LaunchProductionCommand(
        idempotency_key=key, project="591K.3", item="591K.3.КБ1", name="Жгут",
        quantity=10, fulfillment_request_id=None, deadline=date(2026, 10, 1),
        operations=route(), actor=actor, now=now,
    ), uow)


def test_launch_is_idempotent_and_route_snapshot_is_not_rebuilt():
    uow = Uow(); actor = Actor(name="Мастер", department="Производство")
    created = launch(uow, actor, datetime(2026, 9, 22, 8, 0))
    retry = launch(uow, actor, datetime(2026, 9, 22, 8, 0))

    assert retry == created
    assert created.number == "ПЗ-0001"
    assert uow.commits == 1
    assert [event.event_type for event in uow.audit.items] == ["production.launched"]
    with pytest.raises(ProductionValidationError, match="другого задания"):
        launch_production(
            LaunchProductionCommand(
                idempotency_key="task-1", project="591K.3", item="Другое", name="",
                quantity=10, fulfillment_request_id=None, deadline=None,
                operations=route(), actor=actor, now=datetime(2026, 9, 22, 8, 0),
            ), uow,
        )


def test_assignment_uses_calendar_and_cancel_keeps_history():
    uow = Uow(); actor = Actor(name="Мастер", department="Производство")
    created = launch(uow, actor, datetime(2026, 9, 22, 16, 0))
    assigned, plan = assign_production(AssignProductionCommand(
        task_id=created.id, expected_version=created.version,
        operation_id=created.operations[0].id, operator_ids=(OPERATOR,),
        requested_start=None, actor=actor, now=datetime(2026, 9, 22, 16, 0),
    ), uow)
    cancelled = set_production_cancelled(SetProductionCancelledCommand(
        assigned.id, assigned.version, True, actor, datetime(2026, 9, 22, 16, 1),
    ), uow)

    assert plan.start == datetime(2026, 9, 22, 16, 0)
    assert plan.finish == datetime(2026, 9, 22, 16, 12)
    assert assigned.operations[0].operator_ids == (OPERATOR,)
    assert cancelled.status is ProductionStatus.CANCELLED
    assert cancelled.operations == assigned.operations
    with pytest.raises(ProductionValidationError, match="отменено"):
        assign_production(AssignProductionCommand(
            task_id=cancelled.id, expected_version=cancelled.version,
            operation_id=cancelled.operations[0].id, operator_ids=(OPERATOR,),
            requested_start=None, actor=actor, now=datetime(2026, 9, 22, 16, 2),
        ), uow)
