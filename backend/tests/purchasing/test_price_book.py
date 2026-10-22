"""«Цены и карточки» (references.price_book): price book rules, UPD prices of an item, supplier card, catalog of
purchases, items from UPD lines, the option gate and «раньше дешевле» in the request pool."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.infrastructure import SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing import price_book as book
from app.modules.purchasing import upd_prices as prices
from app.modules.purchasing.infrastructure import (
    InvoiceRow,
    ItemAliasRow,
    ItemRow,
    PriceRefRow,
    SqlAlchemyPurchasingUnitOfWork,
    SupplierRow,
    UpdDocumentRow,
    UpdLineRow,
)
from app.modules.purchasing.items import alias_key, part_key
from app.modules.purchasing.suppliers import supplier_key
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.purchasing.test_rfq_pool import stand  # noqa: F401 - the request pool stand

NOW = datetime(2026, 10, 12, 12, 0)


# ---- pure rules -----------------------------------------------------------------------------------------------------

def test_unit_factor_converts_kilometres_and_refuses_unknown_units():
    assert prices.unit_factor("км", "", "м") == 1000
    assert prices.unit_factor("", "", "шт") == 1  # the UPD names none: assumed the same
    assert prices.unit_factor("шт", "796", "шт") == 1
    assert prices.unit_factor("бухта", "", "м") is None
    assert prices.unit_factor("шт", "", "м") is None
    assert prices.unit_factor("г", "163", "кг") == 0.001


def test_unit_gross_from_totals_or_from_price_and_vat():
    assert prices.upd_unit_gross(Decimal("10"), 95_000, None, "") == 9_500
    assert prices.upd_unit_gross(None, None, Decimal("100"), "22") == 12_200
    assert prices.upd_unit_gross(Decimal("0"), 100, None, "") is None
    assert prices.upd_unit_gross(None, None, Decimal("100"), "без НДС") == 10_000


def test_catalog_key_ignores_numeric_articles():
    assert prices.catalog_key("n1", "SMA-J 01", "SMAJ01") == "a:SMAJ01"
    assert prices.catalog_key("n1", "8534001900", "8534001900") == "n:n1"  # a customs code
    assert prices.catalog_key("", "", None) == ""


def test_advance_and_service_wordings():
    assert prices.is_advance_line("Предварительная оплата по счёту")
    assert prices.is_advance_line("Аванс")
    assert prices.unit_from_upd("усл.") is None
    assert prices.unit_from_upd("км") == "м" and prices.unit_from_upd("", "796") == "шт"


def test_suppliers_are_matched_across_alphabets_and_legal_forms():
    directory = [(1, 'ООО "Группа МЕТТАТРОН"'), (2, 'ООО "ДАРТ ХОЛДИНГ"'), (3, 'АО "СЭГ-Электро"')]
    assert book.match_supplier("МЕТТАТРОН", directory)[0] == 1
    assert book.match_supplier("DART", directory)[0] == 2
    assert book.match_supplier("СЭГ", directory) is None
    assert book.match_supplier("CINTY", directory) is None


def _row(**over):
    base = dict(row=2, article="GHR-05V-S", type="Соединитель", series="GH", manufacturer="JST",
                offered_part="GHR-05V-S", supplier_name="ELITAN", qty=1000, currency="RUB", price=3.43, lead_time="2 р.д.")
    return book.PriceBookRow(**{**base, **over})


def test_price_book_groups_original_and_analog_into_one_item():
    groups = book.group_price_book([
        _row(),
        _row(row=3, supplier_name="FRAXIS", manufacturer="SHENGHUI", offered_part="SH10000-05Y", currency="CNY", price=0.0177),
        _row(row=4, article="Т/уcaд. тpубкa RBF Raychman D:3,2/1,6 mm кpacнaя (200m/буxтa)", type="Термоусадочная трубка",
             series="", manufacturer="МЕТТАТРОН", offered_part="RBF 3,2"),
    ])
    assert len(groups) == 2
    gh = next(g for g in groups if g.part_number == "GHR-05V-S")
    assert gh.name == "Соединитель GHR-05V-S (JST, GH)" and gh.manufacturer == "JST" and len(gh.rows) == 2
    tube = next(g for g in groups if g.type == "Термоусадочная трубка")
    assert tube.name == "Т/усад. трубка RBF Raychman D:3,2/1,6 mm красная (200м/бухта)"
    assert tube.unit == "м" and tube.part_number == ""


# ---- the API stand --------------------------------------------------------------------------------------------------

@pytest.fixture()
def env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="p@example.test",
        roles=frozenset({"buyer"}), permissions={"procurement": "edit"},
    )
    app.dependency_overrides[moscow_now] = lambda: NOW
    yield TestClient(app), sessions
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.purchasing_uow_factory
    del app.state.procurement_uow_factory
    engine.dispose()


class Data:
    def __init__(self, sessions):
        self.sessions = sessions

    def supplier(self, name: str) -> UUID:
        supplier_id = uuid4()
        with self.sessions() as s:
            s.add(SupplierRow(id=supplier_id, name=name, name_key=supplier_key(name), categories=[], is_key=False,
                              domains=[], created_at=NOW, version=1))
            s.commit()
        return supplier_id

    def item(self, name: str, part_number: str | None = None, unit: str = "шт", aliases=()) -> UUID:
        item_id = uuid4()
        with self.sessions() as s:
            s.add(ItemRow(id=item_id, name=name, part_number=part_number, part_key=part_key(part_number),
                          category="other", unit=unit, version=1, created_at=NOW))
            s.flush()
            for text, supplier_id in aliases:
                s.add(ItemAliasRow(id=uuid4(), item_id=item_id, supplier_id=supplier_id, alias_key=alias_key(text),
                                   alias_text=text, created_at=NOW))
            s.commit()
        return item_id

    def upd(self, doc_id: str, supplier_id: UUID | None, day: str, lines: list[dict], *, status="Разобран", twin_of=None,
            kind="УПД", manual=None, verified=False, total=None) -> None:
        with self.sessions() as s:
            s.add(UpdDocumentRow(
                id=doc_id, origin="google", file_name=f"{doc_id}.pdf", status=status, kind=kind, number=f"№{doc_id}",
                doc_date=date.fromisoformat(day), seller_name="ООО Продавец", seller_inn="", seller_kpp="", buyer_inn="",
                supplier_id=supplier_id, twin_of=twin_of, total_gross=total, synced_at=NOW, created_at=NOW,
                manual_header=manual, verified_at=NOW if verified else None,
            ))
            s.flush()
            for seq, line in enumerate(lines, 1):
                s.add(UpdLineRow(
                    id=uuid4(), document_id=doc_id, source=line.get("source", "google"), seq=seq, name=line["name"],
                    article=line.get("article", ""), article_key=part_key(line.get("article")), name_key=alias_key(line["name"]),
                    unit=line.get("unit", "шт"), unit_code=line.get("code", ""), quantity=Decimal(line.get("qty", "1")),
                    gross=line["gross"], vat_rate="20", is_service=line.get("service", False),
                ))
            s.commit()

    def ref(self, item_id: UUID, supplier: str, price4: int, currency="RUB", vat=True) -> None:
        with self.sessions() as s:
            s.add(PriceRefRow(id=uuid4(), item_id=item_id, supplier_name=supplier, currency=currency, unit_price4=price4,
                              vat_included=vat, source="прайс.xlsx", imported_at=NOW))
            s.commit()


@pytest.fixture()
def data(env):
    return Data(env[1])


def test_item_prices_match_by_article_name_and_supplier_alias_only_in_force(env, data):
    client, sessions = env
    alpha, beta = data.supplier("ООО Альфа"), data.supplier("ООО Бета")
    item = data.item("Разъём SMA-F", "SMA-F 01", aliases=[("Гнездо ЭС", alpha)])
    other = data.item("Другое", "XH-2P")
    data.upd("u1", alpha, "2026-09-01", [{"name": "Разъем sma f01 в сборе", "article": "sma-f01", "qty": "10", "gross": 100_000}])
    data.upd("u2", beta, "2026-10-01", [{"name": "Разъём SMA-F", "qty": "5", "gross": 60_000}])  # by the name
    data.upd("u3", alpha, "2026-09-15", [{"name": "Гнездо ЭС", "qty": "2", "gross": 30_000}])  # alpha's alias
    data.upd("u4", beta, "2026-09-16", [{"name": "Гнездо ЭС", "qty": "2", "gross": 99_000}])  # alias of another supplier
    data.upd("u5", alpha, "2026-10-05", [{"name": "Разъём SMA-F", "gross": 1}], twin_of="u2")  # a twin copy
    data.upd("u6", alpha, "2026-10-06", [{"name": "Разъём SMA-F", "gross": 1}], status="Дубликат")
    data.upd("u7", alpha, "2026-10-07", [{"name": "Разъём SMA-F", "gross": 1, "service": True}])
    data.upd("u8", alpha, "2026-10-08", [{"name": "Разъём SMA-F", "gross": 5_000}], status="Требует проверки")
    data.upd("u9", alpha, "2026-10-09", [{"name": "Разъём SMA-F", "gross": 777}], kind="Корректировочный УПД")
    data.upd("u10", beta, "2026-08-01", [{"name": "Разъём SMA-F", "gross": 1_000, "source": "google"},
                                          {"name": "Разъём SMA-F", "gross": 2_000, "qty": "1", "source": "manual"}])
    data.ref(item, "Элитан", 34_300, vat=False)
    data.ref(item, "Fraxis", 177, currency="CNY", vat=False)
    data.ref(other, "Чужой", 1)

    card = client.get(f"/api/purchasing/prices/item/{item}").json()
    by_doc = {row["upd_id"]: row for row in card["upd"]}
    assert sorted(by_doc) == ["u1", "u10", "u2", "u3", "u8"]
    assert by_doc["u1"]["unit_price_with_vat"] == 10_000 and by_doc["u1"]["number"] == "№u1"
    assert by_doc["u8"]["needs_review"] is True and by_doc["u2"]["needs_review"] is False
    assert by_doc["u10"]["unit_price_with_vat"] == 2_000  # the manual lines replace the Google ones
    assert card["upd"][0]["upd_id"] == "u8"  # newest first
    assert card["summary"] == {"count": 4, "min": 2_000, "last": 12_000, "last_date": "2026-10-01", "last_supplier": "ООО Бета"}
    assert [(r["currency"], r["unit_price4"], r["vat_included"]) for r in card["refs"]] == [("RUB", 34_300, False), ("CNY", 177, False)]


def test_a_numeric_article_never_matches_a_part_number(env, data):
    client, _ = env
    item = data.item("Плата", "8534001900")
    data.upd("n1", None, "2026-09-01", [{"name": "Другая плата", "article": "8534001900", "gross": 100}])
    assert client.get(f"/api/purchasing/prices/item/{item}").json()["upd"] == []


def test_unit_conversion_and_incomparable_units(env, data):
    client, _ = env
    cable = data.item("Кабель РК-50", unit="м")
    data.upd("c1", None, "2026-09-01", [{"name": "Кабель РК-50", "unit": "км", "code": "008", "qty": "0.5", "gross": 50_000_000}])
    data.upd("c2", None, "2026-09-02", [{"name": "Кабель РК-50", "unit": "бухта", "qty": "1", "gross": 9_000_000}])
    card = client.get(f"/api/purchasing/prices/item/{cable}").json()
    by_doc = {row["upd_id"]: row for row in card["upd"]}
    assert by_doc["c1"]["unit_price_with_vat"] == 100_000_000 and by_doc["c1"]["item_unit_price_with_vat"] == 100_000
    assert by_doc["c2"]["item_unit_price_with_vat"] is None
    assert card["summary"]["count"] == 1 and card["summary"]["last"] == 100_000


def test_item_card_lists_kaiten_invoice_positions(env, data):
    client, sessions = env
    supplier = data.supplier("ООО Альфа")
    item = data.item("Разъём XH-2P", "XH-2P")
    with sessions() as s:
        s.add(InvoiceRow(
            id=uuid4(), source="kaiten", external_id="1", title="Счёт 5", status="paid", card_created=NOW, card_updated=NOW,
            number="5", doc_date=date(2026, 9, 3), supplier_id=supplier, synced_at=NOW,
            lines=[{"seq": 1, "name": "Колодка", "article": "xh 2p", "qty": 10, "unit": "шт", "price": 900, "sum": 9000},
                   {"seq": 2, "name": "Прочее", "article": "", "qty": 1, "unit": "шт", "price": 1, "sum": 1}]))
        s.commit()
    rows = client.get(f"/api/purchasing/prices/item/{item}").json()["invoices"]
    assert [(r["number"], r["price"], r["supplier_name"]) for r in rows] == [("5", 900, "ООО Альфа")]


def test_supplier_card_totals_share_invoices_and_top_positions(env, data):
    client, sessions = env
    alpha, beta = data.supplier("ООО Альфа"), data.supplier("ООО Бета")
    data.upd("a1", alpha, "2026-09-01", [{"name": "Кабель", "qty": "2", "gross": 20_000}, {"name": "Вилка", "gross": 5_000}], total=25_000)
    data.upd("a2", alpha, "2026-10-01", [{"name": "Кабель", "qty": "1", "gross": 12_000}], total=12_000)
    data.upd("b1", beta, "2026-10-02", [{"name": "Кабель", "gross": 63_000}], total=63_000)
    data.upd("a3", alpha, "2026-10-03", [{"name": "Кабель", "gross": 1}], total=100, twin_of="a1")
    item = data.item("Разъём XH-2P", "XH-2P")
    data.ref(item, "Альфа", 10_000)
    with sessions() as s:
        s.get(PriceRefRow, s.scalars(select(PriceRefRow.id)).first()).supplier_id = alpha
        for n, status, total in ((1, "to_pay", 7_000), (2, "paid", 3_000)):
            s.add(InvoiceRow(id=uuid4(), source="kaiten", external_id=str(n), title="С", status=status, card_created=NOW,
                             card_updated=NOW, number=str(n), doc_date=date(2026, 9, n), supplier_id=alpha, total=total,
                             synced_at=NOW, lines=[]))
        s.commit()
    card = client.get(f"/api/purchasing/prices/supplier/{alpha}").json()
    assert (card["upd"]["count"], card["upd"]["total_gross"], card["upd"]["last_date"], card["upd"]["share_pct"]) == (2, 37_000, "2026-10-01", 37.0)
    assert card["invoices"]["count"] == 2 and card["invoices"]["unpaid_count"] == 1 and card["invoices"]["unpaid_total"] == 7_000
    assert [(p["name"], p["sum"], p["purchases"]) for p in card["top_positions"]] == [("Кабель", 32_000, 2), ("Вилка", 5_000, 1)]
    assert card["offers_total"] == 1 and card["offers"][0]["item_name"] == "Разъём XH-2P"
    assert client.get(f"/api/purchasing/prices/supplier/{uuid4()}").status_code == 404


def test_catalog_groups_by_article_then_name_and_searches_in_pages(env, data):
    client, _ = env
    alpha, beta = data.supplier("ООО Альфа"), data.supplier("ООО Бета")
    data.upd("k1", alpha, "2026-09-01", [{"name": "Колодка XH", "article": "XH-2P", "qty": "10", "gross": 100_000},
                                         {"name": "Лента", "gross": 500}])
    data.upd("k2", beta, "2026-10-01", [{"name": "Колодка 2P", "article": "xh 2p", "qty": "10", "gross": 80_000}])
    data.upd("k3", beta, "2026-10-02", [{"name": "Лента", "gross": 700}], status="Требует проверки")  # not priced
    data.upd("k4", beta, "2026-10-03", [{"name": "Лента", "gross": 900}], verified=True, status="Требует проверки")
    body = client.get("/api/purchasing/prices/catalog").json()
    assert body["total"] == 2
    first = body["entries"][0]
    assert (first["key"], first["purchases"], first["suppliers"], first["last_price"], first["last_date"],
            first["last_supplier"], first["min_price"], first["max_price"]) == (
        "a:XH2P", 2, 2, 8_000, "2026-10-01", "ООО Бета", 8_000, 10_000)
    tape = next(e for e in body["entries"] if e["key"].startswith("n:"))
    assert tape["purchases"] == 2  # the verified document counts, the unchecked one does not
    assert client.get("/api/purchasing/prices/catalog", params={"q": "колодка"}).json()["total"] == 1
    points = client.get("/api/purchasing/prices/catalog/points", params={"key": "a:XH2P"}).json()
    assert [(p["upd_id"], p["unit_price_with_vat"]) for p in points] == [("k2", 8_000), ("k1", 10_000)]


def test_items_from_upd_plan_then_apply_creates_items_and_supplier_aliases(env, data):
    client, sessions = env
    alpha = data.supplier("ООО Альфа")
    data.item("Известный разъём", "KNOWN-1")
    data.upd("i1", alpha, "2026-09-01", [
        {"name": "Колодка XH 2 pin", "article": "XH-2P", "gross": 100}, {"name": "Известный разъём", "article": "KNOWN-1", "gross": 100},
        {"name": "Доставка", "unit": "усл.", "gross": 100}, {"name": "Аванс по счёту", "gross": 100},
        {"name": "Кабель МГТФ", "unit": "м", "gross": 100}])
    data.upd("i2", None, "2026-09-02", [{"name": "Колодка XH2P двухпиновая", "article": "xh 2p", "gross": 100}])
    plan = client.get("/api/purchasing/prices/upd-items/plan").json()
    assert plan["known"] == 1 and plan["skipped_services"] == 1 and plan["skipped_advances"] == 1
    assert len(plan["candidates"]) == 2
    with sessions() as s:
        assert len(s.scalars(select(ItemRow.id)).all()) == 1  # a plan writes nothing
    keys = [c["key"] for c in plan["candidates"]]
    done = client.post("/api/purchasing/prices/upd-items/apply", json={"keys": keys}).json()
    assert done["created"] == 2
    with sessions() as s:
        made = s.scalars(select(ItemRow).where(ItemRow.part_key == "XH2P")).one()
        assert made.unit == "шт"
        assert s.scalars(select(ItemRow).where(ItemRow.name == "Кабель МГТФ")).one().unit == "м"
    again = client.get("/api/purchasing/prices/upd-items/plan").json()
    assert again["candidates"] == [] and again["known"] == 3


def test_writes_are_gated_by_rights(env):
    client, _ = env
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Читатель", department="Закупки"), email="r@example.test",
        roles=frozenset({"buyer"}), permissions={"procurement": "view"})
    assert client.get("/api/purchasing/prices/catalog").status_code == 200
    assert client.post("/api/purchasing/prices/upd-items/apply", json={"keys": []}).status_code == 403


# ---- the request pool -----------------------------------------------------------------------------------------------

def test_pool_cheaper_before_counts_upd_and_price_refs(stand):  # noqa: F811
    data = Data(stand.sessions)
    item = data.item("Разъём XH-2P", "XH-2P")
    cheap = stand.active("Разъём XH-2P", design_article="XH-2P", calculated_price_with_vat=Decimal("12.00"))
    refd = stand.active("Колодка 3P", design_article="XH-3P", calculated_price_with_vat=Decimal("10.00"))
    km = stand.active("Кабель РК", calculated_price_with_vat=Decimal("100.00"), calculated_unit="м")
    data.upd("p1", None, "2026-09-01", [{"name": "Разъём XH-2P", "article": "xh2p", "qty": "100", "gross": 80_000}])  # 8,00 per шт
    data.upd("p2", None, "2026-09-02", [{"name": "Кабель РК", "unit": "км", "qty": "1", "gross": 5_000_000}])  # 50,00 per м
    data.item("Кабель РК", unit="м")
    other = data.item("Колодка 3P", "XH-3P")
    data.ref(other, "Элитан", 75_000, vat=False)  # 7,50 + the default VAT 22 % = 9,15
    data.ref(other, "Fraxis", 10, currency="CNY")  # foreign money is never compared

    def lines():
        body = stand.client.get("/api/purchasing/rfqs/pool").json()
        return {line["id"]: line for group in body["groups"] for line in group["lines"]}

    on = lines()
    assert (on[str(cheap)]["cheaper_before_pct"], on[str(cheap)]["cheaper_source"], on[str(cheap)]["cheaper_source_label"]) == ("33.3", "upd", "по УПД")
    assert (on[str(refd)]["cheaper_before_pct"], on[str(refd)]["cheaper_source"], on[str(refd)]["cheaper_source_label"]) == ("8.5", "ref", "прайс")
    assert on[str(km)]["cheaper_before_pct"] is None or on[str(km)]["cheaper_source"] == "upd"
    assert item
