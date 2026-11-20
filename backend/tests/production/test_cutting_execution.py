"""One real work session, atomic per-KB production and resumable group work."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.infrastructure import ProductionWorkSessionRow, ProductionOutputRow
from app.modules.production.resource_assignment_infrastructure import SqlAlchemyProductionResources
from app.modules.production.resource_infrastructure import ActiveResourceRow, ResourceGenerationRow
from app.shared.clock import moscow_now
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_cutting_groups import batch, document, issue, groups, NOW


@pytest.fixture
def assigned(production_api):
    client, operator = production_api
    batch(client, [issue("КБ1"), issue("КБ2")])
    group = groups(client)[0]
    response = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
                          json={"version": group["version"], "operator_ids": [str(operator)]})
    assert response.status_code == 200, response.text
    return client, operator, response.json()["group"]


def action(client, operator, group, name, *, key=None, **payload):
    return client.post(f"/api/production/cutting-groups/{group['id']}/{name}",
                       params={"on_behalf_of": str(operator)},
                       headers={"Idempotency-Key": key or str(uuid4())},
                       json={"version": group["version"], **payload})


def start(assigned):
    client, operator, group = assigned
    response = action(client, operator, group, "start")
    assert response.status_code == 200, response.text
    return client, operator, response.json()["group"]


def quantities(group, first=(3, 1), second=(7, 0)):
    return [{"member_id": row["id"], "good_quantity": str(good), "defect_quantity": str(defect),
             "defect_character": "Размер" if defect else "", "defect_description": "Короткий рез" if defect else ""}
            for row, (good, defect) in zip(group["rows"], (first, second))]


def test_my_execution_after_group_start(assigned):
    client, operator, group = start(assigned)
    response = client.get("/api/production/my/execution", params={"operator_id": str(operator)})
    assert response.status_code == 200, response.text
    session = response.json()["session"]
    assert session["id"] == group["session"]["id"]
    assert session["cutting_group_id"] == group["id"]


def test_legacy_receipt_replay_refreshes_display_only(assigned):
    from copy import deepcopy
    from app.modules.production.cutting_infrastructure import CuttingActionRow

    client, operator, group = assigned
    started = action(client, operator, group, "start", key="legacy-start")
    assert started.status_code == 200, started.text
    expected = started.json()
    legacy = deepcopy(expected)
    legacy["group"].update(number="РЗ-D1E5842A0FD74795", project="708К.1")
    with app.state.production_uow_factory() as uow:
        receipt = uow.session.get(CuttingActionRow, "legacy-start")
        fingerprint = receipt.fingerprint
        receipt.result = legacy
        uow.commit()
    replay = action(client, operator, group, "start", key="legacy-start")
    assert replay.status_code == 200, replay.text
    assert replay.json() == expected
    with app.state.production_uow_factory() as uow:
        receipt = uow.session.get(CuttingActionRow, "legacy-start")
        assert receipt.fingerprint == fingerprint
        assert receipt.result == legacy
        assert len(uow.session.scalars(select(ProductionWorkSessionRow)).all()) == 1
        assert not uow.session.scalars(select(ProductionOutputRow)).all()


def test_renamed_group_can_be_revalidated_and_started(production_api, monkeypatch):
    from copy import deepcopy

    from app.modules.production.cutting_infrastructure import CuttingMemberRow
    from app.modules.techcards.kitsets import Kitset
    from app.workflows import rename_project as rename_cli
    from app.workflows.rename_project import rename_project

    client, operator = production_api
    cards = []
    for index in ("КБ1", "КБ2"):
        doc = document()
        doc["build_config"].update(projectNo="708К.", itemId="708К." + index)
        for marks in (doc["build_config"]["ops"][1]["marks"], doc["operations"][1]["fields"]["coaxMarks"]):
            for mark in marks:
                mark["text"] = "708К." + index
        cards.append(issue(index, doc))
    batch(client, cards)
    group = groups(client)[0]
    response = client.post(f"/api/production/cutting-groups/{group['id']}/assign",
                           json={"version": group["version"], "operator_ids": [str(operator)]})
    assert response.status_code == 200, response.text
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(Kitset(project="708К.", customer="", calc_source="", assemblies=(),
                               updated_at=NOW, updated_by="Технолог", created_at=NOW))
        uow.commit()
    with app.state.production_uow_factory() as uow:
        engine = uow.session.get_bind()
        before = {row.id: deepcopy(row.row_snapshot) for row in uow.session.scalars(select(CuttingMemberRow))}
    dry_run = rename_project(engine, "708К.", "709К.")
    assert dry_run.counts["production_cutting_members"] == 2
    with app.state.production_uow_factory() as uow:
        assert {row.id: row.row_snapshot for row in uow.session.scalars(select(CuttingMemberRow))} == before
    groups_before = groups(client)

    def fail_after_writes(*_args):
        raise RuntimeError("rename interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(rename_cli, "_restore_checks", fail_after_writes)
        with pytest.raises(RuntimeError, match="rename interrupted"):
            rename_project(engine, "708К.", "709К.", apply=True)
    assert groups(client) == groups_before
    with app.state.production_uow_factory() as uow:
        assert {row.id: row.row_snapshot for row in uow.session.scalars(select(CuttingMemberRow))} == before
    applied = rename_project(engine, "708К.", "709К.", apply=True)
    assert applied.counts == dry_run.counts
    with app.state.production_uow_factory() as uow:
        for row in uow.session.scalars(select(CuttingMemberRow)):
            expected = deepcopy(before[row.id])
            for mark in expected["labels"]:
                mark["text"] = mark["text"].replace("708К.", "709К.")
            doc = expected["document"]
            doc["build_config"]["projectNo"] = "709К."
            doc["build_config"]["itemId"] = doc["build_config"]["itemId"].replace("708К.", "709К.")
            for marks in (doc["build_config"]["ops"][1]["marks"], doc["operations"][1]["fields"]["coaxMarks"]):
                for mark in marks:
                    mark["text"] = mark["text"].replace("708К.", "709К.")
            assert row.row_snapshot == expected
    renamed = groups(client)[0]
    response = client.post(f"/api/production/cutting-groups/{renamed['id']}/revalidate",
                           json={"version": renamed["version"]})
    assert response.status_code == 200, response.text
    _, _, started = start((client, operator, response.json()["group"]))
    assert started["id"] == group["id"]
    assert started["session"]["status"] == "В работе"


def test_downstream_downtime_while_another_kb_is_still_cutting(assigned):
    client, operator, group = start(assigned)
    response = action(client, operator, group, "submit", rows=quantities(group, (10, 0), (1, 0)))
    assert response.status_code == 200, response.text
    group = response.json()["group"]
    row = group["rows"][0]
    with app.state.production_uow_factory() as uow:
        task = uow.production.get(UUID(row["task_id"]))
        downstream_id = str(task.operations[1].id)
    for operation_id in (None, row["operation_id"]):
        blocked = client.post("/api/production/downtime", json={
            "task_id": row["task_id"], "operation_id": operation_id, "reason": "equipment"})
        assert blocked.status_code == 422, blocked.text
    response = client.post("/api/production/downtime", json={
        "task_id": row["task_id"], "operation_id": downstream_id, "reason": "equipment"})
    assert response.status_code == 200, response.text
    assert response.json()["downtime"]["operation_id"] == downstream_id
    assert groups(client)[0]["session"]["status"] == "В работе"


def test_one_session_partial_final_and_idempotent_outputs(assigned):
    client, operator, group = start(assigned)
    session_id = group["session"]["id"]
    with app.state.production_uow_factory() as uow:
        assert len(uow.session.scalars(select(ProductionWorkSessionRow)).all()) == 1
    rows = quantities(group)
    response = action(client, operator, group, "submit", key="partial", rows=rows)
    assert response.status_code == 200, response.text
    assert action(client, operator, group, "submit", key="partial", rows=rows).json() == response.json()
    assert action(client, operator, group, "submit", key="partial", rows=quantities(group, (2, 0))).status_code == 409
    group = response.json()["group"]
    assert group["session"]["id"] == session_id
    assert group["session"]["status"] == "В работе"
    for row, count in zip(group["rows"], (3, 7)):
        assert float(row["good"]) == count
        assert len(row["boxes"]) == 1
        assert float(row["boxes"][0]["quantity"]) == count
    final = action(client, operator, group, "submit", rows=quantities(group, (6, 0), (3, 0)))
    assert final.status_code == 200, final.text
    assert final.json()["group"]["session"] is None
    assert all(row["state"] == "done" for row in final.json()["group"]["rows"])
    with app.state.production_uow_factory() as uow:
        outputs = uow.session.scalars(select(ProductionOutputRow)).all()
        assert len(outputs) == 4
        assert {str(output.task_id) for output in outputs} == {row["task_id"] for row in group["rows"]}
        assert uow.work.current(operator) is None


def test_submission_is_atomic_and_versioned(assigned):
    client, operator, group = start(assigned)
    response = action(client, operator, group, "submit", rows=quantities(group, (3, 0), (11, 0)))
    assert response.status_code == 422, response.text
    with app.state.production_uow_factory() as uow:
        assert not uow.session.scalars(select(ProductionOutputRow)).all()
    assert action(client, operator, {**group, "version": 1}, "submit", rows=quantities(group)).status_code == 409
    duplicate = quantities(group)
    duplicate[1]["member_id"] = duplicate[0]["member_id"]
    assert action(client, operator, group, "submit", rows=duplicate).status_code == 422


def test_member_single_start_conflicts_with_group_and_reverse(assigned):
    client, operator, group = assigned
    row = group["rows"][1]
    url = f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/work/start"
    single = client.post(url, params={"on_behalf_of": str(operator)}, json={})
    assert single.status_code == 200, single.text
    current = groups(client)[0]
    assert action(client, operator, current, "start").status_code == 422
    cancelled = client.post(f"/api/production/work/{single.json()['session']['id']}/cancel",
                            params={"on_behalf_of": str(operator)})
    assert cancelled.status_code == 200, cancelled.text
    client, operator, group = start((client, operator, groups(client)[0]))
    assert client.post(url, params={"on_behalf_of": str(operator)}, json={}).status_code == 422
    for command in ("pause", "resume", "cancel"):
        result = client.post(f"/api/production/work/{group['session']['id']}/{command}",
                             params={"on_behalf_of": str(operator)})
        assert result.status_code == 422, result.text


def test_group_downtime_pauses_one_timer(assigned):
    app.dependency_overrides[moscow_now] = lambda: NOW
    try:
        client, operator, group = start(assigned)
        app.dependency_overrides[moscow_now] = lambda: NOW + timedelta(minutes=2)
        paused = action(client, operator, group, "downtime/start", reason="other", comment="Настройка")
        assert paused.status_code == 200, paused.text
        group = paused.json()["group"]
        assert group["session"]["status"] == "Пауза"
        assert group["downtime"]["comment"] == "Настройка"
        with app.state.production_uow_factory() as uow:
            from app.modules.production.downtime_infrastructure import SqlAlchemyDowntimeRepository
            downtime = SqlAlchemyDowntimeRepository(uow.session).get(UUID(group["downtime"]["id"]))
            assert downtime.project == "708К."
            assert downtime.task_number == group["number"] == "РЗ-0001"
        assert client.post(f"/api/production/downtime/{group['downtime']['id']}/stop",
                           params={"on_behalf_of": str(operator)}).status_code == 422
        assert client.post("/api/production/downtime", params={"on_behalf_of": str(operator)}, json={
            "task_id": group["rows"][1]["task_id"], "reason": "equipment"}).status_code == 422
        assert action(client, operator, group, "submit", rows=quantities(group)).status_code == 422
        app.dependency_overrides[moscow_now] = lambda: NOW + timedelta(minutes=7)
        resumed = action(client, operator, group, "downtime/stop")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["group"]["session"]["pause_seconds"] == 300
        assert resumed.json()["group"]["downtime"] is None
    finally:
        app.dependency_overrides.pop(moscow_now, None)


def test_start_replay_second_operator_and_member_started_elsewhere(assigned):
    from app.modules.identity.infrastructure import IdentityUserRow
    client, operator, group = assigned
    second = uuid4()
    with app.state.production_uow_factory() as uow:
        uow.session.add(IdentityUserRow(id=second, login="second", email="second@example.test", name="Второй",
            department="Производство", role="staff", site="ЦАР", active=True, shop_worker=True))
        uow.commit()
    response = client.post(f"/api/production/cutting-groups/{group['id']}/assign", json={
        "version": group["version"], "operator_ids": [str(operator), str(second)]})
    assert response.status_code == 200, response.text
    group = response.json()["group"]
    row = group["rows"][1]
    single = client.post(f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/work/start",
                        params={"on_behalf_of": str(second)}, json={})
    assert single.status_code == 200, single.text
    group = groups(client)[0]
    assert action(client, operator, group, "start").status_code == 422
    assert client.post(f"/api/production/work/{single.json()['session']['id']}/cancel",
                       params={"on_behalf_of": str(second)}).status_code == 200
    group = groups(client)[0]
    first = action(client, operator, group, "start", key="start-retry")
    assert first.status_code == 200, first.text
    assert action(client, operator, group, "start", key="start-retry").json() == first.json()
    group = first.json()["group"]
    assert action(client, second, group, "start").status_code == 422
    assert action(client, second, group, "submit", rows=quantities(group)).status_code == 422


def test_rejects_child_mutations_and_allows_completed_kb_status(assigned):
    from dataclasses import replace
    from app.modules.production.domain import ProductionStatus, ProductionValidationError
    client, operator, group = start(assigned)
    row = group["rows"][1]
    url = f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}"
    assert client.post(f"{url}/manual-output", json={"good_quantity": 1}).status_code == 422
    assert client.post(f"{url}/assign", json={"version": row["task_version"], "operator_ids": [str(operator)]}).status_code == 422
    with app.state.production_uow_factory() as uow:
        task = uow.production.get(UUID(row["task_id"]))
        with pytest.raises(ProductionValidationError, match="группы"):
            uow.production.add(replace(task, status=ProductionStatus.CANCELLED))
    response = action(client, operator, group, "submit", rows=quantities(group, (10, 0), (1, 0)))
    assert response.status_code == 200, response.text
    with app.state.production_uow_factory() as uow:
        task = uow.production.get(UUID(group["rows"][0]["task_id"]))
        # The ordinary close use case validates downstream output; the group guard must permit its save.
        uow.production.add(replace(task, status=ProductionStatus.DONE))
        uow.commit()
    group = groups(client)[0]
    response = action(client, operator, group, "submit", rows=quantities(group, (0, 0), (9, 0)))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("bad", ["abc", "", "NaN", "Infinity", "-1", "1.0000001"])
def test_rejects_invalid_quantities(assigned, bad):
    client, operator, group = start(assigned)
    rows = quantities(group)
    rows[0]["good_quantity"] = bad
    assert action(client, operator, group, "submit", rows=rows).status_code == 422


@pytest.mark.parametrize("field", ["good_quantity", "defect_quantity"])
def test_rejects_fractional_pieces(assigned, field):
    client, operator, group = start(assigned)
    rows = quantities(group)
    rows[0][field] = "1.5"
    response = action(client, operator, group, "submit", rows=rows)
    assert response.status_code == 422 and "целое число штук" in response.json()["detail"]


def test_partial_submissions_count_shared_elapsed_time_once(assigned):
    from app.modules.production.infrastructure import ProductionWorkTimeLogRow
    client, operator, group = start(assigned)
    app.dependency_overrides[moscow_now] = lambda: NOW + timedelta(minutes=4)
    first = action(client, operator, group, "submit", rows=quantities(group, (2, 0), (4, 0)))
    assert first.status_code == 200, first.text
    app.dependency_overrides[moscow_now] = lambda: NOW + timedelta(minutes=10)
    final = action(client, operator, first.json()["group"], "submit", rows=quantities(group, (8, 0), (6, 0)))
    assert final.status_code == 200, final.text
    with app.state.production_uow_factory() as uow:
        logs = uow.session.scalars(select(ProductionWorkTimeLogRow)).all()
        assert sum(log.running_seconds or 0 for log in logs) == 600


def test_group_materials_keep_per_kb_entitlements_and_rollback(assigned):
    from dataclasses import replace
    from app.modules.fulfillment.domain import FulfillmentStatus
    from app.modules.fulfillment.infrastructure import FulfillmentRequestRow, FulfillmentLineRow
    client, operator, group = assigned
    requests, lines = [uuid4(), uuid4()], [uuid4(), uuid4()]
    with app.state.production_uow_factory() as uow:
        for row, request, line in zip(group["rows"], requests, lines):
            uow.session.add(FulfillmentRequestRow(id=request, number=f"REQ.{row['item']}", source_idempotency_key=str(request),
                source_fingerprint="f", version=1, created_at=NOW, requester="Мастер", purpose=f"708К. {row['item']}",
                deadline=None, status=FulfillmentStatus.ISSUED.value, handout_location=None, return_location=None))
            uow.session.add(FulfillmentLineRow(id=line, request_id=request, position=0, requirement_id=line,
                article="RTK 031", name="Кабель", unit="м", requirement=5, reserved=0, issued=5, returned_good=0,
                defective_returned=0, defective_missing=0, lot_id=uuid4(), lot_warehouse="01", lot_grn="PPM.26.1",
                lot_package="П1", lot_storage_location="A-01"))
            uow.session.flush()
            task = uow.production.get(UUID(row["task_id"]))
            uow.production.add(replace(task, fulfillment_request_id=request))
        uow.commit()
    client, operator, group = start((client, operator, groups(client)[0]))
    rows = quantities(group, (3, 0), (2, 0))
    rows[0]["materials"] = [{"fulfillment_line_id": str(lines[0]), "quantity": "3"}]
    rows[1]["materials"] = [{"fulfillment_line_id": str(lines[0]), "quantity": "1"}]
    assert action(client, operator, group, "submit", rows=rows).status_code == 422
    with app.state.production_uow_factory() as uow:
        assert uow.materials.source(requests[0], lines[0]).available == 5
        assert not uow.session.scalars(select(ProductionOutputRow)).all()
    rows[1]["materials"][0]["fulfillment_line_id"] = str(lines[1])
    response = action(client, operator, group, "submit", rows=rows)
    assert response.status_code == 200, response.text
    with app.state.production_uow_factory() as uow:
        assert uow.materials.source(requests[0], lines[0]).available == 2
        assert uow.materials.source(requests[1], lines[1]).available == 4


def test_group_start_revalidates_card_and_checks_site(assigned):
    from app.modules.identity.infrastructure import IdentityUserRow
    client, operator, group = assigned
    with app.state.techcards_uow_factory() as uow:
        task_id = UUID(group["rows"][0]["task_id"])
        with app.state.production_uow_factory() as production:
            task = production.production.get(task_id)
        card = uow.techcards.get(task.techcard_id)
        uow.techcards.save(card.rebuild(card.document, NOW))
        uow.commit()
    assert action(client, operator, group, "start").status_code == 409
    with app.state.production_uow_factory() as uow:
        uow.session.get(IdentityUserRow, operator).site = "ЖУК"
        uow.commit()
    assert action(client, operator, group, "start").status_code == 403


@pytest.mark.parametrize("mode", ["same", "different", "first_finished"])
def test_group_with_live_resource_claims(assigned, mode):
    from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
    from app.modules.production.resource_assignment_infrastructure import SqlAlchemyProductionResources
    from app.modules.production.resource_infrastructure import ActiveResourceRow, ResourceGenerationRow
    from tests.production.test_resource_assignment_infrastructure import Resolver
    client, operator, group = assigned
    sessions = app.state.production_uow_factory().session_factory
    with sessions() as db:
        db.add(ResourceGenerationRow(id=1, version=0))
        db.commit()
    app.state.production_uow_factory = lambda: SqlAlchemyProductionUnitOfWork(sessions,
        lambda db: SqlAlchemyProductionResources(db, Resolver()))
    machine = str(uuid4())
    bindings = {row["operation_id"]: [{"etk_position": 1, "machine_id": str(uuid4()) if mode == "different" else machine}]
                for row in group["rows"]}
    assigned_response = client.post(f"/api/production/cutting-groups/{group['id']}/assign", json={
        "version": group["version"], "operator_ids": [str(operator)], "machine_bindings": bindings})
    assert assigned_response.status_code == 200, assigned_response.text
    group = assigned_response.json()["group"]
    if mode == "first_finished":
        row = group["rows"][0]
        single = client.post(f"/api/production/tasks/{row['task_id']}/operations/{row['operation_id']}/work/start",
                            params={"on_behalf_of": str(operator)}, json={})
        assert single.status_code == 200, single.text
        finished = client.post(f"/api/production/work/{single.json()['session']['id']}/submit",
                               params={"on_behalf_of": str(operator)}, json={"good_quantity": "10"})
        assert finished.status_code == 200, finished.text
        group = groups(client)[0]
    started = action(client, operator, group, "start")
    if mode == "different":
        assert started.status_code == 422, started.text
        with sessions() as db:
            assert not db.scalars(select(ActiveResourceRow)).all()
        return
    assert started.status_code == 200, started.text
    with sessions() as db:
        assert len(db.scalars(select(ActiveResourceRow)).all()) == 2  # One operator and one shared machine.
    result = action(client, operator, started.json()["group"], "submit", rows=quantities(group, (0 if mode == "first_finished" else 10, 0), (10, 0)))
    assert result.status_code == 200, result.text
    with sessions() as db:
        assert not db.scalars(select(ActiveResourceRow)).all()
