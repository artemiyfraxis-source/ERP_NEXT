"""SK-14: export of the stock table and the import of its reference fields with a preview."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.reference_fields import row_edit_values
from app.modules.warehouse.domain import WarehouseValidationError
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


def _as_admin():
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Админ", department="ИТ"), email="admin@example.test",
        roles=frozenset({"admin"}), permissions={"warehouse": "edit"},
    )


def _lot(grn, warehouse="ЦАР", package="", **extra):
    return StockLot.create(
        warehouse=warehouse, grn=grn, package=package, name=extra.pop("name", "Позиция"),
        item_type=extra.pop("item_type", "Кабель"), purpose="Склад", unit="шт", quantity=Decimal("5"),
        storage_location="1F4", source_receiving_ids=(f"test:{warehouse}:{grn}:{package}",), **extra,
    )


def _put(*lots):
    with app.state.warehouse_uow_factory() as uow:
        for lot in lots:
            uow.warehouse.add(lot)
        uow.commit()


def _lots():
    with app.state.warehouse_uow_factory() as uow:
        return {(lot.warehouse, lot.grn, lot.package): lot for lot in uow.warehouse.every()}


def _journal():
    with app.state.warehouse_uow_factory() as uow:
        return [row[0] for row in uow.session.execute(
            select(AuditEventRow.details).where(AuditEventRow.event_type == "warehouse.row_edited")
        )]


def test_row_values_follow_stock05_row_diff():
    assert row_edit_values({"name": "", "item_type": " тут ", "unit": "", "mass": "x"}) == {
        "item_type": "Термоусадочная трубка", "unit": "",  # an empty name is not written, other empties clear
    }
    assert row_edit_values({"unit_mass_g": "1,5", "expiry_date": "01.03.2027"}) == {
        "unit_mass_g": Decimal("1.5"), "expiry_date": date(2027, 3, 1),
    }
    assert row_edit_values({"expiry_date": "2027-03-01"})["expiry_date"] == date(2027, 3, 1)
    assert row_edit_values({"expiry_date": ""}) == {"expiry_date": None}
    with pytest.raises(WarehouseValidationError, match="Срок годности"):
        row_edit_values({"expiry_date": "31.02.2027"})
    with pytest.raises(WarehouseValidationError, match="ЕД.ИЗМ: длиннее 32"):
        row_edit_values({"unit": "ш" * 33})


def test_export_gives_every_row_with_the_key_balance_and_reference_fields(warehouse_api):  # noqa: F811
    client, _ = warehouse_api
    _put(_lot("OLD.26.0000001", warehouse="ЖУК", unit_mass_g=Decimal("1.5"), expiry_date=date(2027, 3, 1)))

    rows = client.get("/api/stock/export").json()["rows"]

    assert [(row["warehouse"], row["grn"]) for row in rows] == [("ЦАР", "FRX.26.0000001"), ("ЖУК", "OLD.26.0000001")]
    assert rows[1] | {"balance": "5", "unit_mass_g": "1,5", "expiry_date": "01.03.2027", "item_type": "Кабель",
                      "storage_location": "1F4", "package": ""} == rows[1]


def test_preview_shows_the_diff_and_writes_nothing_then_apply_writes_and_journals(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    _as_admin()
    edits = [
        {"warehouse": "ЦАР", "grn": lot.grn, "name": "", "item_type": "ТуТ", "storage_location": "Стеллаж у окна",
         "manufacturer": "Molex", "unit": "шт"},
        {"warehouse": "", "grn": "НЕТ.26.0000009", "package": "П1", "name": "Что-то"},
    ]

    preview = client.post("/api/stock/import", json={"edits": edits})

    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body | {"applied": False, "rows": 1, "count": 3, "missing": ["НЕТ.26.0000009 / П1"], "missing_count": 1,
                   "errors": []} == body
    assert [(c["column"], c["was"], c["now"]) for c in body["changes"]] == [
        ("ТИП", "Комплектующие", "Термоусадочная трубка"),
        ("МЕСТО ХРАНЕНИЯ", "1F4", "Стеллаж у окна"),
        ("ПРОИЗВОДИТЕЛЬ", "", "Molex"),
    ]
    assert body["changes"][0]["name"] == lot.article
    assert _lots()[("ЦАР", lot.grn, "")].version == lot.version  # the preview wrote nothing
    assert _journal() == []

    applied = client.post("/api/stock/import", json={"edits": edits, "apply": True})

    assert applied.json()["applied"] is True and applied.json()["count"] == 3
    stored = _lots()[("ЦАР", lot.grn, "")]
    assert (stored.name, stored.item_type, stored.storage_location, stored.manufacturer, stored.inbound) == (
        "Разъём", "Термоусадочная трубка", "Стеллаж у окна", "Molex", Decimal("10"),
    )
    assert stored.version == lot.version + 1
    [details] = _journal()
    assert details["reason"] == "импорт таблицы"
    assert details["changes"].startswith("ТИП: «Комплектующие» → «Термоусадочная трубка»; ")

    again = client.post("/api/stock/import", json={"edits": edits, "apply": True}).json()
    assert (again["count"], again["rows"]) == (0, 0)


def test_rows_are_found_by_warehouse_grn_and_package(warehouse_api):  # noqa: F811
    client, _ = warehouse_api
    _as_admin()
    _put(_lot("OLD.26.0000001", "ЦАР", "П1"), _lot("OLD.26.0000001", "ЖУК", "П1"), _lot("OLD.26.0000001", "ЦАР", "П2"))

    client.post("/api/stock/import", json={"apply": True, "edits": [
        {"warehouse": "ЖУК", "grn": "OLD.26.0000001", "package": "П1", "description": "на ЖУК"},
        {"warehouse": "?", "grn": " OLD.26.0000001 ", "package": "П2", "description": "без склада"},
    ]})

    lots = _lots()
    assert lots[("ЖУК", "OLD.26.0000001", "П1")].description == "на ЖУК"
    assert lots[("ЦАР", "OLD.26.0000001", "П1")].description == ""
    assert lots[("ЦАР", "OLD.26.0000001", "П2")].description == "без склада"


def test_a_bad_value_skips_its_row_only_and_quantities_never_come_from_the_table(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    _as_admin()
    _put(_lot("OLD.26.0000001"))

    response = client.post("/api/stock/import", json={"apply": True, "edits": [
        {"grn": lot.grn, "unit": ""},
        {"grn": "OLD.26.0000001", "unit_mass_g": "abc"},
        {"grn": "OLD.26.0000001", "series": "Micro-Fit"},
    ]})

    body = response.json()
    assert [e["error"] for e in body["errors"]] == ["Единица измерения обязательна", "Масса 1 шт — не число: «abc»"]
    assert body["rows"] == 1
    assert _lots()[("ЦАР", "OLD.26.0000001", "")].series == "Micro-Fit"
    assert _lots()[("ЦАР", lot.grn, "")].unit == "шт"
    refused = client.post("/api/stock/import", json={"edits": [{"grn": lot.grn, "inbound": "99"}]})
    assert refused.status_code == 422


def test_import_is_for_the_admin_only_and_has_a_limit(warehouse_api):  # noqa: F811
    client, lot = warehouse_api

    storekeeper = client.post("/api/stock/import", json={"edits": [{"grn": lot.grn, "name": "X"}]})
    assert storekeeper.status_code == 403
    assert client.get("/api/stock/export").status_code == 200  # the export is the storekeeper's too

    _as_admin()
    too_many = client.post("/api/stock/import", json={"edits": [{"grn": "G"}] * 5001})
    assert too_many.status_code == 422
