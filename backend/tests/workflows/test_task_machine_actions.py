"""Task machine discovery: exact navigation, capability gates and zero side effects."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4
import threading
import traceback

import pytest
from sqlalchemy import create_engine, event

from app.main import app
from app.modules.identity.infrastructure import IdentityApiTokenRow
from app.modules.production import machine_runs, machine_writes, machines
from app.modules.production.domain import ProductionStatus, link_inputs
from app.modules.production.infrastructure import ProductionTaskRow, SqlAlchemyProductionUnitOfWork
from app.modules.production.resource_assignment_infrastructure import (
    ResourceAllocationMachineRow, ResourceAllocationMemberRow, ResourceAllocationRow,
)
from app.modules.references.infrastructure import EquipmentMachineRow, SqlAlchemyReferencesUnitOfWork
from app.modules.techcards.infrastructure import ProjectTechcardRow, TechcardRevisionRow
from app.modules.warehouse.devices import PrinterRow
from app.modules.warehouse.marking_profiles import MarkingBindingRow, MarkingProfileRow
from app.modules.warehouse.marking_render import presets
from app.modules.warehouse.scan_agents import ScanAgentRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.clock import moscow_now
from app.workflows.production_resource_context import production_resources
from tests.factories import Actor
from tests.production.test_api import issue_card, production_api  # noqa: F401
from tests.production.test_cutting_groups import batch, document
from tests.production.test_cutting_queue import _no_stale_references, issue, on_machine  # noqa: F401
from tests.production.test_my_readiness import _as_operator, _launch
from tests.production.test_network_execution import _op
from tests.test_etk_setup_units import _setup, _unit


def url(task, operation):
    return f"/api/production/my/tasks/{task.id}/operations/{operation.id}/etk/machine-actions"


def read(client, task, operation):
    response = client.get(url(task, operation))
    assert response.status_code == 200, response.text
    return response.json()["actions"]


def seed(production_api, *, inventory="ОБ.027.2", duplicate=False):
    client, operator = production_api
    ops = (_op(1, "Сборка"), _op(2, "Сборка"))
    if duplicate:
        ops = tuple(replace(op, code="DUP") for op in ops)
    task = _launch(app.state.production_uow_factory, operator, "machine-actions", link_inputs(ops, {}))
    card = issue_card(task.project, "КБ1", [(op.code, op.stage, 10, 0) for op in ops])
    unit = _unit(inventory)
    with app.state.production_uow_factory() as uow:
        stored = uow.session.get(ProjectTechcardRow, card.id)
        doc = deepcopy(stored.document)
        for built in doc["operations"]:
            built["type"] = "assembly"
            built["card"] = {"tooling": [{"section": "Оборудование", "inventory_number": inventory}]}
        stored.document = doc
        uow.commit()
    sessions = app.state.techcards_uow_factory().session_factory
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    return client, operator, task, unit, card


def permissions(actor, **values):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=actor, name="Оператор", department="Производство"), email="operator@example.test",
        roles=frozenset({"staff"}), permissions=values)


def test_generic_exact_position_duplicate_codes_and_no_hardware_upload(production_api):
    client, _, task, unit, _ = seed(production_api, duplicate=True)
    second = task.operations[1]
    [row] = read(client, task, second)
    assert row["etk_position"] == 2 and row["equipment_id"] == str(unit)
    assert row["card_kind"] == "generic" and row["can_open"] is True
    assert row["can_load"] is False and row["connectivity"]["fresh"] is False
    assert row["source"] == {"kind": "generic", "task_id": str(task.id), "operation_id": str(second.id),
                             "etk_position": 2, "queue_kind": "machine"}


def test_visibility_reuses_assigned_operation_access_and_section_gate(production_api):
    client, operator, task, _, _ = seed(production_api)
    _as_operator(operator)
    assert client.get(url(task, task.operations[0])).status_code == 200
    _as_operator(uuid4())
    assert client.get(url(task, task.operations[0])).status_code == 403
    permissions(operator)
    assert client.get(url(task, task.operations[0])).status_code == 403
    permissions(operator, myops="view")
    assert client.get(url(task, task.operations[0])).status_code == 200
    assert client.get(url(replace(task, id=uuid4()), task.operations[0])).status_code == 404
    assert client.get(url(task, replace(task.operations[0], id=uuid4()))).status_code == 404


@pytest.mark.parametrize("change", ["missing", "position", "code", "pin"])
def test_missing_or_stale_instruction_never_selects_another_source(production_api, change):
    client, _, task, _, card = seed(production_api)
    with app.state.production_uow_factory() as uow:
        stored = uow.session.get(ProjectTechcardRow, card.id)
        if change == "missing":
            stored.status = "draft"
        elif change == "pin":
            uow.session.get(ProductionTaskRow, task.id).techcard_revision = 999
        else:
            doc = deepcopy(stored.document)
            if change == "position":
                doc["operations"] = []
                doc["build_config"]["ops"] = []
            else:
                doc["operations"][0]["code"] = "OTHER"
                doc["build_config"]["ops"][0]["opNumber"] = "OTHER"
            stored.document = doc
        uow.commit()
    [row] = read(client, task, task.operations[0])
    assert row["source"] is None and row["blocker"]
    assert not row["can_open"] and not row["can_load"]


def test_ambiguous_units_never_pick_first_and_ready_setup_resolves_identity(production_api):
    client, _, task, first, _ = seed(production_api)
    second = _unit("ОБ.027.2")  # duplicate inventory: identities differ
    operation = task.operations[0]
    [unresolved] = read(client, task, operation)
    assert unresolved["equipment_id"] is None and unresolved["card_kind"] == "none"
    assert not unresolved["can_open"]
    _setup(task, operation, second)
    # The explicit model and READY unit resolve a legacy duplicate inventory. A number
    # alone must never pick one of two UUIDs/models.
    with app.state.production_uow_factory() as uow:
        card = uow.session.query(ProjectTechcardRow).one()
        doc = deepcopy(card.document)
        doc["operations"][0]["card"]["tooling"][0]["equipment_model_id"] = str(
            uow.session.get(EquipmentMachineRow, second).equipment_model_id)
        card.document = doc
        uow.commit()
    [row] = read(client, task, operation)
    assert row["equipment_id"] == str(second) and row["equipment_id"] != str(first)


def marking_seed(production_api):
    client, operator, task, unit, card = seed(production_api, inventory="ОБ.101")
    now = app.dependency_overrides[moscow_now]()
    with app.state.production_uow_factory() as uow:
        row = uow.session.get(ProjectTechcardRow, card.id)
        doc = deepcopy(row.document)
        doc["operations"][0]["fields"] = {"marks": [{"text": "№ {№}", "qty": "1", "material": "Этикетка"}]}
        row.document = doc
        uow.session.add_all([
            MarkingProfileRow(id=uuid4(), equipment_id=unit, inventory_number="ОБ.101", name="Флажок",
                version=1, calibrated=True, geometry=presets()[-1][2]),
            ScanAgentRow(machine_id="godex", pc_name="ПК", version="2.11.0", first_seen=now, last_seen=now),
            PrinterRow(machine_id="godex", name="Godex", driver="Godex", port="USB001", kind="gdi_png",
                present=True, last_seen=now),
            MarkingBindingRow(equipment_id=unit, version=1, machine_id="godex", printer="Godex"),
        ])
        uow.commit()
    permissions(operator, myops="edit", print="edit")
    return client, operator, task, unit, card


@pytest.mark.parametrize("change,expected", [("ready", True), ("permission", False), ("offline", False),
    ("stale_agent", False), ("hold", False), ("uncalibrated", False)])
def test_marking_readiness_print_permissions_and_exact_versioned_source(production_api, change, expected):
    client, operator, task, _, _ = marking_seed(production_api)
    with app.state.production_uow_factory() as uow:
        if change == "offline":
            uow.session.get(PrinterRow, ("godex", "Godex")).present = False
        if change == "stale_agent":
            uow.session.get(ScanAgentRow, "godex").last_seen -= timedelta(minutes=5)
        if change == "hold":
            uow.session.get(PrinterRow, ("godex", "Godex")).hold = "Проверьте отделитель"
        if change == "uncalibrated":
            uow.session.query(MarkingProfileRow).update({"calibrated": False})
        uow.commit()
    if change == "permission":
        permissions(operator, myops="edit")
    [row] = read(client, task, task.operations[0])
    assert row["can_open"] is True and row["can_load"] is expected
    assert row["card_kind"] == "marking"
    assert row["source"] == {"kind": "marking", "source": {"task_id": str(task.id),
        "operation_id": str(task.operations[0].id), "version": task.version}, "selected": [f"{task.id}:0:0"]}
    assert bool(row["blocker"]) is not expected


ADAPTERS = ["marking", "ob019", "generic", "generic_cut"]


def adapter_seed(production_api, adapter):
    if adapter == "marking":
        client, _, task, _, _ = marking_seed(production_api)
        return client, task
    if adapter == "generic":
        client, _, task, _, _ = seed(production_api)
        return client, task
    client, _ = production_api
    inventory = "ОБ.019" if adapter == "ob019" else "ОБ.095"
    unit = _unit(inventory)
    batch(client, [issue("708К.", "КБ1", on_machine(wire_document(), inventory))])
    with app.state.production_uow_factory() as uow:
        [task] = list(uow.production.with_status(ProductionStatus.RUNNING))
    if adapter == "ob019":
        connect(unit)
    sessions = app.state.techcards_uow_factory().session_factory
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    return client, task


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_discovery_repeatedly_executes_only_selects_and_never_issues_serials(production_api, monkeypatch, adapter):
    client, task = adapter_seed(production_api, adapter)
    def forbidden(*args, **kwargs):
        pytest.fail("Discovery called a serial issuer or writer")
    monkeypatch.setattr("app.modules.production.api.issue_mark_serials", forbidden)
    monkeypatch.setattr("app.workflows.marking_labels.issue_mark_serials_in", forbidden)
    monkeypatch.setattr("app.modules.production.application.issue_mark_serials_in", forbidden)
    monkeypatch.setattr("app.modules.production.machine_writes.enqueue", forbidden)
    statements = []
    with app.state.production_uow_factory() as uow:
        engine = uow.session.get_bind()
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for _ in range(2):
            row = read(client, task, task.operations[0])[0]
            assert row["can_open"] is True
            assert row["can_load"] is (adapter in ("marking", "ob019"))
            if adapter == "generic_cut":
                assert row["source"]["queue_kind"] == "cutting"
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements and set(statements) == {"SELECT"}
    with app.state.production_uow_factory() as uow:
        assert task.id not in uow.production.marks_issued([task.id])


@pytest.mark.parametrize("inventory,kind", [("ОБ.019", "ob019"), ("ОБ.099.1", "ob099")])
def test_supported_cards_do_not_imply_loading_or_connectivity(production_api, inventory, kind):
    client, _, task, _, _ = seed(production_api, inventory=inventory)
    [row] = read(client, task, task.operations[0])
    assert row["card_kind"] == kind and not row["can_load"]
    assert not row["connectivity"]["fresh"] and row["blocker"]
    if kind == "ob099":
        assert row["can_open"] is True


def test_group_source_carries_exact_length_members_and_detects_stale_card(production_api):
    client, _ = production_api
    unit = _unit("ОБ.019")
    cards = [issue("708К.", index, on_machine(document(length=length), "ОБ.019"))
             for index, length in [("КБ1", 1000), ("КБ2", 1500), ("КБ3", 1000)]]
    batch(client, cards)
    with app.state.production_uow_factory() as uow:
        tasks = list(uow.production.with_status(ProductionStatus.RUNNING))
    task = next(task for task in tasks if "КБ1" in task.item)
    actions = read(client, task, task.operations[0])
    row = actions[0]
    assert row["equipment_id"] == str(unit) and row["can_open"] and not row["can_load"]
    assert row["source"]["kind"] == "ob019" and row["source"]["length_mm"] == "1000"
    assert len(row["source"]["members"]) == 2
    assert len(actions) == 3  # every operation of the mixed preparation PF, not only the cable cut
    with app.state.production_uow_factory() as uow:
        uow.session.get(ProjectTechcardRow, cards[0].id).version += 1
        uow.commit()
    stale = read(client, task, task.operations[0])[0]
    assert stale["source"] is None and not stale["can_open"] and "изменилась" in stale["blocker"]


def test_partial_group_permissions_never_expose_other_members(production_api):
    client, operator = production_api
    _unit("ОБ.019")
    batch(client, [issue("708К.", index, wire_document()) for index in ("КБ1", "КБ2")])
    with app.state.production_uow_factory() as uow:
        tasks = list(uow.production.with_status(ProductionStatus.RUNNING))
    task = tasks[0]
    operation = task.operations[0]
    response = client.post(f"/api/production/tasks/{task.id}/operations/{operation.id}/assign",
        json={"version": task.version, "operator_ids": [str(operator)]})
    assert response.status_code == 200, response.text
    _as_operator(operator)
    [row, *_] = read(client, task, operation)
    assert row["source"] is None and not row["can_open"] and not row["can_load"]
    assert "не назначена" in row["blocker"]


def test_current_allocation_uses_stable_unit_and_ignores_retired_members(production_api):
    client, operator, task, unit, _ = seed(production_api)
    operation = task.operations[0]
    with app.state.production_uow_factory() as uow:
        for current, machine in [(True, unit), (False, uuid4())]:
            allocation = uuid4()
            uow.session.add(ResourceAllocationRow(id=allocation, task_id=task.id, operation_id=operation.id, crew=False))
            uow.session.flush()
            if current:
                uow.session.add(ResourceAllocationMachineRow(allocation_id=allocation, etk_position=1, machine_id=machine))
            uow.session.add(ResourceAllocationMemberRow(id=uuid4(), allocation_id=allocation,
                operation_id=operation.id, operator_id=operator, current=current))
        uow.commit()
    [row] = read(client, task, operation)
    assert row["equipment_id"] == str(unit) and row["can_open"]


def test_pinned_history_is_used_instead_of_current_instruction(production_api):
    client, _, task, unit, card = seed(production_api)
    with app.state.production_uow_factory() as uow:
        stored = uow.session.get(ProjectTechcardRow, card.id)
        revision = stored.revision
        uow.session.add(TechcardRevisionRow(id=uuid4(), project_card_id=card.id, revision=revision,
            reason="Выдано", actor_name="Технолог", occurred_at=stored.updated_at,
            source="manual", document=deepcopy(stored.document)))
        doc = deepcopy(stored.document)
        doc["operations"][0]["card"]["tooling"] = []
        stored.document, stored.revision = doc, revision + 1
        task_row = uow.session.get(ProductionTaskRow, task.id)
        task_row.techcard_revision, task_row.techcard_id = revision, card.id
        uow.commit()
    [row] = read(client, task, task.operations[0])
    assert row["equipment_id"] == str(unit) and row["can_open"]


def wire_document():
    doc = on_machine(document(), "ОБ.019")
    doc["operations"][0].update(type="cutWireStrip", code="CUT_WIRE")
    doc["operations"][0]["fields"] = {"strip": {
        "a": True, "b": True, "lenA": "5", "lenB": "6", "modeA": "full", "modeB": "full"}}
    doc["build_config"]["ops"][0].update(type="cutWireStrip", opNumber="CUT_WIRE")
    doc["build_config"]["wires"] = [{"art": "ПВАМ 0,35", "length": "1000", "qty": 1}]
    return doc


def connect(unit):
    now = app.dependency_overrides[moscow_now]()
    token = uuid4()
    with app.state.production_uow_factory() as uow:
        uow.session.add(IdentityApiTokenRow(id=token, name="Агент", token_hash=str(token), scopes=["machine:report"],
            created_at=now, expires_at=now + timedelta(days=1)))
        uow.session.flush()
        uow.session.add(machines.MachineLinkRow(equipment_id=unit, inventory_number="ОБ.019", kind=machines.KIND_OB019,
            token_id=token, machine_id="machine", bound_at=now, version=1))
        uow.session.flush()
        uow.session.add(machines.MachineSnapshotRow(equipment_id=unit, link_state="ok", boot="boot", seq=1,
            proto=1, registers={"766": 102, "649": 0, "650": 0}, reported_at=now))
        uow.commit()


@pytest.mark.parametrize("change,expected", [("ready", True), ("frozen", True), ("stale", False),
    ("counter_stale", False), ("busy", False), ("permission", False), ("active_same", True), ("active_other", False)])
def test_ob019_loading_requires_live_supported_source_and_permission(production_api, change, expected):
    client, operator = production_api
    unit = _unit("ОБ.019")
    batch(client, [issue("708К.", "КБ1", wire_document())])
    with app.state.production_uow_factory() as uow:
        [task] = list(uow.production.with_status(ProductionStatus.RUNNING))
    connect(unit)
    with app.state.production_uow_factory() as uow:
        snapshot = uow.session.get(machines.MachineSnapshotRow, unit)
        if change == "stale":
            snapshot.reported_at -= timedelta(seconds=20)
        elif change == "counter_stale":
            snapshot.reported_at -= timedelta(seconds=7)
        elif change == "busy":
            snapshot.link_state = "port_busy"
        elif change == "frozen":
            member = uow.cutting.for_operation(task.operations[0].id)
            uow.cutting.get(member.group_id).state = "frozen"
        elif change in ("active_same", "active_other"):
            member = uow.cutting.for_operation(task.operations[0].id)
            now = snapshot.reported_at
            run = machine_runs.cutting(member.group_id if change == "active_same" else uuid4(), "1000", "Резка", 10)
            uow.session.add(machine_writes.MachineWriteRow(id=uuid4(), equipment_id=unit, purpose="transfer", status="done",
                payload={"run": run}, summary="Резка", created_by=operator, created_at=now, finished_at=now))
        uow.commit()
    if change == "permission":
        permissions(operator, production="view", myops="view")
    [row, *_] = read(client, task, task.operations[0])
    assert row["card_kind"] == "ob019" and row["can_open"]
    assert row["can_load"] is expected, row
    assert bool(row["blocker"]) is not expected
    assert row["source"]["length_mm"] == "1000"


class _OneUowPerThread:
    """Wraps the production UoW factory and records every UoW entered while another is open in the same thread.

    Production UoWs take one global resource lock (PostgreSQL advisory lock / SQLite BEGIN IMMEDIATE): a second
    one in the same request waits forever for the first (prod 23.11). Detected here without any real lock."""

    def __init__(self, factory):
        self.factory, self.local, self.nested = factory, threading.local(), []

    def __call__(self):
        guard, uow = self, self.factory()

        class Guarded:
            def __getattr__(self, name):
                return getattr(uow, name)

            def __enter__(self):
                depth = getattr(guard.local, "depth", 0)
                if depth:
                    guard.nested.append("".join(traceback.format_stack(limit=8)))
                guard.local.depth = depth + 1
                try:
                    uow.__enter__()
                except BaseException:
                    guard.local.depth = depth
                    raise
                return uow

            def __exit__(self, *exc):
                guard.local.depth -= 1
                return uow.__exit__(*exc)

        return Guarded()


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_discovery_never_opens_a_second_production_uow_inside_the_first(production_api, adapter):
    client, task = adapter_seed(production_api, adapter)
    guard = app.state.production_uow_factory = _OneUowPerThread(app.state.production_uow_factory)
    row = read(client, task, task.operations[0])[0]
    assert guard.nested == [], guard.nested[0]
    assert row["can_open"] is True
    if adapter.startswith("generic"):
        assert row["source"]["queue_kind"] == ("cutting" if adapter == "generic_cut" else "machine")


@pytest.fixture
def file_production_api(monkeypatch, tmp_path, request):
    """production_api over a file SQLite with several connections; the lock wait is cut to 0.5 s."""
    from tests.production import test_api

    monkeypatch.setattr(test_api, "create_engine", lambda *_args, **_kwargs: create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'erp.db'}", connect_args={"check_same_thread": False, "timeout": 0.5}))
    return request.getfixturevalue("production_api")


@pytest.mark.parametrize("adapter", ["generic", "generic_cut"])
def test_discovery_with_the_resource_lock_does_not_wait_for_itself(file_production_api, adapter):
    """The runtime factory (resource_factory set): each production UoW begins IMMEDIATE, so a nested one is refused."""
    client, task = adapter_seed(file_production_api, adapter)
    sessions = app.state.production_uow_factory().session_factory
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions, production_resources)
    row = read(client, task, task.operations[0])[0]
    assert row["can_open"] is True and row["source"]["queue_kind"] == (
        "cutting" if adapter == "generic_cut" else "machine")
