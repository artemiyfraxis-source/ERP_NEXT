from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.cutting_infrastructure import CuttingGroupRow, CuttingMemberRow
from app.modules.production.domain import link_inputs
from app.modules.references.infrastructure import EquipmentMachineRow, EquipmentModelRow, EquipmentUnitRow
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.modules.warehouse.devices import PrinterRow, printer_heartbeat
from app.modules.warehouse.label_jobs import LabelAgentRow, LabelJobRow, claim_marking, report_marking, JobFinished, claim
from app.modules.warehouse.marking_profiles import MarkingBatchRow, MarkingBindingRow, MarkingProfileRow
from app.modules.warehouse.marking_render import presets
from app.modules.warehouse.scan_agents import ScanAgentRow
from app.shared.http_auth import authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.production.test_api import production_api, issue_card  # noqa: F401
from tests.production.test_my_readiness import _as_operator, _launch
from tests.production.test_network_execution import _op

BASE = "/api/production/marking"
NOW = datetime(2026, 9, 22, 9)


@pytest.fixture
def marking(production_api):
    client, operator_id = production_api
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "print": "edit"})
    factory = app.state.production_uow_factory
    task = _launch(factory, operator_id, "marking", link_inputs((_op(1, "Нарезка"), _op(2, "Маркировка")), {2: ((1, Decimal(1)),)}))
    card = issue_card("591K.3", "КБ1", [("OP-01", "Нарезка", "10", "0"), ("OP-02", "Маркировка", "10", "0")])
    equipment_id, model_id, profile_id = uuid4(), uuid4(), uuid4()
    with factory() as uow:
        stored = uow.session.get(ProjectTechcardRow, card.id)
        document = deepcopy(stored.document)
        document["operations"][0]["fields"] = {"marks": [{"text": "КБ-{№:3}", "qty": "1", "material": "Этикетка"},
            {"text": "АБВ", "qty": "2", "material": "Этикетка"}]}
        stored.document = document
        uow.session.add_all([
            EquipmentModelRow(id=model_id),
            EquipmentUnitRow(id=equipment_id, catalog="ob", model_id=model_id, ob_id=equipment_id),
            EquipmentMachineRow(id=equipment_id, tab="ob", equipment_model_id=model_id, display_text="ZCUT-RT430",
                normalized_search="", source_sheet="", sort_key="", fields={"Инв. номер": "ОБ.101"}),
            MarkingProfileRow(id=profile_id, equipment_id=equipment_id, inventory_number="ОБ.101", name="Флажок",
                version=1, calibrated=True, geometry=presets()[-1][2]),
            ScanAgentRow(machine_id="godex-pc", pc_name="Цех", version="2.9.0", first_seen=NOW, last_seen=NOW),
            PrinterRow(machine_id="godex-pc", name="Godex EZ2350i", driver="Godex", port="USB001", kind="gdi_png",
                present=True, last_seen=NOW),
            MarkingBindingRow(equipment_id=equipment_id, version=1, machine_id="godex-pc", printer="Godex EZ2350i"),
        ])
        uow.commit()
    payload = {"profile_id": str(profile_id), "profile_version": 1,
               "source": {"task_id": str(task.id), "operation_id": str(task.operations[0].id), "version": task.version}}
    return client, factory, payload, task, card, equipment_id, operator_id


def preview(marking, payload=None):
    response = marking[0].post(BASE + "/preview", json=payload or marking[2])
    assert response.status_code == 200, response.text
    return response.json()


def enqueue(marking, key="batch", payload=None):
    body = payload or marking[2]
    shown = preview(marking, body)
    request = {**body, "idempotency_key": key, "manifest_hash": shown["manifest_hash"]}
    response = marking[0].post(BASE + "/batches", json=request)
    assert response.status_code == 200, response.text
    return response.json(), request, shown


def test_preview_enqueue_idempotency_and_exact_png_snapshot(marking):
    client, factory, payload, task, *_ = marking
    batch, request, shown = enqueue(marking)
    assert shown["total"] == 15 and batch["total"] == 15
    assert [row["text"] for row in shown["labels"]] == ["КБ-001", "КБ-002", "КБ-003", "КБ-004", "КБ-005", "АБВ"]
    assert client.post(BASE + "/batches", json=request).json()["id"] == batch["id"]
    assert client.post(BASE + "/batches", json={**request, "reason": "Другое"}).status_code == 409
    with factory() as uow:
        assert len(list(uow.session.scalars(select(MarkingBatchRow)))) == 1
        assert task.id in uow.production.marks_issued([task.id])
        assert len(list(uow.session.scalars(select(LabelJobRow)))) == 15
    token = uuid4()
    job = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    assert job["png_b64"] == shown["labels"][0]["png_b64"]
    assert job["sha256"] == shown["labels"][0]["sha256"]
    assert job["copies"] == 1 and job["printer"] == "Godex EZ2350i"


@pytest.mark.parametrize("change", ["profile", "document", "binding", "task"])
def test_enqueue_rejects_stale_preview_without_partial_jobs(marking, change):
    client, factory, payload, task, card, equipment_id, _ = marking
    shown = preview(marking)
    with factory() as uow:
        if change == "profile":
            uow.session.get(MarkingProfileRow, UUID(payload["profile_id"])).version += 1
        elif change == "document":
            row = uow.session.get(ProjectTechcardRow, card.id)
            doc = deepcopy(row.document)
            doc["operations"][0]["fields"]["marks"][0]["text"] = "NEW-{№:3}"
            row.document = doc
        elif change == "binding":
            uow.session.get(MarkingBindingRow, equipment_id).version += 1
        else:
            from app.modules.production.infrastructure import ProductionTaskRow
            uow.session.get(ProductionTaskRow, task.id).version += 1
        uow.commit()
    response = client.post(BASE + "/batches", json={**payload, "idempotency_key": "stale", "manifest_hash": shown["manifest_hash"]})
    assert response.status_code == 409, response.text
    with factory() as uow:
        assert uow.session.scalar(select(LabelJobRow.id)) is None


def test_uncalibrated_overflow_and_access_are_blocked(marking):
    client, factory, payload, task, card, _equipment_id, _operator = marking
    _as_operator(uuid4())
    assert client.post(BASE + "/preview", json=payload).status_code == 403
    _as_operator(task.operations[0].operator_ids[0])
    assigned = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(assigned, permissions={**assigned.permissions, "print": "edit"})
    with factory() as uow:
        uow.session.get(MarkingProfileRow, UUID(payload["profile_id"])).calibrated = False
        uow.commit()
    assert client.post(BASE + "/preview", json=payload).status_code == 409
    with factory() as uow:
        uow.session.get(MarkingProfileRow, UUID(payload["profile_id"])).calibrated = True
        row = uow.session.get(ProjectTechcardRow, card.id)
        doc = deepcopy(row.document)
        doc["operations"][0]["fields"]["marks"][0]["text"] = "Ж" * 300 + "{№}"
        row.document = doc
        uow.commit()
    assert client.post(BASE + "/preview", json=payload).status_code == 422
    with factory() as uow:
        assert not uow.production.marks_issued([task.id])
        assert uow.session.scalar(select(LabelJobRow.id)) is None


def test_queue_unknown_pauses_only_its_target_and_rejects_wrong_attempts(marking):
    client, factory, *_ = marking
    batch, _, _ = enqueue(marking)
    token = uuid4()
    assert claim_marking(factory(), machine_id="wrong-pc", token_id=token, now=NOW) is None
    job = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    assert claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW) is None
    args = dict(machine_id="godex-pc", token_id=token, attempt=job["attempt"], outcome="unknown",
                message="Сбой при передаче", spool_job_id=None, now=NOW)
    with pytest.raises(PermissionError):
        report_marking(factory(), UUID(job["id"]), **{**args, "token_id": uuid4()})
    with pytest.raises(JobFinished):
        report_marking(factory(), UUID(job["id"]), **{**args, "attempt": str(uuid4())})
    report_marking(factory(), UUID(job["id"]), **args)
    report_marking(factory(), UUID(job["id"]), **args)
    assert claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW) is None
    status = client.get(BASE + "/batches/" + batch["id"]).json()
    assert status["status"] == "unknown" and status["status_label"] == "Результат неизвестен"
    resolved = client.post(BASE + f"/batches/{batch['id']}/jobs/{job['id']}/resolve",
                           json={"outcome": "spooled", "reason": "Проверено на станке"})
    assert resolved.status_code == 200, resolved.text
    # The agent re-sends its unknown receipt and learns the operator's decision.
    assert report_marking(factory(), UUID(job["id"]), **args) == "spooled"
    next_job = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    assert next_job["id"] == batch["jobs"][1]["id"]


def test_late_receipt_is_shown_to_the_operator_and_keeps_unknown(marking):
    client, factory, *_ = marking
    batch, _, _ = enqueue(marking)
    token = uuid4()
    job = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    late = NOW + timedelta(seconds=121)
    assert report_marking(factory(), UUID(job["id"]), machine_id="godex-pc", token_id=token, attempt=job["attempt"],
                          outcome="spooled", message="Передано принтеру", spool_job_id=7, now=late) == "unknown"
    with factory() as uow:
        row = uow.session.get(LabelJobRow, UUID(job["id"]))
        assert row.status == "unknown" and row.receipt["spool_job_id"] == 7
    status = client.get(BASE + "/batches/" + batch["id"]).json()
    first = status["jobs"][0]
    assert first["status"] == "unknown" and "Агент сообщил позже — Передано принтеру" in first["message"]


def test_expired_marking_is_never_reclaimed_and_legacy_cannot_take_it(marking):
    _, factory, *_ = marking
    enqueue(marking)
    token = uuid4()
    job = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    assert claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW + timedelta(seconds=121)) is None
    assert claim(factory(), printer="TSC", host="Склад", now=NOW, token_id=uuid4()) is None
    with factory() as uow:
        assert uow.session.get(LabelJobRow, UUID(job["id"])).status == "unknown"
        assert uow.session.get(LabelAgentRow, 1).printer == "TSC"


def test_missing_printer_stops_claim_and_does_not_refresh_inventory(marking):
    _, factory, *_ = marking
    enqueue(marking)
    printer_heartbeat(factory(), "godex-pc", [], NOW)
    assert claim_marking(factory(), machine_id="godex-pc", token_id=uuid4(), now=NOW) is None


def test_explicit_reprint_keeps_serials_and_records_reason(marking):
    client, factory, *_ = marking
    first, request, _ = enqueue(marking)
    reprint = {**request, "idempotency_key": "repeat", "reprint_of": first["id"], "reason": "Повреждена этикетка"}
    response = client.post(BASE + "/batches", json=reprint)
    assert response.status_code == 200, response.text
    assert response.json()["id"] != first["id"] and response.json()["reprint_of"] == first["id"]
    with factory() as uow:
        batches = list(uow.session.scalars(select(MarkingBatchRow)))
        assert batches[0].manifest["labels"] == batches[1].manifest["labels"]


@pytest.mark.parametrize("excluded_state", ["split", "cancelled"])
def test_group_preserves_member_order_quantities_and_excludes_split(marking, excluded_state):
    client, factory, payload, task, card, _equipment, operator = marking
    second = _launch(factory, operator, "second", link_inputs((_op(1, "Нарезка"), _op(2, "Маркировка")), {2: ((1, Decimal(1)),)}))
    excluded = _launch(factory, operator, "excluded", link_inputs((_op(1, "Нарезка"), _op(2, "Маркировка")), {2: ((1, Decimal(1)),)}))
    group_id = uuid4()
    with factory() as uow:
        document = uow.session.get(ProjectTechcardRow, card.id).document
        uow.session.add(CuttingGroupRow(id=group_id, number="РЗ-ТЕСТ", idempotency_key="group",
            project_key=task.project, site="ЦАР", recipe_key="test", recipe_version=1, recipe={}, version=1,
            state="frozen", joinable=False, created_at=NOW))
        for position, entry, state in [(1, second, "active"), (2, excluded, excluded_state), (3, task, "active")]:
            uow.session.add(CuttingMemberRow(id=uuid4(), group_id=group_id, task_id=entry.id,
                operation_id=entry.operations[0].id, position=position, assembly_quantity=entry.quantity,
                blank_quantity=entry.quantity, row_snapshot={"document": document, "techcard_revision": entry.techcard_revision}, state=state))
        uow.commit()
    result = preview(marking, {**payload, "source": {"group_id": str(group_id), "version": 1}})
    assert result["total"] == 30
    assert result["labels"][0]["text"] == "КБ-006"
    assert [row["task_id"] for row in result["labels"]] == [str(second.id)] * 6 + [str(task.id)] * 6
    assert result["labels"][6]["text"] == "КБ-001"
    assert result["labels"][5]["quantity"] == result["labels"][11]["quantity"] == 10


def test_profile_calibration_permissions_and_geometry_revision(marking):
    client, factory, payload, *_ = marking
    path = BASE + "/profiles/" + payload["profile_id"]
    response = client.put(path, json={"version": 1, "calibrated": True, "geometry": presets()[-1][2]})
    assert response.status_code == 403
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "erpref": "edit"})
    response = client.put(path, json={"version": 1, "calibrated": True, "geometry": {**presets()[-1][2], "offset_x_mm": 0.1}})
    assert response.status_code == 200, response.text
    assert response.json()["calibrated"] is False
    sample = client.post(path + "/calibration", json={"version": 2, "print": True, "idempotency_key": "sample"})
    assert sample.status_code == 200, sample.text
    with factory() as uow:
        assert not uow.production.marks_issued([marking[3].id])
        assert len(list(uow.session.scalars(select(LabelJobRow)))) == 1
        assert uow.session.scalar(select(AuditEventRow.id).where(AuditEventRow.event_type == "production.marking_queued"))


def test_cancelled_task_keeps_historical_status_and_unknown_reconciliation(marking):
    client, factory, _payload, task, *_ = marking
    batch, _, _ = enqueue(marking)
    token = uuid4()
    job = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    report_marking(factory(), UUID(job["id"]), machine_id="godex-pc", token_id=token, attempt=job["attempt"],
                   outcome="unknown", message="Проверить", spool_job_id=None, now=NOW)
    with factory() as uow:
        from app.modules.production.infrastructure import ProductionTaskRow
        uow.session.get(ProductionTaskRow, task.id).status = "Отменено"
        uow.commit()
    assert client.get(BASE + "/batches/" + batch["id"]).status_code == 200
    response = client.post(BASE + f"/batches/{batch['id']}/jobs/{job['id']}/resolve",
                           json={"outcome": "failed", "reason": "Проверено"})
    assert response.status_code == 200, response.text


def test_equipment_bootstrap_keeps_stable_uuid_after_inventory_rename(marking):
    client, factory, payload, _task, _card, equipment_id, _operator = marking
    with factory() as uow:
        uow.session.get(MarkingProfileRow, UUID(payload["profile_id"])).equipment_id = None
        uow.commit()
    machine = client.get(BASE + "/machines").json()["machines"][0]
    assert machine["equipment_id"] == str(equipment_id)
    with factory() as uow:
        uow.session.get(EquipmentMachineRow, equipment_id).fields = {"Инв. номер": "ОБ.201"}
        uow.commit()
    machine = client.get(BASE + "/machines").json()["machines"][0]
    assert machine["inventory_number"] == "ОБ.201"
    assert machine["profiles"][0]["equipment_id"] == str(equipment_id)


def test_concurrent_enqueue_and_claim_have_one_winner_and_replay(marking, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
    from app.workflows.marking_labels import BatchInput, enqueue as enqueue_direct

    _client, factory, payload, *_ = marking
    shown = preview(marking)
    principal = app.dependency_overrides[authenticated_principal]()
    request = BatchInput(**payload, idempotency_key="simultaneous", manifest_hash=shown["manifest_hash"])
    path = tmp_path / "concurrent.db"
    with factory().session_factory.kw["bind"].connect() as connection, sqlite3.connect(path) as target:
        connection.connection.driver_connection.backup(target)
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"timeout": 15, "check_same_thread": False})
    sessions = sessionmaker(engine, expire_on_commit=False)
    concurrent_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    try:
        barrier = Barrier(2)
        def create():
            barrier.wait()
            return enqueue_direct(request, principal, concurrent_factory, NOW)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = [future.result() for future in [pool.submit(create), pool.submit(create)]]
        assert a["id"] == b["id"]
        barrier = Barrier(2)
        def take():
            barrier.wait()
            return claim_marking(concurrent_factory(), machine_id="godex-pc", token_id=uuid4(), now=NOW)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.result() for future in [pool.submit(take), pool.submit(take)]]
        assert sum(job is not None for job in outcomes) == 1
        with sessions() as session:
            assert len(list(session.scalars(select(LabelJobRow)))) == 15
    finally:
        engine.dispose()


def test_partial_batch_cannot_be_interrupted_by_new_batch_with_same_timestamp(marking):
    _client, factory, *_ = marking
    first, _, _ = enqueue(marking, "first")
    token = uuid4()
    job = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    report_marking(factory(), UUID(job["id"]), machine_id="godex-pc", token_id=token, attempt=job["attempt"],
                   outcome="spooled", message="", spool_job_id=None, now=NOW)
    second, _, _ = enqueue(marking, "second")
    with factory() as uow:
        for row in uow.session.scalars(select(LabelJobRow).where(LabelJobRow.batch_id == UUID(second["id"]))):
            row.created_at = NOW - timedelta(seconds=1)
        uow.commit()
    following = claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)
    assert following["id"] == first["jobs"][1]["id"]


def test_print_permission_is_required_even_for_an_assigned_operator(marking):
    client, _factory, payload, task, *_ = marking
    _as_operator(task.operations[0].operator_ids[0])
    assert client.post(BASE + "/preview", json=payload).status_code == 403


def test_calibration_preview_can_be_used_before_binding_the_printer(marking):
    client, factory, payload, _task, _card, equipment_id, _operator = marking
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "erpref": "edit"})
    with factory() as uow:
        uow.session.delete(uow.session.get(MarkingBindingRow, equipment_id))
        uow.commit()
    path = BASE + "/profiles/" + payload["profile_id"] + "/calibration"
    assert client.post(path, json={"version": 1, "print": False}).status_code == 200
    assert client.post(path, json={"version": 1, "print": True, "idempotency_key": "sample"}).status_code == 409
