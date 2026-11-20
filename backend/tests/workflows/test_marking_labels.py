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
            ScanAgentRow(machine_id="godex-pc", pc_name="Цех", version="2.11.0", first_seen=NOW, last_seen=NOW),
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


def test_flag_mark_prints_different_text_on_each_half(marking):
    client, factory, payload, task, card, *_ = marking
    with factory() as uow:
        stored = uow.session.get(ProjectTechcardRow, card.id)
        document = deepcopy(stored.document)
        document["operations"][0]["fields"] = {"marks": [{"text": "X1 | КБ-{№:3}", "qty": "1", "material": "Флажок"},
            {"text": "АБВ", "qty": "1", "material": "Флажок"}]}
        stored.document = document
        uow.commit()
    shown = preview(marking)
    assert [row["halves"] for row in shown["labels"]][:2] == [["X1", "КБ-001"], ["X1", "КБ-002"]]
    assert shown["labels"][-1]["halves"] == ["АБВ", "АБВ"]
    assert shown["labels"][0]["text"] == "X1 | КБ-001"


def test_profile_rename_keeps_calibration_and_is_unique_per_machine(marking):
    client, factory, payload, _task, _card, equipment_id, _ = marking
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "erpref": "edit"})
    with factory() as uow:
        uow.session.add(MarkingProfileRow(id=uuid4(), equipment_id=equipment_id, inventory_number="ОБ.101", name="92,5×20",
                                          version=1, calibrated=False, geometry=presets()[-1][2]))
        uow.commit()
    path = BASE + "/profiles/" + payload["profile_id"]
    body = {"version": 1, "calibrated": True, "geometry": presets()[-1][2]}
    for name, error in (("  ", "Укажите название профиля"), ("92,5×20", "уже есть профиль"), ("Я" * 81, "80 знаков")):
        response = client.put(path, json={**body, "name": name})
        assert response.status_code == 422 and error in response.json()["detail"], response.text
    response = client.put(path, json={**body, "name": " Флажок  100×20 "})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Флажок 100×20" and response.json()["calibrated"] is True
    assert client.put(path, json={**body, "version": 2}).json()["name"] == "Флажок 100×20"
    with factory() as uow:
        event = uow.session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "production.marking_profile_saved")
                                   .order_by(AuditEventRow.occurred_at))
        assert event.details["previous_name"] == "Флажок"


def test_profile_save_rejects_reversed_halves_and_a_raced_duplicate_name(marking, monkeypatch):
    client, factory, payload, _task, _card, equipment_id, _ = marking
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "erpref": "edit"})
    with factory() as uow:
        uow.session.add(MarkingProfileRow(id=uuid4(), equipment_id=equipment_id, inventory_number="ОБ.101", name="92,5×20",
                                          version=1, calibrated=False, geometry=presets()[-1][2]))
        uow.commit()
    path = BASE + "/profiles/" + payload["profile_id"]
    raw = presets()[-1][2]
    response = client.put(path, json={"version": 1, "calibrated": True, "geometry": {**raw, "zones": list(reversed(raw["zones"]))}})
    assert response.status_code == 422 and "левая половина" in response.json()["detail"]
    # The other session commits the same name after our uniqueness check: the database constraint answers in Russian.
    import app.workflows.marking_labels as marking_labels
    monkeypatch.setattr(marking_labels, "profile_name", lambda _session, _profile, _requested: "92,5×20")
    response = client.put(path, json={"version": 1, "calibrated": True, "geometry": raw, "name": "92,5×20"})
    assert response.status_code == 422 and "уже есть профиль" in response.json()["detail"], response.text
    with factory() as uow:
        assert uow.session.get(MarkingProfileRow, UUID(payload["profile_id"])).name == "Флажок"


def test_new_profile_from_copy_is_uncalibrated_audited_and_unique_per_machine(marking):
    client, factory, payload, _task, _card, equipment_id, _ = marking
    path = f"{BASE}/machines/{equipment_id}/profiles"
    body = {"name": " 100×20 ", "copy_from": payload["profile_id"]}
    # Editing the catalog needs the reference-edit right, like saving a profile.
    assert client.post(path, json=body).status_code == 403
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "erpref": "edit"})
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["name"] == "100×20" and created["version"] == 1 and created["calibrated"] is False
    assert created["equipment_id"] == str(equipment_id) and created["geometry"] == presets()[-1][2] and created["geometry"]["peel"] is True
    with factory() as uow:
        row = uow.session.get(MarkingProfileRow, UUID(created["id"]))
        assert row.inventory_number == "ОБ.101" and row.calibrated is False
        assert uow.session.get(MarkingProfileRow, UUID(payload["profile_id"])).calibrated is True
        event = uow.session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "production.marking_profile_created"))
        assert event.details["name"] == "100×20" and event.details["copy_from"] == payload["profile_id"]
    machine = next(item for item in client.get(BASE + "/machines").json()["machines"] if item["equipment_id"] == str(equipment_id))
    assert created in machine["profiles"]
    clash = client.post(path, json={"name": "100×20", "geometry": presets()[-1][2]})
    assert clash.status_code == 409 and "уже есть профиль" in clash.json()["detail"]
    assert client.post(path, json={"name": "ЖУК"}).status_code == 422
    assert client.post(f"{BASE}/machines/{uuid4()}/profiles", json=body).status_code == 404


def test_new_profile_on_another_machine_starts_uncalibrated_from_zero_offsets_and_only_for_marking(marking):
    client, factory, payload, _task, _card, ob101, _ = marking
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "erpref": "edit"})
    wrap, crimper, model = uuid4(), uuid4(), uuid4()
    shifted = {**presets()[-1][2], "offset_x_mm": 1.5, "offset_y_mm": -0.5}
    with factory() as uow:
        source = uow.session.get(MarkingProfileRow, UUID(payload["profile_id"]))
        source.geometry = shifted
        uow.session.add_all([EquipmentModelRow(id=model)] + [row for unit, name, inventory in
            ((wrap, "ZCUT-RT660", "ОБ.102"), (crimper, "Пресс", "ОБ.099")) for row in (
            EquipmentUnitRow(id=unit, catalog="ob", model_id=model, ob_id=unit),
            EquipmentMachineRow(id=unit, tab="ob", equipment_model_id=model, display_text=name, normalized_search="",
                                source_sheet="", sort_key="", fields={"Инв. номер": inventory}))])
        uow.session.add(MarkingBindingRow(equipment_id=wrap, version=1, machine_id="godex-pc", printer="Godex EZ2350i"))
        uow.commit()
    body = {"name": "100×20", "copy_from": payload["profile_id"]}
    # A crimper is not a marking machine: no profile, no bound printer.
    refused = client.post(f"{BASE}/machines/{crimper}/profiles", json=body)
    assert refused.status_code == 422 and "не станок маркировки" in refused.text
    created = client.post(f"{BASE}/machines/{wrap}/profiles", json=body)
    assert created.status_code == 200, created.text
    geometry = created.json()["geometry"]
    # The other printer's calibration offsets are not carried over; the layout is.
    assert geometry["offset_x_mm"] == 0 and geometry["offset_y_mm"] == 0 and geometry["zones"] == shifted["zones"]
    assert created.json()["calibrated"] is False
    # Same machine: its own printer, the offsets stay.
    same = client.post(f"{BASE}/machines/{ob101}/profiles", json={"name": "Копия", "copy_from": payload["profile_id"]})
    assert same.status_code == 200 and same.json()["geometry"]["offset_x_mm"] == 1.5


def test_a_zone_flush_with_the_edge_fits_despite_float_rounding():
    from app.modules.warehouse.marking_render import Geometry
    Geometry.model_validate({**presets()[0][2], "width_mm": 149.7, "zones": [{"x": 139.4, "y": 1, "width": 10.3, "height": 13}],
                             "blank_zones": []})


def test_new_profile_geometry_overrides_the_copy_and_is_validated(marking):
    client, factory, payload, _task, _card, equipment_id, _ = marking
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(principal, permissions={**principal.permissions, "erpref": "edit"})
    path = f"{BASE}/machines/{equipment_id}/profiles"
    raw = {key: value for key, value in presets()[0][2].items() if key != "peel"}
    response = client.post(path, json={"name": "10×60", "copy_from": payload["profile_id"], "geometry": raw})
    assert response.status_code == 200, response.text
    assert response.json()["geometry"]["width_mm"] == 10 and response.json()["geometry"]["peel"] is True and response.json()["calibrated"] is False
    bad = {**raw, "zones": [{"x": 1, "y": 1, "width": 50, "height": 13}]}
    assert client.post(path, json={"name": "Кривой", "geometry": bad}).status_code == 422


def test_preview_does_not_issue_serials_only_the_batch_does(marking):
    """Owner 20.11: «предпросмотр маркировки не должен выдавать серийные номера» — a preview (plain and «оставшиеся»)
    records nothing; the enqueue that persists the batch issues the numbers, and the manifest matches the preview."""
    client, factory, payload, task, *_ = marking
    shown = preview(marking)
    assert [row["text"] for row in shown["labels"]][:2] == ["КБ-001", "КБ-002"]
    preview(marking, {**payload, "remaining": True})
    with factory() as uow:
        assert task.id not in uow.production.marks_issued([task.id])
        assert not uow.session.scalars(select(AuditEventRow).where(
            AuditEventRow.aggregate_id == task.id, AuditEventRow.event_type == "production.marks_issued")).all()
        assert not uow.session.scalars(select(MarkingBatchRow)).all()
    batch, _request, again = enqueue(marking)
    assert again["manifest_hash"] == shown["manifest_hash"] and batch["total"] == shown["total"]
    with factory() as uow:
        assert task.id in uow.production.marks_issued([task.id])