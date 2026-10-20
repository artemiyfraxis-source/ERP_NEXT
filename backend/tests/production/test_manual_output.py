"""Ввод готовых мастером (владелец, 20.10.2026): штуки, сделанные мимо терминала, и «отбить готовыми» пропущенные
этапы-входы — чтобы следующие этапы брали полуфабрикаты из учёта."""

from decimal import Decimal

import pytest

from app.main import app
from app.modules.production.application import AssignProductionCommand, LaunchProductionCommand, assign_production, launch_production
from app.modules.production.domain import ProductionValidationError
from app.modules.production.execution import ManualOutputCommand, StartWorkCommand, record_manual_output, start_work
from app.modules.production.traceability import WorkStatus
from tests.factories import Actor
from tests.production.test_api import production_api  # noqa: F401 — фикстура API цеха
from tests.production.test_network_execution import MASTER, NOW, OPERATOR, merge_operations, network_shop


def _manual(shop, position, good, *, performer=None, fill=False):
    return record_manual_output(ManualOutputCommand(
        task_id=shop.task.id, operation_id=shop.operation(position).id, good_quantity=Decimal(good),
        actor=MASTER, now=NOW, performer=performer, fill_inputs=fill,
    ), shop.factory())


def _done(shop, position):
    with shop.factory() as uow:
        return uow.outputs.total_for_operation(shop.operation(position).id)


def test_assembly_without_inputs_in_records_is_refused_with_the_short_stages():
    shop = network_shop()

    with pytest.raises(ProductionValidationError, match="не хватает полуфабрикатов.*есть 0, нужно 6.*есть 0, нужно 3"):
        _manual(shop, 3, 3, performer=OPERATOR)

    assert _done(shop, 3) == 0
    shop.engine.dispose()


def test_fill_inputs_marks_skipped_stages_done_and_takes_them_into_the_fitters_box():
    shop = network_shop()

    box, output, filled, _closed = _manual(shop, 3, 3, performer=OPERATOR, fill=True)

    assert output.session_id is None and output.operator_id == OPERATOR.id and box.quantity == 3
    # Нарезка А идёт 2 на сборку: отбито 6; нарезка Б — 3. Отбитые входы — запись мастера, целиком взяты в сборку.
    assert sorted((item.operation_position, item.quantity, item.operator_id) for item in filled) == [
        (1, Decimal(6), MASTER.id), (2, Decimal(3), MASTER.id),
    ]
    assert all(shop.box(item).available == 0 for item in filled)
    assert (_done(shop, 1), _done(shop, 2), _done(shop, 3)) == (6, 3, 3)
    with shop.factory() as uow:
        assert uow.work.current(OPERATOR.id) is None
    shop.engine.dispose()


def test_entered_stage_feeds_the_terminal_and_existing_inputs_are_used_before_filling():
    shop = network_shop()
    _manual(shop, 1, 10)  # нарезка А готова целиком

    _box, _output, filled, _closed = _manual(shop, 3, 2, performer=OPERATOR, fill=True)
    # Маркировка с терминала берёт коробку сборки, введённую мастером.
    session = start_work(StartWorkCommand(
        task_id=shop.task.id, operation_id=shop.operation(4).id, box_numbers=(_box.number,), actor=OPERATOR, now=NOW,
    ), shop.factory())

    assert [(item.operation_position, item.quantity) for item in filled] == [(2, Decimal(2))]
    assert session.operation_id == shop.operation(4).id
    shop.engine.dispose()


def _start(shop, position):
    return start_work(StartWorkCommand(
        task_id=shop.task.id, operation_id=shop.operation(position).id, box_numbers=(), actor=OPERATOR, now=NOW,
    ), shop.factory())


def test_stage_entered_in_full_cancels_open_work_on_it():
    shop = network_shop()
    session = _start(shop, 1)

    *_rest, closed = _manual(shop, 1, 10)

    assert closed == ("Оператор (этап 1)",)
    with shop.factory() as uow:
        assert uow.work.current(OPERATOR.id) is None
        assert uow.work.get(session.id).status is WorkStatus.CANCELLED
    shop.engine.dispose()


def test_fitters_pieces_cancel_her_open_work_but_a_partial_master_entry_and_other_stages_leave_it():
    shop = network_shop()
    _start(shop, 2)

    *_rest, untouched = _manual(shop, 1, 10)  # другой этап — работа на этапе 2 идёт
    *_rest, partial = _manual(shop, 2, 1)  # мастер без исполнителя, этап не сдан целиком
    with shop.factory() as uow:
        still = uow.work.current(OPERATOR.id)
    *_rest, closed = _manual(shop, 2, 2, performer=OPERATOR)

    assert (untouched, partial) == ((), ())
    assert still is not None and still.operation_id == shop.operation(2).id
    assert closed == ("Оператор (этап 2)",)
    with shop.factory() as uow:
        assert uow.work.current(OPERATOR.id) is None
    shop.engine.dispose()


def test_more_than_the_stage_requires_is_refused():
    shop = network_shop()
    _manual(shop, 2, 4)

    with pytest.raises(ProductionValidationError, match="уже сдано 4 из 5, осталось 1"):
        _manual(shop, 2, 2)
    shop.engine.dispose()


def test_fitter_not_on_the_stage_is_refused():
    shop = network_shop()
    stranger = Actor(name="Чужой", department="Производство")

    with pytest.raises(ProductionValidationError, match="не назначен"):
        _manual(shop, 1, 1, performer=stranger)
    shop.engine.dispose()


def test_api_enters_fitters_pieces_and_reports_filled_stages(production_api):  # noqa: F811
    client, operator_id = production_api
    factory = app.state.production_uow_factory
    task = launch_production(LaunchProductionCommand(
        idempotency_key="591K.3:КБ1:ручной", project="591K.3", item="591K.3.КБ1", name="Жгут",
        quantity=5, fulfillment_request_id=None, deadline=None,
        operations=merge_operations(), actor=MASTER, now=NOW,
    ), factory())
    for operation in task.operations:
        task, _ = assign_production(AssignProductionCommand(
            task_id=task.id, expected_version=task.version, operation_id=operation.id,
            operator_ids=(operator_id,), requested_start=None, actor=MASTER, now=NOW,
        ), factory())
    assembly = next(item for item in task.operations if item.position == 3)
    url = f"/api/production/tasks/{task.id}/operations/{assembly.id}/manual-output"

    refused = client.post(url, json={"good_quantity": 3, "operator_id": str(operator_id)})
    entered = client.post(url, json={"good_quantity": 3, "operator_id": str(operator_id), "fill_inputs": True})
    over = client.post(url, json={"good_quantity": 3, "operator_id": str(operator_id), "fill_inputs": True})

    assert refused.status_code == 422 and "не хватает полуфабрикатов" in refused.json()["detail"]
    assert entered.status_code == 200, entered.text
    body = entered.json()
    assert body["box"]["operator_id"] == str(operator_id) and body["box"]["quantity"] == "3"
    assert body["warnings"][1] == "Отбиты готовыми: этап 1 — 6 шт, этап 2 — 3 шт."
    assert over.status_code == 422 and "осталось 2" in over.json()["detail"]
