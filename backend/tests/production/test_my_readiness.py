"""«Мои задания» монтажника: готовность входов, коробки только своего ПЗ, лёгкое чтение."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.main import app
from app.modules.production.application import (
    AssignProductionCommand,
    LaunchProductionCommand,
    assign_production,
    launch_production,
)
from app.modules.production.domain import ProductionValidationError, link_inputs
from app.modules.production.execution import StartWorkCommand, start_work
from app.modules.production.infrastructure import SqlAlchemyBoxRepository, SqlAlchemyProductionRepository
from app.modules.production.traceability import ProductionBox, format_box_number
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import production_api  # noqa: F401 — фикстура API цеха
from tests.production.test_network_execution import MASTER, NOW, _op, merge_operations


def _launch(factory, operator_id, key, operations, item="591K.3.КБ1"):
    task = launch_production(LaunchProductionCommand(
        idempotency_key=key, project="591K.3", item=item, name="Жгут", quantity=5,
        fulfillment_request_id=None, deadline=None, operations=operations, actor=MASTER, now=NOW,
    ), factory())
    for operation in task.operations:
        task, _ = assign_production(AssignProductionCommand(
            task_id=task.id, expected_version=task.version, operation_id=operation.id,
            operator_ids=(operator_id,), requested_start=None, actor=MASTER, now=NOW,
        ), factory())
    return task


def _box(task, position, sequence, operator_id, quantity="5", confirmed=True):
    operation = next(item for item in task.operations if item.position == position)
    box = ProductionBox.create(
        task_id=task.id, operation_id=operation.id, item=task.item,
        operation_position=position, operator_id=operator_id, created_at=NOW,
    ).add_output(Decimal(quantity)).close(format_box_number(sequence), "Оператор", NOW)
    return box.confirm("Мастер", NOW) if confirmed else box


def _save(factory, *boxes):
    with factory() as uow:
        for box in boxes:
            uow.boxes.save(box)
        uow.commit()


def _as_operator(operator_id):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"operator"}), permissions={"myops": "edit"},
    )


def _readiness(client):
    rows = {row["operations"][0]["position"]: row["operations"][0] for row in client.get("/api/production/my").json()["tasks"]}
    return {position: row["readiness"] for position, row in rows.items()}, rows


def test_readiness_walks_from_not_submitted_to_ready_to_assemble(production_api):  # noqa: F811
    client, operator_id = production_api
    factory = app.state.production_uow_factory
    task = _launch(factory, operator_id, "ready-1", merge_operations())
    _as_operator(operator_id)

    states, _ = _readiness(client)
    assert states[1] == {"state": "ready", "text": "можно начинать", "boxes": []}
    assert states[3]["state"] == "waiting_stage"
    assert states[3]["text"] == "ждёт: Нарезка А не сдан"

    waiting = _box(task, 1, 104301, operator_id, "10", confirmed=False)
    _save(factory, waiting)
    states, _ = _readiness(client)
    assert states[3]["state"] == "waiting_master"
    assert states[3]["text"] == "ждёт: Нарезка А — коробка ещё у мастера"

    cut_a = _box(task, 1, 104302, operator_id, "10")
    _save(factory, cut_a)
    states, _ = _readiness(client)
    assert states[3]["text"] == "ждёт: Нарезка Б не сдан"
    assert [(box["number"], box["quantity"], box["stage"]) for box in states[3]["boxes"]] == [(cut_a.number, "10", "Нарезка А")]

    cut_b = _box(task, 2, 104303, operator_id, "5")
    _save(factory, cut_b)
    states, _ = _readiness(client)
    assert states[3]["state"] == "assembly"
    assert states[3]["text"] == "готово к сборке"
    assert sorted(box["number"] for box in states[3]["boxes"]) == sorted([cut_a.number, cut_b.number])
    # Маркировка ждёт сборку, которой ещё нет.
    assert states[4]["text"] == "ждёт: Сборка не сдан"


def test_single_input_ready_and_remaining_is_the_share(production_api):  # noqa: F811
    client, operator_id = production_api
    factory = app.state.production_uow_factory
    operations = link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)})
    task = _launch(factory, operator_id, "ready-2", operations)
    _save(factory, _box(task, 1, 104311, operator_id, "5"))
    _as_operator(operator_id)

    states, rows = _readiness(client)
    assert states[2]["state"] == "ready" and states[2]["text"] == "можно начинать"
    assert [box["quantity"] for box in states[2]["boxes"]] == ["5"]
    assert rows[2]["remaining"] == "5"


def test_my_reads_only_own_tasks_and_their_boxes(production_api, monkeypatch):  # noqa: F811
    client, operator_id = production_api
    factory = app.state.production_uow_factory
    mine = _launch(factory, operator_id, "light-mine", link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)}))
    foreign = launch_production(LaunchProductionCommand(
        idempotency_key="light-foreign", project="591K.4", item="591K.4.КБ1", name="Чужой", quantity=3,
        fulfillment_request_id=None, deadline=None,
        operations=link_inputs((_op(1, "Нарезка"),), {}), actor=MASTER, now=NOW,
    ), factory())
    _save(factory, _box(foreign, 1, 104321, operator_id))
    _as_operator(operator_id)
    calls = []
    real_box_list, real_task_list = SqlAlchemyBoxRepository.list, SqlAlchemyProductionRepository.list
    monkeypatch.setattr(SqlAlchemyBoxRepository, "list", lambda self, **kw: calls.append(("boxes", kw)) or real_box_list(self, **kw))
    monkeypatch.setattr(SqlAlchemyProductionRepository, "list", lambda self, *a, **kw: calls.append(("tasks", kw)) or real_task_list(self, *a, **kw))

    tasks = client.get("/api/production/my").json()["tasks"]

    assert {row["id"] for row in tasks} == {str(mine.id)}
    assert ("tasks", {}) not in calls and not any(name == "tasks" for name, _ in calls)
    box_calls = [kw for name, kw in calls if name == "boxes"]
    assert box_calls and all(kw.get("task_id") == mine.id and kw["limit"] <= 2_000 for kw in box_calls)


def test_start_work_refuses_a_box_of_another_task_with_the_same_item(production_api):  # noqa: F811
    client, operator_id = production_api
    factory = app.state.production_uow_factory
    chain = lambda: link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)})
    first = _launch(factory, operator_id, "foreign-1", chain())
    second = _launch(factory, operator_id, "foreign-2", chain())
    foreign_box = _box(first, 1, 104331, operator_id)
    _save(factory, foreign_box)
    actor = Actor(id=operator_id, name="Оператор", department="Производство")
    operation = next(item for item in second.operations if item.position == 2)

    with pytest.raises(ProductionValidationError, match="из другого производственного задания"):
        start_work(StartWorkCommand(
            task_id=second.id, operation_id=operation.id, box_numbers=(foreign_box.number,), actor=actor, now=NOW,
        ), factory())

    own = _box(second, 1, 104332, operator_id)
    _save(factory, own)
    session = start_work(StartWorkCommand(
        task_id=second.id, operation_id=operation.id, box_numbers=(own.number,), actor=actor, now=NOW,
    ), factory())
    assert session.task_id == second.id


def _etk_url(task, position=2):
    operation = next(item for item in task.operations if item.position == position)
    return f"/api/production/my/tasks/{task.id}/operations/{operation.id}/etk"


def test_operation_etk_for_assigned_fitter_and_master_but_not_a_stranger(production_api):  # noqa: F811
    from tests.production.test_api import issue_card

    client, operator_id = production_api
    factory = app.state.production_uow_factory
    task = _launch(factory, operator_id, "etk-1", link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)}))
    card = issue_card("591K.3", "КБ1", [("OP-01", "Нарезка", "10", "0"), ("OP-02", "Сборка", "10", "0")])
    url = _etk_url(task)

    master = client.get(url)  # фикстура входит мастером с правом production
    _as_operator(operator_id)
    fitter = client.get(url)
    _as_operator(uuid4())
    stranger = client.get(url)

    assert master.status_code == 200, master.text
    assert fitter.status_code == 200, fitter.text
    body = fitter.json()
    assert body["card"]["id"] == str(card.id) and body["card"]["status"] == "Выдана"
    assert body["focus"]["position"] == 2
    assert [item["code"] for item in body["card"]["document"]["operations"]] == ["OP-01", "OP-02"]
    assert stranger.status_code == 403


def test_operation_etk_without_an_issued_card_is_a_russian_404(production_api):  # noqa: F811
    from datetime import datetime

    from app.modules.techcards.project_cards import ProjectTechcard

    client, operator_id = production_api
    factory = app.state.production_uow_factory
    task = _launch(factory, operator_id, "etk-2", link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)}))
    _as_operator(operator_id)

    missing = client.get(_etk_url(task))
    draft = ProjectTechcard.create(project="591K.3", item_index="КБ1", product_name="Жгут", source_calculation="", deadline=None, now=datetime(2026, 9, 22, 9, 0))
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(draft)
        uow.commit()
    not_issued = client.get(_etk_url(task))

    assert missing.status_code == 404 and missing.json()["detail"] == "Нет выданной ЭТК по этой позиции"
    assert not_issued.status_code == 404 and not_issued.json()["detail"] == "Нет выданной ЭТК по этой позиции"


def test_fitter_reads_sketches_of_his_issued_card_only_and_cannot_edit_techcards(production_api):  # noqa: F811
    from datetime import datetime

    from app.modules.techcards.project_cards import OperationSketch
    from tests.production.test_api import issue_card

    client, operator_id = production_api
    factory = app.state.production_uow_factory
    task = _launch(factory, operator_id, "etk-sk", link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)}))
    card = issue_card("591K.3", "КБ1", [("OP-01", "Нарезка", "10", "0"), ("OP-02", "Сборка", "10", "0")])
    other = issue_card("591K.9", "КБ1", [("OP-01", "Нарезка", "10", "0")])
    png = b"PNG-sketch-bytes"
    for target, key in ((card, "OP-02"), (other, "SECRET")):
        with app.state.techcards_uow_factory() as uow:
            uow.techcards.put_sketch(target.id, OperationSketch(
                operation_key=key, file_name=f"{key}.png", content_type="image/png", size=len(png),
                uploaded_by="Технолог", uploaded_at=datetime(2026, 9, 22, 10, 0)), png)
            uow.commit()
    base = _etk_url(task) + "/sketches"

    _as_operator(operator_id)
    listing = client.get(base)
    image = client.get(base + "/OP-02")
    foreign = client.get(base + "/SECRET")
    traversal = client.get(base + "/../../OP-02")
    put = client.put(f"/api/techcards/projects/{card.id}/sketches/OP-02", json={"file_name": "a.png", "content_type": "image/png", "content_base64": "AAAA"})
    delete = client.delete(f"/api/techcards/projects/{card.id}/sketches/OP-02")
    direct = client.get(f"/api/techcards/projects/{card.id}/sketches")
    _as_operator(uuid4())
    stranger_list, stranger_image = client.get(base), client.get(base + "/OP-02")

    assert listing.status_code == 200 and [item["operation_key"] for item in listing.json()["items"]] == ["OP-02"]
    assert image.status_code == 200 and image.content == png
    assert foreign.status_code == 404 and traversal.status_code == 404
    assert put.status_code == 403 and delete.status_code == 403 and direct.status_code == 403
    assert stranger_list.status_code == 403 and stranger_image.status_code == 403


def test_fitter_with_only_tasks_right_reads_the_saved_harness_of_his_task(production_api):  # noqa: F811
    from datetime import datetime

    from app.modules.techcards.kitsets import KitAssembly, Kitset
    from tests.techcards import grapher_fixture as fx

    client, operator_id = production_api
    factory = app.state.production_uow_factory
    task = _launch(factory, operator_id, "harness-1", link_inputs((_op(1, "Нарезка"), _op(2, "Сборка")), {2: ((1, Decimal(1)),)}))
    other = launch_production(LaunchProductionCommand(
        idempotency_key="harness-2", project="591K.4", item="591K.4.КБ1", name="Чужой", quantity=3,
        fulfillment_request_id=None, deadline=None,
        operations=link_inputs((_op(1, "Нарезка"),), {}), actor=MASTER, now=NOW,
    ), factory())
    now = datetime(2026, 10, 6, 10, 0)
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(Kitset(project="591K.3", customer="Заказчик", calc_source="", updated_at=now, updated_by="Технолог",
                                created_at=now, assemblies=(KitAssembly(index="КБ1", name="Жгут", run=Decimal("5")),)))
        uow.commit()
    fx.library(app.state.techcards_uow_factory)
    master_principal = app.dependency_overrides[authenticated_principal]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=uuid4(), name="Технолог", department="Производство"),
        email="engineer@example.test", roles=frozenset({"engineer"}), permissions={"projects": "edit"},
    )
    saved = client.put("/api/techcards/kitsets/591K.3/harness/КБ1", json={"design": fx.design()})
    assert saved.status_code == 200, saved.text
    app.dependency_overrides[authenticated_principal] = master_principal

    url = f"/api/production/my/tasks/{task.id}/harness"
    master = client.get(url)
    _as_operator(operator_id)
    fitter = client.get(url)
    direct = client.get("/api/techcards/kitsets/591K.3/harness/КБ1")
    foreign = client.get(f"/api/production/my/tasks/{other.id}/harness")
    missing = client.get(f"/api/production/my/tasks/{uuid4()}/harness")

    assert master.status_code == 200 and fitter.status_code == 200, fitter.text
    body = fitter.json()
    assert body["design"] == saved.json()["design"] and body["revision"] == saved.json()["revision"]
    assert set(body) == {"document", "design", "revision", "version", "warnings"}
    assert direct.status_code == 403  # раздел «Проекты» монтажнику по-прежнему закрыт
    assert foreign.status_code == 403
    assert missing.status_code == 404


def test_task_harness_without_a_saved_drawing_is_empty(production_api):  # noqa: F811
    client, operator_id = production_api
    task = _launch(app.state.production_uow_factory, operator_id, "harness-3", link_inputs((_op(1, "Нарезка"),), {}))
    _as_operator(operator_id)

    response = client.get(f"/api/production/my/tasks/{task.id}/harness")

    assert response.status_code == 200
    assert response.json() == {"document": None, "design": None, "revision": None, "version": None, "warnings": []}
