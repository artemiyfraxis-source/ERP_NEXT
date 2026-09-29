"""SK-7 over HTTP: PUT /api/stock/batches/{id}/row — the whole stock row by hand."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


def _audit(event_type):
    with app.state.warehouse_uow_factory() as uow:
        rows = uow.session.execute(
            select(AuditEventRow.actor_name, AuditEventRow.details).where(AuditEventRow.event_type == event_type)
        )
        return [tuple(row) for row in rows]


def test_row_edit_sets_fields_recounts_balance_and_journals_the_diff(warehouse_api):  # noqa: F811
    client, lot = warehouse_api

    response = client.put(f"/api/stock/batches/{lot.id}/row", json={
        "version": lot.version, "item_type": "Разъёмы", "storage_location": "Стеллаж у окна",
        "unit_mass_g": "0,5", "expiry_date": "2027-03-01", "manufacturer": "Molex",
        "inbound": "12", "written_off": "2", "reserved": "",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lot"] | {
        "item_type": "Разъёмы", "storage_location": "Стеллаж у окна", "unit_mass_g": "0.5",
        "expiry_date": "2027-03-01", "manufacturer": "Molex", "inbound": "12", "written_off": "2",
        "reserved": "0", "balance": "10", "version": lot.version + 1, "grn": lot.grn,
    } == body["lot"]
    assert body["message"] == "Строка FRX.26.0000001 обновлена: 7 поле(й)."
    assert [change["column"] for change in body["changes"]] == [
        "ТИП", "МЕСТО ХРАНЕНИЯ", "МАССА 1 ШТ, Г", "СРОК ГОДНОСТИ", "ПРОИЗВОДИТЕЛЬ", "ПРИХОД", "СПИСАНО",
    ]
    [(actor_name, details)] = _audit("warehouse.row_edited")
    assert details["changes"].startswith("ТИП: «Комплектующие» → «Разъёмы»; МЕСТО ХРАНЕНИЯ: «1F4» → «Стеллаж у окна»")
    assert actor_name == "Кладовщик"


def test_empty_numbers_are_not_touched_and_same_values_change_nothing(warehouse_api):  # noqa: F811
    client, lot = warehouse_api

    response = client.put(f"/api/stock/batches/{lot.id}/row", json={
        "version": lot.version, "article": lot.article, "inbound": " ", "written_off": None,
    })

    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Изменений нет."
    assert response.json()["lot"]["version"] == lot.version
    assert response.json()["lot"]["inbound"] == "10"
    assert _audit("warehouse.row_edited") == []


@pytest.mark.parametrize(("extra", "detail"), [
    ({"grn": "FRX.26.0000002"}, None),
    ({"package": "P2"}, None),
    ({"warehouse": "ЖУК"}, None),
    ({"written_off": "11"}, "СПИСАНО (11) больше ПРИХОДА (10): остаток ушёл бы в минус."),
    ({"reserved": "11"}, "РЕЗЕРВ (11) больше остатка (10 шт)."),
    ({"inbound": "-1"}, "ПРИХОД не может быть отрицательным"),
    ({"inbound": "abc"}, None),
    ({"unit_mass_g": "abc"}, "Масса 1 шт — не число: «abc»"),
    ({"expiry_date": "31.12.2030"}, "Срок годности — дата ГГГГ-ММ-ДД, а не «31.12.2030»"),
    ({"article": ""}, "Артикул обязателен"),
])
def test_row_edit_refuses_key_fields_and_broken_quantities(warehouse_api, extra, detail):  # noqa: F811
    client, lot = warehouse_api

    response = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version, **extra})

    assert response.status_code == 422, response.text
    if detail:
        assert response.json()["detail"] == detail
    assert client.get("/api/stock").json()["lots"][0]["version"] == lot.version


def test_row_edit_of_a_stale_form_is_a_conflict(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    first = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version, "name": "A"})

    stale = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version, "name": "B"})

    assert first.status_code == 200, first.text
    assert stale.status_code == 409


def test_reserve_cannot_go_under_what_kitting_holds(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    with app.state.warehouse_uow_factory() as uow:
        uow.warehouse.add(uow.warehouse.get(lot.id).reserve(Decimal("4")))
        uow.commit()

    class Kitting:
        def __init__(self):
            self.fulfillment = self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def reserved_on_lot(self, lot_id):
            assert lot_id == lot.id
            return Decimal("4")

    app.state.fulfillment_uow_factory = Kitting
    try:
        refused = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version + 1, "reserved": "0"})
        raised = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version + 1, "reserved": "5"})
    finally:
        del app.state.fulfillment_uow_factory

    assert refused.status_code == 422
    assert "Комплектация держит на партии FRX.26.0000001 резерв 4 шт" in refused.json()["detail"]
    assert raised.status_code == 200, raised.text
    assert (raised.json()["lot"]["reserved"], raised.json()["lot"]["available"]) == ("5", "5")


def test_row_edit_is_for_the_storekeeper_and_admin_only(warehouse_api):  # noqa: F811
    client, lot = warehouse_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Закупщик", department="Закупки"),
        email="buyer@example.test",
        roles=frozenset({"buyer"}),
        permissions={"warehouse": "edit"},
    )

    response = client.put(f"/api/stock/batches/{lot.id}/row", json={"version": lot.version, "name": "X"})

    assert response.status_code == 403
