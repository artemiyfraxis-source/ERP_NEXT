"""Номенклатура ↔ склад и приёмка: chain, explicit link, matching, «Собрать со склада», card, list, gates."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, Numeric, Uuid, func, select

from app.main import app
from app.modules.purchasing.infrastructure import ItemRow, LotItemRow
from app.modules.receiving.infrastructure import ReceivingLineRow, SqlAlchemyReceivingUnitOfWork
from app.modules.warehouse.domain import StockLot
from app.modules.warehouse.infrastructure import SqlAlchemyWarehouseUnitOfWork
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor
from tests.purchasing.test_items import NOW, add_line, create, env  # noqa: F401  (env is a fixture)

_grn = iter(range(1, 10_000))
BASE = "/api/purchasing/items"


@pytest.fixture()
def stock(env):  # noqa: F811
    client, sessions, path = env
    app.state.warehouse_uow_factory = lambda: SqlAlchemyWarehouseUnitOfWork(sessions)
    app.state.receiving_uow_factory = lambda: SqlAlchemyReceivingUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="p@example.test", roles=frozenset({"buyer"}),
        permissions={"procurement": "edit", "warehouse": "edit", "receiving": "view"},
    )
    yield client, sessions, path
    del app.state.warehouse_uow_factory
    del app.state.receiving_uow_factory


def add_lot(sessions, name="Разъём", article="", unit="шт", qty="10", reserved="0", receiving=None, warehouse="ЦАР"):
    lot = StockLot.create(
        warehouse=warehouse, grn=f"FRX.26.{next(_grn):07d}", package="", name=name, item_type="Комплектующие",
        purpose="FRX.001", unit=unit, quantity=Decimal(qty), storage_location="1F4", article=article,
        source_receiving_ids=(str(receiving or uuid4()),),
    )
    lot = lot.__class__(**{**lot.__dict__, "reserved": Decimal(reserved)})
    with SqlAlchemyWarehouseUnitOfWork(sessions) as uow:
        uow.warehouse.add(lot)
        uow.commit()
    return lot.id


def add_receiving(sessions, procurement_line_id, **fields):
    """A receiving line with every NOT NULL column filled by its type."""
    values = {}
    for column in ReceivingLineRow.__table__.columns:
        if column.nullable or column.default is not None or column.server_default is not None:
            continue
        kind = column.type
        values[column.name] = (
            uuid4() if isinstance(kind, Uuid) else 1 if isinstance(kind, Integer) else False if isinstance(kind, Boolean)
            else Decimal(0) if isinstance(kind, Numeric) else [] if isinstance(kind, JSON)
            else NOW if isinstance(kind, DateTime) else date(2026, 10, 1) if isinstance(kind, Date) else "x"
        )
    rid = uuid4()
    values.update(
        id=rid, procurement_line_id=procurement_line_id, source_idempotency_key=f"k{rid}", legacy_id="SM-1",
        status="Принято", warehouse="ЦАР", name="Разъём", unit="шт", package="", ordered_quantity=Decimal("5"),
        fact_quantity=Decimal("4"), grn="FRX.26.9", document_number="УПД-7", document_date=date(2026, 10, 2),
        verdict="Годно", supplier="ООО Ромашка", nonconformities=[],
    )
    values.update(fields)
    with sessions() as s:
        s.add(ReceivingLineRow(**values))
        s.commit()
    return rid


def make_items(client, *names):
    return [create(client, n).json()["id"] for n in names]


def for_lots(client, *lots):
    return client.get(f"{BASE}/for-lots", params={"ids": ",".join(map(str, lots))}).json()


def test_chain_lot_receiving_procurement_item(stock):
    client, sessions, _ = stock
    [item] = make_items(client, "Разъём SMA")
    line = add_line(sessions, "SMA штекер")
    client.post(f"{BASE}/link", json={"procurement_line_id": str(line), "item_id": item})
    rid = add_receiving(sessions, line)
    lot = add_lot(sessions, name="Что-то другое", receiving=rid)
    assert for_lots(client, lot) == {str(lot): {"item_id": item, "name": "Разъём SMA", "category": "connector", "via": "chain"}}
    rec = client.get(f"{BASE}/for-receiving", params={"ids": str(rid)}).json()
    assert rec[str(rid)]["item_id"] == item and rec[str(rid)]["via"] == "chain"


def test_explicit_link_overrides_chain_and_can_be_removed(stock):
    client, sessions, _ = stock
    a, b = make_items(client, "Первый", "Второй")
    line = add_line(sessions, "X")
    client.post(f"{BASE}/link", json={"procurement_line_id": str(line), "item_id": a})
    lot = add_lot(sessions, receiving=add_receiving(sessions, line))
    assert client.post(f"{BASE}/link-lot", json={"lot_id": str(lot), "item_id": b}).status_code == 200
    assert for_lots(client, lot)[str(lot)]["via"] == "manual"
    assert for_lots(client, lot)[str(lot)]["item_id"] == b
    assert client.delete(f"{BASE}/link-lot/{lot}").status_code == 200
    assert for_lots(client, lot)[str(lot)]["item_id"] == a
    assert client.delete(f"{BASE}/link-lot/{lot}").status_code == 404
    assert client.post(f"{BASE}/link-lot", json={"lot_id": str(uuid4()), "item_id": a}).status_code == 404
    with sessions() as s:
        events = s.scalars(select(AuditEventRow.event_type)).all()
    assert "purchasing.item_lot_linked" in events and "purchasing.item_lot_unlinked" in events


def test_import_matches_by_article_alias_and_name_and_reports_ambiguous(stock):
    client, sessions, _ = stock
    sma, cable, one, two = make_items(client, "Разъём SMA", "Кабель МГТФ", "Реле А", "Реле общее")
    client.patch(f"{BASE}/{sma}", json={"version": 1, "part_number": "SMA-F 01"})
    client.post(f"{BASE}/{cable}/aliases", json={"text": "Провод МГТФ 0.35"})
    client.post(f"{BASE}/{one}/aliases", json={"text": "Реле общее"})  # also the very name of `two`
    by_article = add_lot(sessions, name="Совсем иначе", article="sma f01")
    by_alias = add_lot(sessions, name="провод мгтф 0.35")
    by_name = add_lot(sessions, name="Разъём  sma")
    ambiguous = add_lot(sessions, name="Реле общее")
    result = client.post(f"{BASE}/import-from-stock", json={"dry_run": False}).json()
    reasons = {row["lot_id"]: (row["item_id"], row["reason"]) for row in result["linked"]}
    assert reasons[str(by_article)] == (sma, "part_key")
    assert reasons[str(by_alias)] == (cable, "name") and reasons[str(by_name)] == (sma, "name")
    assert [row["lot_id"] for row in result["ambiguous"]] == [str(ambiguous)]
    assert len(result["ambiguous"][0]["candidates"]) == 2 and result["create"] == []
    found = for_lots(client, by_article, by_alias, ambiguous)
    assert str(ambiguous) not in found and found[str(by_article)]["via"] == "match"


def test_import_dry_run_equals_apply_creates_groups_and_is_idempotent(stock):
    client, sessions, _ = stock
    add_lot(sessions, name="Разъём DB-9", article="DB9-M", unit="штук")
    add_lot(sessions, name="Разъём DB-9 вилка", article="db9 m", unit="шт")
    add_lot(sessions, name="Кабель МГТФ", unit="м", qty="50")
    add_lot(sessions, name="кабель  мгтф", unit="м")
    add_lot(sessions, name="Болт М4")

    def counts():
        with sessions() as s:
            return s.scalar(select(func.count()).select_from(ItemRow)), s.scalar(select(func.count()).select_from(LotItemRow))

    dry = client.post(f"{BASE}/import-from-stock", json={"dry_run": True}).json()
    assert counts() == (0, 0)
    assert dry["dry_run"] is True and len(dry["create"]) == 3
    article = next(g for g in dry["create"] if g["kind"] == "article")
    assert article["part_number"] and len(article["lots"]) == 2 and article["category"] == "connector"
    assert {g["category"] for g in dry["create"]} == {"connector", "cable", "other"}
    applied = client.post(f"{BASE}/import-from-stock", json={"dry_run": False}).json()

    def plan(r):
        return [(g["kind"], g["key"], g["name"], g["unit"], len(g["lots"])) for g in r["create"]]

    assert plan(applied) == plan(dry)
    assert counts() == (3, 5)
    again = client.post(f"{BASE}/import-from-stock", json={"dry_run": False}).json()
    assert again["create"] == [] and again["linked"] == [] and again["lots"] == 0 and counts() == (3, 5)


def test_card_stock_receipts_and_list_available(stock):
    client, sessions, _ = stock
    item, other = make_items(client, "Кабель МГТФ", "Болт")
    line = add_line(sessions, "Кабель")
    client.post(f"{BASE}/link", json={"procurement_line_id": str(line), "item_id": item})
    rid = add_receiving(sessions, line)
    chain = add_lot(sessions, unit="м", qty="30", reserved="5", receiving=rid)
    manual = add_lot(sessions, unit="м", qty="20", warehouse="ЖУК")
    client.post(f"{BASE}/link-lot", json={"lot_id": str(manual), "item_id": item})
    for unit in ("шт", "кг"):
        client.post(f"{BASE}/link-lot", json={"lot_id": str(add_lot(sessions, unit=unit, qty="1")), "item_id": other})
    card = client.get(f"{BASE}/{item}").json()
    assert {(l["lot_id"], l["via"]) for l in card["stock"]["lots"]} == {(str(chain), "chain"), (str(manual), "manual")}
    assert card["stock"]["totals"] == [{"unit": "м", "balance": "50", "reserved": "5", "available": "45"}]
    [receipt] = card["receipts"]
    assert receipt["document_number"] == "УПД-7" and receipt["date"] == "2026-10-02"
    assert (receipt["ordered"], receipt["fact"], receipt["status"], receipt["verdict"], receipt["grn"]) == (
        "5", "4", "Принято", "Годно", "FRX.26.9",
    )
    rows = {r["id"]: r for r in client.get(BASE).json()["items"]}
    assert rows[item]["available"] == "45" and rows[item]["available_unit"] == "м"
    assert rows[other]["available"] is None and rows[other]["stock_lots"] == 2


def test_option_off_is_404_and_gates(stock):
    client, sessions, path = stock
    lot = add_lot(sessions)
    [item] = make_items(client, "Позиция")

    def as_user(**permissions):
        app.dependency_overrides[authenticated_principal] = lambda: Principal(
            actor=Actor(name="Н", department="Д"), email="n@example.test", roles=frozenset({"x"}), permissions=permissions,
        )

    as_user(warehouse="view")
    assert client.get(f"{BASE}/for-lots", params={"ids": str(lot)}).status_code == 200
    assert client.post(f"{BASE}/link-lot", json={"lot_id": str(lot), "item_id": item}).status_code == 403
    assert client.get(f"{BASE}/for-receiving", params={"ids": str(lot)}).status_code == 403
    as_user(receiving="view")
    assert client.get(f"{BASE}/for-receiving", params={"ids": str(lot)}).status_code == 200
    assert client.get(f"{BASE}/for-lots", params={"ids": str(lot)}).status_code == 403
    as_user(warehouse="edit", receiving="view")
    assert client.post(f"{BASE}/import-from-stock", json={"dry_run": True}).status_code == 200
    path.write_text("{}", encoding="utf-8")
    for call in (
        lambda: client.get(f"{BASE}/for-lots", params={"ids": str(lot)}),
        lambda: client.get(f"{BASE}/for-receiving", params={"ids": str(lot)}),
        lambda: client.post(f"{BASE}/link-lot", json={"lot_id": str(lot), "item_id": item}),
        lambda: client.delete(f"{BASE}/link-lot/{lot}"),
        lambda: client.post(f"{BASE}/import-from-stock", json={"dry_run": True}),
    ):
        assert call().status_code == 404
