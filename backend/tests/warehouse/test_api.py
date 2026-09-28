from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.warehouse.application import PoisonWarehouseMessage, dispatch_next_warehouse_received
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.clock import moscow_now
from app.shared.events import OutboxMessage
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor


@pytest.fixture()
def warehouse_api():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.state.warehouse_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кладовщик", department="Склад"),
        email="warehouse@example.test",
        roles=frozenset({"receiver"}),
        permissions={"warehouse": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, 21, 12, 0)
    lot = StockLot.create(
        warehouse="ЦАР",
        grn="FRX.26.0000001",
        package="",
        name="Разъём",
        item_type="Комплектующие",
        purpose="FRX.001",
        unit="шт",
        quantity=Decimal("10"),
        storage_location="1F4",
        source_receiving_ids=("20000000-0000-0000-0000-000000000001",),
    )
    with factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    yield TestClient(app), lot
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.warehouse_uow_factory
    engine.dispose()


def test_warehouse_requires_receiver_role(warehouse_api):
    client, _ = warehouse_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Закупщик", department="Закупки"),
        email="buyer@example.test",
        roles=frozenset({"buyer"}),
        permissions={"warehouse": "edit"},
    )

    assert client.get("/api/stock").status_code == 403


def test_warehouse_has_stable_cursor_pages(warehouse_api):
    client, first_lot = warehouse_api
    factory = app.state.warehouse_uow_factory
    second_lot = StockLot.create(
        warehouse="ЖУК",
        grn="PPM.26.0000002",
        package="",
        name="Корпус",
        item_type="Материалы",
        purpose="Производство",
        unit="шт",
        quantity=Decimal("5"),
        storage_location="2A1",
        source_receiving_ids=("20000000-0000-0000-0000-000000000002",),
    )
    with factory() as uow:
        uow.warehouse.add(second_lot)
        uow.commit()

    first_page = client.get("/api/stock?limit=1")
    cursor = first_page.json()["next_cursor"]
    second_page = client.get(f"/api/stock?limit=1&after={cursor}")

    assert first_page.status_code == 200
    assert cursor is not None
    assert second_page.status_code == 200
    assert second_page.json()["next_cursor"] is None
    assert {
        first_page.json()["lots"][0]["id"],
        second_page.json()["lots"][0]["id"],
    } == {str(first_lot.id), str(second_lot.id)}


def test_lot_can_move_and_writeoff_but_stale_update_is_rejected(warehouse_api):
    client, lot = warehouse_api
    moved = client.post(
        f"/api/stock/batches/{lot.id}/move",
        json={"version": lot.version, "storage_location": "1F5"},
    )
    written = client.post(
        f"/api/stock/batches/{lot.id}/writeoff",
        json={
            "version": moved.json()["lot"]["version"],
            "quantity": "2",
            "purpose": "FRX.001", "reason": "Выдача в производство",
        },
    )
    stale = client.post(
        f"/api/stock/batches/{lot.id}/writeoff",
        json={
            "version": moved.json()["lot"]["version"],
            "quantity": "1",
            "purpose": "FRX.001", "reason": "Устаревшая форма",
        },
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["lot"]["storage_location"] == "1F5"
    assert written.status_code == 200, written.text
    assert written.json()["lot"]["balance"] == "8"
    assert stale.status_code == 409


def test_receiver_can_edit_article_description_and_series(warehouse_api):
    client, lot = warehouse_api

    response = client.put(
        f"/api/stock/batches/{lot.id}/identity",
        json={
            "version": lot.version,
            "article": "CON-001",
            "name": "Разъём питания",
            "series": "LOT-2409",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["lot"] | {
        "article": "CON-001",
        "name": "Разъём питания",
        "series": "LOT-2409",
    } == response.json()["lot"]


def test_stock_filters_match_the_live_erp_contract(warehouse_api):
    client, first_lot = warehouse_api
    factory = app.state.warehouse_uow_factory
    empty_lot = StockLot.create(
        warehouse="ЖУК",
        grn="PPM.26.0000002",
        package="",
        name="Корпус",
        item_type="Материалы",
        purpose="Производство",
        unit="шт",
        quantity=Decimal("5"),
        storage_location="2A1",
        source_receiving_ids=("20000000-0000-0000-0000-000000000002",),
    ).write_off(Decimal("5"))
    with factory() as uow:
        uow.warehouse.add(empty_lot)
        uow.commit()

    visible = client.get("/api/stock?q=%D1%80%D0%B0%D0%B7%D1%8A%D1%91%D0%BC&warehouse=%D0%A6%D0%90%D0%A0")
    with_zero = client.get("/api/stock?zero=true&warehouse=%D0%96%D0%A3%D0%9A")

    assert visible.status_code == 200
    assert visible.json()["total"] == 1
    assert visible.json()["lots"][0]["id"] == str(first_lot.id)
    assert with_zero.status_code == 200
    assert with_zero.json()["lots"][0]["balance"] == "0"


def test_stock_searches_article_and_series(warehouse_api):
    client, _ = warehouse_api

    response = client.post(
        "/api/stock/batches",
        json={"items": [{
            "warehouse": "ЦАР",
            "article": "GHR-04V-S",
            "series": "LOT-2409",
            "name": "Разъём питания",
            "quantity": "2",
            "storage_location": "2A1",
        }]},
    )

    assert response.status_code == 200, response.text
    by_article = client.get("/api/stock?q=GHR-04V-S")
    by_series = client.get("/api/stock?q=LOT-2409")
    assert by_article.json()["lots"][0]["article"] == "GHR-04V-S"
    assert by_series.json()["lots"][0]["series"] == "LOT-2409"


def test_stock_article_info_infers_current_supplymap_coax_codes(warehouse_api):
    client, _ = warehouse_api

    response = client.get("/api/stock/article-info?q=N-C58P")

    assert response.status_code == 200, response.text
    assert response.json()["info"] == {
        "series": "N",
        "name": "N-male RG-58",
        "item_type": "Коаксиальный соединитель",
        "source": "разбор кода",
    }


def test_receiver_can_add_manually_counted_stock(warehouse_api):
    client, _ = warehouse_api

    response = client.post(
        "/api/stock/batches",
        json={
            "items": [{
                "warehouse": "ЖУК",
                "article": "CASE-01",
                "series": "BATCH-7",
                "name": "Корпус",
                "item_type": "Материалы",
                "purpose": "Склад",
                "unit": "шт",
                "quantity": "4",
                "storage_location": "2A1",
            }],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["lots"][0]["grn"] == "OLD.26.0000001"
    assert response.json()["lots"][0]["balance"] == "4"
    assert response.json()["lots"][0]["article"] == "CASE-01"
    assert response.json()["lots"][0]["series"] == "BATCH-7"


def test_manual_stock_rejects_whitespace_article_even_with_a_name(warehouse_api):
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{
        "warehouse": "ЦАР", "article": "   ", "name": "Разъём",
        "quantity": "1", "storage_location": "2A1",
    }]})

    assert response.status_code == 422


def test_receiver_moves_a_whole_location_and_preserves_each_slot(warehouse_api):
    client, first = warehouse_api
    factory = app.state.warehouse_uow_factory
    first = first.move("18C3A")
    second = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000002", package="", name="Контакт",
        item_type="Комплектующие", purpose="FRX.001", unit="шт",
        quantity=Decimal("5"), storage_location="18C3B",
        source_receiving_ids=("20000000-0000-0000-0000-000000000002",),
    )
    with factory() as uow:
        uow.warehouse.add(first)
        uow.warehouse.add(second)
        uow.commit()

    response = client.post("/api/stock/locations/move", json={
        "warehouse": "ЦАР", "source_location": "18C3", "target_location": "20B5",
    })

    assert response.status_code == 200, response.text
    assert [item["storage_location"] for item in response.json()["lots"]] == ["20B5A", "20B5B"]


def test_whole_location_move_leaves_old_addresses_and_lots_without_a_place(warehouse_api):
    client, first = warehouse_api
    factory = app.state.warehouse_uow_factory
    first = first.move("18C3")
    others = [
        StockLot.create(
            warehouse="ЦАР", grn=f"OLD.25.000000{index}", package="", article="CON-OLD", name="",
            item_type="", purpose="Склад", unit="шт", quantity=Decimal("2"),
            storage_location=location, source_receiving_ids=(f"import:ЦАР:{index}",),
            location_as_is=True,
        )
        for index, location in enumerate(("", "A-01", "18C3Д", "18C3 угол"))
    ]
    with factory() as uow:
        for lot in (first, *others):
            uow.warehouse.add(lot)
        uow.commit()

    response = client.post("/api/stock/locations/move", json={
        "warehouse": "ЦАР", "source_location": "18C3", "target_location": "20B5",
    })

    assert response.status_code == 200, response.text
    assert [item["storage_location"] for item in response.json()["lots"]] == ["20B5"]
    with factory() as uow:
        assert [uow.warehouse.get(lot.id).storage_location for lot in others] == [
            "", "A-01", "18C3Д", "18C3 угол",
        ]


def test_receiver_writes_an_old_address_as_is_then_clears_it_and_moves_to_a_rack(warehouse_api):
    client, lot = warehouse_api

    legacy = client.put(f"/api/stock/batches/{lot.id}/location", json={
        "version": lot.version, "storage_location": "  A-01 ",
    })
    found = client.get("/api/stock?q=a-01")
    cleared = client.put(f"/api/stock/batches/{lot.id}/location", json={
        "version": legacy.json()["lot"]["version"], "storage_location": "",
    })
    moved = client.post(f"/api/stock/batches/{lot.id}/move", json={
        "version": cleared.json()["lot"]["version"], "storage_location": "01f05",
    })

    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["lot"]["storage_location"] == "A-01"
    assert [item["id"] for item in found.json()["lots"]] == [str(lot.id)]
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["lot"]["storage_location"] == ""
    assert moved.status_code == 200, moved.text
    assert moved.json()["lot"]["storage_location"] == "1F5"
    assert moved.json()["lot"]["balance"] == "10"


def test_receiver_transfers_a_whole_lot_between_warehouses(warehouse_api):
    client, lot = warehouse_api
    written = client.post(f"/api/stock/batches/{lot.id}/writeoff", json={
        "version": lot.version, "quantity": "4", "purpose": "FRX.001", "reason": "Выдача",
    }).json()["lot"]

    moved = client.post(f"/api/stock/batches/{lot.id}/move", json={
        "version": written["version"], "warehouse": "ЖУК",
    })
    in_zhuk = client.get("/api/stock?warehouse=ЖУК").json()["lots"]
    in_car = client.get("/api/stock?warehouse=ЦАР&zero=true").json()["lots"]

    assert moved.status_code == 200, moved.text
    assert {key: moved.json()["lot"][key] for key in ("id", "warehouse", "storage_location", "inbound", "written_off", "balance")} == {
        "id": str(lot.id), "warehouse": "ЖУК", "storage_location": "1F4",
        "inbound": "10", "written_off": "4", "balance": "6",
    }
    assert [item["id"] for item in in_zhuk] == [str(lot.id)]
    assert in_car == []


def test_transfer_of_an_empty_lot_is_rejected_with_the_supplymap_text(warehouse_api):
    client, lot = warehouse_api
    written = client.post(f"/api/stock/batches/{lot.id}/writeoff", json={
        "version": lot.version, "quantity": "10", "purpose": "FRX.001", "reason": "Выдача",
    }).json()["lot"]

    empty = client.post(f"/api/stock/batches/{lot.id}/move", json={
        "version": written["version"], "warehouse": "ЖУК", "storage_location": "2A1",
    })
    nothing = client.post(f"/api/stock/batches/{lot.id}/move", json={"version": written["version"]})
    unknown = client.post(f"/api/stock/batches/{lot.id}/move", json={
        "version": written["version"], "warehouse": "СПБ",
    })

    assert empty.status_code == 422
    assert empty.json()["detail"] == "Нечего перемещать: остаток по FRX.26.0000001 пуст."
    assert nothing.status_code == 422
    assert nothing.json()["detail"] == "Укажи новое место или склад."
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == "Неизвестный склад «СПБ»."


def test_long_old_address_is_kept_but_regular_move_rejects_it(warehouse_api):
    client, lot = warehouse_api
    long_place = "стеллаж у окна, верхняя полка"

    kept = client.put(f"/api/stock/batches/{lot.id}/location", json={
        "version": lot.version, "storage_location": long_place,
    })
    too_long = client.put(f"/api/stock/batches/{lot.id}/location", json={
        "version": kept.json()["lot"]["version"], "storage_location": "я" * 256,
    })
    moved = client.post(f"/api/stock/batches/{lot.id}/move", json={
        "version": kept.json()["lot"]["version"], "storage_location": "A-02",
    })

    assert kept.status_code == 200, kept.text
    assert kept.json()["lot"]["storage_location"] == long_place
    assert too_long.status_code == 422
    assert moved.status_code == 422
    assert "Место хранения" in moved.json()["detail"]


def test_location_correction_needs_the_warehouse_receiver_or_admin(warehouse_api):
    client, lot = warehouse_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Закупщик", department="Закупки"),
        email="buyer@example.test",
        roles=frozenset({"buyer"}),
        permissions={"warehouse": "edit"},
    )

    response = client.put(f"/api/stock/batches/{lot.id}/location", json={
        "version": lot.version, "storage_location": "A-01",
    })

    assert response.status_code == 403


def test_manual_stock_drops_leading_zeros_of_the_rack_address(warehouse_api):
    client, _ = warehouse_api

    response = client.post("/api/stock/batches", json={"items": [{
        "warehouse": "ЦАР", "article": "CON-001", "quantity": "1", "storage_location": "01a01",
    }]})

    assert response.status_code == 200, response.text
    assert response.json()["lots"][0]["storage_location"] == "1A1"


def test_receiver_returns_a_writeoff_to_the_lot_with_its_source(warehouse_api):
    client, lot = warehouse_api
    written = client.post(f"/api/stock/batches/{lot.id}/writeoff", json={
        "version": lot.version, "quantity": "4", "purpose": "FRX.001", "reason": "Выдача в производство",
    }).json()["lot"]

    returned = client.post(f"/api/stock/batches/{lot.id}/return", json={
        "version": written["version"], "quantity": "3", "source": "FRX.001", "reason": "не израсходовано",
    })
    over = client.post(f"/api/stock/batches/{lot.id}/return", json={
        "version": returned.json()["lot"]["version"], "quantity": "2", "source": "FRX.001",
    })

    assert returned.status_code == 200, returned.text
    assert (returned.json()["lot"]["written_off"], returned.json()["lot"]["balance"]) == ("1", "9")
    assert over.status_code == 422
    assert over.json()["detail"].startswith("Больше списанного: по FRX.26.0000001 списано 1 шт")


@pytest.mark.parametrize("body", [
    {"quantity": "0", "source": "FRX.001"},
    {"quantity": "-1", "source": "FRX.001"},
    {"quantity": "1", "source": ""},
    {"quantity": "1", "source": "   "},
    {"quantity": "1"},
])
def test_writeoff_return_needs_a_positive_quantity_and_where_it_comes_from(warehouse_api, body):
    client, lot = warehouse_api
    written = client.post(f"/api/stock/batches/{lot.id}/writeoff", json={
        "version": lot.version, "quantity": "4", "purpose": "FRX.001", "reason": "Выдача",
    }).json()["lot"]

    response = client.post(f"/api/stock/batches/{lot.id}/return", json={"version": written["version"], **body})

    assert response.status_code == 422
    assert client.get("/api/stock").json()["lots"][0]["written_off"] == "4"


def test_writeoff_return_needs_the_warehouse_receiver_or_admin(warehouse_api):
    client, lot = warehouse_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Мастер", department="Производство"),
        email="master@example.test",
        roles=frozenset({"master"}),
        permissions={"warehouse": "edit"},
    )

    response = client.post(f"/api/stock/batches/{lot.id}/return", json={
        "version": lot.version, "quantity": "1", "source": "FRX.001",
    })

    assert response.status_code == 403


@pytest.mark.parametrize("body", [{}, {"purpose": ""}, {"purpose": "   "}])
def test_writeoff_needs_a_purpose(warehouse_api, body):
    client, lot = warehouse_api

    response = client.post(f"/api/stock/batches/{lot.id}/writeoff", json={
        "version": lot.version, "quantity": "1", "reason": "Выдача", **body,
    })

    assert response.status_code == 422
    assert response.json()["detail"] == "Укажи назначение: номер проекта или фамилию ответственного."
    assert client.get("/api/stock").json()["lots"][0]["written_off"] == "0"


def test_writeoff_without_a_basis_is_an_issue(warehouse_api):
    client, lot = warehouse_api

    response = client.post(f"/api/stock/batches/{lot.id}/writeoff", json={
        "version": lot.version, "quantity": "1", "purpose": "Иванов",
    })

    assert response.status_code == 200, response.text
    assert response.json()["lot"]["written_off"] == "1"


def _repeated_receipt(factory, lot, *, purpose, storage_location):
    with factory() as uow:
        uow.outbox.add(OutboxMessage(
            id=uuid4(), topic="warehouse.received", idempotency_key=f"receiving:{uuid4()}:warehouse",
            created_at=datetime(2026, 9, 22, 10, 0),
            payload={
                "receiving_line_id": str(uuid4()), "legacy_id": "SM-2", "warehouse": lot.warehouse,
                "grn": lot.grn, "package": lot.package, "article": "", "series": "", "name": lot.name,
                "item_type": lot.item_type, "purpose": purpose, "unit": lot.unit, "quantity": "5",
                "storage_location": storage_location,
            },
        ))
        uow.commit()
    return dispatch_next_warehouse_received(datetime(2026, 9, 22, 10, 1), factory())


def test_repeated_receipt_moves_the_lot_to_the_new_place_and_purpose(warehouse_api):
    client, lot = warehouse_api
    factory = app.state.warehouse_uow_factory

    _repeated_receipt(factory, lot, purpose="FRX.002", storage_location="02b07")
    found = client.get("/api/stock").json()["lots"]

    assert [(item["id"], item["inbound"], item["purpose"], item["storage_location"]) for item in found] == [
        (str(lot.id), "15", "FRX.002", "2B7"),
    ]
    with factory() as uow:
        details = uow.session.scalars(
            select(AuditEventRow.details).where(AuditEventRow.event_type == "warehouse.received")
        ).one()
    assert (details["storage_location_from"], details["storage_location_to"]) == ("1F4", "2B7")
    assert (details["purpose_from"], details["purpose_to"]) == ("FRX.001", "FRX.002")


def test_repeated_receipt_with_an_old_address_is_quarantined_and_leaves_the_lot(warehouse_api):
    client, lot = warehouse_api

    with pytest.raises(PoisonWarehouseMessage):
        _repeated_receipt(app.state.warehouse_uow_factory, lot, purpose="FRX.002", storage_location="A-01")

    found = client.get("/api/stock").json()["lots"][0]
    assert (found["inbound"], found["purpose"], found["storage_location"]) == ("10", "FRX.001", "1F4")
