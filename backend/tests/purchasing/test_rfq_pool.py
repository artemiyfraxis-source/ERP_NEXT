"""«Пул запроса КП»: the buyer's lines by nomenclature category with item match and price history, the supplier
suggestion by purchase statistics, and a request created with its suppliers in one go."""

import json
import time
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, insert, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.modules.purchasing.infrastructure import (
    ItemAliasRow,
    ItemRow,
    LineItemRow,
    RfqRow,
    RfqSupplierRow,
    SqlAlchemyPurchasingUnitOfWork,
    SupplierNameRow,
    SupplierRow,
)
from app.modules.purchasing.items import alias_key, part_key
from app.modules.purchasing.suppliers import supplier_key
from app.modules.requests.api import moscow_now
from app.shared import stage_options
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import create_schema
from tests.factories import Actor
from tests.procurement.test_api import message

BASE = "/api/purchasing/rfqs"
NOW = datetime(2026, 9, 15, 11, 30)
COLUMNS = [attr.key for attr in inspect(ProcurementLineRow).column_attrs]


def _as(name: str, level: str = "edit") -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name=name, department="Закупки"), email=f"{name}@example.test", roles=frozenset({"buyer"}),
        permissions={"procurement": level},
    )


def _options(monkeypatch, tmp_path, on: bool) -> None:
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"procurement.rfq": on}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


class Stand:
    def __init__(self, client, sessions, template: dict):
        self.client, self.sessions, self.template = client, sessions, template
        self.counter = 100

    def rows(self, specs: list[dict]) -> list[UUID]:
        """Procurement lines cloned from a taken line (the buyer's, «В работе»), with the given fields."""
        out, batch = [], []
        for spec in specs:
            self.counter += 1
            row = {**self.template, "id": uuid4(), "source_event_id": uuid4(),
                   "source_idempotency_key": f"seed:{self.counter}", "legacy_id": f"SM-{self.counter:05d}", **spec}
            out.append(row["id"])
            batch.append(row)
        with self.sessions() as session:
            session.execute(insert(ProcurementLineRow), batch)
            session.commit()
        return out

    def active(self, name: str, **fields) -> UUID:
        return self.rows([{"calculated_name": name, **fields}])[0]

    def paid(self, name: str, supplier: str, price: str, day: str, **fields) -> UUID:
        return self.rows([{
            "calculated_name": name, "status": "Готово", "supplier": supplier, "actual_price_with_vat": Decimal(price),
            "actual_unit": fields.pop("unit", "шт"), "paid_at": datetime.fromisoformat(f"{day}T10:00:00"), **fields,
        }])[0]

    def item(self, name: str, category: str, part_number: str | None = None, aliases=(), links=()) -> UUID:
        item_id = uuid4()
        with self.sessions() as session:
            session.add(ItemRow(id=item_id, name=name, part_number=part_number, part_key=part_key(part_number),
                                category=category, unit="шт", version=1, created_at=NOW))
            session.flush()
            for text in aliases:
                session.add(ItemAliasRow(id=uuid4(), item_id=item_id, alias_key=alias_key(text), alias_text=text,
                                         created_at=NOW))
            for line_id in links:
                session.add(LineItemRow(procurement_line_id=line_id, item_id=item_id, linked_at=NOW))
            session.commit()
        return item_id

    def supplier(self, name: str, *, key: bool = False, categories=(), archived: bool = False, learned=()) -> UUID:
        supplier_id = uuid4()
        with self.sessions() as session:
            session.add(SupplierRow(id=supplier_id, name=name, name_key=supplier_key(name), categories=list(categories),
                                    is_key=key, domains=[], created_at=NOW, version=1,
                                    archived_at=NOW if archived else None))
            session.flush()
            for text in learned:
                session.add(SupplierNameRow(name_key=supplier_key(text), supplier_id=supplier_id, created_at=NOW))
            session.commit()
        return supplier_id


@pytest.fixture()
def stand(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    create_schema(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.procurement_uow_factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    app.state.purchasing_uow_factory = lambda: SqlAlchemyPurchasingUnitOfWork(sessions)
    _as("Петров")
    app.dependency_overrides[moscow_now] = lambda: NOW
    _options(monkeypatch, tmp_path, True)
    client = TestClient(app)
    line = consume_procurement_requested(message(1), app.state.procurement_uow_factory())
    assert client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).status_code == 200
    with sessions() as session:
        row = session.get(ProcurementLineRow, line.id)
        template = {key: getattr(row, key) for key in COLUMNS}
        session.delete(row)  # only the seeded lines below are in the pool
        session.commit()
    template.update(calculated_name="", design_article=None, supplier=None, calculated_price_with_vat=None)
    yield Stand(client, sessions, template)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.procurement_uow_factory
    del app.state.purchasing_uow_factory
    engine.dispose()


def _pool(stand) -> dict:
    response = stand.client.get(f"{BASE}/pool")
    assert response.status_code == 200, response.text
    return response.json()


def _lines(pool: dict) -> dict[str, dict]:
    return {line["id"]: line for group in pool["groups"] for line in group["lines"]}


def test_pool_groups_by_the_item_category_else_the_guess_with_the_original_suppliers(stand):
    elitan = stand.supplier("ООО «Элитан Тре»", learned=["Элитан"])
    wire = stand.active("МГТФ 0,12 белый", supplier="Элитан")  # no cable word: the linked item says cable
    stand.item("Провод МГТФ 0,12", "cable", links=[wire])
    stand.active("Кабель ПВС 2х0,75", supplier="ООО «Элитан Тре»")
    stand.active("Кабель КСПВ 4х0,5", supplier="ООО Снабкабель")
    stand.active("Разъём XH-2P")
    stand.active("Резистор 10 кОм 0805")
    stand.active("Термоусадка 6 мм")
    stand.active("Кабель НВ-4")
    stand.active("Кабель весь со склада", stock_covered=Decimal("2"))  # to_buy 0: nothing to ask for
    taken = stand.active("Кабель для запроса")
    assert stand.client.post(BASE, json={"line_ids": [str(taken)]}).status_code == 201

    pool = _pool(stand)
    assert [(g["category"], g["label"], g["count"]) for g in pool["groups"]] == [
        ("cable", "Кабель и провод", 4), ("connector", "Разъёмы и клеммы", 1), ("ekb", "ЭКБ", 1), ("other", "Прочее", 1),
    ]
    cable = pool["groups"][0]
    assert cable["suppliers"] == [
        {"name": "ООО «Элитан Тре»", "supplier_id": str(elitan), "lines": 2},
        {"name": "ООО Снабкабель", "supplier_id": None, "lines": 1},
    ]
    line = _lines(pool)[str(wire)]
    assert line["category"] == "cable" and line["item"]["matched_by"] == "link"
    assert line["supplier"] == "Элитан" and line["supplier_name"] == "ООО «Элитан Тре»"
    assert str(taken) not in _lines(pool) and pool["total"] == 7


def test_nomenclature_match_by_link_part_key_and_alias(stand):
    linked = stand.active("Что-то своё")
    by_article = stand.active("Колодка на кабель", design_article="shr 02v-s-b")
    by_alias = stand.active("WAGO 221-412  клемма")
    other_article = stand.active("Клемма WAGO 221-412", design_article="221-413")  # a name never crosses part numbers
    nothing = stand.active("Неизвестная деталь")
    item_link = stand.item("Деталь", "other", links=[linked])
    item_part = stand.item("Корпус SHR-02V", "connector", part_number="SHR-02V-S-B")
    item_alias = stand.item("Клемма WAGO 221-412", "connector", part_number="221-412", aliases=["wago 221-412 клемма"])

    lines = _lines(_pool(stand))
    assert (lines[str(linked)]["item"]["id"], lines[str(linked)]["item"]["matched_by"]) == (str(item_link), "link")
    assert (lines[str(by_article)]["item"]["id"], lines[str(by_article)]["item"]["matched_by"]) == (str(item_part), "part_key")
    assert lines[str(by_article)]["category"] == "connector"  # the item's, though the name says «кабель»
    assert (lines[str(by_alias)]["item"]["id"], lines[str(by_alias)]["item"]["matched_by"]) == (str(item_alias), "alias")
    assert lines[str(other_article)]["item"] is None
    assert lines[str(nothing)]["item"] is None and lines[str(nothing)]["history"] is None


def test_price_history_and_cheaper_before(stand):
    item = stand.item("Разъём XH-2P", "connector", part_number="XH-2P")
    stand.paid("Разъём XH-2P", "ООО Альфа", "9.10", "2026-09-03", design_article="XH-2P")
    stand.paid("Разъём XH-2P", "ООО Бета", "9.80", "2026-09-10", design_article="XH 2P")
    stand.paid("Разъём XH-2P", "ООО Гамма", "8.00", "2026-08-01", design_article="XH-2P", unit="уп")
    cheaper = stand.active("Разъём XH-2P", design_article="XH-2P", calculated_price_with_vat=Decimal("12.00"))
    dearer = stand.active("Разъём XH-2P вилка", design_article="xh-2p", calculated_price_with_vat=Decimal("9.00"))
    metres = stand.active("Разъём XH-2P", design_article="XH-2P", calculated_unit="м",
                          calculated_price_with_vat=Decimal("50"))

    lines = _lines(_pool(stand))
    line = lines[str(cheaper)]
    assert line["item"]["id"] == str(item)
    assert line["history"] == {
        "purchases": 3,
        "last": {"price": "9.8", "date": "2026-09-10", "supplier": "ООО Бета", "unit": "шт"},
        "min": {"price": "8", "date": "2026-08-01", "supplier": "ООО Гамма", "unit": "уп"},
    }
    assert line["cheaper_before_pct"] == "24.2"  # (12 − 9.10) / 12: the lowest in «шт», not the «уп» one
    assert lines[str(dearer)]["cheaper_before_pct"] is None  # bought dearer than the budget
    assert lines[str(metres)]["cheaper_before_pct"] is None  # never bought in metres


def _suggest(stand, *line_ids) -> dict:
    response = stand.client.get(f"{BASE}/suggest", params={"line_ids": ",".join(str(i) for i in line_ids)})
    assert response.status_code == 200, response.text
    return response.json()


def test_suggestion_reasons_ranking_unknown_and_archived(stand):
    alfa = stand.supplier("ООО Альфа", categories=["connector"])
    beta = stand.supplier("ООО Бета")
    gamma = stand.supplier("ООО Гамма")
    key = stand.supplier("ООО Ключ", key=True, categories=["Разъёмы"])
    stand.supplier("ООО Ключ-кабель", key=True, categories=["cable"])
    rotation = stand.supplier("ООО Ротация", categories=["connector"])
    asked = stand.supplier("ООО Спрашивали", categories=["connector"])
    stand.supplier("ООО Архив", archived=True)
    stand.item("Разъём XH-2P", "connector", part_number="XH-2P")
    for day in ("2026-08-01", "2026-09-01"):
        stand.paid("Разъём XH-2P", "Альфа", "9", day, design_article="XH-2P")
    stand.paid("Разъём XH-2P", "ООО Архив", "8", "2026-09-02", design_article="XH-2P")
    for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
        stand.paid("Клемма винтовая", "ООО Бета", "5", day)
    stand.paid("Клеммник 2-конт.", "ИП Неизвестный", "4", "2026-06-01")
    stand.paid("Кабель ПВС", "ООО Кабельщик", "40", "2026-06-01")  # another category
    chosen = stand.active("Разъём XH-2P", design_article="XH-2P", supplier="Гамма")
    with stand.sessions() as session:  # «ООО Спрашивали» was asked recently: the rotation goes to the other one
        rfq = RfqRow(id=uuid4(), number="КП-2609-900", buyer_id=uuid4(), created_at=NOW, version=1)
        session.add(rfq)
        session.flush()
        session.add(RfqSupplierRow(id=uuid4(), rfq_id=rfq.id, supplier_id=asked, reason="manual", created_at=NOW,
                                   sent_at=NOW))
        session.commit()

    body = _suggest(stand, chosen)
    assert body["categories"] == ["connector"]
    rows = body["suggestions"]
    names = [row["name"] for row in rows]
    assert names == ["ООО Альфа", "ООО Бета", "ИП Неизвестный", "ООО Гамма", "ООО Ключ", "ООО Ротация", "ООО Спрашивали"]
    by_name = {row["name"]: row for row in rows}
    alfa_row = by_name["ООО Альфа"]
    assert [r["code"] for r in alfa_row["reasons"]] == ["items", "category_history"]
    assert alfa_row["reasons"][0] == {"code": "items", "label": "поставлял эти позиции", "lines": 2, "last_date": "2026-09-01"}
    assert alfa_row["reasons"][1]["label"] == "поставлял такие же (разъёмы и клеммы)"
    assert (alfa_row["supplier_id"], alfa_row["rfq_reason"], alfa_row["preselect"]) == (str(alfa), "category", True)
    assert by_name["ООО Бета"]["supplier_id"] == str(beta) and by_name["ООО Бета"]["reasons"][0]["lines"] == 3
    unknown = by_name["ИП Неизвестный"]
    assert unknown["supplier_id"] is None and unknown["reason"] == "category_history" and unknown["preselect"]
    assert (by_name["ООО Гамма"]["supplier_id"], by_name["ООО Гамма"]["reason"], by_name["ООО Гамма"]["rfq_reason"]) == (
        str(gamma), "original", "plan")
    assert (by_name["ООО Ключ"]["supplier_id"], by_name["ООО Ключ"]["reason"], by_name["ООО Ключ"]["preselect"]) == (
        str(key), "key", True)
    assert (by_name["ООО Ротация"]["supplier_id"], by_name["ООО Ротация"]["reason"]) == (str(rotation), "rotation")
    assert by_name["ООО Ротация"]["preselect"] is False  # rotation is offered unticked
    assert (by_name["ООО Спрашивали"]["reason"], by_name["ООО Спрашивали"]["preselect"]) == ("category", False)
    assert "ООО Архив" not in by_name and "ООО Кабельщик" not in by_name and "ООО Ключ-кабель" not in by_name


def test_suggest_needs_lines_and_keeps_the_request_variant(stand):
    assert stand.client.get(f"{BASE}/suggest").status_code == 422
    assert stand.client.get(f"{BASE}/suggest", params={"line_ids": "не-uuid"}).status_code == 422
    stand.supplier("ООО Ключ", key=True, categories=["connector"])
    line = stand.active("Разъём XH-2P")
    rfq = stand.client.post(BASE, json={"line_ids": [str(line)]}).json()
    by_rfq = stand.client.get(f"{BASE}/suggest", params={"rfq_id": rfq["id"]}).json()
    assert [(row["name"], row["reason"], row["in_rfq"]) for row in by_rfq["suggestions"]] == [("ООО Ключ", "key", False)]
    legacy = stand.client.get(f"{BASE}/suggest-suppliers", params={"rfq_id": rfq["id"]})
    assert legacy.status_code == 200 and legacy.json()["suggestions"][0]["name"] == "ООО Ключ"


def test_create_with_suppliers_in_one_go(stand):
    alfa = stand.supplier("ООО Альфа")
    gamma = stand.supplier("ООО Гамма")
    archived = stand.supplier("ООО Архив", archived=True)
    lines = [stand.active("Разъём XH-2P"), stand.active("Разъём XH-3P")]
    body = {
        "line_ids": [str(line) for line in lines], "title": "Разъёмы",
        "suppliers": [
            {"supplier_id": str(alfa), "reason": "items"},
            {"name": "ИП Неизвестный", "reason": "category_history"},
            {"supplier_id": str(gamma), "reason": "original"},
            {"supplier_id": str(alfa), "reason": "key"},  # twice — once in the request
        ],
    }
    refused = stand.client.post(BASE, json={**body, "suppliers": [*body["suppliers"], {"supplier_id": str(archived)}]})
    assert refused.status_code == 422 and "в архиве" in refused.json()["detail"]
    with stand.sessions() as session:  # all or nothing: no request, no quick-added supplier
        assert session.scalar(select(func.count()).select_from(RfqRow)) == 0
        assert session.scalar(select(SupplierRow).where(SupplierRow.name == "ИП Неизвестный")) is None

    created = stand.client.post(BASE, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["suppliers"] == 3
    matrix = stand.client.get(f"{BASE}/{created.json()['id']}").json()
    assert len(matrix["lines"]) == 2
    assert {s["name"]: s["reason"] for s in matrix["suppliers"]} == {
        "ООО Альфа": "category", "ИП Неизвестный": "category", "ООО Гамма": "plan",
    }
    bad = stand.client.post(BASE, json={"line_ids": [str(uuid4())], "suppliers": [{"name": "x", "reason": "чужая"}]})
    assert bad.status_code == 422


def test_option_off_is_404_after_auth_and_a_viewer_write_is_403(stand, monkeypatch, tmp_path):
    _options(monkeypatch, tmp_path, False)
    assert stand.client.get(f"{BASE}/pool").status_code == 404
    assert stand.client.get(f"{BASE}/suggest", params={"line_ids": str(uuid4())}).status_code == 404
    _as("Зритель", "view")
    assert stand.client.post(BASE, json={"line_ids": [str(uuid4())], "suppliers": []}).status_code == 403
    _options(monkeypatch, tmp_path, True)
    assert stand.client.get(f"{BASE}/pool").status_code == 403  # the buyer's desk: edit only, as /candidates
    app.dependency_overrides.pop(authenticated_principal, None)
    _options(monkeypatch, tmp_path, False)
    assert stand.client.get(f"{BASE}/pool").status_code == 401


def test_pool_and_suggest_stay_fast_on_thousands_of_lines(stand):
    names = [f"Разъём серии {n} контакт" for n in range(300)]
    for n in range(40):
        stand.supplier(f"ООО Поставщик {n}", categories=["connector"], key=n < 3)
    item_ids = []
    with stand.sessions() as session:
        for n, name in enumerate(names):
            item_id = uuid4()
            item_ids.append(item_id)
            session.add(ItemRow(id=item_id, name=name, part_number=f"PN-{n:04d}", part_key=part_key(f"PN-{n:04d}"),
                                category="connector" if n % 3 else "cable", unit="шт", version=1, created_at=NOW))
        session.commit()
    paid = stand.rows([
        {"calculated_name": names[n % 300], "design_article": f"PN-{n % 300:04d}" if n % 2 else None,
         "status": "Готово", "supplier": f"ООО Поставщик {n % 40}", "actual_price_with_vat": Decimal(10 + n % 7),
         "actual_unit": "шт", "paid_at": datetime(2026, 1 + n % 9, 1 + n % 27, 10)}
        for n in range(4500)
    ])
    active = stand.rows([
        {"calculated_name": names[n % 300] if n % 4 else f"Прочая позиция {n}", "design_article": f"PN-{n % 300:04d}",
         "supplier": f"Поставщик {n % 40}", "calculated_price_with_vat": Decimal("12.5"), "project_reference": "RND.012"}
        for n in range(500)
    ])
    with stand.sessions() as session:
        for line_id, item_id in zip(paid[:1500], item_ids * 5):
            session.add(LineItemRow(procurement_line_id=line_id, item_id=item_id, linked_at=NOW))
        session.commit()

    _pool(stand)  # warm-up: imports, statement caches
    started = time.perf_counter()
    pool = _pool(stand)
    elapsed = time.perf_counter() - started
    assert pool["total"] == 500 and sum(group["count"] for group in pool["groups"]) == 500
    # Measured ~0.16 s alone; the bound only has to catch a per-line query (seconds), not a busy test run.
    assert elapsed < 1.0, f"пул строится {elapsed:.3f} с"
    started = time.perf_counter()
    body = _suggest(stand, *active[:50])
    elapsed = time.perf_counter() - started
    assert body["suggestions"] and elapsed < 1.0, f"подсказка {elapsed:.3f} с"
