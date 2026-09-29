"""SK-14: the item type dictionary — canonical list, the stock tail, aliases, saving and renaming a type."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.item_types import (
    ITEM_TYPES_CANON,
    canonical_item_type,
    merge_item_types,
    normalize_item_types,
)
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


@pytest.mark.parametrize(("typed", "canonical"), [
    ("Мультипровод", "Кабель"),
    ("  МУЛЬТИПРОВОД ", "Кабель"),
    ("ТуТ", "Термоусадочная трубка"),
    ("тут", "Термоусадочная трубка"),
    ("ТтК", "Термоусадочная трубка"),
    ("термоусадка", "Термоусадочная трубка"),
    ("Термоусадочная  трубка (ТуТ)", "Термоусадочная трубка"),
    ("Коаксиальный провод", "Коаксиальный кабель"),
    ("  Разъёмы  ", "Разъёмы"),  # unknown: kept as typed, only trimmed
    ("Тутовник", "Тутовник"),  # the whole value must match, not a prefix
    ("", ""),
    (None, ""),
])
def test_old_spellings_go_to_the_canonical_type(typed, canonical):
    assert canonical_item_type(typed) == canonical


def test_merge_keeps_the_list_order_and_adds_new_values_without_case_or_space_duplicates():
    assert merge_item_types(["Кабель", "Лента"], ["кабель", " ЛЕНТА ", "Разъём", "", "разъём", "Реле"]) == [
        "Кабель", "Лента", "Разъём", "Реле",
    ]


def test_saved_list_is_trimmed_deduplicated_and_capped():
    assert normalize_item_types("  Лента \r\nКабель\n\nкабель\nПровод   Монтажный\n" + "Я" * 81) == [
        "Лента", "Кабель", "Провод Монтажный",
    ]
    assert len(normalize_item_types([f"Тип {n}" for n in range(250)])) == 200


def _lot(grn, item_type, warehouse="ЦАР", package=""):
    return StockLot.create(
        warehouse=warehouse, grn=grn, package=package, name="Позиция", item_type=item_type, purpose="Склад",
        unit="шт", quantity=Decimal("1"), storage_location="1F4", source_receiving_ids=(f"test:{grn}",),
    )


def _put(*lots):
    with app.state.warehouse_uow_factory() as uow:
        for lot in lots:
            uow.warehouse.add(lot)
        uow.commit()


def _audit(event_type):
    with app.state.warehouse_uow_factory() as uow:
        return list(uow.session.execute(
            select(AuditEventRow.aggregate_id, AuditEventRow.details).where(AuditEventRow.event_type == event_type)
        ))


def _types_of_lots():
    with app.state.warehouse_uow_factory() as uow:
        return {lot.grn: (lot.item_type, lot.version) for lot in uow.warehouse.every()}


def test_dictionary_is_the_canon_until_saved_and_the_stock_types_are_its_canonical_tail(warehouse_api):  # noqa: F811
    client, _lot_fixture = warehouse_api  # «Комплектующие»
    _put(_lot("OLD.26.0000001", "ТуТ"), _lot("OLD.26.0000002", "тут", warehouse="ЖУК"), _lot("OLD.26.0000003", "кабель"))

    body = client.get("/api/stock/item-types").json()

    assert body["types"] == list(ITEM_TYPES_CANON)
    assert body["counts"] == {"Комплектующие": 1, "ТуТ": 1, "тут": 1, "кабель": 1}
    # The tail is canonical and without the case duplicates of the list («кабель» is «Кабель»).
    assert body["choices"] == [*ITEM_TYPES_CANON, "Комплектующие"]


def test_saving_the_dictionary_replaces_it_and_is_journaled(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.put("/api/stock/item-types", json={"types": [" Лента", "Кабель", "лента", ""]})

    assert response.status_code == 200, response.text
    assert response.json()["types"] == ["Лента", "Кабель"]
    assert response.json()["message"] == "Справочник типов сохранён: 2."
    assert client.get("/api/stock/item-types").json()["choices"] == ["Лента", "Кабель", "Комплектующие"]
    [(_, details)] = _audit("warehouse.item_types_saved")
    assert json.loads(details["types"]) == ["Лента", "Кабель"]

    empty = client.put("/api/stock/item-types", json={"types": ["", "  "]})
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Список типов пуст — нужен хотя бы один."
    assert client.get("/api/stock/item-types").json()["types"] == ["Лента", "Кабель"]


def test_rename_changes_the_type_in_every_row_of_both_warehouses_and_journals_each(warehouse_api):  # noqa: F811
    client, _ = warehouse_api
    _put(_lot("OLD.26.0000001", "Разъёмы"), _lot("OLD.26.0000002", "Разъёмы", warehouse="ЖУК", package="П1"),
         _lot("OLD.26.0000003", "разъёмы"))

    response = client.post("/api/stock/item-types/rename", json={"source": " Разъёмы ", "target": "Соединитель"})

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Тип «Разъёмы» → «Соединитель»: строк 2."
    types = _types_of_lots()
    assert types["OLD.26.0000001"] == ("Соединитель", 2)
    assert types["OLD.26.0000002"] == ("Соединитель", 2)
    assert types["OLD.26.0000003"] == ("разъёмы", 1)  # exact match only, as stock05TypeRename_
    events = _audit("warehouse.row_edited")
    assert len(events) == 2
    assert {details["reason"] for _, details in events} == {"переименование типа «Разъёмы» → «Соединитель»"}
    assert {details["changes"] for _, details in events} == {"ТИП: «Разъёмы» → «Соединитель»"}


def test_rename_to_an_alias_writes_the_canonical_type_and_merges_into_an_existing_one(warehouse_api):  # noqa: F811
    client, _ = warehouse_api
    _put(_lot("OLD.26.0000001", "ТтК"), _lot("OLD.26.0000002", "Термоусадочная трубка"))

    response = client.post("/api/stock/item-types/rename", json={"source": "ТтК", "target": "ТуТ"})

    assert response.json()["message"] == "Тип «ТтК» → «ТуТ»: строк 1."
    assert _types_of_lots()["OLD.26.0000001"] == ("Термоусадочная трубка", 2)
    assert client.get("/api/stock/item-types").json()["counts"]["Термоусадочная трубка"] == 2


def test_rename_without_rows_or_names(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    none = client.post("/api/stock/item-types/rename", json={"source": "Реле", "target": "Кабель"})
    assert none.status_code == 200
    assert none.json()["message"] == "Строк с типом «Реле» на складе нет."
    same = client.post("/api/stock/item-types/rename", json={"source": "Комплектующие", "target": "Комплектующие"})
    assert same.json()["message"] == "Тип «Комплектующие» → «Комплектующие»: строк 0."
    assert _audit("warehouse.row_edited") == []

    blank = client.post("/api/stock/item-types/rename", json={"source": "Комплектующие", "target": " "})
    assert blank.status_code == 422
    assert blank.json()["detail"] == "Нужны оба названия: что меняем и на что."


def test_quick_entry_writes_the_canonical_type(warehouse_api):  # noqa: F811
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [
        {"article": "A-1", "quantity": "1", "storage_location": "1F4", "item_type": " мультипровод "},
        {"article": "A-2", "quantity": "1", "storage_location": "1F4", "item_type": "Реле"},
    ]})

    assert response.status_code == 200, response.text
    assert [lot["item_type"] for lot in response.json()["lots"]] == ["Кабель", "Реле"]


@pytest.mark.parametrize(("method", "path", "body"), [
    ("get", "/api/stock/item-types", None),
    ("put", "/api/stock/item-types", {"types": ["Кабель"]}),
    ("post", "/api/stock/item-types/rename", {"source": "Комплектующие", "target": "Кабель"}),
])
def test_the_dictionary_is_for_the_storekeeper_and_the_admin_only(warehouse_api, method, path, body):  # noqa: F811
    client, _ = warehouse_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сметчик", department="КБ"), email="estimator@example.test",
        roles=frozenset({"estimator"}), permissions={"warehouse": "edit"},
    )

    response = getattr(client, method)(path, **({"json": body} if body is not None else {}))

    assert response.status_code == 403
