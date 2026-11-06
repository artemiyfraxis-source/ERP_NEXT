from uuid import UUID, uuid4

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import launch_from_card, production_api


def task(client):
    return launch_from_card(client, "resources-api", "591K.3", "КБ1", 10,
        [("OP-01", "Разделка", 60, 120)]).json()["task"]


def test_runtime_not_ready_is_explicit_and_does_not_discard_physical_choice(production_api):
    client, operator_id = production_api
    item = task(client)
    url = f"/api/production/tasks/{item['id']}/operations/{item['operations'][0]['id']}"
    response = client.get(f"{url}/resources")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    refused = client.post(f"{url}/assign", json={"version": item["version"], "operator_ids": [str(operator_id)],
        "machine_bindings": [{"etk_position": 1, "machine_id": str(uuid4())}]})
    assert refused.status_code == 422
    with app.state.production_uow_factory() as uow:
        assert uow.production.get(UUID(item["id"])).version == item["version"]


def test_unknown_operation_is_404_and_overview_permission_is_required(production_api):
    client, _ = production_api
    item = task(client)
    assert client.get(f"/api/production/tasks/{item['id']}/operations/{uuid4()}/resources").status_code == 404
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Читатель", department="Производство"), email="reader@example.test", roles=frozenset({"staff"}), permissions={"myops": "view"})
    assert client.get(f"/api/production/tasks/{item['id']}/operations/{item['operations'][0]['id']}/resources").status_code == 403
