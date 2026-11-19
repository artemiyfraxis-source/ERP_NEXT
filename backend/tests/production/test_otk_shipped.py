"""Владелец 18.11: отгруженное не висит в «Очереди ОТК» — коробка КБ, уехавшей целиком, помечена `kb_shipped`."""

from datetime import datetime
from decimal import Decimal

from app.main import app
from app.modules.shipping.application import ShipBeforeErpCommand, ship_before_erp
from app.modules.shipping.domain import FinishedGood
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import launch_from_card, production_api  # noqa: F401

NOW = datetime(2026, 11, 18, 9, 0)
SHIPPER = Actor(id=None, name="Кладовщик", department="Склад")


def finished_good(project: str, index: str, ordered: str, sent: str) -> None:
    """КБ в ГП с заказом `ordered`, из него уехало `sent` (как отгруженное до ERP — приняли и отгрузили)."""
    with app.state.shipping_uow_factory() as uow:
        uow.goods.add(FinishedGood.create(project=project, item_index=index, customer="Заказчик", article="",
                                          name="Жгут", ordered=Decimal(ordered), unit="шт"))
        uow.commit()
    if Decimal(sent):
        ship_before_erp(ShipBeforeErpCommand(project, index, Decimal(sent), SHIPPER, NOW), app.state.shipping_uow_factory())


def closed_final_box(client, operator_id, key: str, project: str, index: str) -> dict:
    master = app.dependency_overrides[authenticated_principal]
    task = launch_from_card(client, key, project, index, 5, [("", "Подготовка", 0, 0)]).json()["task"]
    operation = task["operations"][0]
    task = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
                       json={"version": task["version"], "operator_ids": [str(operator_id)]}).json()["task"]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"operator"}), permissions={"myops": "edit"},
    )
    try:
        started = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start",
                              json={"inputs": []}).json()["session"]
        submitted = client.post(f"/api/production/work/{started['id']}/submit", json={"good_quantity": "5"})
    finally:
        app.dependency_overrides[authenticated_principal] = master
    assert submitted.status_code == 200, submitted.text
    return submitted.json()["box"]


def test_box_of_a_fully_shipped_kb_is_marked_and_a_partly_shipped_one_is_not(production_api):
    client, operator_id = production_api
    shipped = closed_final_box(client, operator_id, "otk-shipped-1", "180К.11", "КБ1")
    partly = closed_final_box(client, operator_id, "otk-shipped-2", "180К.11", "КБ2")
    waiting = closed_final_box(client, operator_id, "otk-shipped-3", "180К.11", "КБ3")
    finished_good("180К.11", "КБ1", "5", "5")
    finished_good("180К.11", "КБ2", "5", "3")

    response = client.get("/api/production/boxes")

    assert response.status_code == 200, response.text
    flags = {box["id"]: box["kb_shipped"] for box in response.json()["boxes"]}
    assert flags[shipped["id"]] is True
    assert flags[partly["id"]] is False  # уехала не вся КБ — коробка ещё ждёт ОТК
    assert flags[waiting["id"]] is False  # КБ ещё нет в ГП
    assert {box["status"] for box in response.json()["boxes"]} == {"Закрыта оператором"}  # данные не меняются
