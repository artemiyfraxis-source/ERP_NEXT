"""SK-10: «Внести позиции» row by row — a bad row is skipped and reported as {i, error}, the good ones are
entered (stock05Add_, Stock05.gs:734-794)."""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.application import (
    AddManualStockCommand,
    ManualStockItem,
    add_manual_stock,
)
from app.modules.warehouse.domain import WarehouseValidationError
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture
from tests.warehouse.test_application import Uow, message

ROW = {"warehouse": "ЦАР", "article": "CON-001", "quantity": "1", "storage_location": "2A1"}


def _command(*items):
    return AddManualStockCommand(
        items=items, actor=Actor(name="Кладовщик", department="Склад"), now=datetime(2026, 9, 22, 12, 0),
    )


def _item(**fields):
    base = dict(warehouse="ЦАР", name="", item_type="", purpose="", unit="", quantity="1",
                storage_location="1F4", article="CON-001")
    return ManualStockItem(**{**base, **fields})


def test_bad_rows_are_skipped_and_the_good_ones_get_consecutive_numbers():
    uow = Uow(message())

    result = add_manual_stock(_command(
        _item(article="A-1"),
        _item(article="", name=""),
        _item(quantity="0"),
        _item(quantity="abc"),
        _item(warehouse="СКЛ"),
        _item(storage_location=""),
        _item(storage_location="18A1"),
        _item(article="A-2", quantity="2,5"),
    ), uow)

    assert [(row.i, row.lot.grn, row.lot.article, row.lot.inbound) for row in result.assigned] == [
        (0, "OLD.26.0000001", "A-1", Decimal("1")),
        (7, "OLD.26.0000002", "A-2", Decimal("2.5")),
    ]
    assert [(row.i, row.error) for row in result.errors] == [
        (1, "пустой артикул"),
        (2, "кол-во должно быть больше нуля"),
        (3, "кол-во должно быть больше нуля"),
        (4, "неизвестный склад «СКЛ»"),
        (5, "укажи стеллаж, полку и № на полке"),
        (6, "Места 18A1 нет: проверь номер стеллажа и полку."),
    ]
    assert [event.details["grn"] for event in uow.audit.items] == ["OLD.26.0000001", "OLD.26.0000002"]
    assert uow.commits == 1


def test_all_bad_rows_commit_nothing_and_reserve_no_number():
    uow = Uow(message())

    result = add_manual_stock(_command(_item(quantity="-1"), _item(article="", name=" ")), uow)

    assert result.assigned == ()
    assert [row.i for row in result.errors] == [0, 1]
    assert (uow.commits, uow.audit.items, uow.warehouse.sequence) == (0, [], 0)


def test_empty_article_takes_the_name_and_empty_defaults_follow_supplymap():
    result = add_manual_stock(_command(_item(article="  ", name=" Разъём ", warehouse="")), Uow(message()))

    [lot] = result.lots
    assert (lot.article, lot.name, lot.warehouse, lot.unit, lot.purpose) == ("Разъём", "Разъём", "ЦАР", "шт", "Склад")


def test_more_than_200_rows_refuse_the_whole_batch():
    uow = Uow(message())

    with pytest.raises(WarehouseValidationError, match="За раз не больше 200 позиций."):
        add_manual_stock(_command(*[_item()] * 201), uow)
    assert uow.commits == 0


def test_quantity_finer_than_the_stock_column_is_the_row_error():
    result = add_manual_stock(_command(_item(quantity="0.0000001")), Uow(message()))

    assert [row.error for row in result.errors] == ["кол-во — не больше 6 знаков после запятой"]


def _manual_audit():
    with app.state.warehouse_uow_factory() as uow:
        rows = uow.session.execute(
            select(AuditEventRow.details).where(AuditEventRow.event_type == "warehouse.manually_received")
        )
        return [row[0]["grn"] for row in rows]


def test_http_mixed_batch_returns_assigned_and_errors_and_journals_only_the_entered(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [
        ROW,
        {**ROW, "article": "", "name": ""},
        {**ROW, "article": "", "name": "Корпус", "quantity": 3},
        {**ROW, "storage_location": "99A1"},
    ]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["added"] == 2
    assert [(row["i"], row["grn"], row["article"], row["quantity"]) for row in body["assigned"]] == [
        (0, "OLD.26.0000001", "CON-001", "1"),
        (2, "OLD.26.0000002", "Корпус", "3"),
    ]
    assert body["errors"] == [
        {"i": 1, "error": "пустой артикул"},
        {"i": 3, "error": "Места 99A1 нет: проверь номер стеллажа и полку."},
    ]
    assert [lot["name"] for lot in body["lots"]] == ["", "Корпус"]
    assert _manual_audit() == ["OLD.26.0000001", "OLD.26.0000002"]


def test_http_all_bad_rows_answer_200_with_errors_only(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{**ROW, "quantity": "0"}]})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "added": 0, "lots": [], "assigned": [], "errors": [{"i": 0, "error": "кол-во должно быть больше нуля"}],
    }
    assert _manual_audit() == []


def test_http_more_than_200_rows_is_422(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [ROW] * 201})

    assert response.status_code == 422
    assert _manual_audit() == []


def test_http_200_rows_are_entered(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [ROW] * 200})

    assert response.status_code == 200, response.text
    assert response.json()["added"] == 200
    assert response.json()["assigned"][-1]["grn"] == "OLD.26.0000200"


def test_http_whitespace_article_with_a_name_enters_the_name_as_article(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{**ROW, "article": "   ", "name": "Разъём"}]})

    assert response.status_code == 200, response.text
    assert (response.json()["lots"][0]["article"], response.json()["lots"][0]["name"]) == ("Разъём", "Разъём")
