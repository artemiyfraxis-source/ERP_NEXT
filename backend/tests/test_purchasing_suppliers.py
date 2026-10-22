"""«Справочник поставщиков» и «Настройки закупки»: pure helpers, HTTP API, import from procurement, card stats."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing.infrastructure import SqlAlchemyPurchasingUnitOfWork, SupplierNameRow
from app.modules.purchasing.suppliers import inn_valid, normalize_domain, supplier_key
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, Base, create_schema
from tests.factories import Actor
from tests.procurement.test_api import message

VALID_INN10 = "7707083893"  # Sberbank
VALID_INN12 = "500100732259"


def test_supplier_key_drops_legal_forms_quotes_and_spaces():
    assert supplier_key('ООО «Чип  и Дип»') == "чип и дип"
    assert supplier_key("  чип и дип ") == "чип и дип"
    assert supplier_key('ИП Иванов  И. И.') == "иванов и и"
    assert supplier_key("Rosen Co., Ltd.") == "rosen"
    assert supplier_key("ООО") == "ооо"  # nothing left — keep the text
    assert supplier_key("Ёлка") == supplier_key("елка")


def test_inn_checksum():
    assert inn_valid(VALID_INN10) and inn_valid(VALID_INN12)
    assert not inn_valid("7707083894") and not inn_valid("500100732258") and not inn_valid("123")
    assert not inn_valid("77070838a3")


def test_domain_normalization():
    assert normalize_domain("https://www.ChipDip.ru/catalog?x=1") == "chipdip.ru"
    assert normalize_domain("WWW.rosen.cn/") == "rosen.cn"


@pytest.fixture()
def api():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    procurement_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.procurement_uow_factory = procurement_factory
    app.state.procurement_supplier_directory = None

    def as_role(roles, permissions):
        app.dependency_overrides[authenticated_principal] = lambda: Principal(
            actor=Actor(name="Петров", department="Закупки"), email="p@example.test",
            roles=frozenset(roles), permissions=permissions,
        )

    as_role({"buyer"}, {"procurement": "edit"})
    yield TestClient(app), sessions, procurement_factory, as_role
    app.dependency_overrides.pop(authenticated_principal, None)
    for name in ("purchasing_uow_factory", "procurement_uow_factory", "procurement_supplier_directory"):
        delattr(app.state, name)
    engine.dispose()


def _add_lines(procurement_factory, sessions, specs):
    for index, spec in enumerate(specs, start=10):
        line = consume_procurement_requested(message(index), procurement_factory())
        with sessions.begin() as session:
            row = session.get(ProcurementLineRow, line.id)
            for field, value in spec.items():
                setattr(row, field, value)


def test_create_validates_and_audits(api):
    client, sessions, *_ = api
    bad = client.post("/api/purchasing/suppliers", json={"name": "ООО «Ромашка»", "inn": "7707083894"})
    assert bad.status_code == 422 and "контрольной суммы" in bad.json()["detail"]
    assert client.post("/api/purchasing/suppliers", json={"name": "  "}).status_code == 422
    assert client.post("/api/purchasing/suppliers", json={"name": "Х", "categories": ["nope"]}).status_code == 422
    ok = client.post(
        "/api/purchasing/suppliers",
        json={"name": "ООО «Ромашка»", "inn": VALID_INN10, "categories": ["connector", "cable"], "domains": ["https://www.Romashka.ru/x"]},
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["categories"] == ["cable", "connector"] and body["domains"] == ["romashka.ru"] and body["version"] == 1
    dup = client.post("/api/purchasing/suppliers", json={"name": "Ромашка"})
    assert dup.status_code == 409
    dup_inn = client.post("/api/purchasing/suppliers", json={"name": "Другой", "inn": VALID_INN10})
    assert dup_inn.status_code == 409
    with sessions() as session:
        events = [row.event_type for row in session.scalars(select(AuditEventRow))]
    assert events.count("purchasing.supplier_created") == 1


def test_patch_version_conflict_archive_and_filters(api):
    client, *_ = api
    created = client.post("/api/purchasing/suppliers", json={"name": "Альфа", "inn": VALID_INN12}).json()
    sid = created["id"]
    patched = client.patch(f"/api/purchasing/suppliers/{sid}", json={"version": 1, "is_key": True, "phone": "+7 495", "inn": None})
    assert patched.status_code == 200 and patched.json()["version"] == 2
    assert patched.json()["is_key"] is True and patched.json()["inn"] is None
    stale = client.patch(f"/api/purchasing/suppliers/{sid}", json={"version": 1, "phone": "1"})
    assert stale.status_code == 409
    client.post("/api/purchasing/suppliers", json={"name": "Бета", "categories": ["ekb"]})
    assert [s["name"] for s in client.get("/api/purchasing/suppliers?key=true").json()] == ["Альфа"]
    assert [s["name"] for s in client.get("/api/purchasing/suppliers?category=ekb").json()] == ["Бета"]
    assert [s["name"] for s in client.get("/api/purchasing/suppliers?q=альф").json()] == ["Альфа"]
    assert client.post(f"/api/purchasing/suppliers/{sid}/archive").json()["archived"] is True
    assert [s["name"] for s in client.get("/api/purchasing/suppliers").json()] == ["Бета"]
    assert [s["name"] for s in client.get("/api/purchasing/suppliers?archived=true").json()] == ["Альфа"]
    assert client.post(f"/api/purchasing/suppliers/{sid}/unarchive").json()["archived"] is False
    assert client.get("/api/purchasing/suppliers/00000000-0000-0000-0000-000000000099").status_code == 404


def test_import_dry_run_then_apply_groups_variants(api):
    client, sessions, procurement_factory, *_ = api
    _add_lines(procurement_factory, sessions, [
        {"supplier": 'ООО «ЧипДип»'}, {"supplier": "ЧипДип"}, {"supplier": "чипдип "}, {"supplier": "Rosen"}, {"supplier": "chipdip.ru"},
    ])
    assert client.post("/api/purchasing/suppliers", json={"name": "Rosen"}).status_code == 201

    class Directory:
        def supplier_map(self):
            return {"chipdip.ru": "ЧипДип"}

    app.state.procurement_supplier_directory = Directory()
    dry = client.post("/api/purchasing/suppliers/import-from-procurement", json={"dry_run": True}).json()
    assert dry["dry_run"] is True and len(dry["new"]) == 1 and len(dry["existing"]) == 1
    new = dry["new"][0]
    assert new["name"] == "ЧипДип" and new["lines"] == 4 and new["domains"] == ["chipdip.ru"]
    assert {v["text"] for v in new["variants"]} == {'ООО «ЧипДип»', "ЧипДип", "чипдип", "chipdip.ru"}
    assert client.get("/api/purchasing/suppliers").json()[0]["name"] == "Rosen"  # dry run wrote nothing
    assert len(client.get("/api/purchasing/suppliers").json()) == 1

    applied = client.post("/api/purchasing/suppliers/import-from-procurement", json={"dry_run": False}).json()
    assert applied["created"] == 1
    rows = {s["name"]: s for s in client.get("/api/purchasing/suppliers").json()}
    assert set(rows) == {"ЧипДип", "Rosen"} and rows["ЧипДип"]["domains"] == ["chipdip.ru"]
    assert rows["ЧипДип"]["purchases_count"] == 4 and rows["Rosen"]["purchases_count"] == 1
    again = client.post("/api/purchasing/suppliers/import-from-procurement", json={"dry_run": True}).json()
    assert again["new"] == [] and all(not e["new_variants"] and not e["new_domains"] for e in again["existing"])
    with sessions() as session:
        assert session.get(SupplierNameRow, "chipdip.ru") is not None


def test_name_mapping_attaches_free_text_to_a_supplier(api):
    client, sessions, procurement_factory, *_ = api
    sid = client.post("/api/purchasing/suppliers", json={"name": "Фраксис"}).json()["id"]
    _add_lines(procurement_factory, sessions, [{"supplier": "Fraxis shop"}])
    assert client.get(f"/api/purchasing/suppliers/{sid}").json()["procurement"]["lines_total"] == 0
    assert client.post("/api/purchasing/suppliers/names", json={"name": "Fraxis shop", "supplier_id": sid}).status_code == 200
    assert client.get(f"/api/purchasing/suppliers/{sid}").json()["procurement"]["lines_total"] == 1


def test_card_stats_from_procurement_lines(api):
    client, sessions, procurement_factory, *_ = api
    sid = client.post("/api/purchasing/suppliers", json={"name": "Альфа"}).json()["id"]
    _add_lines(procurement_factory, sessions, [
        {
            "supplier": "ООО Альфа", "paid_at": datetime(2026, 9, 20, 10), "actual_sum": Decimal("1000.00"),
            "actual_price_with_vat": Decimal("500"), "actual_quantity": Decimal("2"), "actual_unit": "шт",
            "order_number": "З-1", "order_date": date(2026, 9, 18), "status": "Готово",
            "expected_arrival": date(2026, 9, 25), "actual_arrival": date(2026, 9, 24),
        },
        {
            "supplier": "Альфа", "paid_at": datetime(2026, 9, 21, 10), "actual_sum": Decimal("300.50"), "status": "Карантин",
            "order_number": "З-2", "order_date": date(2026, 9, 19),
            "expected_arrival": date(2026, 9, 25), "actual_arrival": date(2026, 9, 28),
        },
        {"supplier": "Альфа", "status": "В пути"},
        {"supplier": "Чужой", "paid_at": datetime(2026, 9, 21, 10), "actual_sum": Decimal("99")},
    ])
    card = client.get(f"/api/purchasing/suppliers/{sid}").json()
    stats = card["procurement"]
    assert stats["lines_total"] == 3 and stats["paid_lines"] == 2 and Decimal(stats["paid_sum"]) == Decimal("1300.50")
    assert stats["on_time_pct"] == 50 and stats["received"] == 2
    assert stats["open_by_stage"] == {"Карантин": 1, "В пути": 1}
    assert [p["order_number"] for p in stats["last_purchases"]] == ["З-2", "З-1"]
    assert stats["last_purchases"][1]["unit_price_with_vat"] == "500.000000" or Decimal(stats["last_purchases"][1]["unit_price_with_vat"]) == 500
    assert card["rfq"] == {"sent": 0, "answered": 0, "quotes": 0, "answered_pct": None}


def test_settings_read_for_buyer_write_for_admin_only(api):
    client, sessions, _, as_role = api
    rows = client.get("/api/purchasing/settings").json()
    assert {r["key"] for r in rows} >= {"gate_min_quotes", "default_vat_rate"}
    vat = next(r for r in rows if r["key"] == "default_vat_rate")
    assert vat["value"] == "22" and vat["default"] == "22" and vat["kind"] == "decimal"
    assert client.put("/api/purchasing/settings", json={"changes": {"gate_min_quotes": 2}}).status_code == 403
    as_role({"admin"}, {})
    changed = client.put("/api/purchasing/settings", json={"changes": {"gate_min_quotes": 2}})
    assert changed.status_code == 200
    assert next(r for r in changed.json() if r["key"] == "gate_min_quotes")["value"] == 2
    assert client.put("/api/purchasing/settings", json={"changes": {"gate_min_quotes": 0}}).status_code == 422
    with sessions() as session:
        events = [row.event_type for row in session.scalars(select(AuditEventRow))]
    assert events == ["purchasing.settings_changed"]
