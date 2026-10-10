"""Цель 2: план задания (режим, партия, «первые k раньше», команда), нормы из ЭТК по кнопке, «Сдано N шт» ЖУК,
статус «Выполнено» — переходы и версии проверяет сервер."""

from datetime import datetime
from uuid import UUID

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import MASTER_ID, issue_card, launch_from_card, production_api  # noqa: F401

OPS = [("OP-01", "Резка", 60, 120), ("OP-02", "Опрессовка", 30, 60), ("OP-03", "Сборка", 90, 0)]


def _launch(client, key="flow-1", quantity=10, ops=OPS):
    response = launch_from_card(client, key, "591K.3", "КБ1", quantity, ops)
    assert response.status_code == 200, response.text
    return response.json()["task"]


def _flow(client, task, **body):
    return client.put(f"/api/production/tasks/{task['id']}/flow", json={
        "version": task["version"], "flow_mode": "mixed", "transfer_batch": None, "early_quantity": None, "team": [],
        **body,
    })


def _as_operator(operator_id):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"operator"}), permissions={"myops": "edit"},
    )


def _as_master():
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=MASTER_ID, name="Мастер", department="Производство"),
        email="master@example.test", roles=frozenset({"foreman"}), permissions={"production": "edit", "myops": "edit"},
    )


def test_launch_keeps_the_techcard_and_its_revision_and_a_default_plan(production_api):
    client, _ = production_api
    task = _launch(client)

    listed = client.get("/api/production/tasks").json()["tasks"][0]

    assert task["techcard_id"] and task["techcard_revision"] == 1
    assert (task["flow_mode"], task["transfer_batch"], task["early_quantity"], task["team"]) == ("mixed", None, None, [])
    assert listed["delivered"] == 0 and listed["status"] == "Запущено"


def test_job_plan_panel_reads_the_flow_in_shop_dates(production_api):
    client, _ = production_api
    task = _launch(client)

    flow = client.get(f"/api/production/tasks/{task['id']}/flow")

    assert flow.status_code == 200, flow.text
    body = flow.json()
    assert [mode["value"] for mode in body["modes"]] == ["sequential", "parallel", "mixed"]
    assert body["thresholds"] == {"notice": "5", "limit": "15"}
    assert [step["stage"] for step in body["plan"]["steps"]] == ["Резка", "Опрессовка", "Сборка"]
    # Без партии — вся партия целиком: 120 + 600, 60 + 300, 900 чистых секунд одним человеком; подготовку второго
    # этапа (60 с) делают, пока первый ещё режет.
    assert body["plan"]["transfer_batch"] == 10
    assert body["plan"]["work_seconds"] == 720 + 300 + 900
    assert body["plan"]["anchor"].startswith("2026-09-22T09:00")
    assert body["norms"]["outdated"] is False and body["norms"]["current_revision"] == 1


def test_master_sets_mode_batch_early_pieces_and_team_with_a_version_check(production_api):
    client, operator_id = production_api
    task = _launch(client)

    saved = _flow(client, task, flow_mode="parallel", transfer_batch=2, early_quantity=1, team=[str(operator_id)])
    stale = _flow(client, task, flow_mode="sequential")
    flow = client.get(f"/api/production/tasks/{task['id']}/flow").json()

    assert saved.status_code == 200, saved.text
    updated = saved.json()["task"]
    assert (updated["flow_mode"], updated["transfer_batch"], updated["early_quantity"]) == ("parallel", 2, 1)
    assert updated["team"] == [str(operator_id)] and updated["version"] == task["version"] + 1
    assert stale.status_code == 409
    assert flow["plan"]["first_quantity"] == 1 and flow["plan"]["level"] == "notice"  # 1 из 10 — 10 % тиража

    sequential = _flow(client, updated, flow_mode="sequential", transfer_batch=3).json()["task"]
    # Последовательно партия идёт целиком — своя партия не хранится.
    assert (sequential["flow_mode"], sequential["transfer_batch"]) == ("sequential", None)


def test_plan_values_out_of_range_and_strangers_in_the_team_are_refused(production_api):
    client, _ = production_api
    task = _launch(client)

    too_many = _flow(client, task, early_quantity=10)
    batch = _flow(client, task, transfer_batch=11)
    not_fitter = _flow(client, task, team=[str(MASTER_ID)])
    unknown = _flow(client, task, team=[str(UUID(int=99))])

    assert too_many.status_code == 422 and "от 1 до 9 шт" in too_many.json()["detail"]
    assert batch.status_code == 422 and "от 1 до 10 шт" in batch.json()["detail"]
    assert not_fitter.status_code == 422 and "не монтажник" in not_fitter.json()["detail"]
    assert unknown.status_code == 422 and "справочнике" in unknown.json()["detail"]


def test_norms_refresh_from_the_etk_only_by_the_button(production_api):
    client, _ = production_api
    task = _launch(client)
    card_id = task["techcard_id"]
    with app.state.techcards_uow_factory() as uow:
        card = uow.techcards.get(UUID(card_id))
        document = {**card.document, "build_config": {"ops": [
            {"type": "assembly", "stage": stage, "opNumber": code, "tOp": t_op * 2, "tPrep": t_prep}
            for code, stage, t_op, t_prep in OPS
        ]}}
        uow.techcards.save(card.rebuild(document, datetime(2026, 9, 23, 9, 0)))
        uow.commit()

    flow = client.get(f"/api/production/tasks/{task['id']}/flow").json()
    unchanged = client.get("/api/production/tasks").json()["tasks"][0]
    refreshed = client.post(f"/api/production/tasks/{task['id']}/norms/refresh", json={"version": task["version"]})

    # ЭТК перевыпущена (ревизия 2): нормы задания сами не меняются, панель предлагает обновить.
    assert flow["norms"] == {"techcard_id": card_id, "revision": 1, "current_revision": 2, "outdated": True}
    assert unchanged["operations"][0]["seconds_per_unit"] == "60"
    assert refreshed.status_code == 200, refreshed.text
    after = refreshed.json()["task"]
    assert after["techcard_revision"] == 2
    assert [item["seconds_per_unit"] for item in after["operations"]] == ["120", "60", "180"]
    assert after["operations"][0]["norm_seconds"] == 120 + 1200


def test_norms_are_not_refreshed_when_the_etk_steps_changed(production_api):
    client, _ = production_api
    task = _launch(client)
    with app.state.techcards_uow_factory() as uow:
        card = uow.techcards.get(UUID(task["techcard_id"]))
        document = {**card.document, "build_config": {"ops": [
            {"type": "assembly", "stage": stage, "opNumber": code, "tOp": t_op, "tPrep": t_prep}
            for code, stage, t_op, t_prep in [*OPS, ("OP-04", "Контроль", 10, 0)]
        ]}}
        uow.techcards.save(card.rebuild(document, datetime(2026, 9, 23, 9, 0)))
        uow.commit()

    refused = client.post(f"/api/production/tasks/{task['id']}/norms/refresh", json={"version": task["version"]})

    assert refused.status_code == 422 and "Этапы техкарты изменились" in refused.json()["detail"]


def test_zhuk_hands_over_the_whole_run_in_one_go_and_the_task_is_done(production_api):
    client, _ = production_api
    task = _launch(client)
    handed = client.post(f"/api/production/tasks/{task['id']}/handover", json={"version": task["version"], "site": "ЖУК"}).json()["task"]

    delivered = client.post(f"/api/production/tasks/{task['id']}/deliveries", json={"version": handed["version"]})
    again = client.post(f"/api/production/tasks/{task['id']}/deliveries", json={"version": delivered.json()["task"]["version"]})
    listed = client.get("/api/production/tasks").json()["tasks"][0]

    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["delivery"]["quantity"] == 10 and delivered.json()["delivery"]["site"] == "ЖУК"
    assert delivered.json()["task"]["status"] == "Выполнено"
    assert again.status_code == 422
    assert listed["delivered"] == 10 and listed["status"] == "Выполнено"


def test_zhuk_partial_deliveries_cannot_exceed_the_run_and_cancel_reopens(production_api):
    client, _ = production_api
    task = _launch(client)
    current = client.post(f"/api/production/tasks/{task['id']}/handover", json={"version": task["version"], "site": "ЖУК"}).json()["task"]
    path = f"/api/production/tasks/{task['id']}/deliveries"

    first = client.post(path, json={"version": current["version"], "quantity": 4}).json()
    too_many = client.post(path, json={"version": first["task"]["version"], "quantity": 7})
    stale = client.post(path, json={"version": current["version"], "quantity": 1})
    rest = client.post(path, json={"version": first["task"]["version"], "quantity": 6}).json()
    back = client.post(f"/api/production/tasks/{task['id']}/handover", json={"version": rest["task"]["version"], "site": ""})
    cancelled = client.post(
        f"{path}/{rest['delivery']['id']}/cancel", json={"version": rest["task"]["version"]},
    )
    flow = client.get(f"/api/production/tasks/{task['id']}/flow").json()

    assert first["task"]["status"] == "Запущено"
    assert too_many.status_code == 422 and "осталось сдать 6 шт" in too_many.json()["detail"]
    assert stale.status_code == 409
    assert rest["task"]["status"] == "Выполнено"
    assert back.status_code == 422
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["task"]["status"] == "Запущено"
    assert flow["delivered"] == 4
    assert {item["quantity"]: item["cancelled_by"] for item in flow["deliveries"]} == {4: None, 6: "Мастер"}
    # С живой сдачей вернуть на распределение по людям нельзя.
    blocked = client.post(f"/api/production/tasks/{task['id']}/handover", json={"version": cancelled.json()["task"]["version"], "site": ""})
    assert blocked.status_code == 422 and "уже сдал 4 шт" in blocked.json()["detail"]


def test_deliveries_only_for_a_task_handed_to_a_site(production_api):
    client, _ = production_api
    task = _launch(client)

    refused = client.post(f"/api/production/tasks/{task['id']}/deliveries", json={"version": task["version"], "quantity": 2})

    assert refused.status_code == 422 and "только у площадки" in refused.json()["detail"]


def test_shop_task_is_done_only_when_the_final_stage_handed_the_run(production_api):
    client, operator_id = production_api
    task = _launch(client, quantity=3, ops=[("", "Сборка", 0, 0)])
    operation = task["operations"][0]
    early = client.post(f"/api/production/tasks/{task['id']}/status", json={"version": task["version"], "done": True})
    assigned = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id)]},
    ).json()["task"]
    _as_operator(operator_id)
    started = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", json={}).json()
    client.post(f"/api/production/work/{started['session']['id']}/submit", json={"good_quantity": "3"})
    _as_master()
    current = next(item for item in client.get("/api/production/tasks").json()["tasks"] if item["id"] == task["id"])

    done = client.post(f"/api/production/tasks/{task['id']}/status", json={"version": current["version"], "done": True})
    cancel = client.post(f"/api/production/tasks/{task['id']}/cancel", json={"version": done.json()["task"]["version"], "cancelled": True})
    assign = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": done.json()["task"]["version"], "operator_ids": []},
    )
    plan = _flow(client, done.json()["task"], flow_mode="parallel")
    reopened = client.post(f"/api/production/tasks/{task['id']}/status", json={"version": done.json()["task"]["version"], "done": False})

    assert early.status_code == 422 and "сдано 0 из 3 шт" in early.json()["detail"]
    assert assigned["version"] > task["version"]
    assert done.status_code == 200, done.text
    assert done.json()["task"]["status"] == "Выполнено"
    assert cancel.status_code == 422 and "перевести в «Отменено» нельзя" in cancel.json()["detail"]
    assert assign.status_code == 422 and "выполнено" in assign.json()["detail"]
    assert plan.status_code == 422
    assert reopened.status_code == 200 and reopened.json()["task"]["status"] == "Запущено"


def test_reopen_needs_a_done_task(production_api):
    client, _ = production_api
    task = _launch(client)

    refused = client.post(f"/api/production/tasks/{task['id']}/status", json={"version": task["version"], "done": False})

    assert refused.status_code == 422 and "уже в статусе «Запущено»" in refused.json()["detail"]


def test_autoplan_takes_people_only_from_the_task_team(production_api):
    client, operator_id = production_api
    client.put(f"/api/production/skills/{operator_id}", json={"skills": [
        {"operation_code": code, "allowed": True, "stars": 5, "coefficient": "1"} for code, *_ in OPS
    ]})
    task = _launch(client)
    with_team = _flow(client, task, flow_mode="sequential", team=[str(operator_id)]).json()["task"]

    variants = client.post(f"/api/production/tasks/{task['id']}/schedule", json={"version": with_team["version"], "replan": False, "max_crew": 1})

    assert variants.status_code == 200, variants.text
    # Последовательно — один вариант: партия целиком.
    assert [item["transfer_batch"] for item in variants.json()["variants"]] == [10]
    assert {value for item in variants.json()["variants"][0]["operations"] for value in item["operator_ids"]} == {str(operator_id)}
