"""Complete calculation evidence is read-only until explicit source refresh."""

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.requests.application import CalcSheetError, CalcSpreadsheet
from app.modules.requests.infrastructure import CalcCompositionRow, RequestRow
from app.modules.warehouse.infrastructure import StockLotRow
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, OutboxMessageRow
from tests.factories import Actor
from tests.procurement.test_backorder_flow import db  # noqa: F401
from tests.workflows.test_kitset_requests_to_procurement import FakeCalc, kitset_requests, drain

BASE = "/api/procurement/requests-composition"
BODY = {"project": "708К.4", "url": "https://docs.google.com/spreadsheets/d/1calc/edit", "expected_version": 0}


@pytest.fixture()
def api(db, monkeypatch):
    monkeypatch.setattr(app.state, "uow_factory", db.requests, raising=False)
    monkeypatch.setattr(app.state, "procurement_uow_factory", db.procurement, raising=False)
    monkeypatch.setattr(app.state, "calc_spreadsheet_reader", FakeCalc(), raising=False)
    old = dict(app.dependency_overrides)
    _access("edit")
    with TestClient(app) as client:
        yield client, db
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)


def _access(level):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Закупщик", department="Закупки"), email="buyer@example.test",
        roles=frozenset({"buyer"}), permissions={"procurement": level},
    )


def _counts(db):
    with db.sessions() as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in (
            RequestRow, OutboxMessageRow, AuditEventRow, CalcCompositionRow,
        ))


def _lot(db, grn, article="МГТФ 0,35", unit="м", **changes):
    values = dict(
        id=uuid4(), version=1, warehouse="ЦАР", grn=grn, package="1", article=article,
        name="МГТФ 0,35", series="", item_type="Провод", purpose="", unit=unit, storage_location="",
        inbound=Decimal("10"), written_off=Decimal("2"), reserved=Decimal("3"), source_receiving_ids=[],
    )
    values.update(changes)
    with db.sessions() as session:
        session.add(StockLotRow(**values))
        session.commit()
    return str(values["id"])


def test_missing_source_is_honest_and_get_never_reads_google_or_writes(api, monkeypatch):
    client, db = api
    class ForbiddenReader:
        def read_calc(self, _):
            raise AssertionError("GET must not fetch source")
    monkeypatch.setattr(app.state, "calc_spreadsheet_reader", ForbiddenReader())
    before = _counts(db)
    result = client.get(BASE, params={"project": "708К.4"})
    assert result.status_code == 200
    assert result.json() == {"project": "708К.4", "state": "missing", "version": 0, "source": None, "rows": []}
    assert _counts(db) == before


def test_refresh_full_zkp_including_covered_invalid_rows_and_individual_lots(api):
    client, db = api
    first = _lot(db, "TEST-1")
    second = _lot(db, "TEST-2", warehouse="ЖУК", package="2", unit="М.", reserved=Decimal("8"))
    _lot(db, "WRONG-UNIT", unit="шт")
    _lot(db, "UNKNOWN-UNIT", unit="")
    response = client.post(BASE + "/refresh", json=BODY)
    assert response.status_code == 200, response.text
    before = _counts(db)
    result = client.get(BASE, params={"project": "708К.4"}).json()
    assert result["coverage"] == "full_source" and not result["valid"]
    assert len(result["rows"]) == 3
    row = result["rows"][0]
    assert row["quantity"] == "12.5" and row["price_with_vat"] == "1200.60"
    assert row["request_id"] is None and row["procurement_ids"] == []
    assert {lot["id"] for lot in row["lots"]} == {first, second}
    assert {Decimal(lot["free"]) for lot in row["lots"]} == {Decimal("5"), Decimal("0")}
    assert all(lot["match_source"] == "exact_article" for lot in row["lots"])
    assert _counts(db) == before == (0, 0, 1, 1)
    assert client.post(BASE + "/refresh", json=BODY).status_code == 409
    assert client.post(BASE + "/refresh", json={**BODY, "expected_version": 1}).json()["version"] == 1
    assert _counts(db) == before


def test_recorded_import_links_are_exact_not_name_guesses(api):
    client, db = api
    kitset_requests(db)
    lines = drain(db.procurement)
    result = client.get(BASE, params={"project": "708К.4"}).json()
    assert {p for row in result["rows"] for p in row["procurement_ids"]} == {str(line.id) for line in lines}
    assert result["rows"][2]["request_id"] is None


def test_name_matches_across_different_articles_are_explicitly_ambiguous(api):
    client, db = api
    _lot(db, "TEST-1", article="OTHER-A")
    _lot(db, "TEST-2", article="OTHER-B")
    client.post(BASE + "/refresh", json=BODY)
    lots = client.get(BASE, params={"project": "708К.4"}).json()["rows"][0]["lots"]
    assert len(lots) == 2
    assert all(lot["match_state"] == "ambiguous" and lot["match_source"] == "exact_name" for lot in lots)


@pytest.mark.parametrize("level,read,write", [("none", 403, 403), ("view", 200, 403), ("edit", 200, 200)])
def test_permissions(api, level, read, write):
    client, _ = api
    _access(level)
    assert client.get(BASE, params={"project": "708К.4"}).status_code == read
    assert client.post(BASE + "/refresh", json=BODY).status_code == write


def test_refresh_failure_or_project_mismatch_rolls_back(api, monkeypatch):
    client, db = api
    before = _counts(db)
    assert client.post(BASE + "/refresh", json={**BODY, "project": "709К.1"}).status_code == 422
    class BrokenReader:
        def read_calc(self, _):
            raise CalcSheetError("Источник недоступен")
    monkeypatch.setattr(app.state, "calc_spreadsheet_reader", BrokenReader())
    assert client.post(BASE + "/refresh", json=BODY).status_code == 503
    assert _counts(db) == before


@pytest.mark.parametrize("item_type", ["", "Неизвестный тип", "Разъём"])
def test_default_piece_unit_is_not_source_evidence_or_stock_match(api, monkeypatch, item_type):
    client, db = api
    _lot(db, "PIECES", unit="шт")
    class Source:
        def read_calc(self, _):
            return CalcSpreadsheet(title="708К.4", project_cell="708К.4", zpk_grid=[
                ["Тип", "Обозначение", "На проект", "Без НДС"],
                [item_type, "МГТФ 0,35", "1", "10"],
            ])
    monkeypatch.setattr(app.state, "calc_spreadsheet_reader", Source())
    assert client.post(BASE + "/refresh", json=BODY).status_code == 200
    result = client.get(BASE, params={"project": "708К.4"}).json()
    row = result["rows"][0]
    assert row["unit_source"] == "unknown" and row["unit"] == row["raw"]["unit"] == ""
    assert row["lots"] == [] and row["issues"] == ["unit"] and not result["valid"]


@pytest.mark.parametrize("header", ["Ед. изм.", "Единица измерения", "Ед.изм", "Ед."])
def test_explicit_kg_is_preserved_and_matches_only_kg(api, monkeypatch, header):
    client, db = api
    kg_id = _lot(db, "KG", unit="кг")
    _lot(db, "PIECES", unit="шт")
    _lot(db, "METERS", unit="м")
    class Source:
        def read_calc(self, _):
            return CalcSpreadsheet(title="708К.4", project_cell="708К.4", zpk_grid=[
                ["Тип", "Обозначение", "На проект", "Без НДС", header],
                ["Провод", "МГТФ 0,35", "1", "10", " кг "],
            ])
    monkeypatch.setattr(app.state, "calc_spreadsheet_reader", Source())
    assert client.post(BASE + "/refresh", json=BODY).status_code == 200
    row = client.get(BASE, params={"project": "708К.4"}).json()["rows"][0]
    assert row["unit_source"] == "explicit" and row["unit"] == "кг" and row["raw"]["unit"] == " кг "
    assert [lot["id"] for lot in row["lots"]] == [kg_id]
