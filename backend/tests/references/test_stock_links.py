"""«Склад → база данных» (T14): a stock article tied to, or made into, a reference record — each event is journaled
with the record change in one transaction; the journal reads per record and as a whole list."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.references.infrastructure import (
    STOCK_LINK_EVENTS, STOCK_LINK_TABS, ReferenceStockLinkRow, SqlAlchemyReferencesUnitOfWork,
)
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor

ACTOR = Actor(id=UUID("10000000-0000-0000-0000-000000000007"), name="Кладовщик Петров", department="Склад")
NOW = datetime(2026, 10, 13, 11, 30)


def _principal(permissions: dict[str, str]) -> Principal:
    return Principal(actor=ACTOR, email="stock@example.test", roles=frozenset({"receiver"}), permissions=permissions)


@pytest.fixture()
def stock_refs():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    warehouse = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.state.warehouse_uow_factory = warehouse
    app.dependency_overrides[authenticated_principal] = lambda: _principal({"erpref": "edit", "warehouse": "edit"})
    app.dependency_overrides[moscow_now] = lambda: NOW
    lot = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000042", package="2", name="Контакт обжимной", article="SXH-001T-P0.6",
        item_type="Комплектующие", purpose="FRX.001", unit="шт", quantity=Decimal("500"), storage_location="1F4",
        source_receiving_ids=("20000000-0000-0000-0000-000000000001",),
    )
    with warehouse() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    client = TestClient(app)
    record = _seed(client)
    yield client, lot, record, sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.references_uow_factory
    del app.state.warehouse_uow_factory
    engine.dispose()


def _seed(client) -> dict:
    created = client.post("/api/references/tech-operations", json={"tab": "mat", "fields": {
        "Наименование": "Контакт SXH", "Производитель": "", "Серия": "XH"}})
    assert created.status_code == 201
    return created.json()


def _rows(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(ReferenceStockLinkRow))


def test_tying_a_lot_to_a_record_is_journaled_once(stock_refs):
    client, lot, record, sessions = stock_refs

    first = client.post("/api/references/stock-links", json={"lot_id": str(lot.id), "record_id": record["id"]})
    again = client.post("/api/references/stock-links", json={"lot_id": str(lot.id), "record_id": record["id"]})

    assert first.status_code == 200, first.text
    entry = first.json()["entry"]
    assert entry["event"] == "linked" and entry["changes"] == []
    assert (entry["warehouse"], entry["grn"], entry["package"]) == ("ЦАР", "FRX.26.0000042", "2")
    assert entry["article"] == "SXH-001T-P0.6" and entry["name"] == "Контакт обжимной"
    assert entry["record_id"] == record["id"] and entry["record_tab"] == "mat" and entry["record_text"] == "Контакт SXH"
    assert entry["actor_name"] == "Кладовщик Петров" and entry["occurred_at"].startswith("2026-10-13T11:30")
    assert again.status_code == 200 and again.json()["entry"] is None  # the same tie again writes nothing
    assert _rows(sessions) == 1


def test_updating_a_record_from_a_lot_journals_what_changed_and_the_record_changes(stock_refs):
    client, lot, record, _ = stock_refs

    response = client.post("/api/references/stock-links", json={
        "lot_id": str(lot.id), "record_id": record["id"],
        "edits": [{"field": "Производитель", "old": "", "value": "JST"}, {"field": "Серия", "old": "XH", "value": "XH"}],
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["record"]["fields"]["Производитель"] == "JST"
    assert body["entry"]["event"] == "updated"
    assert body["entry"]["changes"] == [{"field": "Производитель", "old": "", "new": "JST"}]  # «Серия» did not change
    journal = client.get("/api/references/stock-links", params={"record_id": record["id"]}).json()["entries"]
    assert [item["event"] for item in journal] == ["updated"]


def test_a_new_record_made_from_a_lot_is_journaled_as_created(stock_refs):
    client, lot, _, sessions = stock_refs

    response = client.post("/api/references/stock-links", json={
        "lot_id": str(lot.id), "tab": "mat", "fields": {"Наименование": "Контакт обжимной", "Серия": "", "Производитель": "JST"}})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entry"]["event"] == "created" and body["entry"]["record_id"] == body["record"]["id"]
    assert {(change["field"], change["old"], change["new"]) for change in body["entry"]["changes"]} == {
        ("Наименование", "", "Контакт обжимной"), ("Производитель", "", "JST")}
    with sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "references.tech_record_created")).all()
        assert any(event.details.get("lot_id") == str(lot.id) and event.details.get("source") == "warehouse" for event in events)


def test_the_whole_journal_is_newest_first_and_per_record_shows_only_that_record(stock_refs):
    client, lot, record, _ = stock_refs
    stamps = iter([datetime(2026, 10, 13, 9, 0), datetime(2026, 10, 13, 10, 0)])
    app.dependency_overrides[moscow_now] = lambda: next(stamps)
    client.post("/api/references/stock-links", json={"lot_id": str(lot.id), "record_id": record["id"]})
    other = client.post("/api/references/stock-links", json={"lot_id": str(lot.id), "tab": "mat",
                                                              "fields": {"Наименование": "Другая"}}).json()["record"]

    everything = client.get("/api/references/stock-links").json()["entries"]
    mine = client.get("/api/references/stock-links", params={"record_id": record["id"]}).json()["entries"]

    assert [(item["event"], item["record_text"]) for item in everything] == [("created", "Другая"), ("linked", "Контакт SXH")]
    assert [item["record_id"] for item in mine] == [record["id"]] and other["id"] != record["id"]


def test_refusals_write_no_journal_and_change_no_record(stock_refs):
    client, lot, record, sessions = stock_refs
    url = "/api/references/stock-links"

    unknown_lot = client.post(url, json={"lot_id": str(uuid4()), "record_id": record["id"]})
    unknown_record = client.post(url, json={"lot_id": str(lot.id), "record_id": str(uuid4())})
    stale = client.post(url, json={"lot_id": str(lot.id), "record_id": record["id"],
                                   "edits": [{"field": "Серия", "old": "не то", "value": "PH"}]})
    view_only = client.post(url, json={"lot_id": str(lot.id), "tab": "spr_kab", "fields": {"a": "1"}})
    other_base = client.post(url, json={"lot_id": str(lot.id), "record_id": record["id"], "tab": "ter"})
    empty = client.post(url, json={"lot_id": str(lot.id), "tab": "mat", "fields": {"Наименование": " "}})
    formula = client.post(url, json={"lot_id": str(lot.id), "tab": "mat", "fields": {"Наименование": "=1+1"}})

    assert unknown_lot.status_code == 404 and "Партия" in unknown_lot.json()["detail"]
    assert unknown_record.status_code == 404
    assert stale.status_code == 409
    assert view_only.status_code == 422 and other_base.status_code == 422
    assert empty.status_code == 422 and formula.status_code == 422
    assert _rows(sessions) == 0
    current = client.get("/api/references/tech-operations", params={"tab": "mat"}).json()["records"]
    assert [item["fields"]["Серия"] for item in current] == ["XH"]


def test_reading_needs_the_references_view_and_writing_their_edit(stock_refs):
    client, lot, record, _ = stock_refs
    app.dependency_overrides[authenticated_principal] = lambda: _principal({"erpref": "view", "warehouse": "edit"})

    refused = client.post("/api/references/stock-links", json={"lot_id": str(lot.id), "record_id": record["id"]})
    read = client.get("/api/references/stock-links")

    assert refused.status_code == 403
    assert read.status_code == 200 and read.json() == {"entries": []}


def test_a_terminal_edit_journals_the_column_named_not_the_schema_value_derived_from_it(stock_refs):
    client, lot, _, _ = stock_refs
    terminal = client.post("/api/references/tech-operations", json={"tab": "ter", "fields": {
        "Производитель": "Connfly", "Product Name": "", "Series": "I-DS1066"}}).json()
    assert terminal["fields"].get("terComponent", "") == ""

    response = client.post("/api/references/stock-links", json={"lot_id": str(lot.id), "record_id": terminal["id"],
        "edits": [{"field": "Product Name", "old": "", "value": "Контакт обжимной"}]})

    assert response.status_code == 200, response.text
    assert response.json()["record"]["fields"]["terComponent"] == "Контакт обжимной"  # derived from the column
    assert response.json()["entry"]["changes"] == [{"field": "Product Name", "old": "", "new": "Контакт обжимной"}]


def test_database_checks_name_every_event_and_editable_base():
    checks = {constraint.name: str(constraint.sqltext) for constraint in ReferenceStockLinkRow.__table__.constraints
              if constraint.name and constraint.name.startswith("ck_")}
    assert checks["ck_reference_stock_links_event"] == "event IN (" + ", ".join(f"'{e}'" for e in STOCK_LINK_EVENTS) + ")"
    assert checks["ck_reference_stock_links_tab"] == "record_tab IN (" + ", ".join(f"'{t}'" for t in STOCK_LINK_TABS) + ")"
