from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.project_cards import ProjectTechcard
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.shipping.infrastructure import SqlAlchemyShippingUnitOfWork
from app.modules.requests.infrastructure import SqlAlchemyUnitOfWork
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.production.traceability import ProductionBox, format_box_number
from app.shared.clock import MOSCOW, moscow_now, moscow_today
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor


@pytest.fixture()
def production_api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    factory = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    master_id = UUID("10000000-0000-0000-0000-000000000001")
    operator_id = UUID("10000000-0000-0000-0000-000000000002")
    with sessions() as session:
        session.add_all([
            IdentityUserRow(id=master_id, login="master", email="master@example.test", name="Мастер", department="Производство", role="master", site="ЦАР", active=True),
            IdentityUserRow(id=operator_id, login="operator", email="operator@example.test", name="Оператор", department="Производство", role="staff", site="ЦАР", active=True),
        ])
        session.commit()
    app.state.production_uow_factory = factory
    app.state.techcards_uow_factory = lambda: SqlAlchemyTechcardsUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.shipping_uow_factory = lambda: SqlAlchemyShippingUnitOfWork(sessions)
    app.state.uow_factory = lambda: SqlAlchemyUnitOfWork(sessions)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=master_id, name="Мастер", department="Производство"),
        email="master@example.test", roles=frozenset({"master"}), permissions={"production": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 9, 0)
    app.dependency_overrides[moscow_today] = lambda: datetime(2026, 9, 22).date()
    yield TestClient(app), operator_id
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    app.dependency_overrides.pop(moscow_today, None)
    del app.state.production_uow_factory
    del app.state.techcards_uow_factory
    del app.state.fulfillment_uow_factory
    del app.state.shipping_uow_factory
    del app.state.uow_factory
    del app.state.procurement_uow_factory
    engine.dispose()


def issue_card(project, index, ops, name="Жгут"):
    now = datetime(2026, 9, 22, 9, 0)
    config = {"ops": [
        {"type": "assembly", "stage": stage, "opNumber": code, "tOp": t_op, "tPrep": t_prep}
        for code, stage, t_op, t_prep in ops
    ]}
    card = ProjectTechcard.create(
        project=project, item_index=index, product_name=name,
        source_calculation="", deadline=None, now=now,
    ).rebuild({
        "operations": [{"code": code or f"OP-{i}", "connector": str(i), "side": ""} for i, (code, *_rest) in enumerate(ops, 1)],
        "build_config": config, "kit": {"part_quantity": "10"},
    }, now).issue(now)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def launch_from_card(client, key, project, index, quantity, ops, **extra):
    card = issue_card(project, index, ops)
    return client.post(
        "/api/production/tasks", headers={"Idempotency-Key": key},
        json={"techcard_id": str(card.id), "quantity": quantity, **extra},
    )


def test_master_launches_route_snapshot_assigns_multiple_people_and_preserves_cancelled_task(production_api):
    client, operator_id = production_api
    launched = launch_from_card(
        client, "project-591k3-kb1-v1", "591K.3", "КБ1", 10,
        [("OP-01", "Разделка", 60, 120)], deadline="2026-10-01",
    )
    task = launched.json()["task"]
    assigned = client.post(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id)]},
    )
    assigned_task = assigned.json()["task"]
    cancelled = client.post(
        f"/api/production/tasks/{task['id']}/cancel",
        json={"version": assigned_task["version"], "cancelled": True},
    )
    listed = client.get("/api/production/tasks")

    assert launched.status_code == 200, launched.text
    assert task["number"] == "ПЗ-0001"
    assert (task["project"], task["item"], task["name"]) == ("591K.3", "КБ1", "Жгут")
    assert task["operations"][0]["code"] == "OP-01"
    assert task["operations"][0]["norm_seconds"] == 720
    assert assigned.status_code == 200, assigned.text
    assert assigned_task["operations"][0]["operator_ids"] == [str(operator_id)]
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["task"]["status"] == "Отменено"
    assert listed.json()["tasks"][0]["operations"][0]["operator_ids"] == [str(operator_id)]


def test_operator_cannot_mutate_resource_plan(production_api):
    client, operator_id = production_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"staff"}), permissions={"production": "view"},
    )

    response = client.post(
        "/api/production/tasks", headers={"Idempotency-Key": "forbidden"},
        json={"techcard_id": "10000000-0000-0000-0000-00000000000a", "quantity": 1},
    )

    assert response.status_code == 403


def test_master_replaces_shop_calendar_with_validated_absence(production_api):
    client, operator_id = production_api
    saved = client.put("/api/production/calendar", json={
        "holidays": ["2026-11-04"],
        "shift": {
            "weekdays": [0, 1, 2, 3, 4], "start": "08:30", "finish": "18:00",
            "breaks": [["12:30", "13:00"]], "effective_hours": "8.5",
        },
        "absences": [{
            "operator_id": str(operator_id), "from_date": "2026-10-01",
            "to_date": "2026-10-03", "kind": "Отпуск",
        }],
    })
    loaded = client.get("/api/production/calendar")

    assert saved.status_code == 204, saved.text
    assert loaded.json()["holidays"] == ["2026-11-04"]
    assert loaded.json()["shift"] == {
        "weekdays": [0, 1, 2, 3, 4], "start": "08:30", "finish": "18:00",
        "breaks": [["12:30", "13:00"]], "effective_hours": "8.5",
    }
    absence = loaded.json()["absences"][0]
    assert absence["id"]
    assert {key: absence[key] for key in ("operator_id", "from_date", "to_date", "kind")} == {
        "operator_id": str(operator_id),
        "from_date": "2026-10-01",
        "to_date": "2026-10-03",
        "kind": "Отпуск",
    }


def test_master_previews_and_applies_schedule_from_competency_matrix(production_api):
    client, operator_id = production_api
    skills = client.put(f"/api/production/skills/{operator_id}", json={"skills": [
        {"operation_code": "OP-1", "allowed": True, "stars": 5, "coefficient": "1.2"},
        {"operation_code": "OP-2", "allowed": True, "stars": 4, "coefficient": "1"},
    ]})
    launched = launch_from_card(
        client, "auto-schedule-v1", "708К.2", "КБ1", 20,
        [("OP-1", "Резка", 60, 0), ("OP-2", "Сборка", 120, 0)], deadline="2026-09-30",
    ).json()["task"]
    preview = client.post(f"/api/production/tasks/{launched['id']}/schedule", json={
        "version": launched["version"], "replan": False, "max_crew": 2,
    })
    whole = preview.json()["variants"][0]
    applied = client.post(f"/api/production/tasks/{launched['id']}/schedule/apply", json={
        "version": launched["version"], "replan": False, "max_crew": 2,
        "transfer_batch": whole["transfer_batch"],
    })

    assert skills.status_code == 204, skills.text
    assert preview.status_code == 200, preview.text
    assert whole["transfer_batch"] == 20
    assert len(whole["operations"]) == 2
    assert applied.status_code == 200, applied.text
    assert applied.json()["task"]["version"] == launched["version"] + 1
    assert all(item["operator_ids"] == [str(operator_id)] for item in applied.json()["task"]["operations"])


def test_native_production_plan_combines_project_snapshot_techcard_task_and_journal(production_api):
    client, _operator_id = production_api
    now = datetime(2026, 9, 22, 9, 0)
    card = ProjectTechcard.create(
        project="708К.4", item_index="КБ1", product_name="Жгут",
        source_calculation="", deadline=None, now=now,
    ).rebuild({"operations": [{"code": "OP-1", "connector": "A", "side": "A"}]}, now).issue(now)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()

    snapshot = client.put("/api/production/plan/projects-snapshot", json={"projects": [{
        "project": "708к.4", "customer": "Заказчик", "opened_on": "2026-09-01",
        "deadline": "2026-10-01", "status": "Новый", "terminal": False,
    }]})
    blocked = client.post("/api/production/plan/events", json={
        "project": "708К.4", "kind": "block", "blocker": "СТОП", "text": "Ждём детали",
    })
    deadline = client.post("/api/production/plan/events", json={
        "project": "708К.4", "kind": "deadline", "deadline": "2026-09-30",
    })
    launched = client.post(
        "/api/production/tasks", headers={"Idempotency-Key": "plan-708k4-kb1"},
        json={"techcard_id": str(card.id), "quantity": 10},
    )
    loaded = client.get("/api/production/plan")

    assert snapshot.status_code == 200, snapshot.text
    assert blocked.status_code == 201, blocked.text
    assert deadline.status_code == 201, deadline.text
    assert launched.status_code == 200, launched.text
    assert loaded.status_code == 200, loaded.text
    row = loaded.json()["rows"][0]
    assert row["project"] == "708К.4"
    assert row["customer"] == "Заказчик"
    assert row["stage"] == "В работе"
    assert row["stage_manual"] is False
    assert row["kits"] == [{
        "item_index": "КБ1", "status": "В работе", "manual": False,
        "tc": True, "kit": False,
        "review": {
            "value": "не проверена", "text": "ТК выдана", "who": "генератор",
            "occurred_at": "2026-09-22T09:00:00",
        },
    }]
    assert row["deadline"] == "2026-09-30"
    assert row["days_left"] == 8
    assert row["blocker"]["type"] == "СТОП"
    assert row["plan"] == {"tasks": 1, "assigned": 0, "total": 1, "start": None, "finish": None}
    assert [event["kind"] for event in row["events"]] == ["Дедлайн", "Блокер"]


def test_production_plan_persists_manual_kit_stage_and_review(production_api):
    client, _operator_id = production_api
    client.put("/api/production/plan/projects-snapshot", json={"projects": [{
        "project": "708К.4", "status": "Новый", "terminal": False,
    }]})
    stage = client.post("/api/production/plan/events", json={
        "project": "708К.4", "kind": "stage", "item_index": "КБ1", "value": "КБ набрана",
    })
    review = client.post("/api/production/plan/events", json={
        "project": "708К.4", "kind": "tcReview", "item_index": "КБ1",
        "value": "с погрешностями", "text": "перепутан контакт 2",
    })

    assert stage.status_code == 201, stage.text
    assert review.status_code == 201, review.text
    assert review.json()["event"]["item_index"] == "КБ1"
    assert review.json()["event"]["value"] == "с погрешностями"


def test_production_plan_rejects_note_for_unknown_project(production_api):
    client, _operator_id = production_api
    response = client.post("/api/production/plan/events", json={
        "project": "1К.1", "kind": "note", "text": "Нет в реестре",
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "Проект не найден в реестре."


def test_bare_registry_project_without_kits_or_journal_is_not_in_the_plan(production_api):
    # PP-1, ProdPlan.gs prodPlanData_: строка реестра без состава и без журнала в план не попадает.
    client, _operator_id = production_api
    saved = client.put("/api/production/plan/projects-snapshot", json={"projects": [{
        "project": "714К.", "customer": "Заказчик", "opened_on": "2026-09-01",
        "status": "Новый", "terminal": False,
    }]})
    loaded = client.get("/api/production/plan")

    assert saved.status_code == 200, saved.text
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["rows"] == []


def test_stage_mark_without_kb_sets_stage_of_project_without_kits(production_api):
    client, _operator_id = production_api
    client.put("/api/production/plan/projects-snapshot", json={"projects": [{
        "project": "70К.26.13", "customer": "Заказчик", "status": "Новый", "terminal": False,
    }]})
    stage = client.post("/api/production/plan/events", json={
        "project": "70К.26.13", "kind": "stage", "item_index": "", "value": "Отгружено",
        "text": "по листу цеха",
    })
    blocked = client.post("/api/production/plan/events", json={
        "project": "70К.26.13", "kind": "block", "blocker": "ОЖИД ИНФ", "text": "ждём ответ",
    })
    review = client.post("/api/production/plan/events", json={
        "project": "70К.26.13", "kind": "tcReview", "item_index": "", "value": "проверена",
    })
    bad_kb = client.post("/api/production/plan/events", json={
        "project": "70К.26.13", "kind": "stage", "item_index": "корпус", "value": "В работе",
    })
    loaded = client.get("/api/production/plan")

    assert stage.status_code == 201, stage.text
    assert stage.json()["event"]["item_index"] == ""
    assert blocked.status_code == 201, blocked.text
    assert review.status_code == 422
    assert review.json()["detail"] == "Укажи КБ."
    assert bad_kb.status_code == 422
    assert bad_kb.json()["detail"] == "Укажи КБ."
    row = loaded.json()["rows"][0]
    assert (row["project"], row["stage"], row["stage_manual"], row["kits"]) == ("70К.26.13", "Отгружено", True, [])
    assert row["blocker"]["type"] == "ОЖИД ИНФ"


def test_operator_starts_and_cancels_work_from_confirmed_previous_box(production_api):
    client, operator_id = production_api
    launched = launch_from_card(
        client, "execution-api-v1", "591K.3", "КБ-1", 10,
        [("", "Подготовка", 0, 0), ("", "Сборка", 0, 0)],
    ).json()["task"]
    task = launched
    for operation in launched["operations"]:
        response = client.post(
            f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
            json={"version": task["version"], "operator_ids": [str(operator_id)]},
        )
        assert response.status_code == 200, response.text
        task = response.json()["task"]
    now = datetime(2026, 9, 22, 9, 0)
    source = ProductionBox.create(
        task_id=UUID(task["id"]), operation_id=UUID(task["operations"][0]["id"]),
        item="КБ-1", operation_position=1, operator_id=operator_id, created_at=now,
    ).add_output(Decimal("10"))
    source = source.close(format_box_number(104233), "Оператор", now).confirm("Мастер", now)
    with app.state.production_uow_factory() as uow:
        uow.boxes.save(source)
        uow.commit()
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"staff"}), permissions={"production": "view"},
    )

    inputs = client.get(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][1]['id']}/inputs",
    )
    started = client.post(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][1]['id']}/work/start",
        json={"inputs": [source.number]},
    )
    cancelled = client.post(f"/api/production/work/{started.json()['session']['id']}/cancel")

    assert inputs.status_code == 200, inputs.text
    assert inputs.json()["boxes"][0]["number"] == "104233-2"
    assert started.status_code == 200, started.text
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["session"]["status"] == "Отменена"


def test_operator_corrects_returned_box_down_through_api(production_api):
    client, operator_id = production_api
    master = app.dependency_overrides[authenticated_principal]
    operator = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"staff"}), permissions={"production": "view"},
    )
    task = launch_from_card(
        client, "correction-api-v1", "591K.3", "КБ-1", 10,
        [("", "Подготовка", 0, 0), ("", "Сборка", 0, 0)],
    ).json()["task"]
    for operation in list(task["operations"]):
        task = client.post(
            f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
            json={"version": task["version"], "operator_ids": [str(operator_id)]},
        ).json()["task"]
    now = datetime(2026, 9, 22, 9, 0)
    source = ProductionBox.create(
        task_id=UUID(task["id"]), operation_id=UUID(task["operations"][0]["id"]),
        item="КБ-1", operation_position=1, operator_id=operator_id, created_at=now,
    ).add_output(Decimal("10")).close(format_box_number(104233), "Оператор", now).confirm("Мастер", now)
    with app.state.production_uow_factory() as uow:
        uow.boxes.save(source)
        uow.commit()

    app.dependency_overrides[authenticated_principal] = operator
    started = client.post(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][1]['id']}/work/start",
        json={"inputs": [source.number]},
    ).json()["session"]
    box = client.post(f"/api/production/work/{started['id']}/submit", json={"good_quantity": "10"}).json()["box"]
    app.dependency_overrides[authenticated_principal] = master
    box = client.post(
        f"/api/production/boxes/{box['id']}/return", json={"version": box["version"], "reason": "Пересчитать"},
    ).json()["box"]
    app.dependency_overrides[authenticated_principal] = operator

    outputs = client.get(f"/api/production/boxes/{box['id']}/outputs")
    corrected = client.post(f"/api/production/boxes/{box['id']}/correct", json={
        "version": box["version"], "output_id": outputs.json()["outputs"][0]["id"],
        "good_quantity": "8", "defect_quantity": "0",
    })
    again = client.post(f"/api/production/boxes/{box['id']}/correct", json={
        "version": corrected.json()["box"]["version"], "output_id": corrected.json()["output"]["id"],
        "good_quantity": "7", "defect_quantity": "0",
    })
    after = client.get(f"/api/production/boxes/{box['id']}/outputs")

    assert outputs.status_code == 200, outputs.text
    assert [item["good_quantity"] for item in outputs.json()["outputs"]] == ["10"]
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["box"]["quantity"] == "8"
    assert corrected.json()["box"]["status"] == "Закрыта оператором"
    assert corrected.json()["output"]["correction_of_id"] == outputs.json()["outputs"][0]["id"]
    assert again.status_code == 422
    assert "не возвращена мастером" in again.json()["detail"]
    assert [item["id"] for item in after.json()["outputs"]] == [corrected.json()["output"]["id"]]


def test_launch_only_from_issued_techcard_and_launchable_counts_launched(production_api):
    client, _operator_id = production_api
    now = datetime(2026, 9, 22, 9, 0)
    draft = ProjectTechcard.create(
        project="800К.1", item_index="КБ2", product_name="Черновик",
        source_calculation="", deadline=None, now=now,
    ).rebuild({"operations": [{"code": "OP-1", "connector": "A", "side": "A"}]}, now)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(draft)
        uow.commit()

    refused = client.post(
        "/api/production/tasks", headers={"Idempotency-Key": "draft"},
        json={"techcard_id": str(draft.id), "quantity": 5},
    )
    manual = client.post(
        "/api/production/tasks", headers={"Idempotency-Key": "manual"},
        json={"project": "P", "item": "I", "quantity": 1, "operations": [{"position": 1, "stage": "Этап"}]},
    )
    launched = launch_from_card(client, "kb1-a", "800К.1", "кб1", 7, [("OP-1", "Резка", 30, 60)])
    launchable = client.get("/api/production/launchable")

    assert refused.status_code == 422
    assert refused.json()["detail"] == "Техкарта «800К.1КБ2» не выдана — запускать нечего"
    assert manual.status_code == 422
    assert launched.status_code == 200, launched.text
    assert launchable.json()["items"] == [{
        "techcard_id": launchable.json()["items"][0]["techcard_id"], "project": "800К.1", "index": "кб1",
        "item": "800К.1КБ1", "name": "Жгут", "customer": "", "run": "10", "deadline": None, "launched": 7, "operations": 1,
    }]


def test_assign_preview_warns_about_admission_and_applies_speed_coefficient(production_api):
    client, operator_id = production_api
    master_id = UUID("10000000-0000-0000-0000-000000000001")
    client.put(f"/api/production/skills/{operator_id}", json={"skills": [
        {"operation_code": "OP-1", "allowed": True, "stars": 5, "coefficient": "2"},
    ]})
    task = launch_from_card(client, "coef", "801К.1", "КБ1", 10, [("OP-1", "Резка", 360, 0)]).json()["task"]
    operation = task["operations"][0]

    preview = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id), str(master_id)], "dry": True},
    )
    after = client.get("/api/production/tasks").json()["tasks"][0]
    fast = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id)]},
    )
    codes = client.get("/api/production/skills").json()["operation_codes"]

    assert preview.status_code == 200, preview.text
    assert preview.json()["dry"] is True
    assert preview.json()["plan"]["warnings"] == ["Мастер: нет допуска к операции OP-1"]
    assert after["version"] == task["version"]
    assert after["operations"][0]["operator_ids"] == []
    # 360 с/шт × 10 шт при коэффициенте 2 — 30 минут вместо часа.
    assert fast.json()["plan"] == {"start": "2026-09-22T09:00:00", "finish": "2026-09-22T09:30:00", "warnings": []}
    assert codes == [{"code": "OP-1", "name": "Резка"}]


def test_task_list_reports_operation_fact_state_and_wip(production_api):
    client, operator_id = production_api
    task = launch_from_card(
        client, "fact", "802К.1", "КБ1", 10, [("", "Подготовка", 0, 0), ("", "Сборка", 0, 0)],
    ).json()["task"]
    for operation in list(task["operations"]):
        task = client.post(
            f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
            json={"version": task["version"], "operator_ids": [str(operator_id)]},
        ).json()["task"]
    now = datetime(2026, 9, 22, 9, 0)
    source = ProductionBox.create(
        task_id=UUID(task["id"]), operation_id=UUID(task["operations"][0]["id"]),
        item="КБ1", operation_position=1, operator_id=operator_id, created_at=now,
    ).add_output(Decimal("10")).close(format_box_number(104300), "Оператор", now).confirm("Мастер", now)
    with app.state.production_uow_factory() as uow:
        uow.boxes.save(source)
        uow.commit()
    master = app.dependency_overrides[authenticated_principal]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"staff"}), permissions={"production": "view"},
    )
    started = client.post(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][1]['id']}/work/start",
        json={"inputs": [source.number]},
    ).json()["session"]
    client.post(f"/api/production/work/{started['id']}/submit", json={"good_quantity": "3", "defect_quantity": "1", "defect_character": "Обрыв", "defect_description": "обрыв жилы"})
    mine = client.get("/api/production/my").json()["tasks"]
    app.dependency_overrides[authenticated_principal] = master
    listed = client.get("/api/production/tasks").json()["tasks"][0]["operations"]

    assert listed[0]["state"] == "не начата"
    assert listed[0]["wip"] == {"ok": "6", "closed": "0"}
    assert listed[1]["fact"] == {"good": "3", "defect": "1", "processed": "4"}
    assert listed[1]["state"] == "в работе"
    assert listed[1]["wip"] == {"ok": "0", "closed": "3"}
    assert [item["operations"][0]["first"] for item in mine] == [True, False]
    assert mine[1]["operations"][0]["mates"] == []


def test_production_plan_survives_a_failing_source(production_api):
    # ProdPlan.gs prodPlanData_: «Недоступный источник не валит план — его колонка будет пустой» (PP-5).
    client, _operator_id = production_api
    client.put("/api/production/plan/projects-snapshot", json={"projects": [{
        "project": "714К.", "customer": "Заказчик", "status": "Новый", "terminal": False,
    }]})
    client.post("/api/production/plan/events", json={"project": "714К.", "kind": "note", "text": "в работе"})

    def broken():
        raise RuntimeError("источник недоступен")

    saved = app.state.shipping_uow_factory, app.state.procurement_uow_factory
    app.state.shipping_uow_factory = broken
    app.state.procurement_uow_factory = broken
    try:
        loaded = client.get("/api/production/plan")
    finally:
        app.state.shipping_uow_factory, app.state.procurement_uow_factory = saved

    assert loaded.status_code == 200, loaded.text
    assert [row["project"] for row in loaded.json()["rows"]] == ["714К.1"]


def test_first_operation_starts_and_submits_without_an_issued_kit(production_api):
    # SUPPLYMAP начинает первый этап без выданного комплекта: складские партии — по желанию.
    client, operator_id = production_api
    task = launch_from_card(
        client, "no-kit-v1", "591K.3", "КБ-1", 10, [("", "Подготовка", 0, 0)],
    ).json()["task"]
    operation = task["operations"][0]
    assigned = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id)]},
    )
    assert assigned.status_code == 200, assigned.text
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"staff"}), permissions={"production": "view"},
    )

    started = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", json={},
    )
    assert started.status_code == 200, started.text
    submitted = client.post(
        f"/api/production/work/{started.json()['session']['id']}/submit",
        json={"good_quantity": "3", "defect_quantity": "0"},
    )
    assert submitted.status_code == 200, submitted.text


def test_assigning_operations_one_by_one_keeps_one_time_form(production_api):
    # Демо 29.09: второе поштучное назначение падало 500 — даты из базы без пояса, новая с поясом.
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 22, 9, 0, tzinfo=MOSCOW)
    task = launch_from_card(
        client, "one-by-one-v1", "591K.3", "КБ-1", 10,
        [("", "Подготовка", 60, 0), ("", "Сборка", 60, 0)],
    ).json()["task"]
    for operation in task["operations"]:
        response = client.post(
            f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
            json={"version": task["version"], "operator_ids": [str(operator_id)]},
        )
        assert response.status_code == 200, response.text
        task = response.json()["task"]

    assert task["assigned"] == 2
    assert task["planned_start"] == task["operations"][0]["planned_start"]
    assert task["planned_finish"] == task["operations"][1]["planned_finish"]
