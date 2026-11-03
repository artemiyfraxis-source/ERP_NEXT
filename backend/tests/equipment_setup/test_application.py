"""Transactional contract tests; the sequential fake does not prove database concurrency or production occupancy."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.modules.equipment_setup.application import (
    AssignSetupCommand, ChangeSetupCommand, ConcurrentSetupUpdate, IssuedSetupOperation,
    SetupAction, SetupCommandConflict, SetupMachine, SetupPermissionError, SetupPerson,
    assign_setup_task, change_setup_task,
)
from app.modules.equipment_setup.domain import IssuedOperationRef, RecipeVersionRef, SetupStatus, SetupValidationError
from app.shared.http_auth import Principal
from app.shared.identity import Actor


NOW = datetime(2026, 11, 2, 8, tzinfo=UTC)


class Items:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)


class Tasks:
    def __init__(self):
        self.items = {}
        self.fail_cas = False

    def get(self, task_id):
        return self.items.get(task_id)

    def list(self, *, technician_id=None, machine_ids=None, limit=100, offset=0):
        return tuple(task for task in self.items.values()
            if (technician_id is None or task.technician_id == technician_id)
            and (machine_ids is None or task.machine_id in machine_ids))[offset:offset+limit]

    def save(self, task, *, expected_version):
        previous = self.get(task.id)
        if self.fail_cas or (previous.version if previous else 0) != expected_version:
            raise ConcurrentSetupUpdate("CAS")
        self.items[task.id] = task


class Commands:
    def __init__(self):
        self.items = {}

    def claim(self, key):
        # Sequential contract fake only: absent-key serialization requires a real adapter.
        return self.items.get(key)

    def add(self, receipt):
        assert receipt.key not in self.items
        self.items[receipt.key] = receipt


class Context:
    def __init__(self):
        self.people = {}
        self.operations = {}
        self.machines = {}
        self.recipes = set()

    def person(self, user_id):
        return self.people.get(user_id)

    def issued_operation(self, reference):
        return self.operations.get(reference)

    def machine(self, machine_id):
        return self.machines.get(machine_id)

    def machine_ids_for_site(self, site):
        return tuple(item.id for item in self.machines.values() if item.site == site and site in {"ЦАР", "ЖУК"})

    def recipe_exists(self, reference, machine_id):
        return (reference, machine_id) in self.recipes


class Reservations:
    def __init__(self):
        self.items = {}
        self.calls = []
        self.external_busy = False

    def reserve(self, task):
        if self.external_busy or any(
            item.id != task.id and item.machine_id == task.machine_id
            and item.planned_start < task.planned_finish and task.planned_start < item.planned_finish
            for item in self.items.values()
        ):
            raise ConcurrentSetupUpdate("Станок занят")
        self.items[task.id] = task
        self.calls.append(("reserve", task.id))

    def release(self, task):
        self.items.pop(task.id, None)
        self.calls.append(("release", task.id))

    def block(self, task):
        self.calls.append(("release_person", task.id))
        if task.blocked_machine_available:
            self.release(task)


class Uow:
    PORTS = ("tasks", "commands", "context", "reservations", "transitions", "audit", "outbox")

    def __init__(self):
        self.tasks = Tasks()
        self.commands = Commands()
        self.context = Context()
        self.reservations = Reservations()
        self.transitions = Items()
        self.audit = Items()
        self.outbox = Items()
        self.commits = 0
        self.fail_commit = False

    def __enter__(self):
        self.snapshot = deepcopy({name: getattr(self, name) for name in self.PORTS})
        self.committed = False
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None or not self.committed:
            for name, port in self.snapshot.items():
                setattr(self, name, port)
        return False

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("commit failed")
        self.commits += 1
        self.committed = True


def principal(person, *, full_access=False, **changes):
    values = dict(actor=person.actor, email="", roles=frozenset({"admin" if full_access else "staff"}), permissions={})
    values.update(changes)
    return Principal(**values)


@pytest.fixture
def env():
    uow = Uow()
    manager = SetupPerson(Actor(uuid4(), "Мастер", "Производство"), "ЦАР", True, site_master=True)
    technician = SetupPerson(Actor(uuid4(), "Наладчик", "Производство"), "ЦАР", True)
    outsider = SetupPerson(Actor(uuid4(), "Другой сотрудник", "Производство"), "ЖУК", True, site_master=True)
    for person in (manager, technician, outsider):
        uow.context.people[person.actor.id] = person
    operation = IssuedOperationRef(uuid4(), 3, uuid4(), uuid4(), 4)
    recipe = RecipeVersionRef(uuid4(), 2)
    uow.context.operations[operation] = IssuedSetupOperation(operation, "ЦАР", ("ОБ.027.2",))
    uow.context.machines["ОБ.027.2"] = SetupMachine("ОБ.027.2", "ЦАР")
    uow.context.recipes.add((recipe, "ОБ.027.2"))
    command = AssignSetupCommand("assign-1", 0, operation, "ОБ.027.2", technician.actor.id,
                                 recipe, NOW, NOW + timedelta(hours=1))
    return uow, manager, technician, outsider, command


def assign(env, *, command=None, caller=None):
    uow, manager, _, _, original = env
    return assign_setup_task(command or original, uow, principal=caller or principal(manager), now=NOW)


def change(env, task, action, *, caller=None, key=None, **changes):
    uow, manager, technician, _, _ = env
    command = ChangeSetupCommand(key or f"{action.value}-{task.version}", task.version, task.id, action, **changes)
    actor = manager if action is SetupAction.CANCEL else technician
    return change_setup_task(command, uow, principal=caller or principal(actor), now=NOW + timedelta(minutes=task.version))


def effects(uow):
    return (len(uow.tasks.items), len(uow.commands.items), len(uow.transitions.items),
            len(uow.audit.items), len(uow.outbox.items), len(uow.reservations.calls), uow.commits)


def test_assignment_pins_context_and_atomically_publishes_direct_recipient(env):
    uow, manager, technician, _, command = env
    task = assign(env)
    assert task.operation == command.operation
    assert task.recipe == command.recipe
    assert task.technician_id == technician.actor.id
    assert task.version == 1 and task.status is SetupStatus.NEW
    assert uow.reservations.items[task.id] == task
    assert uow.audit.items[0].actor == manager.actor
    assert uow.audit.items[0].event_type == "equipment_setup.assigned"
    assert uow.outbox.items[0].payload["recipient_user_ids"] == str(technician.actor.id)
    assert uow.outbox.items[0].payload["recipient_audiences"] == ""
    assert uow.outbox.items[0].payload["body"] == "Станок ОБ.027.2: Назначена"
    assert effects(uow) == (1, 1, 0, 1, 1, 1, 1)


@pytest.mark.parametrize("changes", [
    {"idempotency_key": ""}, {"idempotency_key": " key"}, {"idempotency_key": "x" * 129},
    {"expected_version": True}, {"expected_version": -1}, {"expected_version": 1},
    {"technician_id": None}, {"operation": None}, {"recipe": None}, {"machine_id": ""},
    {"planned_start": NOW.replace(tzinfo=None)}, {"planned_finish": NOW},
])
def test_assignment_command_validation(env, changes):
    with pytest.raises(SetupValidationError):
        replace(env[-1], **changes)


@pytest.mark.parametrize("changes", [
    {"idempotency_key": ""}, {"expected_version": 0}, {"expected_version": True},
    {"task_id": None}, {"action": "start"}, {"reason": None},
    {"machine_available": True},
    {"action": SetupAction.BLOCK},
    {"action": SetupAction.BLOCK, "reason": "нет", "machine_available": 1},
    {"action": SetupAction.BLOCK, "reason": "  ", "machine_available": False},
])
def test_transition_command_validation(changes):
    with pytest.raises(SetupValidationError):
        replace(ChangeSetupCommand("start", 1, uuid4(), SetupAction.START), **changes)


@pytest.mark.parametrize("part", ["operation", "revision", "position", "machine", "machine_identity", "technician", "recipe"])
def test_exact_server_context_required(env, part):
    uow, _, technician, _, command = env
    if part == "operation":
        uow.context.operations.clear()
    elif part in {"revision", "position"}:
        reference = replace(command.operation, **({"card_revision": 2} if part == "revision" else {"etk_position": 5}))
        uow.context.operations[command.operation] = IssuedSetupOperation(reference, "ЦАР", ("ОБ.027.2",))
    elif part == "machine":
        uow.context.machines.clear()
    elif part == "machine_identity":
        uow.context.machines[command.machine_id] = SetupMachine("ОБ.027.1", "ЦАР")
    elif part == "technician":
        uow.context.people[technician.actor.id] = replace(technician, active=False)
    else:
        uow.context.recipes.clear()
    with pytest.raises(SetupValidationError):
        assign(env)
    assert effects(uow) == (0, 0, 0, 0, 0, 0, 0)


@pytest.mark.parametrize("part", ["operation", "machine", "technician", "unknown_machine", "unknown_operation"])
def test_cross_site_or_unknown_source_rejected_even_for_admin(env, part):
    uow, manager, technician, _, command = env
    if part.endswith("operation"):
        uow.context.operations[command.operation] = IssuedSetupOperation(command.operation, "" if part.startswith("unknown") else "ЖУК", ("ОБ.027.2",))
    elif part.endswith("machine"):
        uow.context.machines[command.machine_id] = SetupMachine(command.machine_id, "" if part.startswith("unknown") else "ЖУК")
    else:
        uow.context.people[technician.actor.id] = replace(technician, site="ЖУК")
    with pytest.raises(SetupPermissionError):
        assign(env, caller=principal(manager, full_access=True))
    assert effects(uow) == (0, 0, 0, 0, 0, 0, 0)


def test_full_access_can_assign_across_own_site_but_master_cannot(env):
    _, _, _, outsider, _ = env
    with pytest.raises(SetupPermissionError):
        assign(env, caller=principal(outsider))
    assert assign(env, caller=principal(outsider, full_access=True)).status is SetupStatus.NEW


def test_caller_cannot_supply_master_status_or_employee_site(env):
    uow, manager, technician, _, _ = env
    with pytest.raises(SetupPermissionError):
        assign(env, caller=principal(technician))
    uow.context.people[manager.actor.id] = replace(manager, site="ЖУК")
    with pytest.raises(SetupPermissionError):
        assign(env, caller=principal(manager))
    assert effects(uow) == (0, 0, 0, 0, 0, 0, 0)


@pytest.mark.parametrize("kind", ["inactive", "missing", "service", "view_as", "wrong_identity", "dto"])
def test_authentication_context_rejected(env, kind):
    uow, manager, _, outsider, _ = env
    caller = principal(manager)
    if kind == "inactive":
        uow.context.people[manager.actor.id] = replace(manager, active=False)
    elif kind == "missing":
        uow.context.people.pop(manager.actor.id)
    elif kind == "service":
        caller = replace(caller, service=True)
    elif kind == "view_as":
        caller = replace(caller, viewer=outsider.actor)
    elif kind == "wrong_identity":
        uow.context.people[manager.actor.id] = outsider
    else:
        caller = {"actor": manager.actor, "full_access": True}
    with pytest.raises(SetupPermissionError):
        assign(env, caller=caller)
    assert effects(uow) == (0, 0, 0, 0, 0, 0, 0)


def test_assignment_replay_returns_original_after_later_changes_with_no_effects(env):
    uow = env[0]
    original = assign(env)
    change(env, original, SetupAction.START)
    before = effects(uow)
    assert assign_setup_task(env[-1], uow, principal=principal(env[1]), now=NOW + timedelta(hours=2)) == original
    assert effects(uow) == before


@pytest.mark.parametrize("part", ["window", "recipe", "actor"])
def test_same_key_different_payload_or_actor_conflicts(env, part):
    uow, manager, _, outsider, command = env
    assign(env)
    before = effects(uow)
    changed = replace(command, **({"planned_finish": NOW + timedelta(hours=2)} if part == "window" else
                                 {"recipe": replace(command.recipe, version=3)} if part == "recipe" else {}))
    with pytest.raises(SetupCommandConflict):
        assign(env, command=changed, caller=principal(outsider if part == "actor" else manager, full_access=True))
    assert effects(uow) == before


def test_replay_does_not_bypass_inactive_identity(env):
    uow, manager, _, _, _ = env
    assign(env)
    uow.context.people[manager.actor.id] = replace(manager, active=False)
    with pytest.raises(SetupPermissionError):
        assign(env)
    assert uow.commits == 1


def test_complete_transition_history_and_ready_release(env):
    uow = env[0]
    task = assign(env)
    task = change(env, task, SetupAction.START)
    task = change(env, task, SetupAction.BLOCK, reason="Нет оснастки", machine_available=False)
    assert task.id in uow.reservations.items
    task = change(env, task, SetupAction.RESUME)
    task = change(env, task, SetupAction.READY)
    assert task.status is SetupStatus.READY and task.version == 5
    assert task.id not in uow.reservations.items
    assert [item.version for item in uow.transitions.items] == [2, 3, 4, 5]
    assert [item.status for item in uow.transitions.items] == [
        SetupStatus.IN_PROGRESS, SetupStatus.BLOCKED, SetupStatus.IN_PROGRESS, SetupStatus.READY,
    ]
    assert all(item.actor_id == env[2].actor.id for item in uow.transitions.items)
    assert len(uow.audit.items) == len(uow.outbox.items) == len(uow.commands.items) == 5


@pytest.mark.parametrize("available", [True, False])
def test_block_release_depends_on_explicit_availability(env, available):
    uow = env[0]
    task = change(env, assign(env), SetupAction.START)
    task = change(env, task, SetupAction.BLOCK, reason="Проверка", machine_available=available)
    assert (task.id in uow.reservations.items) is not available
    assert task.blocked_machine_available is available
    assert ("release_person", task.id) in uow.reservations.calls


def test_resume_reacquires_released_reservation_and_conflict_rolls_back(env):
    uow = env[0]
    task = change(env, assign(env), SetupAction.START)
    blocked = change(env, task, SetupAction.BLOCK, reason="Проверка", machine_available=True)
    before = effects(uow)
    uow.reservations.external_busy = True
    with pytest.raises(ConcurrentSetupUpdate, match="занят"):
        change(env, blocked, SetupAction.RESUME)
    assert uow.tasks.get(task.id) == blocked
    assert effects(uow) == before
    uow.reservations.external_busy = False
    resumed = change(env, blocked, SetupAction.RESUME)
    assert resumed.id in uow.reservations.items


def test_same_site_machine_not_eligible_for_issued_operation_rejected(env):
    command = env[4]
    env[0].context.operations[command.operation] = IssuedSetupOperation(command.operation, "ЦАР", ("ОБ.027.3",))
    with pytest.raises(SetupValidationError, match="не подходит"):
        assign(env)


def test_assign_replay_requires_current_master_authority(env):
    assign(env)
    uow, manager, _, _, _ = env
    before = effects(uow)
    uow.context.people[manager.actor.id] = replace(manager, site_master=False)
    with pytest.raises(SetupPermissionError):
        assign(env)
    assert effects(uow) == before


def test_transition_replay_requires_current_assignee_site(env):
    initial = assign(env)
    change(env, initial, SetupAction.START, key="start-once")
    uow, _, technician, _, _ = env
    before = effects(uow)
    uow.context.people[technician.actor.id] = replace(technician, site="ЖУК")
    with pytest.raises(SetupPermissionError):
        change(env, initial, SetupAction.START, key="start-once")
    assert effects(uow) == before


@pytest.mark.parametrize("action", [SetupAction.START, SetupAction.BLOCK, SetupAction.RESUME, SetupAction.READY])
def test_only_assignee_can_transition_even_admin_cannot_work_on_behalf(env, action):
    uow, manager, _, _, _ = env
    task = assign(env)
    if action in {SetupAction.BLOCK, SetupAction.RESUME, SetupAction.READY}:
        task = change(env, task, SetupAction.START)
    if action is SetupAction.RESUME:
        task = change(env, task, SetupAction.BLOCK, reason="нет", machine_available=False)
    before = effects(uow)
    with pytest.raises(SetupPermissionError):
        change(env, task, action, caller=principal(manager, full_access=True),
               **({"reason": "нет", "machine_available": False} if action is SetupAction.BLOCK else {}))
    assert effects(uow) == before


@pytest.mark.parametrize("state", ["new", "started", "blocked_available", "blocked_unavailable"])
def test_cancel_requires_manager_and_releases_only_setup_reservation(env, state):
    uow, _, technician, outsider, _ = env
    task = assign(env)
    if state != "new":
        task = change(env, task, SetupAction.START)
    if state.startswith("blocked"):
        task = change(env, task, SetupAction.BLOCK, reason="нет", machine_available=state == "blocked_available")
    before = effects(uow)
    for caller in (principal(technician), principal(outsider)):
        with pytest.raises(SetupPermissionError):
            change(env, task, SetupAction.CANCEL, caller=caller)
        assert effects(uow) == before
    result = change(env, task, SetupAction.CANCEL, reason="Изменился план")
    assert result.status is SetupStatus.CANCELLED
    assert result.id not in uow.reservations.items
    assert uow.transitions.items[-1].reason == "Изменился план"


@pytest.mark.parametrize("assignee_change", [{"active": False}, {"site": "ЖУК"}], ids=["deactivated", "transferred"])
@pytest.mark.parametrize("admin", [False, True], ids=["site-master", "full-access"])
def test_cancel_after_assignee_change_uses_machine_site_and_releases_reservation(env, assignee_change, admin):
    uow, manager, technician, outsider, _ = env
    task = assign(env)
    uow.context.people[technician.actor.id] = replace(technician, **assignee_change)
    before = effects(uow)
    with pytest.raises(SetupPermissionError):
        change(env, task, SetupAction.CANCEL, caller=principal(outsider))
    assert effects(uow) == before
    caller = principal(outsider, full_access=True) if admin else principal(manager)
    with pytest.raises(ConcurrentSetupUpdate):
        change(env, replace(task, version=task.version + 1), SetupAction.CANCEL, caller=caller)
    assert effects(uow) == before
    assert task.id in uow.reservations.items
    cancelled = change(env, task, SetupAction.CANCEL, caller=caller, reason="Переназначение работ")
    assert cancelled.status is SetupStatus.CANCELLED
    assert cancelled.version == task.version + 1
    assert task.id not in uow.reservations.items
    assert uow.tasks.get(task.id) == cancelled
    assert uow.transitions.items[-1].actor_id == caller.actor.id
    assert uow.audit.items[-1].actor == caller.actor
    assert uow.outbox.items[-1].payload["recipient_user_ids"] == str(technician.actor.id)
    assert effects(uow) == (1, 2, 1, 2, 2, 2, 2)


def test_transition_replay_and_collision_leave_history_unchanged(env):
    uow, _, technician, _, _ = env
    task = assign(env)
    command = ChangeSetupCommand("start-key", task.version, task.id, SetupAction.START)
    started = change_setup_task(command, uow, principal=principal(technician), now=NOW)
    before = effects(uow)
    assert change_setup_task(command, uow, principal=principal(technician), now=NOW + timedelta(hours=1)) == started
    assert effects(uow) == before
    with pytest.raises(SetupCommandConflict):
        change_setup_task(replace(command, reason="Другой запрос"), uow, principal=principal(technician), now=NOW)
    assert effects(uow) == before


def test_stale_version_and_invalid_repeated_transition_emit_nothing(env):
    uow = env[0]
    task = assign(env)
    started = change(env, task, SetupAction.START)
    before = effects(uow)
    with pytest.raises(ConcurrentSetupUpdate):
        change(env, task, SetupAction.START, key="stale")
    with pytest.raises(SetupValidationError):
        change(env, started, SetupAction.START, key="repeated")
    assert effects(uow) == before


@pytest.mark.parametrize("phase", ["assign", "start"])
@pytest.mark.parametrize("failure", ["cas", "commit", "outbox", "receipt"])
def test_all_writes_roll_back_together_on_failure(env, monkeypatch, phase, failure):
    uow = env[0]
    task = assign(env) if phase == "start" else None
    before = effects(uow)
    state = deepcopy(uow.tasks.items), deepcopy(uow.reservations.items)
    if failure == "cas":
        uow.tasks.fail_cas = True
    elif failure == "commit":
        uow.fail_commit = True
    else:
        def fail(_):
            raise RuntimeError("write failed")
        monkeypatch.setattr(type(uow.outbox if failure == "outbox" else uow.commands), "add", lambda self, item: fail(item))
    with pytest.raises((ConcurrentSetupUpdate, RuntimeError)):
        change(env, task, SetupAction.START) if task else assign(env)
    assert effects(uow) == before
    assert (uow.tasks.items, uow.reservations.items) == state


def test_assignment_reservation_conflict_has_no_task_audit_or_notifications(env):
    uow = env[0]
    uow.reservations.external_busy = True
    with pytest.raises(ConcurrentSetupUpdate):
        assign(env)
    assert effects(uow) == (0, 0, 0, 0, 0, 0, 0)


def test_two_assignments_cannot_overlap_in_sequential_fake(env):
    uow = env[0]
    assign(env)
    before = effects(uow)
    with pytest.raises(ConcurrentSetupUpdate):
        assign(env, command=replace(env[-1], idempotency_key="assign-2"))
    assert effects(uow) == before


def test_missing_task_is_rejected_without_events(env):
    uow, _, technician, _, _ = env
    with pytest.raises(SetupValidationError, match="не найдено"):
        change_setup_task(ChangeSetupCommand("missing", 1, uuid4(), SetupAction.START), uow,
                          principal=principal(technician), now=NOW)
    assert effects(uow) == (0, 0, 0, 0, 0, 0, 0)
