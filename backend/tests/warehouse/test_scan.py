"""Stage option warehouse.scan: GET /api/stock/scan — a ГРН (package, case, look-alikes, Russian layout) or a place."""

import json
from decimal import Decimal

import pytest

from app.main import app
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.scan import parse_scan, place_matches
from app.shared import stage_options
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.warehouse.test_api import warehouse_api  # noqa: F401 — fixture


@pytest.fixture()
def stage(monkeypatch, tmp_path):
    path = tmp_path / "options.json"
    monkeypatch.setenv(stage_options.ENV, str(path))
    return lambda **options: path.write_text(json.dumps(options), encoding="utf-8")


def _lot(grn, package="", warehouse="ЦАР", place="1F4", quantity="5") -> StockLot:
    return StockLot.create(
        warehouse=warehouse, grn=grn, package=package, name=f"Лот {grn} {package}", item_type="Комплектующие",
        purpose="FRX.001", unit="шт", quantity=Decimal(quantity), storage_location=place,
        source_receiving_ids=(f"r-{grn}-{package}-{warehouse}",),
    )


@pytest.fixture()
def scan_api(warehouse_api, stage):  # noqa: F811
    client, first = warehouse_api
    stage(**{"warehouse.scan": True})
    emptied = _lot("OLD.26.0000009", place="1F4").write_off(Decimal("5"))
    with app.state.warehouse_uow_factory() as uow:
        for lot in (
            _lot("FRX.26.0000001", "П2"),
            _lot("PPM.26.0000002", warehouse="ЖУК", place="1F4"),
            _lot("OLD.26.0000005", place="1F4A"),
            _lot("OLD.26.0000006", place="1F40"),
            emptied,
        ):
            uow.warehouse.add(lot)
        uow.commit()
    return client, first


def _scan(client, code):
    return client.get("/api/stock/scan", params={"code": code})


def _keys(response):
    assert response.status_code == 200, response.text
    return [(lot["grn"], lot["package"], lot["warehouse"]) for lot in response.json()["lots"]]


@pytest.mark.parametrize("code", ["FRX.26.0000001", " frx.26.0000001 ", "FRХ.26.0000001", "АКЧю26ю0000001"])
def test_a_grn_gives_every_package_of_it(scan_api, code):
    client, _ = scan_api
    response = _scan(client, code)
    assert response.json()["kind"] == "lot"
    assert _keys(response) == [("FRX.26.0000001", "", "ЦАР"), ("FRX.26.0000001", "П2", "ЦАР")]


@pytest.mark.parametrize("code", ["FRX.26.0000001-П2", "frx.26.0000001 п2", "FRX.26.0000001П2", "FRX.26.0000001 / П2"])
def test_a_grn_with_its_package_gives_that_lot(scan_api, code):
    client, _ = scan_api
    assert _keys(_scan(client, code)) == [("FRX.26.0000001", "П2", "ЦАР")]


def test_a_lot_without_balance_is_still_found_by_its_grn(scan_api):
    client, _ = scan_api
    response = _scan(client, "old.26.0000009")
    assert _keys(response) == [("OLD.26.0000009", "", "ЦАР")]
    assert response.json()["lots"][0]["balance"] == "0"


def test_a_place_lists_what_lies_there_in_both_warehouses(scan_api):
    client, _ = scan_api
    response = _scan(client, "1f4")
    body = response.json()
    assert body["kind"] == "place" and body["place"] == "1F4"
    # cells of the place are on it, «1F40» is another place, an empty lot lies nowhere
    assert sorted(_keys(response)) == [
        ("FRX.26.0000001", "", "ЦАР"), ("FRX.26.0000001", "П2", "ЦАР"), ("OLD.26.0000005", "", "ЦАР"),
        ("PPM.26.0000002", "", "ЖУК"),
    ]
    assert _keys(_scan(client, "01F4А")) == [("OLD.26.0000005", "", "ЦАР")]


def test_an_empty_place_is_a_place_with_nothing(scan_api):
    client, _ = scan_api
    response = _scan(client, "9Z9")
    assert response.status_code == 200
    assert response.json() == {"kind": "place", "code": "9Z9", "place": "9Z9", "lots": []}


@pytest.mark.parametrize("code", ["FRX.26.0000001-П9", "FRX.26.0000077", "zzz", ""])
def test_nothing_found_is_404_with_the_code(scan_api, code):
    client, _ = scan_api
    response = _scan(client, code)
    assert response.status_code == 404
    assert response.json()["detail"] == f"Не найдено: {code or '—'}"


def test_option_off_hides_the_route_after_the_gate(warehouse_api, stage):  # noqa: F811
    client, _ = warehouse_api
    stage(**{"warehouse.scan": False})
    assert _scan(client, "FRX.26.0000001").status_code == 404
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Инженер", department="РНД"), email="engineer@example.test",
        roles=frozenset({"tech_lead"}), permissions={"fulfillment": "edit"},
    )
    assert _scan(client, "FRX.26.0000001").status_code == 403


def test_view_mode_may_scan(warehouse_api, stage):  # noqa: F811
    client, _ = warehouse_api
    stage(**{"warehouse.scan": True})
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Мастер", department="Цех"), email="master@example.test",
        roles=frozenset({"foreman"}), permissions={"warehouse": "view"},
    )
    assert _keys(_scan(client, "FRX.26.0000001")) == [("FRX.26.0000001", "", "ЦАР")]


def test_parse_and_place_rules():
    assert parse_scan("R&D.26.0000003").grn.grn == "R&D.26.0000003"
    assert parse_scan("К?Вю26ю0000003").grn.grn == "R&D.26.0000003"  # Russian layout
    assert parse_scan("12c7b").place == "12C7B"
    assert parse_scan("A-01").place is None and parse_scan("A-01").grn is None
    assert place_matches("12C7", "12C7B") and not place_matches("12C7B", "12C7") and not place_matches("12C7", "12C70")
    assert not place_matches("12C7", "") and not place_matches("12C7", "A-01")
