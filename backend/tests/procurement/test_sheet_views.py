"""PR-11, PR-12 and the whole procurement sheet: supplier by link, learning, overview, table, batch paid."""

import pytest

from app.main import app
from app.modules.procurement.application import consume_procurement_requested
from app.modules.procurement.domain import supplier_from_link, supplier_key
from app.modules.procurement.infrastructure import ProcurementLineRow, SqlAlchemyProcurementUnitOfWork
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.procurement.test_api import awaiting_invoice, message, procurement_api, rates  # noqa: F401


class FakeSuppliers:
    def __init__(self, mapping=None):
        self.mapping = dict(mapping or {})
        self.learned = []

    def supplier_map(self):
        return dict(self.mapping)

    def learn(self, link, supplier, actor, now):
        self.learned.append((link, supplier, actor.name))
        return True


@pytest.fixture()
def suppliers():
    fake = FakeSuppliers()
    app.state.procurement_supplier_directory = fake
    yield fake
    del app.state.procurement_supplier_directory


def linked_message(suffix: int, link: str | None):
    item = message(suffix)
    item.payload["link"] = link
    return item


@pytest.mark.parametrize(
    "value,key",
    [
        ("https://www.Chipdip.ru/product/123", "chipdip.ru"),
        ("chipdip.ru/catalog", "chipdip.ru"),
        ("http://shop.elitan.ru:8080/x", "shop.elitan.ru:8080"),
        ("  Склад ", "склад"),
        ("", ""),
    ],
)
def test_supplier_key_mirrors_supplymap(value, key):
    assert supplier_key(value) == key


def test_supplier_from_link_returns_empty_without_match():
    mapping = {"chipdip.ru": "ЧипДип"}
    assert supplier_from_link("https://www.chipdip.ru/p/1", mapping) == "ЧипДип"
    assert supplier_from_link("https://elitan.ru/p/1", mapping) == ""
    assert supplier_from_link(None, mapping) == ""


def test_transfer_sets_supplier_from_link_domain(procurement_api):
    _, _, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    directory = FakeSuppliers({"chipdip.ru": "ЧипДип"})

    matched = consume_procurement_requested(linked_message(2, "https://www.chipdip.ru/product/1"), factory(), directory)
    unmatched = consume_procurement_requested(linked_message(3, "https://elitan.ru/x"), factory(), directory)
    no_port = consume_procurement_requested(linked_message(4, "https://chipdip.ru/y"), factory())

    assert matched.supplier == "ЧипДип"
    assert unmatched.supplier is None
    assert no_port.supplier is None


def test_first_supplier_with_link_teaches_the_map_but_a_change_does_not(procurement_api, suppliers):
    client, line, _ = procurement_api
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id)]}).json()["lines"][0]

    first = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": taken["version"], "supplier": "ЧипДип", "link": "https://www.chipdip.ru/p/1"},
    )
    assert first.status_code == 200
    changed = client.patch(
        f"/api/procurement/{line.id}/facts",
        json={"version": first.json()["line"]["version"], "supplier": "Элитан"},
    )

    assert changed.status_code == 200
    assert suppliers.learned == [("https://www.chipdip.ru/p/1", "ЧипДип", "Петров")]


def test_by_supplier_overview_shows_every_buyer_open_lines_and_stages(procurement_api):
    client, line, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    other = consume_procurement_requested(message(2), factory())
    client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    cancelled = client.post(f"/api/procurement/{other.id}/cancel", json={"version": 1})

    body = client.get("/api/procurement/by-supplier").json()

    assert body["stages"] == ["Новый", "В работе", "Готов к запросу", "Жду счет", "В оплату"]
    statuses = {item["legacy_id"]: item["status"] for item in body["lines"]}
    assert statuses[line.legacy_id] == "В работе"
    assert cancelled.status_code != 200 or other.legacy_id not in statuses
    # a line another buyer holds is still shown: the overview is not a work queue
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Сидоров", department="Закупки"),
        email="sidorov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "view"},
    )
    seen = client.get("/api/procurement/by-supplier").json()["lines"]
    assert [item["buyer"] for item in seen if item["legacy_id"] == line.legacy_id] == ["Петров"]


def test_whole_sheet_filters_by_status_buyer_and_words_with_cursor(procurement_api):
    client, line, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    second = consume_procurement_requested(message(2), factory())
    third = consume_procurement_requested(message(3), factory())
    client.post("/api/procurement/take", json={"line_ids": [str(line.id), str(third.id)]})

    everything = client.get("/api/procurement/lines").json()
    in_work = client.get("/api/procurement/lines", params={"status": "В работе", "buyer": "Петров"}).json()
    by_words = client.get("/api/procurement/lines", params={"q": "RND.002 Разъём"}).json()
    page = client.get("/api/procurement/lines", params={"limit": 2}).json()
    rest = client.get("/api/procurement/lines", params={"limit": 2, "after": page["next_cursor"]}).json()
    wildcard = client.get("/api/procurement/lines", params={"q": "%"}).json()

    assert len(everything["lines"]) == 3
    assert everything["buyers"] == ["Петров"]
    assert {item["id"] for item in in_work["lines"]} == {str(line.id), str(third.id)}
    assert [item["id"] for item in by_words["lines"]] == [str(second.id)]
    assert len(page["lines"]) == 2 and page["next_cursor"]
    assert len(rest["lines"]) == 1 and rest["next_cursor"] is None
    assert wildcard["lines"] == []


def test_meta_lists_units_and_statuses(procurement_api):
    client, _, _ = procurement_api
    body = client.get("/api/procurement/meta").json()
    assert body["units"] == ["шт", "кг", "м", "л", "уп", "компл"]
    assert body["statuses"][:2] == ["Новый", "В работе"] and "Отменено" in body["statuses"]


def to_pay(client, line, sessions) -> int:
    with sessions() as session:  # payment needs a delivery point with a receiving warehouse
        session.get(ProcurementLineRow, line.id).delivery_point = "г. Москва, ул. 6-я Радиальная, д. 17"
        session.commit()
    version = awaiting_invoice(client, line)
    saved = client.patch(
        f"/api/procurement/{line.id}/money",
        json={
            "version": version, "currency": "RUB", "price": "100", "vat_rate": "22",
            "invoice_item_name": "Разъём", "invoice_number": "17", "invoice_date": "2026-09-14",
        },
    ).json()["line"]
    invoiced = client.post(
        f"/api/procurement/{line.id}/invoice",
        json={"version": saved["version"], "invoice_url": "https://files.test/i.pdf", "expected_arrival": "2026-09-25"},
    )
    assert invoiced.json()["line"]["status"] == "В оплату"
    return invoiced.json()["line"]["version"]


def test_batch_paid_moves_invoiced_lines_and_reports_the_rest(procurement_api, rates):
    client, line, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    waiting = consume_procurement_requested(message(2), factory())
    version = to_pay(client, line, sessions)

    response = client.post(
        "/api/procurement/paid",
        json={"items": [{"line_id": str(line.id), "version": version}, {"line_id": str(waiting.id), "version": 1}]},
    )
    again = client.post("/api/procurement/paid", json={"items": [{"line_id": str(line.id), "version": version}]})

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == [], body["failed"]
    assert body["done"] == 1 and body["lines"][0]["status"] == "В пути"
    assert body["no_invoice"] == [waiting.legacy_id]
    assert body["failed"] == []
    assert again.json()["already"] == 1 and again.json()["done"] == 0
    assert client.post("/api/procurement/paid", json={"items": []}).status_code == 422


def test_batch_paid_needs_edit_access(procurement_api):
    client, line, _ = procurement_api
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"),
        email="petrov@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "view"},
    )
    response = client.post("/api/procurement/paid", json={"items": [{"line_id": str(line.id), "version": 1}]})
    assert response.status_code == 403


def as_buyer(name: str):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name=name, department="Закупки"),
        email=f"{name}@example.test",
        roles=frozenset({"buyer"}),
        permissions={"procurement": "edit"},
    )


def test_take_batch_skips_lines_other_buyers_took_and_takes_the_rest(procurement_api):
    client, line, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    free = consume_procurement_requested(message(2), factory())
    as_buyer("Сидоров")
    client.post("/api/procurement/take", json={"line_ids": [str(line.id)]})
    as_buyer("Петров")

    response = client.post("/api/procurement/take", json={"line_ids": [str(line.id), str(free.id)]})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["lines"]] == [str(free.id)]
    assert response.json()["lines"][0]["buyer"] == "Петров"
    assert response.json()["skipped"] == [line.legacy_id]


def test_fill_form_lists_own_in_work_lines_and_saves_marked_rows_in_one_call(procurement_api, suppliers):
    client, line, sessions = procurement_api
    factory = lambda: SqlAlchemyProcurementUnitOfWork(sessions)
    second = consume_procurement_requested(message(2), factory())
    foreign = consume_procurement_requested(message(3), factory())
    taken = client.post("/api/procurement/take", json={"line_ids": [str(line.id), str(second.id)]}).json()["lines"]
    as_buyer("Сидоров")
    client.post("/api/procurement/take", json={"line_ids": [str(foreign.id)]})
    as_buyer("Петров")

    listed = client.get("/api/procurement/in-work/mine").json()["lines"]
    assert {item["id"] for item in listed} == {str(line.id), str(second.id)}

    refused = client.post(
        "/api/procurement/facts",
        json={"rows": [
            {"line_id": str(line.id), "legacy_id": line.legacy_id, "version": taken[0]["version"], "supplier": "ЧипДип"},
            {"line_id": str(second.id), "legacy_id": second.legacy_id, "version": taken[1]["version"], "supplier": " "},
        ]},
    )
    assert refused.status_code == 422
    assert second.legacy_id in refused.json()["detail"]
    assert client.get("/api/procurement/in-work/mine").json()["lines"][0]["supplier"] is None  # nothing written

    saved = client.post(
        "/api/procurement/facts",
        json={"rows": [
            {"line_id": str(line.id), "legacy_id": line.legacy_id, "version": taken[0]["version"],
             "supplier": "ЧипДип", "link": "https://chipdip.ru/p/1", "actual_quantity": "2", "actual_unit": "шт"},
            {"line_id": str(second.id), "legacy_id": second.legacy_id, "version": 99, "supplier": "Элитан"},
        ]},
    ).json()

    assert (saved["saved"], saved["ready"], saved["skipped"], saved["failed"]) == (1, 1, [second.legacy_id], [])
    assert saved["lines"][0]["status"] == "Готов к запросу"
    assert suppliers.learned == [("https://chipdip.ru/p/1", "ЧипДип", "Петров")]

