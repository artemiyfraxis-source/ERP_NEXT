"""The readers outside «База данных» read a sheet family (migration 0165): БД.ТЕР = «Терминалы» + «Наконечники»,
БД.ПРОВ = «Монтажные провода» + the three cable bases. A lug in «lug» and a cable in «cab_*» are found as they were in
«ter» / «prov»; a family lists in its sheet order, so a first match and the ETK stamp stay what they were."""

from datetime import datetime, timezone

from app.main import app
from app.modules.references.domain import CABLE_TABS, TERMINAL_TABS, TechOperationRecord, TechOperationTab
from app.modules.references.technology import TECH_SNAPSHOT_SCHEMA_VERSION
from app.modules.references.wire_makers import LEGACY_FIELD
from app.modules.techcards import api as techcards_api
from app.modules.techcards.harness_catalog_api import reference_tables
from app.workflows import cable_series, td_auto, wire_standard_factory, wire_standard_makers
from app.workflows.item_names import ReferenceNames, reference_records
from app.workflows.lot_names_refresh import Catalog
from app.workflows.stock_receipt import known_articles
from app.workflows.techcard_build import load_coax_refs, load_refs
from app.workflows.techcards_all import generator_refs
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)
from tests.warehouse.test_receipts import stand  # noqa: F401  (fixture)

LUG = {"Тип разъема": "Обжимная клемма", "Product Name": "НШВИ", "Series": "НШВИ 0.5",
       "Артикул контакта (REEL)": "НШВИ 0.5-8", "terManufacturer": "КВТ", "terComponent": "НШВИ",
       "terArticle": "НШВИ 0.5-8"}
CONTACT = {"Тип разъема": "Обжимной контакт", "Product Name": "XH", "Series": "XHP", "Тип контакта": "Female (Socket)",
           "Артикул контакта (REEL)": "SXH-001T-P0.6", "terManufacturer": "JST", "terComponent": "XH",
           "terArticle": "SXH-001T-P0.6", "terType": "Female (Socket)"}
CABLES = ((TechOperationTab.CAB_COAX, "RG-316", {"Марка": "RG-316"}),
          (TechOperationTab.CAB_MULTI, "LiYCY 2x0,34", {"Марка": "LiYCY 2x0,34", "Стандарт": "ГОСТ (Breks, Брянск)",
                                                        LEGACY_FIELD: "DIN VDE 0812 / LAPP"}),
          (TechOperationTab.WIRE, "МГТФ 0,35", {"Марка": "МГТФ 0,35"}))
ACTOR, NOW = Actor(name="Тест", department="Цех"), datetime(2026, 10, 30, tzinfo=timezone.utc)


def _seed(api):
    """БД.ТЕР by a mirror load (the lug goes to «lug»), the cables straight into their bases."""
    response = api.put("/api/references/tech-operations/snapshot", json={
        "schema_version": TECH_SNAPSHOT_SCHEMA_VERSION, "records": [
            {"tab": "ter", "display_text": row["Series"], "source_sheet": "БД.ТЕР", "fields": row} for row in (LUG, CONTACT)]})
    assert response.status_code == 200, response.text
    factory = api.app.state.references_uow_factory
    with factory() as uow:
        for tab, mark, fields in CABLES:
            uow.tech_operations.save(TechOperationRecord.create(
                tab=tab, display_text=mark, search_text="", source_sheet="БД.ПРОВ", sort_key=mark, fields=fields))
        uow.commit()
    with factory() as uow:
        tabs = {item.display_text: item.tab for item in uow.tech_operations.list(tab=None, query="", limit=100)}
    assert tabs["НШВИ 0.5"] is TechOperationTab.LUG and tabs["XHP"] is TechOperationTab.TERMINAL
    return factory


def test_a_family_lists_in_its_sheet_order_not_base_by_base(references_api):
    factory = _seed(references_api)
    with factory() as uow:
        cables = [item.display_text for item in uow.tech_operations.list(tab=CABLE_TABS, query="", limit=100)]
        terminals = [item.display_text for item in uow.tech_operations.list(tab=TERMINAL_TABS, query="", limit=100)]
    assert cables == ["LiYCY 2x0,34", "RG-316", "МГТФ 0,35"]  # sort keys across cab_multi, cab_coax, prov
    assert terminals == ["XHP", "НШВИ 0.5"]


def test_item_names_read_lugs_and_every_cable_base(references_api):
    records = reference_records(_seed(references_api))
    assert {item.tab for item in records} >= {TechOperationTab.LUG, TechOperationTab.CAB_COAX, TechOperationTab.CAB_MULTI}
    names = ReferenceNames(records)
    assert names.name("НШВИ 0.5-8") == "Наконечник КВТ НШВИ"
    assert {wire.mark for wire in names._wires} >= {"RG-316", "LiYCY 2x0,34", "МГТФ 0,35"}


def test_the_etk_part_identifier_names_a_lug(references_api):
    identify = techcards_api.etk_part_identifier(_seed(references_api))
    assert identify("НШВИ 0.5-8") == "Наконечник НШВИ"


def test_the_connector_series_view_reads_lugs_with_contacts(references_api, monkeypatch):
    seen = []
    monkeypatch.setattr(techcards_api, "connector_series", lambda records, coax: seen.extend(records) or [])
    techcards_api.connector_series_view(_seed(references_api), article="")
    assert {row["Series"] for row in seen} == {"НШВИ 0.5", "XHP"}


def test_the_warehouse_article_info_finds_a_lug(stand, references_api):
    _seed(references_api)  # as the references admin (set last), then the article is asked by the storekeeper
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="warehouse@example.test", roles=frozenset({"receiver"}), permissions={"warehouse": "edit"})
    client, _sessions = stand
    found = client.get("/api/stock/article-info", params={"q": "НШВИ 0.5-8"})
    assert found.status_code == 200, found.text
    assert found.json()["info"]["item_type"] == "Наконечник"


def test_td_auto_names_a_lug_of_the_card(references_api, monkeypatch):
    factory = _seed(references_api)
    named = {}

    def views(uow, cards, identify, lots):
        named["lug"] = identify("НШВИ 0.5-8")
        return [{}]
    monkeypatch.setattr(td_auto, "card_views", views)
    with factory() as uow:
        td_auto.snapshot(None, uow.session, None)
    assert named["lug"] == "Наконечник НШВИ"


def test_a_scanned_article_snaps_to_a_lug_and_a_cable_of_their_bases(references_api):
    known = [article for article, _ in known_articles(None, None, _seed(references_api))]
    assert {"НШВИ 0.5", "RG-316", "LiYCY 2x0,34", "МГТФ 0,35"} <= set(known)


def test_the_lot_names_catalog_knows_the_maker_of_a_lug(references_api):
    catalog = Catalog(reference_records(_seed(references_api)))
    assert catalog.lookup("НШВИ 0.5-8", ())["manufacturer"].value == "КВТ"


def test_the_generator_refs_carry_lugs_and_every_cable(references_api):
    class _Cards:
        def __enter__(self):
            self.operation_cards = type("Cards", (), {"list": staticmethod(lambda: [])})()
            return self

        def __exit__(self, *exc):
            return None
    refs = generator_refs(_Cards, _seed(references_api))
    assert {row["fields"]["Series"] for row in refs["ter"]} == {"НШВИ 0.5", "XHP"}
    assert {(row["cable_kind"], row["fields"]["Марка"]) for row in refs["prov"]} == {
        ("coax", "RG-316"), ("multi", "LiYCY 2x0,34"), ("wire", "МГТФ 0,35")}


def test_the_etk_build_reads_lugs_and_every_cable(references_api):
    factory = _seed(references_api)
    refs = load_refs(factory)
    assert {row["Series"] for row in refs.terminal_rows} == {"НШВИ 0.5", "XHP"}
    assert [row["Марка"] for row in refs.wire_rows] == ["LiYCY 2x0,34", "RG-316", "МГТФ 0,35"]
    _rows, _cards, wires = load_coax_refs(factory)
    assert "RG-316" in {row["Марка"] for row in wires}


def test_the_harness_catalog_tables_read_the_families(references_api):
    _metadata, tables = reference_tables(_seed(references_api))
    assert {row["Series"] for row in tables["ter"]} == {"НШВИ 0.5", "XHP"}
    assert {row["Марка"] for row in tables["prov"]} == {"RG-316", "LiYCY 2x0,34", "МГТФ 0,35"}


def test_the_cable_scripts_read_every_cable_base(references_api):
    factory = _seed(references_api)
    assert cable_series.run(factory(), ACTOR, NOW, apply=False, series=[]).total == 3
    moved = wire_standard_factory.run(factory(), ACTOR, NOW, apply=False)
    assert [record.display_text for record, _ in moved.rows] == ["LiYCY 2x0,34"]
    split = wire_standard_makers.run(factory(), ACTOR, NOW, apply=False)
    assert split.total == 3 and "LiYCY 2x0,34" in {record.display_text for record, _ in split.rows}
