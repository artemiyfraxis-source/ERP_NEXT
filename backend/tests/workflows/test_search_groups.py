"""Global search, groups beyond purchasing: «База данных», «Отгрузки», «Готовая продукция», «Техкарты», «Задания цеха»,
«Реестр УПД», «Счета», «Сотрудники». Each answers where it lives and only to whoever may open that section."""

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import event

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.references.infrastructure import EquipmentModelRow, EquipmentMachineRow, EquipmentUnitRow, ReferenceEntryRow, TechOperationRecordRow, WireGaugeRow
from app.modules.shipping.infrastructure import FinishedGoodRow, ShipmentLineRow, ShipmentRow
from app.modules.purchasing.infrastructure import InvoiceRow, UpdDocumentRow
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows import search_api
from tests.factories import Actor
from tests.workflows.test_search_api import NOW, URL, env, fill, groups, lot, titles  # noqa: F401 — env is the fixture


def record(tab, text, *, cable_kind=None, ter_kind=None, fields=None):
    # every base is its own tab since migration 0165: the kind names the base
    tab = {"lug": "lug"}.get(ter_kind, tab) if tab == "ter" else tab
    tab = {"coax": "cab_coax", "multi": "cab_multi", "lan": "cab_lan"}.get(cable_kind, tab) if tab == "prov" else tab
    return fill(TechOperationRecordRow, tab=tab, display_text=text,
                normalized_search=text.casefold(), sort_key=text, fields=fields or {}, mirror_key="")


def seed_equipment(seed, text, *, model=None, fields=None):
    model = model or fill(EquipmentModelRow, name=text, article="", maker="")
    unit = fill(EquipmentMachineRow, tab="ob", display_text=text, normalized_search=text.casefold(),
                sort_key=text, fields=fields or {}, mirror_key="", equipment_model_id=model.id)
    seed(model, unit, EquipmentUnitRow(id=unit.id, model_id=model.id, catalog="ob", ob_id=unit.id))
    return unit


def seed_world(seed):
    seed_equipment(seed, "Пресс 0436-press")
    shipment = fill(ShipmentRow, number="ОТГ-26-0007", status="Собрана", customer="АО Заказчик", receiver="Цех 5",
                    created_at=NOW, version=1, track_number="TRK-991")
    good = fill(FinishedGoodRow, project="591К.3", item_index="2", customer="АО Заказчик", article="CB-77",
                name="Жгут сигнальный", accepted=Decimal(10), shipped=Decimal(4), unit="шт", version=1, grn=None)
    seed(
        record("ter", "Контакт 0436-440", ter_kind="contact"), record("ter", "Наконечник 0436-lug", ter_kind="lug"),
        record("coax", "Разъём SMA 0436-sma"), record("prov", "Провод МС 0436-мс", cable_kind="wire"),
        record("prov", "Кабель 0436-rg", cable_kind="coax"),
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
    assert hit["section"] == "intake" and hit["params"]["tab"] == "journal"
    assert hit["params"]["q"] == "ОТГ-26-0007" and "shipment" in hit["params"]
    assert "Собрана" in hit["subtitle"]
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
    assert (hit["section"], hit["params"]) == ("intake", {"tab": "all", "q": "591К.3"})
    as_person({"warehouse": "view"})
    assert "Готовая продукция" not in groups(client.get(URL, params={"q": "CB-77"}))


def test_techcards_and_production_tasks(env):
    client, seed = env
    seed_world(seed)
    as_person({"techcards": "view", "production": "view"})
    card = results(client, "591К.3 жгут", "Техкарты")[0]
    assert card["title"] == "591К.3 · КБ 2 · Жгут сигнальный" and "ред. 3" in card["subtitle"]
    assert card["section"] == "techcards" and card["params"]["project"] == "591К.3"
    assert set(card["params"]) == {"project", "card"}
    task = results(client, "ПЗ-26-0042", "Задания цеха")[0]
    assert task["section"] == "production" and set(task["params"]) == {"task"} and "591К.3" in task["subtitle"]
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


def test_kit_requests_by_number_project_and_an_article_of_a_line_open_their_screen(env):
    # owner 28.10.2026: «НШВ» must find the requests for the kit and the handout that carry it
    from app.modules.fulfillment.infrastructure import FulfillmentLineRow, FulfillmentRequestRow
    client, seed = env
    picking = fill(FulfillmentRequestRow, number="ЗК-26-0011", purpose="713К.4 КБ1", project_key="713К.4",
                   item_index="КБ1", requester="Мастер", status="Недоукомплектован", created_at=NOW, version=1,
                   source_idempotency_key=uuid4().hex)
    out = fill(FulfillmentRequestRow, number="ЗК-26-0012", purpose="713К.4 КБ2", project_key="713К.4",
               item_index="КБ2", requester="Мастер", status="КБ Выдана", created_at=NOW, version=1,
               source_idempotency_key=uuid4().hex)
    closed = fill(FulfillmentRequestRow, number="ЗК-26-0013", purpose="700К.1 КБ1", project_key="700К.1",
                  item_index="КБ1", requester="Мастер", status="Готово", created_at=NOW, version=1,
                  source_idempotency_key=uuid4().hex)
    seed(picking, out, closed,
         fill(FulfillmentLineRow, request_id=picking.id, position=1, article="НШВ 0,5-8", name="Наконечник НШВ 0,5-8",
              unit="шт", requirement=Decimal(10), reserved=Decimal(0), issued=Decimal(0), returned_good=Decimal(0),
              defective_returned=Decimal(0), defective_missing=Decimal(0)),
         fill(FulfillmentLineRow, request_id=closed.id, position=1, article="НШВ 1,0-8", name="Наконечник НШВ 1,0-8",
              unit="шт", requirement=Decimal(5), reserved=Decimal(0), issued=Decimal(5), returned_good=Decimal(0),
              defective_returned=Decimal(0), defective_missing=Decimal(0)))
    as_person({"fulfillment": "edit", "handout": "edit"})
    hit = {item["title"].split(" · ")[0]: item for item in results(client, "НШВ", "Комплектация и выдача")}
    assert set(hit) == {"ЗК-26-0011", "ЗК-26-0013"}  # by an article of a line; КБ2 carries none
    assert (hit["ЗК-26-0011"]["section"], hit["ЗК-26-0011"]["params"]) == ("issues", {"q": "ЗК-26-0011"})
    assert "Запросы на комплектацию" in hit["ЗК-26-0011"]["subtitle"] and "НШВ 0,5-8" in hit["ЗК-26-0011"]["subtitle"]
    assert hit["ЗК-26-0013"]["params"] == {"q": "ЗК-26-0013", "archive": "1"}
    given = results(client, "713К.4 КБ2", "Комплектация и выдача")
    assert [(item["section"], item["params"]) for item in given] == [("handout", {"q": "ЗК-26-0012"})]
    # only the handout screen allowed — every request opens there; neither — no group
    as_person({"handout": "view"})
    assert {item["section"] for item in results(client, "713К.4", "Комплектация и выдача")} == {"handout"}
    as_person({"warehouse": "view"})
    assert "Комплектация и выдача" not in groups(client.get(URL, params={"q": "НШВ"}))


def test_equipment_matches_model_and_unit_natural_keys_and_opens_the_unit(env):
    client, seed = env
    model = fill(EquipmentModelRow, name="Пресс Штиль", article="P-778", maker="Завод")
    unit = seed_equipment(seed, "ОБ.070.2 · Инв. 5544", model=model, fields={"Инв. номер": "5544"})
    as_person({"erpref": "view"})
    for query in ("ШТИЛЬ", "P-778", "5544", "штиль 5544", "завод"):
        hits = results(client, query, "Оборудование")
        assert len(hits) == 1, query
        assert hits[0]["params"] == {"tab": "ob", "equipment": str(unit.id)}
        assert hits[0]["subtitle"].startswith("База данных → Оборудование")
    as_person({"erptctpl": "view", "warehouse": "view"})
    assert results(client, "5544", "Оборудование") == []


def test_correspondence_tabs_and_gauges_are_searchable_by_content(env):
    client, seed = env
    seed(fill(ReferenceEntryRow, kind="supplier", key="example.test", normalized_key="example.test", value="Поставка", position=1),
         fill(ReferenceEntryRow, kind="country", key="Завод", normalized_key="завод", value="Россия", position=1),
         fill(WireGaugeRow, awg="22", section_mm2=Decimal("0.35")))
    as_person({"erpref": "view"})
    for query, tab in (("поставка", "suppliers"), ("РОССИЯ", "countries"), ("22", "gauges")):
        assert any(hit["params"]["tab"] == tab for hit in results(client, query, "База данных"))
    as_person({"erptctpl": "view"})
    assert results(client, "поставка", "База данных") == []
    assert all(hit["params"]["tab"] != "gauges" for hit in results(client, "22", "База данных"))


def test_shipments_match_words_across_different_lines_without_duplicates(env):
    client, seed = env
    shipment = fill(ShipmentRow, number="ОТГ-9", status="Собрана", created_at=NOW)
    good = fill(FinishedGoodRow, project="Проект-А", item_index="1", accepted=Decimal(2), shipped=Decimal(0))
    seed(shipment, good,
         fill(ShipmentLineRow, shipment_id=shipment.id, finished_good_id=good.id, position=1, project="Проект-А"),
         fill(ShipmentLineRow, shipment_id=shipment.id, finished_good_id=good.id, position=2, project="Проект-Б"))
    as_person({"shipping": "view"})
    assert len(results(client, "Проект-А Проект-Б", "Отгрузки")) == 1


def test_new_sql_groups_limit_after_matching_all_words_and_escape_wildcards(env):
    client, seed = env
    seed(*(fill(ProductionTaskRow, number=f"ПЗ-{i}", project="Общий", name="Другая" if i < 20 else "Нужная_100%",
                quantity=1, created_at=NOW, source_idempotency_key=uuid4().hex) for i in range(40)))
    as_person({"production": "view"})
    hits = results(client, "ОБЩИЙ Нужная_100%", "Задания цеха")
    assert len(hits) == 8
    assert all("Нужная_100%" in hit["title"] for hit in hits)
    assert results(client, "Нет_100%", "Задания цеха") == []


def test_stock_search_bounds_selects_and_ranked_candidates_for_a_common_word(env, monkeypatch):
    client, seed = env
    seed(*(lot(f"FRX.26.{i:07d}", "Контакт общий") for i in range(2300)))
    as_person({"warehouse": "view"})
    with app.state.purchasing_uow_factory() as uow:
        engine = uow.session.get_bind()
    statements = []
    ranked_candidates = 0
    matches_words = search_api.matches_words

    def count_selects(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    def count_candidates(candidate, query):
        nonlocal ranked_candidates
        ranked_candidates += 1
        return matches_words(candidate, query)

    monkeypatch.setattr(search_api, "matches_words", count_candidates)
    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get(URL, params={"q": "Контакт"})
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200, response.text
    assert titles(response, "Склад") == [f"FRX.26.{i:07d}" for i in range(8)]
    assert len(statements) == 1
    assert ranked_candidates == search_api.MAX_CANDIDATES


def test_connector_catalog_search_opens_the_series_and_respects_tab_access(env):
    client, seed = env
    as_person({"erptctpl": "view"})
    hit = results(client, "GHR-04V-S", "База данных")[0]
    assert hit["params"] == {"tab": "connectors", "series": "jst-gh"}
    assert hit["subtitle"] == "База данных → Разъёмы"
    as_person({"erpref": "view"})
    assert results(client, "GHR-04V-S", "База данных") == []


def test_sql_fold_preserves_punctuation_and_numeric_decimal_commas(env):
    client, seed = env
    seed(fill(IdentityUserRow, login="comma", email="comma@example.test", name="Иванов, Пётр", role="operator",
              department="Цех", job_title="Монтажник 0,35", active=True))
    as_person({}, role="admin")
    assert len(results(client, "Иванов,", "Сотрудники")) == 1
    assert len(results(client, "0.35", "Сотрудники")) == 1


def test_postgresql_search_compiles_without_sqlite_functions():
    from types import SimpleNamespace
    from sqlalchemy.dialects import postgresql
    from app.workflows.search_api import _contains
    session = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    expression = _contains([IdentityUserRow.name], ["иванов,"], session)
    sql = str(expression.compile(dialect=postgresql.dialect()))
    assert "ILIKE" in sql and "regexp_replace" in sql
    assert "erp_search_fold" not in sql and "erp_casefold" not in sql
