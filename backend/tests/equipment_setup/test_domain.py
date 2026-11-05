from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.equipment_setup.domain import (
    IssuedOperationRef, RecipeVersionRef, SetupStatus, SetupTask, SetupValidationError,
)


NOW = datetime(2026, 11, 2, 8, tzinfo=UTC)


def make_task(**changes):
    values = dict(
        operation=IssuedOperationRef(uuid4(), 3, uuid4(), uuid4(), 4),
        machine_id="ОБ.027.2", technician_id=uuid4(),
        recipe=RecipeVersionRef(uuid4(), 2), planned_start=NOW,
        planned_finish=NOW + timedelta(hours=1), now=NOW,
    )
    values.update(changes)
    return SetupTask.create(**values)


def move(task, target, **changes):
    values = dict(actor_id=task.technician_id, now=task.updated_at + timedelta(minutes=1))
    if target is SetupStatus.BLOCKED:
        values.update(reason="Нет оснастки", machine_available=False)
    values.update(changes)
    return task.transition(target, **values)


def in_status(status):
    task = make_task()
    if status is SetupStatus.NEW:
        return task
    if status is SetupStatus.CANCELLED:
        return move(task, status)[0]
    task = move(task, SetupStatus.IN_PROGRESS)[0]
    return task if status is SetupStatus.IN_PROGRESS else move(task, status)[0]


ALLOWED = {
    (SetupStatus.NEW, SetupStatus.IN_PROGRESS), (SetupStatus.NEW, SetupStatus.CANCELLED),
    (SetupStatus.IN_PROGRESS, SetupStatus.BLOCKED), (SetupStatus.IN_PROGRESS, SetupStatus.READY),
    (SetupStatus.IN_PROGRESS, SetupStatus.CANCELLED), (SetupStatus.BLOCKED, SetupStatus.IN_PROGRESS),
    (SetupStatus.BLOCKED, SetupStatus.CANCELLED),
}


@pytest.mark.parametrize("source", list(SetupStatus))
@pytest.mark.parametrize("target", list(SetupStatus))
def test_exact_transition_matrix(source, target):
    task = in_status(source)
    if (source, target) not in ALLOWED:
        with pytest.raises(SetupValidationError):
            move(task, target)
        return
    updated, event = move(task, target)
    assert updated.status is target
    assert updated.version == task.version + 1
    assert task.status is source
    assert event.previous_status is source
    assert event.status is target
    assert event.task_id == task.id
    assert event.version == updated.version
    assert event.actor_id == task.technician_id
    assert event.occurred_at == updated.updated_at


@pytest.mark.parametrize("available", [True, False])
def test_blocking_retains_explicit_availability_and_history_after_resume(available):
    running = in_status(SetupStatus.IN_PROGRESS)
    blocked, blocked_event = move(running, SetupStatus.BLOCKED, machine_available=available, reason="  Нет оснастки  ")
    resumed, _ = move(blocked, SetupStatus.IN_PROGRESS)
    assert blocked.blocked_reason == blocked_event.reason == "Нет оснастки"
    assert blocked.blocked_machine_available is blocked_event.machine_available is available
    assert resumed.blocked_reason == ""
    assert resumed.blocked_machine_available is None
    assert blocked_event.reason == "Нет оснастки"
    with pytest.raises(FrozenInstanceError):
        blocked_event.reason = ""


@pytest.mark.parametrize("changes", [
    {"reason": ""}, {"reason": "  "}, {"reason": None},
    {"machine_available": None}, {"machine_available": 0}, {"machine_available": "false"},
])
def test_block_requires_reason_and_boolean_availability(changes):
    with pytest.raises(SetupValidationError):
        move(in_status(SetupStatus.IN_PROGRESS), SetupStatus.BLOCKED, **changes)


def test_ready_is_the_assigned_technicians_statement():
    task = in_status(SetupStatus.IN_PROGRESS)
    with pytest.raises(SetupValidationError, match="назначенный наладчик"):
        move(task, SetupStatus.READY, actor_id=uuid4())
    ready, event = move(task, SetupStatus.READY)
    assert ready.status is SetupStatus.READY
    assert event.actor_id == task.technician_id


def test_recipe_and_issued_operation_are_pinned_across_transitions():
    task = make_task()
    newer_recipe = replace(task.recipe, version=task.recipe.version + 1)
    newer_operation = replace(task.operation, card_revision=task.operation.card_revision + 1)
    running, _ = move(task, SetupStatus.IN_PROGRESS)
    ready, _ = move(running, SetupStatus.READY)
    assert ready.recipe is task.recipe
    assert ready.operation is task.operation
    assert ready.recipe.version != newer_recipe.version
    assert ready.operation.card_revision != newer_operation.card_revision
    assert ready.machine_id == task.machine_id
    assert ready.technician_id == task.technician_id
    for obj, field, value in [(task, "recipe", newer_recipe), (task.recipe, "version", 3),
                              (task.operation, "etk_position", 5)]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)


@pytest.mark.parametrize("field,value", [
    ("card_id", UUID(int=0)), ("production_task_id", ""), ("production_operation_id", None),
    ("card_revision", 0), ("card_revision", True), ("card_revision", 1.5),
    ("etk_position", 0), ("etk_position", -1), ("etk_position", "4"),
])
def test_invalid_issued_operation_reference(field, value):
    with pytest.raises(SetupValidationError):
        replace(make_task().operation, **{field: value})


@pytest.mark.parametrize("field,value", [("recipe_id", UUID(int=0)), ("recipe_id", ""),
                                         ("version", 0), ("version", True), ("version", 1.5)])
def test_recipe_requires_known_positive_version(field, value):
    with pytest.raises(SetupValidationError):
        replace(make_task().recipe, **{field: value})


@pytest.mark.parametrize("changes", [
    {"id": UUID(int=0)}, {"machine_id": "  "}, {"machine_id": None},
    {"technician_id": UUID(int=0)}, {"version": -1}, {"version": True}, {"version": "1"},
    {"operation": None}, {"recipe": None}, {"status": "unknown"},
    {"blocked_reason": "Не относится к New"}, {"blocked_machine_available": False},
    {"status": SetupStatus.BLOCKED, "blocked_reason": "Причина"},
])
def test_task_snapshot_rejects_invalid_identity_version_and_status(changes):
    with pytest.raises(SetupValidationError):
        replace(make_task(), **changes)


def test_version_zero_is_known_and_increments_like_existing_optimistic_snapshots():
    task = replace(make_task(), version=0)
    assert move(task, SetupStatus.IN_PROGRESS)[0].version == 1


@pytest.mark.parametrize("changes", [
    {"planned_finish": NOW}, {"planned_finish": NOW - timedelta(seconds=1)},
    {"planned_start": NOW.replace(tzinfo=None)}, {"planned_finish": date(2026, 11, 2)},
    {"created_at": NOW.replace(tzinfo=None)}, {"updated_at": NOW - timedelta(seconds=1)},
])
def test_dated_interval_and_timestamps_are_validated(changes):
    with pytest.raises(SetupValidationError):
        replace(make_task(), **changes)


def test_different_timezones_compare_actual_instants():
    moscow = timezone(timedelta(hours=3))
    same_instant = NOW.astimezone(moscow)
    with pytest.raises(SetupValidationError, match="Конец наладки"):
        make_task(planned_finish=same_instant)
    task = make_task(planned_start=same_instant, planned_finish=NOW + timedelta(minutes=30))
    assert task.planned_start == NOW


@pytest.mark.parametrize("changes", [
    {"actor_id": UUID(int=0)}, {"now": NOW - timedelta(seconds=1)},
    {"now": NOW.replace(tzinfo=None)}, {"machine_available": False},
])
def test_invalid_transition_metadata_is_rejected_without_mutation(changes):
    task = make_task()
    with pytest.raises(SetupValidationError):
        move(task, SetupStatus.IN_PROGRESS, **changes)
    assert task.status is SetupStatus.NEW
    assert task.version == 1
