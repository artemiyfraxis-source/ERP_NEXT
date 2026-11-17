"""Grouping historical launches preserves child work and excludes all execution history."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.main import app
from app.modules.production.application import LaunchProductionCommand, launch_production
from app.modules.production.domain import OperationShare
from app.modules.production.launchable import launch_operations, launch_route
from app.modules.production.traceability import WorkSession, WorkStatus, ProductionBox, ProductionOutput
from tests.factories import Actor
from tests.production.test_api import production_api, MASTER_ID  # noqa: F401
from tests.production.test_cutting_groups import NOW, document, issue, groups

URL = "/api/production/cutting-groups/from-existing"


def historical(index, doc=None):
    card = issue(index, doc)
    return launch_production(LaunchProductionCommand(
        idempotency_key=f"historical-{index}", project=card.project, item=card.item_index,
        name=card.product_name, quantity=10, fulfillment_request_id=None, deadline=None,
        operations=launch_operations(launch_route(card.document).steps, 10),
        actor=Actor(id=MASTER_ID, name="Мастер", department="Производство"), now=NOW,
        techcard_id=card.id, techcard_revision=card.revision,
    ), app.state.production_uow_factory())


def set_share(task, operator, *, start=NOW, started=False):
    op = task.operations[0]
    changed = replace(op, shares=(OperationShare(operator, planned_start=start,
        planned_finish=start + timedelta(minutes=10), started_at=NOW if started else None),))
    task = replace(task, operations=(changed, *task.operations[1:]), version=task.version + 1)
    with app.state.production_uow_factory() as uow:
        uow.production.add(task)
        uow.commit()
    return task


def test_four_historical_kbs_preview_apply_and_rerun(production_api):
    client, operator = production_api
    tasks = [historical("КБ1"), historical("КБ2", document(length=2000)),
             historical("КБ3", document(strip=4)), historical("КБ4", document(cables=4))]
    tasks = [set_share(t, operator) for t in tasks]
    preview = client.post(URL, json={"project": "708К."})
    assert preview.status_code == 200, preview.text
    assert {g["project"] for g in preview.json()["groups"]} == {"708К."}
    assert sorted(len(g["members"]) for g in preview.json()["groups"]) == [1, 1, 2]
    assert groups(client) == []
    applied = client.post(URL, json={"project": "708К.", "dry_run": False})
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["created"]) == 3
    assert {g["number"] for g in applied.json()["groups"]} == {"РЗ-0001", "РЗ-0002", "РЗ-0003"}
    assert {g["project"] for g in applied.json()["groups"]} == {"708К."}
    common = next(g for g in groups(client) if len(g["rows"]) == 2)
    assert common["operator_ids"] == [str(operator)]
    assert common["assignment_consistent"] and common["planning_consistent"]
    assert {r["item"] for r in common["rows"]} == {"КБ1", "КБ2"}
    with app.state.production_uow_factory() as uow:
        assert [uow.production.get(t.id) for t in tasks] == tasks
    again = client.post(URL, json={"project": "708К.", "dry_run": False}).json()
    assert again["created"] == [] and again["groups"] == []
    assert len(again["skipped"]) == 4


def test_started_member_excluded(production_api):
    client, operator = production_api
    historical("КБ1")
    started = set_share(historical("КБ2"), operator, started=True)
    result = client.post(URL, json={"project": "708К.", "dry_run": False})
    assert result.status_code == 200, result.text
    assert len(result.json()["created"]) == 1
    assert result.json()["refused"][0]["task_id"] == str(started.id)
    assert len(groups(client)[0]["rows"]) == 1


def test_disagreeing_assignments_preserve_children_and_report(production_api):
    client, operator = production_api
    set_share(historical("КБ1"), operator)
    historical("КБ2")
    result = client.post(URL, json={"project": "708К.", "dry_run": False})
    assert result.status_code == 200, result.text
    group = groups(client)[0]
    assert group["operator_ids"] == [] and not group["assignment_consistent"]
    assert result.json()["groups"][0]["warnings"]
    assert sorted(len(row["operator_ids"]) for row in group["rows"]) == [0, 1]


def test_explicit_selection_and_unknown_task(production_api):
    client, _ = production_api
    one, two = historical("КБ1"), historical("КБ2")
    result = client.post(URL, json={"project": "708К.1", "task_ids": [str(one.id)], "dry_run": False})
    assert result.status_code == 200, result.text
    assert len(groups(client)[0]["rows"]) == 1
    assert groups(client)[0]["rows"][0]["task_id"] != str(two.id)
    assert client.post(URL, json={"project": "OTHER", "task_ids": [str(one.id)]}).status_code == 422


def test_changed_card_refused(production_api):
    client, _ = production_api
    task = historical("КБ1")
    with app.state.techcards_uow_factory() as uow:
        card = uow.techcards.get(task.techcard_id)
        uow.techcards.save(card.rebuild(document(strip=4), NOW))
        uow.commit()
    result = client.post(URL, json={"project": "708К.", "dry_run": False})
    assert result.status_code == 200, result.text
    assert result.json()["created"] == []
    assert "ревизи" in result.json()["refused"][0]["reason"].lower()


@pytest.mark.parametrize("project", ["", "   "])
def test_empty_project_rejected(production_api, project):
    client, _ = production_api
    assert client.post(URL, json={"project": project}).status_code == 422


@pytest.mark.parametrize("history,reason", [("session", "сессия"), ("box", "коробки"), ("output", "выпуск")])
def test_execution_history_without_started_share_is_refused(production_api, history, reason):
    client, operator = production_api
    task = historical("КБ1")
    op = task.operations[0]
    with app.state.production_uow_factory() as uow:
        if history == "session":
            uow.work.save(replace(WorkSession.start(task.id, op.id, operator, NOW), status=WorkStatus.CANCELLED))
        else:
            box = ProductionBox.create(task_id=task.id, operation_id=op.id, item=task.item,
                operation_position=op.position, operator_id=operator, created_at=NOW)
            uow.boxes.save(box)
            if history == "output":
                uow.session.flush()
                uow.outputs.add(ProductionOutput(id=uuid4(), session_id=None, task_id=task.id,
                    operation_id=op.id, operator_id=operator, started_at=NOW, finished_at=NOW,
                    pause_seconds=0, good_quantity=Decimal(0), defect_quantity=Decimal(1), output_box_id=box.id))
        uow.commit()
    result = client.post(URL, json={"project": "708К.", "dry_run": False})
    assert result.status_code == 200, result.text
    assert reason in result.json()["refused"][0]["reason"]
    assert result.json()["created"] == []


def test_foreign_site_is_forbidden_before_apply(production_api):
    from app.modules.identity.infrastructure import IdentityUserRow

    client, operator = production_api
    set_share(historical("КБ1"), operator)
    with app.state.production_uow_factory() as uow:
        uow.session.get(IdentityUserRow, MASTER_ID).site = "ЖУК"
        uow.commit()
    assert client.post(URL, json={"project": "708К.", "dry_run": False}).status_code == 403
    with app.state.production_uow_factory() as uow:
        assert uow.cutting.list() == []


def test_read_only_user_cannot_group_or_preview(production_api):
    from app.shared.http_auth import Principal, authenticated_principal

    client, operator = production_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator, name="Оператор", department="Производство"), email="operator@example.test",
        roles=frozenset({"staff"}), permissions={"production": "view"})
    assert client.post(URL, json={"project": "708К."}).status_code == 403


def test_create_failure_rolls_back_group_and_members(production_api, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from app.modules.production.cutting_infrastructure import SqlAlchemyCuttingRepository

    client, _ = production_api
    historical("КБ1")
    historical("КБ2")
    original = SqlAlchemyCuttingRepository.create_existing

    def fail(self, candidates, now):
        original(self, candidates, now)
        raise IntegrityError("injected after member flush", {}, Exception("conflict"))

    monkeypatch.setattr(SqlAlchemyCuttingRepository, "create_existing", fail)
    assert client.post(URL, json={"project": "708К.", "dry_run": False}).status_code == 409
    assert groups(client) == []


def test_apply_rechecks_started_history_after_discovery(production_api, monkeypatch):
    from app.modules.production.cutting_infrastructure import SqlAlchemyCuttingRepository

    client, operator = production_api
    historical("КБ1")
    task = historical("КБ2")
    original = SqlAlchemyCuttingRepository.execution_reason
    calls = 0

    def became_started(self, operation):
        nonlocal calls
        if operation.id == task.operations[0].id:
            calls += 1
            if calls == 2:
                return "У ПФ есть рабочая сессия"
        return original(self, operation)

    monkeypatch.setattr(SqlAlchemyCuttingRepository, "execution_reason", became_started)
    response = client.post(URL, json={"project": "708К.", "dry_run": False})
    assert response.status_code == 200, response.text
    assert len(response.json()["refused"]) == 1
    assert len(groups(client)[0]["rows"]) == 1


def test_different_windows_keep_common_assignee(production_api):
    client, operator = production_api
    set_share(historical("КБ1"), operator)
    set_share(historical("КБ2"), operator, start=NOW + timedelta(hours=1))
    response = client.post(URL, json={"project": "708К.", "dry_run": False})
    assert response.status_code == 200, response.text
    group = groups(client)[0]
    assert group["operator_ids"] == [str(operator)] and group["assignment_consistent"]
    assert not group["planning_consistent"] and group["common_planned_start"] is None
    assert response.json()["groups"][0]["warnings"]


@pytest.mark.parametrize("apply", [False, True])
def test_cli_default_preview_and_apply(production_api, monkeypatch, capsys, apply):
    import json
    from types import SimpleNamespace
    from app import database
    from app.workflows.cutting_group_existing import main

    client, _ = production_api
    historical("КБ1")
    historical("КБ2")
    monkeypatch.setenv("ERP_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(database, "build_engine", lambda url: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(database, "build_production_uow_factory", lambda engine: app.state.production_uow_factory)
    monkeypatch.setattr(database, "build_techcards_uow_factory", lambda engine: app.state.techcards_uow_factory)
    assert main(["--project", "708К.", *(["--apply"] if apply else [])]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is not apply
    assert len(result["groups"][0]["members"]) == 2
    assert len(groups(client)) == int(apply)
