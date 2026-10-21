"""«№ изделия» в надписях меток по КД: номера изделий задания — сквозь задания одной позиции проекта."""

from dataclasses import replace
from decimal import Decimal

from app.main import app
from app.modules.production.domain import ProductionStatus, link_inputs, mark_serial_range
from tests.production.test_api import production_api  # noqa: F401 — фикстура API цеха
from tests.production.test_my_readiness import _as_operator, _etk_url, _launch
from tests.production.test_network_execution import _op


def _chain():
    return link_inputs((_op(1, "Нарезка"), _op(2, "Маркировка")), {2: ((1, Decimal(1)),)})


def test_first_task_numbers_its_items_from_one(production_api):  # noqa: F811
    _client, operator_id = production_api
    task = _launch(app.state.production_uow_factory, operator_id, "serial-1", _chain())
    assert mark_serial_range(task, [task]) == (1, 5)
    assert mark_serial_range(task, []) == (1, 5)


def test_the_next_task_of_the_same_item_continues_and_other_items_or_later_tasks_do_not_count(production_api):  # noqa: F811
    _client, operator_id = production_api
    factory = app.state.production_uow_factory
    first = _launch(factory, operator_id, "serial-a", _chain())
    other_item = _launch(factory, operator_id, "serial-b", _chain(), item="591K.3.КБ2")
    second = _launch(factory, operator_id, "serial-c", _chain())
    later = _launch(factory, operator_id, "serial-d", _chain())
    order = [first, other_item, second, later]

    assert mark_serial_range(first, order) == (1, 5)
    assert mark_serial_range(other_item, order) == (1, 5)
    assert mark_serial_range(second, order) == (6, 10)
    assert mark_serial_range(later, order) == (11, 15)
    # Отменённое задание номера не отдаёт: напечатанные надписи следующих не сдвигаются.
    cancelled = [replace(first, status=ProductionStatus.CANCELLED), other_item, second, later]
    assert mark_serial_range(second, cancelled) == (6, 10)
    # Позиция сравнивается без регистра и пробелов по краям.
    assert mark_serial_range(replace(later, item=" 591k.3.кб1 "), order) == (11, 15)


def test_operation_etk_returns_the_task_serial_range_and_the_card_is_untouched(production_api):  # noqa: F811
    from tests.production.test_api import issue_card

    client, operator_id = production_api
    factory = app.state.production_uow_factory
    first = _launch(factory, operator_id, "serial-etk-1", _chain())
    second = _launch(factory, operator_id, "serial-etk-2", _chain())
    issue_card("591K.3", "КБ1", [("OP-01", "Нарезка", "10", "0"), ("OP-02", "Маркировка", "10", "0")])
    _as_operator(operator_id)

    one = client.get(_etk_url(first))
    two = client.get(_etk_url(second))

    assert one.status_code == 200, one.text
    assert one.json()["serial"] == {"first": 1, "last": 5, "task": first.number}
    assert two.json()["serial"] == {"first": 6, "last": 10, "task": second.number}
    assert one.json()["card"] == two.json()["card"]
