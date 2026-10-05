"""«Номенклатура»: keys, create / merge, «Собрать из закупок», card statistics, option gate."""

import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing import items as repo
from app.modules.purchasing.infrastructure import (
    ItemAliasRow,
    ItemRow,
    LineItemRow,
    QuoteLineRow,
    QuoteRow,
    RfqLineRow,
    RfqRow,
    SqlAlchemyPurchasingUnitOfWork,
    SupplierNameRow,
    SupplierRow,
)
from app.shared import stage_options
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow, create_schema
from tests.factories import Actor
from tests.procurement.test_api import message

NOW = datetime(2026, 10, 5, 12, 0)


# ---- pure keys -----------------------------------------------------------------------------------------------------

def test_part_key_reuses_the_article_normalizer():
    assert repo.part_key("shr 02V-s-b") == "SHR02VSB"
    assert repo.part_key("  ") is None and repo.part_key(None) is None


def test_alias_key_folds_case_spaces_and_look_alikes():
    assert repo.alias_key("  Реле   Т-5 ") == repo.alias_key("Peле T-5")
    assert repo.alias_key("Разъём SMA") == repo.alias_key("РАЗЪЕМ sma")
    assert repo.alias_key("Кабель") != repo.alias_key("Кабели")


@pytest.mark.parametrize("name, expected", [
    ("Кабель МГТФ 0.35", "cable"), ("Провод ПВ-3", "cable"), ("Разъём SMA-F", "connector"),
    ("Клемма WAGO", "connector"), ("Вилка DB9", "connector"), ("Микросхема LM358", "ekb"),
    ("Резистор 10к", "ekb"), ("Транзистор BC547", "ekb"), ("Болт М4", "other"),
])
def test_category_guess(name, expected):
    assert repo.guess_category(name) == expected


def test_unit_normalization():
    assert [repo.normalize_unit(u) for u in ("Шт.", "метров", "КГ", "литр", "упаковка", "комплект", "", "рулон")] == [
        "шт", "м", "кг", "л", "уп", "компл", "шт", "рулон",
    ]


# ---- API fixture ---------------------------------------------------------------------------------------------------

@pytest.fixture()
def env(monkeypatch, tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"references.items": True}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))
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
    yield TestClient(app), sessions, path
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.purchasing_uow_factory
    del app.state.procurement_uow_factory
    engine.dispose()


_counter = iter(range(100, 10_000))


def add_line(sessions, name, article=None, unit="шт", qty="2", status="Новый", invoice_name=None, supplier=None,
             price=None, paid=None, item_type="Комплектующие"):
    suffix = next(_counter)
    msg = message(suffix)
    msg.payload.update(calculated_name=name, design_article=article, calculated_unit=unit, calculated_quantity=qty,
                       item_type=item_type)
    line = consume_procurement_requested(msg, SqlAlchemyProcurementUnitOfWork(sessions))
    with sessions() as s:
        row = s.get(ProcurementLineRow, line.id)
        row.status = status
        row.invoice_item_name = invoice_name
        row.supplier = supplier
        if price is not None:
            row.actual_price_with_vat = Decimal(price)
            row.actual_unit = unit
            row.actual_quantity = Decimal(qty)
        if paid:
            row.paid_at = paid
        s.commit()
    return line.id


def create(client, name="Разъём SMA", **extra):
    return client.post("/api/purchasing/items", json={"name": name, **extra})


# ---- create / update / merge ---------------------------------------------------------------------------------------

def test_create_guesses_category_normalizes_and_conflicts_on_part_key(env):
    client, sessions, _ = env
    made = create(client, "Разъём SMA", part_number="SMA-F 01", unit="штук")
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["category"] == "connector" and body["unit"] == "шт"
    again = create(client, "Другое имя", part_number="sma f01")
    assert again.status_code == 409
    assert again.json()["detail"] == "Такой артикул уже есть: Разъём SMA"
    assert create(client, "   ").status_code == 422
    assert create(client, "Без артикула 1").status_code == 201 and create(client, "Без артикула 2").status_code == 201
    with sessions() as s:
        events = s.scalars(select(AuditEventRow.event_type)).all()
    assert events.count("purchasing.item_created") == 3


def test_patch_checks_version_and_rekeys(env):
    client, _, _ = env
    item = create(client, "Разъём", part_number="A-1").json()
    other = create(client, "Другой", part_number="B-2").json()
    ok = client.patch(f"/api/purchasing/items/{item['id']}", json={"version": 1, "part_number": "A-9", "unit": "м"})
    assert ok.status_code == 200 and ok.json()["version"] == 2 and ok.json()["unit"] == "м"
    assert client.patch(f"/api/purchasing/items/{item['id']}", json={"version": 1, "name": "x"}).status_code == 409
    clash = client.patch(f"/api/purchasing/items/{item['id']}", json={"version": 2, "part_number": "b 2"})
    assert clash.status_code == 409 and "Другой" in clash.json()["detail"]
    assert client.patch(f"/api/purchasing/items/{other['id']}", json={"version": 1, "name": None}).status_code == 422


def test_aliases_are_unique_and_removable(env):
    client, sessions, _ = env
    first = create(client, "Первый").json()
    second = create(client, "Второй").json()
    with sessions() as s:
        supplier = SupplierRow(id=uuid4(), name="ООО Ромашка", name_key="ооо ромашка", categories=[], is_key=False, domains=[], version=1, created_at=NOW)
        s.add(supplier)
        s.commit()
        supplier_id = str(supplier.id)
    added = client.post(f"/api/purchasing/items/{first['id']}/aliases", json={"text": "Штука А", "supplier_id": supplier_id})
    assert added.status_code == 201 and added.json()["aliases"][0]["supplier_name"] == "ООО Ромашка"
    # same text twice on the same item is a no-op; on another item — conflict
    assert client.post(f"/api/purchasing/items/{first['id']}/aliases", json={"text": "штука  а", "supplier_id": supplier_id}).status_code == 201
    assert len(client.get(f"/api/purchasing/items/{first['id']}").json()["aliases"]) == 1
    assert client.post(f"/api/purchasing/items/{second['id']}/aliases", json={"text": "Штука А", "supplier_id": supplier_id}).status_code == 409
    assert client.post(f"/api/purchasing/items/{second['id']}/aliases", json={"text": "Штука А"}).status_code == 201
    alias_id = added.json()["aliases"][0]["id"]
    assert client.delete(f"/api/purchasing/items/{first['id']}/aliases/{alias_id}").json()["aliases"] == []
    assert client.post(f"/api/purchasing/items/{first['id']}/aliases", json={"text": "x", "supplier_id": str(uuid4())}).status_code == 404


def test_search_words_any_order_over_aliases_and_look_alikes_with_category_counts(env):
    client, _, _ = env
    a = create(client, "Разъём SMA штекер", part_number="SMA-123", manufacturer="Amphenol").json()
    create(client, "Кабель МГТФ")
    create(client, "Болт")
    client.post(f"/api/purchasing/items/{a['id']}/aliases", json={"text": "вилка коаксиальная"})
    found = client.get("/api/purchasing/items", params={"q": "штекер sma"}).json()
    assert [i["name"] for i in found["items"]] == ["Разъём SMA штекер"]
    assert client.get("/api/purchasing/items", params={"q": "коаксиальная"}).json()["total"] == 1
    assert client.get("/api/purchasing/items", params={"q": "sma123"}).json()["total"] == 1
    assert client.get("/api/purchasing/items", params={"q": "amphenol"}).json()["total"] == 1
    assert client.get("/api/purchasing/items", params={"q": "Kабель"}).json()["total"] == 1  # Latin K
    everything = client.get("/api/purchasing/items").json()
    assert everything["counts"] == {"cable": 1, "connector": 1, "ekb": 0, "other": 1}
    only = client.get("/api/purchasing/items", params={"category": "cable"}).json()
    assert only["total"] == 1 and only["counts"]["connector"] == 1


def test_merge_moves_aliases_links_and_archives_the_dropped(env):
    client, sessions, _ = env
    keep = create(client, "Разъём SMA").json()
    drop = create(client, "SMA разъем штекер", part_number="SMA-9").json()
    client.post(f"/api/purchasing/items/{drop['id']}/aliases", json={"text": "Штекер SMA"})
    line = add_line(sessions, "SMA штекер")
    assert client.post("/api/purchasing/items/link", json={"procurement_line_id": str(line), "item_id": drop["id"]}).status_code == 200
    assert client.post("/api/purchasing/items/merge", json={"keep_id": keep["id"], "drop_id": keep["id"]}).status_code == 422
    merged = client.post("/api/purchasing/items/merge", json={"keep_id": keep["id"], "drop_id": drop["id"]})
    assert merged.status_code == 200, merged.text
    assert merged.json()["part_number"] == "SMA-9"  # keep had none: the number moves over
    card = client.get(f"/api/purchasing/items/{keep['id']}").json()
    assert card["linked_lines"] == 1
    texts = {a["text"] for a in card["aliases"]}
    assert {"Штекер SMA", "SMA разъем штекер"} <= texts
    assert client.get(f"/api/purchasing/items/{drop['id']}").json()["archived"] is True
    assert client.get("/api/purchasing/items", params={"q": "штекер"}).json()["total"] == 1
    again = client.post("/api/purchasing/items/merge", json={"keep_id": keep["id"], "drop_id": drop["id"]})
    assert again.status_code == 422


def test_link_and_for_lines_batch(env):
    client, sessions, _ = env
    item = create(client, "Разъём", part_number="X-1").json()
    line, other = add_line(sessions, "Разъём"), add_line(sessions, "Болт")
    assert client.post("/api/purchasing/items/link", json={"procurement_line_id": str(uuid4()), "item_id": item["id"]}).status_code == 404
    assert client.post("/api/purchasing/items/link", json={"procurement_line_id": str(line), "item_id": str(uuid4())}).status_code == 404
    assert client.post("/api/purchasing/items/link", json={"procurement_line_id": str(line), "item_id": item["id"]}).status_code == 200
    got = client.get("/api/purchasing/items/for-lines", params={"ids": f"{line},{other}"}).json()
    assert list(got) == [str(line)] and got[str(line)]["item_id"] == item["id"]
    too_many = ",".join(str(uuid4()) for _ in range(201))
    assert client.get("/api/purchasing/items/for-lines", params={"ids": too_many}).status_code == 422
    assert client.get("/api/purchasing/items/for-lines", params={"ids": "не-uuid"}).status_code == 422


# ---- «Собрать из закупок» ------------------------------------------------------------------------------------------

def test_import_dry_run_groups_by_article_then_by_name(env):
    client, sessions, _ = env
    add_line(sessions, "Разъём SMA-F", article="SMA-F 01", unit="шт")
    add_line(sessions, "Разъём SMA-F", article="sma f01", unit="Штук")
    add_line(sessions, "Разъем SMA-F гнездо", article="SMAF01", unit="шт")
    add_line(sessions, "Кабель МГТФ 0.35", unit="м")
    add_line(sessions, "кабель  мгтф 0.35", unit="метров")
    add_line(sessions, "Болт М4", unit="шт")
    dry = client.post("/api/purchasing/items/import-from-procurement", json={"dry_run": True})
    assert dry.status_code == 200, dry.text
    body = dry.json()
    assert body["dry_run"] is True and body["created"] == 3 and body["linked_groups"] == 0
    by_kind = {(g["kind"], g["name"]): g for g in body["groups"]}
    article = by_kind[("article", "Разъём SMA-F")]
    assert article["lines"] == 3 and article["category"] == "connector" and article["part_number"] in ("SMA-F 01", "sma f01", "SMAF01")
    cable = next(g for g in body["groups"] if g["kind"] == "name" and g["lines"] == 2)
    assert cable["category"] == "cable" and cable["unit"] == "м"
    with sessions() as s:  # a dry run leaves no trace
        assert s.scalar(select(func.count()).select_from(ItemRow)) == 0
        assert s.scalar(select(func.count()).select_from(LineItemRow)) == 0
        assert s.scalar(select(func.count()).select_from(AuditEventRow).where(AuditEventRow.event_type.like("purchasing.item%"))) == 0


def test_import_apply_is_idempotent_and_links_collisions(env):
    client, sessions, _ = env
    existing = create(client, "Разъём SMA-F", part_number="SMA-F 01").json()
    named = create(client, "Кабель МГТФ 0.35").json()
    supplier_id = uuid4()
    with sessions() as s:
        s.add(SupplierRow(id=supplier_id, name="ООО Ромашка", name_key="ооо ромашка", categories=[], is_key=False, domains=[], version=1, created_at=NOW))
        s.flush()
        s.add(SupplierNameRow(name_key="ооо ромашка", supplier_id=supplier_id, created_at=NOW))
        s.commit()
    l1 = add_line(sessions, "Разъём SMA штекер", article="sma-f 01", invoice_name="SMA-F plug", supplier="ООО  Ромашка")
    l2 = add_line(sessions, "Кабель МГТФ 0.35", unit="м", invoice_name="МГТФ 0,35 бухта", supplier="ИП Неизвестный")
    l3 = add_line(sessions, "Новая деталь", article="NEW-777")
    l4 = add_line(sessions, "Новая деталь")  # same name, no article: meets the item created for NEW-777

    preview = client.post("/api/purchasing/items/import-from-procurement", json={"dry_run": True}).json()
    reasons = {(g["name"], g["action"], g["reason"]) for g in preview["groups"]}
    assert ("Разъём SMA штекер", "link", "part_key") in reasons
    assert ("Кабель МГТФ 0.35", "link", "alias") in reasons
    assert ("Новая деталь", "create", "new") in reasons and ("Новая деталь", "link", "alias") in reasons

    applied = client.post("/api/purchasing/items/import-from-procurement", json={"dry_run": False}).json()
    assert applied["created"] == 1 and applied["linked_groups"] == 3 and applied["lines"] == 4
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(ItemRow)) == 3
        links = {line: item for line, item in s.execute(select(LineItemRow.procurement_line_id, LineItemRow.item_id))}
        assert links[l1] == UUID(existing["id"]) and links[l2] == UUID(named["id"])
        assert links[l3] == links[l4]
        aliases = s.scalars(select(ItemAliasRow).where(ItemAliasRow.item_id == UUID(existing["id"]))).all()
        assert {(a.alias_text, a.supplier_id) for a in aliases} == {("Разъём SMA штекер", None), ("SMA-F plug", supplier_id)}
        cable_aliases = s.scalars(select(ItemAliasRow).where(ItemAliasRow.item_id == UUID(named["id"]))).all()
        assert [(a.alias_text, a.supplier_id) for a in cable_aliases] == [("МГТФ 0,35 бухта", None)]  # unknown supplier: generic
    again = client.post("/api/purchasing/items/import-from-procurement", json={"dry_run": False}).json()
    assert again["groups"] == [] and again["created"] == 0 and again["lines"] == 0
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(ItemRow)) == 3
        assert s.scalar(select(func.count()).select_from(LineItemRow)) == 4


# ---- card ----------------------------------------------------------------------------------------------------------

def test_card_demand_price_history_and_quotes(env):
    client, sessions, _ = env
    item = create(client, "Разъём", part_number="C-1").json()
    paid_old = add_line(sessions, "Разъём", article="C-1", status="Готово", price="10", paid=datetime(2026, 9, 1, 10), supplier="ООО А", qty="5")
    paid_new = add_line(sessions, "Разъём", article="C-1", status="Готово", price="14", paid=datetime(2026, 9, 20, 10), supplier="ООО Б", qty="3")
    open_a = add_line(sessions, "Разъём", status="В работе", qty="4")
    open_b = add_line(sessions, "Разъём", status="Новый", qty="1.5")
    open_m = add_line(sessions, "Разъём провод", unit="м", status="Новый", qty="10")
    cancelled = add_line(sessions, "Разъём", status="Отменено", qty="100")
    for line in (paid_old, paid_new, open_a, open_b, open_m, cancelled):
        client.post("/api/purchasing/items/link", json={"procurement_line_id": str(line), "item_id": item["id"]})
    with sessions() as s:
        supplier = SupplierRow(id=uuid4(), name="ООО Квота", name_key="ооо квота", categories=[], is_key=False, domains=[], version=1, created_at=NOW)
        rfq = RfqRow(id=uuid4(), number="RFQ-1", version=1, created_at=NOW)
        s.add_all([supplier, rfq])
        s.flush()
        rfq_line = RfqLineRow(id=uuid4(), rfq_id=rfq.id, procurement_line_id=open_a, item_id=UUID(item["id"]), name="Разъём", quantity=Decimal(4), unit="шт", created_at=NOW)
        quote = QuoteRow(id=uuid4(), rfq_id=rfq.id, supplier_id=supplier.id, supplier_is_key=False, received_on=date(2026, 10, 1), created_at=NOW)
        s.add_all([rfq_line, quote])
        s.flush()
        s.add(QuoteLineRow(id=uuid4(), quote_id=quote.id, rfq_line_id=rfq_line.id, unit_price=Decimal("100"), vat_included=False, vat_rate=Decimal("20"), tiers=[]))
        s.commit()

    card = client.get(f"/api/purchasing/items/{item['id']}").json()
    assert card["linked_lines"] == 6
    assert card["demand"] == [{"unit": "м", "quantity": "10", "lines": 1}, {"unit": "шт", "quantity": "5.5", "lines": 2}]
    assert [h["date"] for h in card["history"]] == ["2026-09-20", "2026-09-01"]
    assert card["history"][0]["supplier"] == "ООО Б" and card["history"][0]["unit_price_with_vat"] == "14.00"
    assert card["price"] == {"min": "10.00", "avg": "12.00", "last": "14.00"}
    assert card["quotes"][0]["supplier"] == "ООО Квота" and card["quotes"][0]["unit_price_with_vat"] == "120.00"

    listed = client.get("/api/purchasing/items").json()["items"][0]
    assert listed["last_price"] == "14.00" and listed["purchases"] == 2 and listed["linked_lines"] == 6
    assert client.get("/api/purchasing/items", params={"has_price": "true"}).json()["total"] == 1
    create(client, "Без цены")
    assert client.get("/api/purchasing/items", params={"has_price": "true"}).json()["total"] == 1
    assert client.get("/api/purchasing/items").json()["total"] == 2
    assert client.get(f"/api/purchasing/items/{uuid4()}").status_code == 404


# ---- option gate ---------------------------------------------------------------------------------------------------

def test_option_off_means_404_everywhere_and_rfq_option_opens_only_the_search(env):
    client, _, path = env
    item = create(client, "Разъём").json()
    path.write_text(json.dumps({"references.items": False}), encoding="utf-8")
    for call in (
        lambda: client.get("/api/purchasing/items"),
        lambda: client.get(f"/api/purchasing/items/{item['id']}"),
        lambda: create(client, "Другое"),
        lambda: client.post("/api/purchasing/items/import-from-procurement", json={"dry_run": True}),
        lambda: client.get("/api/purchasing/items/for-lines", params={"ids": ""}),
        lambda: client.post("/api/purchasing/items/merge", json={"keep_id": item["id"], "drop_id": str(uuid4())}),
    ):
        assert call().status_code == 404
    path.write_text(json.dumps({"procurement.rfq": True}), encoding="utf-8")
    assert client.get("/api/purchasing/items").status_code == 200
    assert client.get(f"/api/purchasing/items/{item['id']}").status_code == 404


def test_a_reader_cannot_write(env):
    client, _, _ = env
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Иванов", department="РНД"), email="i@example.test",
        roles=frozenset({"operator"}), permissions={"procurement": "view"},
    )
    assert client.get("/api/purchasing/items").status_code == 200
    assert create(client, "Разъём").status_code == 403
