"""«ЭТК показывает станок наладки»: READY setup tasks as an overlay over the ETK tooling rows (read side)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.main import app
from app.modules.equipment_setup.infrastructure import SetupTaskRow
from app.modules.production.domain import link_inputs
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.references.infrastructure import EquipmentMachineRow, EquipmentModelRow, EquipmentUnitRow
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import production_api  # noqa: F401 - fixture of the production API
from tests.production.test_my_readiness import _as_operator, _launch
from tests.production.test_network_execution import _op

BASE = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
CARD = uuid4()


def _unit(inventory: str, name: str = "Автомат", article: str = "EW-50A") -> UUID:
    unit_id, model_id = uuid4(), uuid4()
    with app.state.production_uow_factory() as uow:
        uow.session.add_all([
            EquipmentModelRow(id=model_id),
            EquipmentUnitRow(id=unit_id, catalog="ob", model_id=model_id, ob_id=unit_id),
            EquipmentMachineRow(id=unit_id, tab="ob", equipment_model_id=model_id, display_text=name, normalized_search="",
                                source_sheet="", sort_key="",
                                fields={"Тип": "Оборудование", "Инв. номер": inventory, "Наименование": name,
                                        "Артикул": article}),
        ])
        uow.commit()
    return unit_id


def _renumber(unit_id: UUID, inventory: str) -> None:
    with app.state.production_uow_factory() as uow:
        row = uow.session.get(EquipmentMachineRow, unit_id)
        row.fields = {**row.fields, "Инв. номер": inventory}
        uow.commit()


def _setup(task, operation, unit_id, *, position=1, status="Ready", minutes=0, card=CARD, task_id=None):
    with app.state.production_uow_factory() as uow:
        uow.session.add(SetupTaskRow(
            id=uuid4(), version=2, status=status, technician_id=uuid4(), machine_id=unit_id, recipe_id=uuid4(),
            recipe_version=1, created_at=BASE, updated_at=BASE + timedelta(minutes=minutes),
            snapshot={"operation": {"card_id": str(card), "card_revision": 1,
                                    "production_task_id": str(task_id or task.id),
                                    "production_operation_id": str(operation.id), "etk_position": position}}))
        uow.commit()


def _launched(production_api, key="u-1"):  # noqa: F811
    client, operator_id = production_api
    task = _launch(app.state.production_uow_factory, operator_id, key,
                   link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)}))
    return client, operator_id, task, task.operations[0], task.operations[1]


def _url(task, operation):
    return f"/api/production/my/tasks/{task.id}/operations/{operation.id}/etk/units"


def test_latest_ready_wins_per_position_and_rows_are_sorted(production_api):  # noqa: F811
    client, _, task, first, _second = _launched(production_api)
    old, new, other = _unit("ОБ.027.1"), _unit("ОБ.027.2"), _unit("ОБ.030.1", "Станок", "XX-1")
    _setup(task, first, old, minutes=1)
    _setup(task, first, new, minutes=5)
    _setup(task, first, other, position=3, minutes=2)

    response = client.get(_url(task, first))

    assert response.status_code == 200, response.text
    units = response.json()["units"]
    assert [(row["etk_position"], row["inventory_number"]) for row in units] == [(1, "ОБ.027.2"), (3, "ОБ.030.1")]
    row = units[0]
    assert row["unit_id"] == str(new) and row["model"] == "EW-50A" and row["name"] == "Автомат"
    assert row["task_number"] == task.number
    assert datetime.fromisoformat(row["ready_at"]) == BASE + timedelta(minutes=5)
    assert datetime.fromisoformat(row["ready_at"]).tzinfo is not None


def test_only_ready_setups_count(production_api):  # noqa: F811
    client, _, task, first, _second = _launched(production_api)
    unit = _unit("ОБ.027.2")
    for status in ("New", "InProgress", "Blocked", "Cancelled"):
        _setup(task, first, unit, status=status, minutes=9)

    assert client.get(_url(task, first)).json() == {"units": []}


def test_other_operation_and_other_task_are_ignored(production_api):  # noqa: F811
    client, _, task, first, second = _launched(production_api)
    unit = _unit("ОБ.027.2")
    _setup(task, second, unit)
    _setup(task, first, unit, task_id=uuid4())

    assert client.get(_url(task, first)).json() == {"units": []}
    assert [row["inventory_number"] for row in client.get(_url(task, second)).json()["units"]] == ["ОБ.027.2"]


def test_renumbered_unit_shows_the_current_number(production_api):  # noqa: F811
    client, _, task, first, _second = _launched(production_api)
    unit = _unit("ОБ.027.2")
    _setup(task, first, unit)
    _renumber(unit, "ОБ.027.7")

    assert client.get(_url(task, first)).json()["units"][0]["inventory_number"] == "ОБ.027.7"


def test_assigned_operator_reads_and_a_stranger_is_refused(production_api):  # noqa: F811
    client, operator_id, task, first, _second = _launched(production_api)
    _setup(task, first, _unit("ОБ.027.2"))

    _as_operator(operator_id)
    assigned = client.get(_url(task, first))
    _as_operator(uuid4())
    stranger = client.get(_url(task, first))
    missing = client.get(f"/api/production/my/tasks/{uuid4()}/operations/{first.id}/etk/units")

    assert assigned.status_code == 200 and len(assigned.json()["units"]) == 1
    assert stranger.status_code == 403
    assert missing.status_code == 404


def _card_url():
    return f"/api/techcards/projects/{CARD}/setup-units"


def _technologist():
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=uuid4(), name="Технолог", department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}), permissions={"techcards": "view"},
    )


def test_card_scope_lists_several_units_per_position_and_skips_closed_tasks(production_api):  # noqa: F811
    client, operator_id, task, first, _second = _launched(production_api)
    closed = _launch(app.state.production_uow_factory, operator_id, "u-closed", link_inputs((_op(1, "Нарезка"),), {}))
    a, b = _unit("ОБ.027.2"), _unit("ОБ.027.1")
    _setup(task, first, a, minutes=1)
    _setup(task, first, a, minutes=4)  # the same unit twice: one row, the latest
    _setup(task, first, b, minutes=2)
    _setup(closed, closed.operations[0], _unit("ОБ.027.3"), minutes=3)
    _setup(task, first, _unit("ОБ.099.1"), card=uuid4())  # another card
    with app.state.production_uow_factory() as uow:
        uow.session.get(ProductionTaskRow, closed.id).status = "Отменено"
        uow.commit()
    _technologist()

    response = client.get(_card_url())

    assert response.status_code == 200, response.text
    units = response.json()["units"]
    assert [(row["etk_position"], row["inventory_number"]) for row in units] == [(1, "ОБ.027.1"), (1, "ОБ.027.2")]
    assert datetime.fromisoformat(units[1]["ready_at"]) == BASE + timedelta(minutes=4)
    assert units[0]["task_number"] == task.number


def test_card_scope_skips_a_done_task_and_is_empty_without_setups(production_api):  # noqa: F811
    client, _, task, first, _second = _launched(production_api)
    _technologist()
    assert client.get(_card_url()).json() == {"units": []}
    _setup(task, first, _unit("ОБ.027.2"))
    with app.state.production_uow_factory() as uow:
        uow.session.get(ProductionTaskRow, task.id).status = "Выполнено"
        uow.commit()

    assert client.get(_card_url()).json() == {"units": []}


def test_card_scope_needs_the_techcards_section(production_api):  # noqa: F811
    client, operator_id, _task, _first, _second = _launched(production_api)
    _as_operator(operator_id)

    assert client.get(_card_url()).status_code == 403
