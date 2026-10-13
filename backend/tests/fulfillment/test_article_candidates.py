"""«Возможно есть» (owner 12.10.2026): the tiers of matching a КД position to warehouse lots, the project's candidate
endpoint, the binding log and the report of KD article → lot article pairs."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.fulfillment.infrastructure import SqlAlchemyFulfillmentUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.shared import article_match
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from app.workflows import article_bindings_report
from tests.factories import Actor

NOW = datetime(2026, 10, 12, 12, 0)


@pytest.mark.parametrize(
    ("kd", "kd_name", "lot", "lot_name", "label"),
    [
        ("BL2018TS", "", "bl2018ts", "", "exact"),
        ("НВ4", "", "HB4", "", "exact"),  # Cyrillic twins of Latin letters
        ("SHF-001T-0.8BS", "", "SHF001T 0.8BS", "", "fuzzy-separators"),
        ("GHR-04V-S", "", "", "Колодка GHR-04V-S 4pin", "fuzzy-in_name"),
        ("BL2018TS", "SHF-001T-0.8BS", "SHF-001T-0.8BS", "Контакт", "exact-name_article"),  # the name is the lot
        ("BL2018P-04", "SMP-04V-BC", "smp04v-bc", "Корпус", "fuzzy-name_separators"),
        ("BL2018P-04", "SMP-04V-BC", "", "Корпус SMP-04V-BC JST", "fuzzy-name_in"),
        ("BL2018P-04", "Корпус SMP-04V-BC", "SMP-04V-BC", "", "fuzzy-name_in"),
        ("", "НВ4 0,2 красный", "НВ-4 0,20 красн.", "", "fuzzy-name_wire"),
        ("ПВВС 4х0,35мм, черный", "ПВВС 4х0,35мм, черный", "ПВВС 4x0,35 черн.", "", "fuzzy-name_wire"),
        ("НВ4 0,2 красный", "", "НВ-4 0,20 красн.", "", "fuzzy-wire"),
        ("", "Вилка USB-A на кабель", "", "вилка usb-a на кабель", "fuzzy-name"),
    ],
)
def test_match_tiers(kd, kd_name, lot, lot_name, label):
    found = article_match.match_lot(kd, kd_name, lot, lot_name)
    assert (found.label if found else None) == label


def test_name_is_matched_against_the_lot_article_and_the_kd_article_is_only_the_fallback():
    """Owner, 13.10.2026: «сверка … не по артикулу КД, а по наименованию с артикулом на складе»."""
    by_name = SimpleNamespace(article="SMP-04V-BC", name="Корпус разъёма")
    by_article = SimpleNamespace(article="BL2018P-04", name="Корпус")
    other = SimpleNamespace(article="SMP-02V-BC", name="Корпус 2pin")

    found = article_match.candidates("BL2018P-04", "SMP-04V-BC", [other, by_article, by_name])
    assert [(match.label, match.basis, lot.article) for match, lot in found] == [
        ("exact-name_article", "name", "SMP-04V-BC"),
    ]
    assert [match.kind for match, _lot in found] == ["exact"]

    fallback = article_match.candidates("BL2018P-04", "SMP-04V-BC", [other, by_article])
    assert [(match.label, match.basis, lot.article) for match, lot in fallback] == [("exact", "article", "BL2018P-04")]


def test_a_name_finding_nothing_gives_no_false_match():
    lots = [
        SimpleNamespace(article="SMP-02V-BC", name="Корпус 2pin"),  # one digit off
        SimpleNamespace(article="SHF-001T-0.8BS", name="Контакт"),
        SimpleNamespace(article="SMP", name="Корпус"),  # too short to stand inside a name
    ]
    assert article_match.candidates("BL2018P-04", "SMP-04V-BC", lots) == []
    assert article_match.candidates("", "Корпус SMP-04V-BC", lots) == []


@pytest.mark.parametrize(
    ("kd", "lot", "lot_name"),
    [
        ("НВ4 0,2 красный", "НВ-4 0,35 красн.", ""),  # another size
        ("НВ4 0,2 красный", "НВ-4 0,20 синий", ""),  # another colour
        ("НВ4 0,2 красный", "МГТФ 0,20 красн.", ""),  # another series
        ("ПВВС 4х0,35 черный", "ПВВС 2х0,35 черный", ""),  # another number of cores
        ("ПВВС 4х0,35 черный", "ПВВС 0,35 черный", ""),
        ("BL2018TS", "XYZ99", "Совсем другая деталь"),
        ("", "", ""),
    ],
)
def test_no_match(kd, lot, lot_name):
    assert article_match.match_lot(kd, "", lot, lot_name) is None


def test_candidates_are_ordered_exact_first():
    lots = [
        SimpleNamespace(article="", name="Колодка GHR-04V-S 4pin"),
        SimpleNamespace(article="ghr04vs", name=""),
        SimpleNamespace(article="GHR-04V-S", name=""),
    ]
    found = article_match.candidates("GHR-04V-S", "", lots)
    assert [match.label for match, _lot in found] == ["exact", "fuzzy-separators", "fuzzy-in_name"]


def test_binding_kind_is_manual_when_no_rule_offers_the_lot():
    assert article_match.binding_kind("BL2018TS", "", "XYZ99", "Деталь") == "manual"
    assert article_match.binding_kind("BL2018TS", "", "BL2018TS", "") == "exact"


@pytest.fixture()
def api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    factory = lambda: SqlAlchemyFulfillmentUnitOfWork(sessions)
    app.state.fulfillment_uow_factory = factory
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Кладовщик", department="Склад"), email="receiver@example.test", roles=frozenset({"receiver"}),
        permissions={"fulfillment": "edit", "handout": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), factory, sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.fulfillment_uow_factory
    engine.dispose()


def lot(grn: str, quantity: str, article: str, name: str, warehouse: str = "ЦАР") -> StockLot:
    return StockLot.create(
        warehouse=warehouse, grn=grn, package="", name=name, item_type="Комплектующие", purpose="Склад", unit="шт",
        quantity=Decimal(quantity), storage_location="1F4", source_receiving_ids=(f"receiving-{grn}",), article=article,
    )


def put(factory, *lots: StockLot) -> None:
    with factory() as uow:
        for item in lots:
            uow.warehouse.add(item)
        uow.commit()


def create(client, purpose: str, *lines: tuple[str, str, str]) -> dict:
    response = client.post(
        "/api/issues", headers={"Idempotency-Key": f"test-{uuid4()}"},
        json={"purpose": purpose, "deadline": "2026-10-20", "lines": [
            {"article": article, "name": name, "unit": "шт", "requirement": need} for article, name, need in lines]},
    )
    assert response.status_code == 200, response.text
    return response.json()["request"]


def test_project_candidates_in_one_pass_exact_before_fuzzy_and_only_lots_with_a_balance(api):
    client, factory, _ = api
    put(
        factory,
        lot("FRX.26.0000001", "10", "ghr-04v-s", "Колодка"),
        lot("FRX.26.0000002", "5", "GHR04VS", "Колодка"),
        lot("FRX.26.0000003", "7", "ZZZ-1", "Другое"),
    )
    create(client, "728К.2 · КБ1 · Жгут", ("GHR-04V-S", "Колодка 4pin", "3"), ("QQQ-777", "Ничего похожего", "1"))
    create(client, "729К.1 · КБ1 · Жгут", ("ZZZ-1", "Чужой проект", "1"))

    response = client.get("/api/issues/projects/728К.2/candidates")

    assert response.status_code == 200, response.text
    positions = {item["article"]: item for item in response.json()["positions"]}
    assert set(positions) == {"GHR-04V-S", "QQQ-777"}  # only this project's positions
    assert [(c["grn"], c["match"], c["kind"], c["by"]) for c in positions["GHR-04V-S"]["candidates"]] == [
        ("FRX.26.0000001", "exact", "exact", "article"), ("FRX.26.0000002", "fuzzy-separators", "fuzzy", "article"),
    ]  # «Колодка 4pin» names no lot: the КД article is the fallback
    assert positions["GHR-04V-S"]["candidates"][0]["available"] == "10"
    assert positions["QQQ-777"]["candidates"] == [] and positions["QQQ-777"]["more"] == 0


def test_project_candidates_match_the_name_against_the_lot_article_and_say_so(api):
    """Owner, 13.10.2026 (533К.12): «BL2018P-04 / SMP-04V-BC» is the lot SMP-04V-BC, not a lot of the КД article."""
    client, factory, _ = api
    put(
        factory,
        lot("FRX.26.0000001", "10", "SMP-04V-BC", "Корпус разъёма"),
        lot("FRX.26.0000002", "4", "BL2018P-04", "Корпус"),
        lot("FRX.26.0000003", "6", "I-DS1069-SCW004", "Разъём"),
    )
    create(client, "533К.12 · КБ1 · Жгут", ("BL2018P-04", "SMP-04V-BC", "2"), ("I-DS1069-SCW004", "I-DS1069-SCW004", "1"),
           ("BL2018TS", "SHF-001T-0.8BS", "5"))

    response = client.get("/api/issues/projects/533К.12/candidates")

    assert response.status_code == 200, response.text
    positions = {item["article"]: item for item in response.json()["positions"]}
    assert [(c["grn"], c["kind"], c["by"]) for c in positions["BL2018P-04"]["candidates"]] == [
        ("FRX.26.0000001", "exact", "name"),
    ]
    assert [(c["grn"], c["kind"], c["by"]) for c in positions["I-DS1069-SCW004"]["candidates"]] == [
        ("FRX.26.0000003", "exact", "name"),
    ]
    assert positions["BL2018TS"]["candidates"] == []  # nothing like the name or the article: no false match


def test_candidates_for_named_positions_without_any_kit_request(api):
    """«Снабжение» asks for its spec positions before anything went to kitting: the same matcher, the same answer."""
    client, factory, _ = api
    put(factory, lot("FRX.26.0000001", "10", "ghr-04v-s", "Колодка"), lot("FRX.26.0000002", "5", "GHR04VS", "Колодка"))

    response = client.post("/api/issues/projects/728К.2/candidates", json={"positions": [
        {"article": "GHR-04V-S", "name": "Колодка 4pin", "unit": "шт"}, {"article": "QQQ-777", "name": ""},
        {"article": "GHR-04V-S", "name": "Колодка 4pin"},  # a repeat is answered once
    ]})

    assert response.status_code == 200, response.text
    positions = response.json()["positions"]
    assert [item["article"] for item in positions] == ["GHR-04V-S", "QQQ-777"]
    assert [(c["grn"], c["kind"]) for c in positions[0]["candidates"]] == [
        ("FRX.26.0000001", "exact"), ("FRX.26.0000002", "fuzzy"),
    ]
    assert positions[1]["candidates"] == []


@pytest.mark.parametrize(("permissions", "status"), [
    ({"warehouse": "view"}, 200),
    ({"fulfillment": "edit"}, 200),
    ({"fulfillment": "view", "projects": "edit"}, 403),
    ({"projects": "view"}, 403),
])
def test_named_candidates_only_for_who_may_see_stock(api, permissions, status):
    client, _factory, _ = api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Технолог", department="КБ"), email="t@example.test", roles=frozenset({"engineer"}),
        permissions=permissions,
    )
    response = client.post("/api/issues/projects/728К.2/candidates", json={"positions": [{"article": "A-1"}]})
    assert response.status_code == status, response.text


def test_binding_writes_what_was_bound_to_what_into_the_audit_and_the_report_counts_the_pairs(api):
    client, factory, sessions = api
    exact, near = lot("FRX.26.0000001", "10", "GHR-04V-S", "Колодка"), lot("FRX.26.0000002", "10", "ghr04vs", "Колодка 4pin")
    manual = lot("FRX.26.0000003", "10", "XYZ99", "Деталь без правила")
    put(factory, exact, near, manual)
    first = create(client, "728К.2 · КБ1 · Жгут", ("GHR-04V-S", "Колодка", "2"))
    second = create(client, "728К.2 · КБ2 · Жгут", ("GHR-04V-S", "Колодка", "2"))
    third = create(client, "728К.2 · КБ3 · Жгут", ("GHR-04V-S", "Колодка", "2"))

    assert client.post(f"/api/issues/{first['id']}/lines/{first['lines'][0]['id']}/bind",
                       json={"version": first["version"], "lot_id": str(exact.id)}).status_code == 200
    assert client.post(f"/api/issues/{second['id']}/lines/{second['lines'][0]['id']}/bind",
                       json={"version": second["version"], "lot_id": str(near.id)}).status_code == 200
    projects = client.post(
        "/api/issues/projects/728К.2/bind-lots",
        json={"article": "GHR-04V-S", "name": "Колодка", "lot_ids": [str(manual.id)], "versions": {third["id"]: third["version"]}},
    )
    assert projects.status_code == 200, projects.text

    with sessions() as session:
        events = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "fulfillment.lot_bound")).all()
        by_lot = {row.details["lot_grn"]: row for row in events}
        assert set(by_lot) == {"FRX.26.0000001", "FRX.26.0000002", "FRX.26.0000003"}
        assert (by_lot["FRX.26.0000001"].details["match_kind"], by_lot["FRX.26.0000001"].details["via"]) == ("exact", "line")
        assert by_lot["FRX.26.0000002"].details["match_kind"] == "fuzzy-separators"
        manual_details = by_lot["FRX.26.0000003"].details
        assert (manual_details["match_kind"], manual_details["via"]) == ("manual", "project")
        assert manual_details["kd_article"] == "GHR-04V-S" and manual_details["position_name"] == "Колодка"
        assert manual_details["lot_article"] == "XYZ99" and manual_details["lot_name"] == "Деталь без правила"
        assert by_lot["FRX.26.0000001"].actor_name == "Кладовщик"

        text = article_bindings_report.report(session)

    rows = [line.split(";") for line in text.strip().splitlines()]
    assert rows[0][:4] == ["артикул КД", "артикул на складе", "вид совпадения", "привязок"]
    pairs = {(row[0], row[1], row[2]): row[3] for row in rows[1:]}
    assert pairs == {
        ("GHR-04V-S", "XYZ99", "manual"): "1",
        ("GHR-04V-S", "ghr04vs", "fuzzy-separators"): "1",
        ("GHR-04V-S", "GHR-04V-S", "exact"): "1",  # exact pairs go last in the file
    }
    assert rows[-1][2] == "exact"


def test_report_counts_repeated_pairs_and_skips_events_without_the_fields():
    rows = [
        ({"kd_article": "A-1", "lot_article": "A1", "match_kind": "fuzzy-separators", "lot_grn": "G1", "position_name": "Деталь", "lot_name": "Д"}, NOW, "Иван"),
        ({"kd_article": "A-1", "lot_article": "A1", "match_kind": "fuzzy-separators", "lot_grn": "G2"}, NOW, "Пётр"),
        ({"line_id": "old event"}, NOW, "Иван"),
    ]
    text = article_bindings_report.to_csv(article_bindings_report.collect(rows))
    lines = text.strip().splitlines()
    assert len(lines) == 2 and lines[1].split(";")[:4] == ["A-1", "A1", "fuzzy-separators", "2"]
    assert "G1" in lines[1] and "Пётр" in lines[1]


def test_kitset_binding_logs_the_pair_too_and_the_report_reads_it(api):
    from app.modules.fulfillment.application import HoldKitReserveCommand, hold_kit_reserve

    _client, factory, sessions = api
    stock = lot("FRX.26.0000009", "10", "ghr04vs", "Колодка")
    put(factory, stock)
    with factory() as uow:
        hold_kit_reserve(HoldKitReserveCommand(
            project="728К.2", item_index="КБ1", article="GHR-04V-S", lot_id=stock.id, quantity=Decimal("2"),
            unit="шт", actor=Actor(name="Технолог", department="КБ"), now=NOW,
        ), uow)
        uow.commit()
    with sessions() as session:
        row = session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "fulfillment.kit_reserve_held")).one()
        assert row.details["kd_article"] == "GHR-04V-S" and row.details["lot_article"] == "ghr04vs"
        assert (row.details["match_kind"], row.details["via"]) == ("fuzzy-separators", "kitset")
        assert "GHR-04V-S;ghr04vs;fuzzy-separators;1" in article_bindings_report.report(session)
