from dataclasses import asdict, replace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
import pytest

from app.modules.equipment_setup.api import router
from tests.equipment_setup.test_application import env, principal
from tests.equipment_setup.test_recipe_application import Recipes


@pytest.fixture
def client(env):
    env[0].recipes = Recipes()
    env[0].PORTS = (*env[0].PORTS, "recipes")
    app = FastAPI()
    app.include_router(router)
    app.state.equipment_setup_uow_factory = lambda: env[0]
    app.state.test_principal = principal(env[1])

    @app.middleware("http")
    async def session(request, call_next):
        request.state.principal = app.state.test_principal
        return await call_next(request)

    with TestClient(app) as client:
        yield client


def payload(env):
    return jsonable_encoder(asdict(env[4]))


def test_assign_and_replay_http_contract(client, env):
    response = client.post("/api/production/equipment-setup/tasks", json=payload(env))
    assert response.status_code == 200, response.text
    assert response.json()["machine_id"] == env[4].machine_id
    again = client.post("/api/production/equipment-setup/tasks", json=payload(env))
    assert again.json() == response.json()
    assert len(env[0].tasks.items) == 1


def test_client_cannot_supply_actor_site_or_eligibility(client, env):
    data = payload(env)
    data.update(actor_id=str(env[2].actor.id), site="ЦАР", eligible_machine_ids=[env[4].machine_id])
    assert client.post("/api/production/equipment-setup/tasks", json=data).status_code == 422
    assert not env[0].tasks.items


@pytest.mark.parametrize("kind", ["service", "view_as", "other_site", "not_manager"])
def test_forbidden_identity_cannot_assign(client, env, kind):
    value = principal(env[1])
    if kind == "service":
        value = replace(value, service=True)
    elif kind == "view_as":
        value = replace(value, viewer=env[3].actor)
    else:
        value = principal(env[3] if kind == "other_site" else env[2])
    client.app.state.test_principal = value
    assert client.post("/api/production/equipment-setup/tasks", json=payload(env)).status_code == 403
    assert not env[0].tasks.items


def test_unconfigured_runtime_returns_503(client, env):
    del client.app.state.equipment_setup_uow_factory
    assert client.post("/api/production/equipment-setup/tasks", json=payload(env)).status_code == 503


def test_transition_uses_session_actor_and_version(client, env):
    task = client.post("/api/production/equipment-setup/tasks", json=payload(env)).json()
    endpoint = f"/api/production/equipment-setup/tasks/{task['id']}/transition"
    data = {"idempotency_key": "start", "expected_version": 1, "action": "start"}
    assert client.post(endpoint, json=data).status_code == 403
    client.app.state.test_principal = principal(env[2])
    response = client.post(endpoint, json=data)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "InProgress"
    data["idempotency_key"] = "different-command"
    assert client.post(endpoint, json=data).status_code == 409


def test_recipe_publish_is_immutable_and_scoped(client, env):
    endpoint = f"/api/production/equipment-setup/recipes/{uuid4()}/versions"
    data = {"expected_version": 0, "machine_id": "ОБ.027.2", "title": "Настройка",
        "panel_settings": [{"label": "Панель", "value": "12"}], "source": "Ручная запись"}
    response = client.post(endpoint, json=data)
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert client.post(endpoint, json=data).json() == response.json()
    data["title"] = "Другая настройка"
    assert client.post(endpoint, json=data).status_code == 409
    client.app.state.test_principal = principal(env[2])
    assert client.post(endpoint, json=data).status_code == 403


def test_trial_http_exact_value_and_no_client_recipe(client, env):
    task = client.post("/api/production/equipment-setup/tasks", json=payload(env)).json()
    client.app.state.test_principal = principal(env[2])
    client.post(f"/api/production/equipment-setup/tasks/{task['id']}/transition",
        json={"idempotency_key": "start", "expected_version": 1, "action": "start"})
    endpoint = f"/api/production/equipment-setup/tasks/{task['id']}/trials/{uuid4()}"
    data = {"expected_version": 2, "label": "Диаметр", "value": "2.0100", "unit": "мм"}
    result = client.put(endpoint, json=data)
    assert result.status_code == 200, result.text
    assert result.json()["value"] == "2.0100"
    assert result.json()["recipe_id"] == task["recipe"]["recipe_id"]
    assert client.put(endpoint, json=data).json() == result.json()
    data["recipe_id"] = str(uuid4())
    assert client.put(endpoint, json=data).status_code == 422


def test_task_reads_are_site_and_assignee_scoped_and_view_as_read_only(client, env):
    task = client.post("/api/production/equipment-setup/tasks", json=payload(env)).json()
    assert len(client.get("/api/production/equipment-setup/tasks").json()["items"]) == 1
    client.app.state.test_principal = principal(env[3])
    assert client.get("/api/production/equipment-setup/tasks").json()["items"] == []
    assert client.get(f"/api/production/equipment-setup/tasks/{task['id']}").status_code == 403
    client.app.state.test_principal = replace(principal(env[2]), viewer=env[3].actor)
    assert client.get(f"/api/production/equipment-setup/tasks/{task['id']}").status_code == 200
    assert client.post(f"/api/production/equipment-setup/tasks/{task['id']}/transition",
        json={"idempotency_key": "start", "expected_version": 1, "action": "start"}).status_code == 403


def test_catalog_reads_are_bounded_minimal_and_current_site_scoped(client, env):
    base = "/api/production/equipment-setup"
    assert client.get(base + "/machines", params={"limit": 201}).status_code == 422
    machines = client.get(base + "/machines").json()["items"]
    assert machines == [{"id": "ОБ.027.2", "name": "ОБ.027.2", "model": "", "site": "ЦАР"}]
    params = {"machine_id": "ОБ.027.2"}
    executors = client.get(base + "/executors", params=params).json()["items"]
    assert {item["id"] for item in executors} == {str(env[1].actor.id), str(env[2].actor.id)}
    assert all(set(item) == {"id", "name", "site"} for item in executors)
    client.app.state.test_principal = principal(env[2])
    assert [item["id"] for item in client.get(base + "/executors", params=params).json()["items"]] == [str(env[2].actor.id)]
    client.app.state.test_principal = principal(env[3])
    assert client.get(base + "/machines").json()["items"] == []
    assert client.get(base + "/executors", params=params).status_code == 403
    assert client.get(base + "/recipes", params=params).status_code == 403


def test_assignment_context_requires_exact_existing_issued_reference(client, env):
    endpoint = "/api/production/equipment-setup/assignment-context"
    params = jsonable_encoder(asdict(env[4].operation))
    result = client.get(endpoint, params=params)
    assert result.status_code == 200, result.text
    assert result.json()["items"][0]["id"] == env[4].machine_id
    params["card_revision"] += 1
    assert client.get(endpoint, params=params).status_code == 404
    params = jsonable_encoder(asdict(env[4].operation))
    params["production_operation_id"] = str(uuid4())
    assert client.get(endpoint, params=params).status_code == 404


def test_recipe_version_and_trial_reads_follow_machine_and_task_rights(client, env):
    base = "/api/production/equipment-setup"
    recipe_id = uuid4()
    data = {"expected_version": 0, "machine_id": "ОБ.027.2", "title": "Настройка",
        "panel_settings": [{"label": "Панель", "value": "12"}], "source": "https://example.test/plain-text-only"}
    recipe = client.post(f"{base}/recipes/{recipe_id}/versions", json=data).json()
    assert client.get(base + "/recipes", params={"machine_id": "ОБ.027.2"}).json()["items"] == [recipe]
    assert client.get(f"{base}/recipes/{recipe_id}/versions/1").json() == recipe
    assert client.get(f"{base}/recipes/{recipe_id}/versions/2").status_code == 404
    task = client.post(base + "/tasks", json=payload(env)).json()
    assert client.get(f"{base}/tasks/{task['id']}/trials").json()["items"] == []
    client.app.state.test_principal = principal(env[3])
    assert client.get(f"{base}/recipes/{recipe_id}/versions/1").status_code == 403
    assert client.get(f"{base}/tasks/{task['id']}/trials").status_code == 403


@pytest.mark.parametrize("endpoint", ["/machines", "/executors?machine_id=ОБ.027.2", "/recipes?machine_id=ОБ.027.2"])
def test_service_identity_cannot_read_setup_catalog(client, env, endpoint):
    client.app.state.test_principal = replace(principal(env[1]), service=True)
    assert client.get("/api/production/equipment-setup" + endpoint).status_code == 403
