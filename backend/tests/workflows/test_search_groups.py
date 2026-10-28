"""Global search, groups beyond purchasing: «База данных», «Отгрузки», «Готовая продукция», «Техкарты», «Задания цеха»,
«Реестр УПД», «Счета», «Сотрудники». Each answers where it lives and only to whoever may open that section."""

from decimal import Decimal
from uuid import uuid4

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.references.infrastructure import TechOperationRecordRow
from app.modules.shipping.infrastructure import FinishedGoodRow, ShipmentLineRow, ShipmentRow
from app.modules.purchasing.infrastructure import InvoiceRow, UpdDocumentRow
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.workflows.test_search_api import NOW, URL, env, fill, groups, titles  # noqa: F401 — env is the fixture


def record(tab, text, *, cable_kind=None, ter_kind=None, fields=None):
    return fill(TechOperationRecordRow, tab=tab, cable_kind=cable_kind, ter_kind=ter_kind, display_text=text,
                normalized_search=text.casefold(), sort_key=text, fields=fields or {}, mirror_key="")


def seed_world(seed):
    shipment = fill(ShipmentRow, number="ОТГ-26-0007", status="Собрана", customer="АО Заказчик", receiver="Цех 5",
                    created_at=NOW, version=1, track_number="TRK-991")
    good = fill(FinishedGoodRow, project="591К.3", item_index="2", customer="АО Заказчик", article="CB-77",
                name="Жгут сигнальный", accepted=Decimal(10), shipped=Decimal(4), unit="шт", version=1, grn=None)
    seed(
        record("ter", "Контакт 0436-440", ter_kind="contact"), record("ter", "Наконечник 0436-lug", ter_kind="lug"),
        record("coax", "Разъём SMA 0436-sma"), record("prov", "Провод МС 0436-мс", cable_kind="wire"),
        record("prov", "Кабель 0436-rg", cable_kind="coax"), record("ob", "Пресс 0436-press"),
        record("op", "Опрессовка 0436-crimp"),
        record("mat", "Припой 0436-solder", fields={"Группа": "припой/флюс"}),
        record("mat", "Стяжка 0436-tie", fields={"Группа": "стяжка"}),
        shipment, good,
        fill(ShipmentLineRow, shipment_id=shipment.id, finished_good_id=good.id, position=1, project="777Л.1",
             item_index="1", article="CB-77", name="Жгут", requested=Decimal(1), picked=Decimal(1), unit="шт"),
        fill(ProjectTechcardRow, project="591К.3", item_index="2", product_name="Жгут сигнальный", status="Выдана",
             revision=3, created_at=NOW, updated_at=NOW, version=1, manual_operation_keys=[]),
        fill(ProductionTaskRow, number="ПЗ-26-0042", project="591К.3", item="2", name="Жгут сигнальный", quantity=5,
             status="Запущено", created_at=NOW, version=1, source_idempotency_key=uuid4().hex),
        fill(UpdDocumentRow, id="upd-1", number="УПД-555", seller_name="ООО Вектор", seller_inn="7700000001",
             status="Разобран", origin="edo"),
        fill(UpdDocumentRow, id="upd-2", number="УПД-555", seller_name="ООО Вектор", seller_inn="7700000001",
             status="Дубликат", origin="edo", twin_of="upd-1"),
        fill(InvoiceRow, number="СЧ-909", seller_name="ООО Вектор", seller_inn="7700000001", title="Счёт СЧ-909",
             source="kaiten", external_id="k1", status="to_pay", card_created=NOW, card_updated=NOW, synced_at=NOW),
        fill(IdentityUserRow, login="ivanov", email="iv@example.test", name="Иванов Пётр", department="Цех",
             role="operator", job_title="Монтажник", active=True),
    )


def as_person(permissions, *, role="buyer"):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Петров", department="Закупки"), email="p@example.test", roles=frozenset({role}),
        permissions=permissions,
    )


def results(client, query, group):
    return [item for item in client.get(URL, params={"q": query}).json()["results"] if item["group"] == group]


def test_database_rows_name_their_tab_and_open_it_with_the_query(env):
    client, seed = env
    seed_world(seed)
    as_person({"erpref": "view", "erptctpl": "view"})
    found = {item["subtitle"] for item in client.get(URL, params={"q": "0436"}).json()["results"]
             if item["group"] == "База данных"}
    assert len(found) == 8  # the cap holds across the tabs
    every = {}
    for suffix in ("440", "lug", "sma", "мс", "rg", "press", "crimp", "solder", "tie"):
        hits = results(client, f"0436-{suffix}", "База данных")
        every[suffix] = hits[0] if hits else None
    assert every["tie"] is None  # a БД.МАТ row no tab shows is not offered
    assert (every["440"]["subtitle"], every["440"]["params"]["tab"]) == ("База данных → Терминалы", "ter")
    assert (every["lug"]["subtitle"], every["lug"]["params"]["tab"]) == ("База данных → Наконечники", "lug")
    assert every["sma"]["params"]["tab"] == "coax" and every["мс"]["params"]["tab"] == "cab_wire"
    assert every["rg"]["params"]["tab"] == "cab_coax" and every["press"]["params"]["tab"] == "ob"
    assert every["crimp"]["params"]["tab"] == "templates" and every["solder"]["params"]["tab"] == "mat_use"
    assert every["solder"]["section"] == "references" and every["440"]["params"]["q"] == "Контакт 0436-440"


def test_database_tabs_follow_their_own_access_key(env):
    client, seed = env
    seed_world(seed)
    as_person({"erpref": "view"})
    assert results(client, "0436-crimp", "База данных") == []
    assert results(client, "0436-440", "База данных") != []
    as_person({"erptctpl": "view"})
    assert results(client, "0436-440", "База данных") == []
    assert results(client, "0436-crimp", "База данных") != []
    as_person({"procurement": "view"})
    assert "База данных" not in groups(client.get(URL, params={"q": "0436"}))


def test_shipments_by_number_customer_track_and_project_of_a_line(env):
    client, seed = env
    seed_world(seed)
    as_person({"shipping": "view"})
    for query in ("ОТГ-26-0007", "заказчик цех", "TRK-991", "777Л.1"):
        hit = results(client, query, "Отгрузки")
        assert [item["title"] for item in hit] == ["ОТГ-26-0007 · АО Заказчик"], query
    hit = results(client, "ОТГ-26-0007", "Отгрузки")[0]
    assert (hit["section"], hit["params"]) == ("shipping", {"tab": "shipments"}) and "Собрана" in hit["subtitle"]
    as_person({"shipping_goods": "view"})
    assert results(client, "ОТГ-26-0007", "Отгрузки") != []
    as_person({"warehouse": "view", "procurement": "view"})
    assert "Отгрузки" not in groups(client.get(URL, params={"q": "ОТГ-26-0007"}))


def test_finished_goods_open_the_warehouse_list_or_the_shipping_screen(env):
    client, seed = env
    seed_world(seed)
    as_person({"shipping": "view", "warehouse": "view"})
    hit = results(client, "сигнальный 591", "Готовая продукция")[0]
    assert hit["title"] == "Жгут сигнальный · CB-77" and "КБ 2" in hit["subtitle"] and "6 шт" in hit["subtitle"]
    assert (hit["section"], hit["params"]) == ("stock", {"view": "goods", "q": "591К.3"})
    as_person({"shipping_goods": "view"})
    hit = results(client, "CB-77", "Готовая продукция")[0]
    assert (hit["section"], hit["params"]) == ("shipping", {"tab": "stock"})
    as_person({"warehouse": "view"})
    assert "Готовая продукция" not in groups(client.get(URL, params={"q": "CB-77"}))


def test_techcards_and_production_tasks(env):
    client, seed = env
    seed_world(seed)
    as_person({"techcards": "view", "production": "view"})
    card = results(client, "591К.3 жгут", "Техкарты")[0]
    assert card["title"] == "591К.3 · КБ 2 · Жгут сигнальный" and "ред. 3" in card["subtitle"]
    assert (card["section"], card["params"]) == ("techcards", {"project": "591К.3"})
    task = results(client, "ПЗ-26-0042", "Задания цеха")[0]
    assert task["section"] == "production" and task["params"] == {} and "591К.3" in task["subtitle"]
    as_person({"techcards": "view"})
    assert "Задания цеха" not in groups(client.get(URL, params={"q": "ПЗ-26-0042"}))
    as_person({"production": "view"})
    assert "Техкарты" not in groups(client.get(URL, params={"q": "591К.3"}))


def test_upd_and_invoices_open_the_documents_and_skip_twins(env):
    client, seed = env
    seed_world(seed)
    as_person({"procurement": "view"})
    upd = results(client, "вектор 555", "Реестр УПД")
    assert [item["params"] for item in upd] == [{"tab": "upd", "upd": "upd-1"}]  # the twin copy is not offered
    assert upd[0]["section"] == "primarydocs" and "ООО Вектор" in upd[0]["subtitle"]
    invoice = results(client, "СЧ-909", "Счета")[0]
    assert (invoice["section"], invoice["params"]) == ("primarydocs", {"tab": "invoices", "q": "СЧ-909"})
    as_person({"warehouse": "view"})
    assert not {"Реестр УПД", "Счета"} & groups(client.get(URL, params={"q": "вектор"}))


def test_staff_only_for_full_access(env):
    client, seed = env
    seed_world(seed)
    as_person({"procurement": "edit", "production": "edit"})
    assert "Сотрудники" not in groups(client.get(URL, params={"q": "иванов"}))
    as_person({}, role="admin")
    hit = results(client, "иванов монтажник", "Сотрудники")[0]
    assert hit["title"] == "Иванов Пётр" and hit["section"] == "staff" and set(hit["params"]) == {"user"}
