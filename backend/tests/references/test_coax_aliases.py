"""Коаксиальные кабели: алиасы и полное название (owner 12.10.2026). A cable is found by its short mark, a supplier's
full name or article; the generator and the harness catalogue get the same D1–D3 through any of them; the БД.ПРОВ
coax cells are rewritten once in the words of the coax vocabulary."""

from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.references import load_cables
from app.modules.references.application import (
    apply_coax_vocabulary, cable_catalog, coax_dia_rows, import_coax_cards, import_coax_names,
)
from app.modules.references.cable_structure import (
    CoaxAlias, CoaxNames, alias_dia_rows, card_for, cards_from_seed, names_from_articles, states_materials,
    validate_aliases, vocabulary_cells, vocabulary_changes,
)
from app.modules.references.cables import CONNECTOR_FIELDS, CableKind, compatible_connectors, connector_cable
from app.modules.references.domain import ReferenceValidationError, TechOperationTab, cable_key
from app.modules.references.infrastructure import CoaxCableAliasRow, SqlAlchemyReferencesUnitOfWork
from app.modules.references.load_cables import ARTICLES_FILE, DEFAULT_FILE, VOCABULARY_FILE, read_seed
from app.modules.references.load_mirror import ACTOR, BUNDLED_MIRROR, load_mirror
from app.modules.techcards.harness_catalog import COAX_CABLES, build_harness_catalog
from app.modules.techcards.harness_catalog_api import reference_tables
from app.shared.clock import moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows.techcards_all import generator_refs
from tests.references.test_cable_structure import _cable
from tests.references.test_load_mirror import NOW, sessions, uow  # noqa: F401  (fixtures)


def _offer(article, full_name, supplier="Крокс", family_only=False, variant=False):
    return {"supplier": supplier, "article": article, "full_name": full_name, "url": f"https://x/{article}",
            "family_only": family_only, **({"variant": True} if variant else {}), "note": ""}


ARTICLES = {"cables": [
    {"mark": "RG-316", "aliases": ["RG316"], "offers": [
        _offer("1951", "Кабель RG-316 50 Ом", "ГСМ Репитеры"),
        _offer("387", "Кабель коаксиальный RG-316 B/U CCS", family_only=True),
    ]},
    {"mark": "RF 1.37", "aliases": ["RF1.37", "RG1.37"], "offers": [_offer("9001", "Кабель RF 1.37 серый")]},
    {"mark": "RG-58 A/U", "aliases": ["RG58 A/U"], "offers": [_offer("0448", "Кабель RG-58 A/U (черный)", "ГСМ Репитеры")]},
    # an article of the family listed under both cables; only one has a card — it is that card's
    {"mark": "RG-58", "aliases": ["RG58"], "offers": [_offer("384", "Кабель коаксиальный RG-58 50 Ом")]},
    {"mark": "RG-58 C/U", "aliases": [], "offers": [_offer("384", "Кабель коаксиальный RG-58 50 Ом")]},
]}


def _cards(*names):
    return cards_from_seed([_cable(name) for name in names])


def _keys(names: CoaxNames):
    return [(alias.alias, alias.kind) for alias in names.aliases]


# --- pure rules ---

def test_supplier_file_gives_each_card_its_full_name_and_aliases():
    plan = names_from_articles(ARTICLES, _cards("RG-316", "RF 1.37", "RG-58", "LMR-400"))
    rg316 = plan.names["rg316"]
    assert rg316.full_name == "Кабель RG-316 50 Ом"
    # «RG316» is the card's own key — a repeat, not an alias; the family-only offer is never one
    assert _keys(rg316) == [("Кабель RG-316 50 Ом", "full_name"), ("1951", "supplier_article")]
    assert rg316.aliases[1].supplier == "ГСМ Репитеры" and rg316.aliases[1].url == "https://x/1951"
    assert ("RG1.37", "mark") in _keys(plan.names["rf1.37"])
    assert [alias.alias for alias in plan.names["rg58"].aliases] == ["Кабель коаксиальный RG-58 50 Ом", "384"]
    assert plan.names["lmr400"] == CoaxNames()  # a card the file does not name has none
    # variants are never merged into the family card
    assert plan.no_card == ("RG-58 A/U", "RG-58 C/U") and plan.family_only == 1 and plan.refused == ()


def test_an_offer_naming_its_materials_is_another_cable_never_an_alias_or_the_full_name():
    # owner 19.10.2026: «ты разные артикулы кабелей записал в одно, там же разные материалы»
    document = {"cables": [{"mark": "5D-FB", "aliases": ["5D FB"], "offers": [
        _offer("3521", "Кабель коаксиальный 5D-FB CCA PE AL PE (для внешней прокладки)", variant=True),
        _offer("0912", "Кабель 5D-FB CU PVC (черный)", "ГСМ Репитеры", variant=True),
        _offer("4653", "Кабель VEGATEL 5D-FB Cu (ГОСТ, синий)", "ГСМ Репитеры", variant=True),
        _offer("7000", "Кабель 5D-FB (черный)", "ГСМ Репитеры"),
    ]}]}
    plan = names_from_articles(document, _cards("5D-FB"))
    names = plan.names["5dfb"]
    assert names.full_name == "Кабель 5D-FB (черный)"
    assert _keys(names) == [("Кабель 5D-FB (черный)", "full_name"), ("7000", "supplier_article")]  # «5D FB» is its own key
    assert plan.variants == 3 and plan.refused == ()
    only_variants = {"cables": [{**document["cables"][0], "offers": document["cables"][0]["offers"][:3]}]}
    assert names_from_articles(only_variants, _cards("5D-FB")).names["5dfb"] == CoaxNames()


@pytest.mark.parametrize("name, marks, stated", [
    ("Кабель коаксиальный 5D-FB CCA PE AL PE (для внешней прокладки)", ["5D-FB", "5D FB"], True),
    ("Кабель 5D-FB CU LSZH FR (черный)", ["5D-FB"], True),
    ("Кабель VEGATEL 5D-FB Cu (ГОСТ, синий)", ["5D-FB"], True),
    ("Кабель VEGATEL 5D-FB Cu (ГОСТ, синий)", ["5D-FB Cu", "5D-FB CU"], False),  # the mark itself says Cu
    ("Кабель коаксиальный 10D-FB CCA PE AL PE (для внешней прокладки)", ["10D-FB PE", "10D-FB CCA PE AL PE"], False),
    ("Кабель коаксиальный RG-174 Многожильный CU Solid PE CU PVC, до 1000 МГц", ["RG-174"], True),
    ("Кабель коаксиальный RF1.37 Многожильный SPC PTFE CU FEP", ["RF 1.37", "RF1.37"], True),
    ("Кабель RG-213 C/U PVC", ["RG213 C/U", "RG-213 C/U"], True),
    ("Кабель RG-316 50 Ом", ["RG-316", "RG316"], False),
    ("Кабель RG-402 (CF-141-50-FEP)", ["RG-402"], False),  # FEP inside a model code, not a jacket
    ("Кабель коаксиальный полужесткий RG-405 CF086-50", ["RG-405"], False),
    ("Кабель RG-6U 75 Ом", ["RG-6/U", "RG6/U", "RG-6U"], False),
])
def test_states_materials_reads_whole_words_beyond_the_mark(name, marks, stated):
    assert states_materials(name, marks) is stated


def test_the_supplier_file_marks_every_offer_that_names_its_materials():
    for entry in read_seed(ARTICLES_FILE)["cables"]:
        marks = [entry["mark"], *entry.get("aliases", [])]
        for offer in entry["offers"]:
            assert bool(offer.get("variant")) is states_materials(offer["full_name"], marks), (entry["mark"], offer["full_name"])


def test_an_alias_of_two_cards_or_named_as_another_card_is_refused():
    document = {"cables": [
        {"mark": "5D-FB", "aliases": ["8D-FB"], "offers": [_offer("3528", "Кабель 5D-FB Cu")]},
        {"mark": "5D-FB Cu", "aliases": [], "offers": [_offer("3528", "Кабель 5D-FB Cu")]},
    ]}
    alone = names_from_articles(document, _cards("5D-FB", "8D-FB"))
    assert alone.refused == ("«8D-FB»: это название другого кабеля «8D-FB»",)
    assert [alias.alias for alias in alone.names["5dfb"].aliases] == ["Кабель 5D-FB Cu", "3528"]
    both = names_from_articles(document, _cards("5D-FB", "8D-FB", "5D-FB Cu"))
    assert "«3528»: так названы разные кабели: 5D-FB, 5D-FB Cu" in both.refused
    assert both.names["5dfb"].aliases == () and both.names["5dfbcu"].aliases == ()


def test_validation_refuses_a_duplicate_alias_before_the_base_does():
    cards = _cards("RG-316", "RG-58")
    twice = CoaxAlias("1951", "1951", "supplier_article", "Крокс")
    with pytest.raises(ReferenceValidationError, match="уже есть у кабеля"):
        validate_aliases({"rg316": CoaxNames("", (twice,)), "rg58": CoaxNames("", (twice,))}, cards)
    with pytest.raises(ReferenceValidationError, match="другого кабеля"):
        validate_aliases({"rg316": CoaxNames("", (CoaxAlias("RG58", "rg58", "mark"),))}, cards)


def test_card_for_finds_a_card_by_alias_exactly_and_keeps_the_family_as_a_suggestion():
    cards = _cards("RG-316", "RG-58")
    rg316 = replace(cards[0], aliases=(CoaxAlias("Кабель RG-316 50 Ом", cable_key("Кабель RG-316 50 Ом"), "full_name"),))
    index = {rg316.name_key: rg316, cards[1].name_key: cards[1]}
    assert card_for(cable_key("Кабель RG-316 50 Ом"), index) == (rg316, True)
    assert card_for("rg58c/u", index) == (cards[1], False)


def test_alias_rows_never_shadow_a_row_of_its_own():
    rg316 = _cards("RG-316")[0]
    rg316 = replace(rg316, aliases=(
        CoaxAlias("RG316", "rg316", "mark"), CoaxAlias("1951", "1951", "supplier_article", "ГСМ"),
        CoaxAlias("RG-316 old", "rg316old", "mark")))
    index = {"rg316": {"Кабель": "RG-316", "D1": "0,5", "D2": "1,48", "D3": "1,95"},
             "rg316old": {"Кабель": "RG-316 old", "D1": "9", "D2": "9", "D3": "9"}}
    assert alias_dia_rows(index, [rg316]) == [{"Кабель": "1951", "D1": "0,5", "D2": "1,48", "D3": "1,95"}]
    # a card without a row of its name takes the D1–D3 of the first alias that has one, its name gets a row too
    only_alias = {"rg316old": index["rg316old"]}
    assert [row["Кабель"] for row in alias_dia_rows(only_alias, [rg316])] == ["RG-316", "RG316", "1951"]


def test_vocabulary_cells_join_the_insulation_parts_and_skip_empty_entries():
    document = {"map": [
        {"source": "card", "cable": "RG-316", "field": "layer:Жила", "old": "a", "new": "b"},
        {"source": "БД.ПРОВ", "cable": "RG316", "field": "Изоляция", "part": "диэлектрик", "old": "ПТФЭ (диэлектрик)", "new": "ПТФЭ"},
        {"source": "БД.ПРОВ", "cable": "RG316", "field": "Изоляция", "part": "оболочка", "old": "ФЭП (оболочка)", "new": "оболочка ФЭП"},
        {"source": "БД.ПРОВ", "cable": "RG316", "field": "Экран", "old": "", "new": ""},
    ]}
    [cell] = vocabulary_cells(document)
    assert (cell.field, cell.old, cell.new) == ("Изоляция", "ПТФЭ (диэлектрик) / ФЭП (оболочка)", "ПТФЭ / оболочка ФЭП")
    assert vocabulary_changes({"Изоляция": "ПТФЭ  (диэлектрик) / ФЭП (оболочка)"}, [cell]) == ({"Изоляция": "ПТФЭ / оболочка ФЭП"}, [])
    assert vocabulary_changes({"Изоляция": "ПТФЭ / оболочка ФЭП"}, [cell]) == ({}, [])
    changes, problems = vocabulary_changes({"Изоляция": "фторопласт"}, [cell])
    assert changes == {} and "не изменено" in problems[0]


# --- the committed files over the bundled mirror ---

def _load_all(uow):
    load_mirror(BUNDLED_MIRROR, uow, dry_run=False, now=NOW)
    import_coax_cards(read_seed(DEFAULT_FILE), ACTOR, NOW, uow)
    return import_coax_names(read_seed(ARTICLES_FILE), ACTOR, NOW, uow)


def _generator_lookup(rows):
    """`coaxDiaFromRecords`: the first value is the cable, the last row of a key wins."""
    found = {}
    for row in rows:
        cable = str(next(iter(row.values()), "")).strip()
        if cable:
            found[cable_key(cable)] = (row.get("D1", ""), row.get("D2", ""), row.get("D3", ""))
    return found


def test_every_old_key_finds_the_same_d_and_every_alias_finds_its_cable(uow):  # noqa: F811
    load_mirror(BUNDLED_MIRROR, uow, dry_run=False, now=NOW)
    with uow:
        before = _generator_lookup([record.fields for record in uow.tech_operations.list(
            tab=TechOperationTab.KAB_COAX, query="", limit=10_000)])
    report = _load_all(uow)
    with uow:
        rows = coax_dia_rows(uow)
        cards = {card.name: card for card in uow.cable_structure.cards()}
    after = _generator_lookup(rows)
    assert {key: after[key] for key in before} == before
    assert report["refused"] == [] and report["aliases"] > 20
    rg316 = before["rg316"]
    assert cards["RG-316"].full_name == "Кабель RG-316 50 Ом"
    assert after[cable_key("Кабель RG-316 50 Ом")] == after[cable_key("1951")] == rg316  # full name, article
    assert cable_key("Кабель коаксиальный RG-316 B/U CCS Solid PTFE CU FEP") not in after  # a family-only offer
    assert after[cable_key("RG1.37")] == before["rf1.37"]  # a БД.ПРОВ mark of another spelling
    # «RG-58 A/U» has no card: it is not given the D1–D3 of the RG-58 family
    assert cable_key("RG-58 A/U") not in after and "RG-58 A/U" in report["no_card"]
    # owner 19.10: the supplier's «5D-FB CCA PE AL PE», «5D-FB CU PVC» … are other cables, not names of the card «5D-FB»
    assert cards["5D-FB"].full_name == "" and cards["5D-FB"].aliases == ()
    assert cable_key("Кабель 5D-FB CU PVC (черный)") not in after and cable_key("3521") not in after
    assert report["variants"] > 50


def test_second_load_changes_nothing(uow):  # noqa: F811
    _load_all(uow)
    cards = import_coax_cards(read_seed(DEFAULT_FILE), ACTOR, NOW, uow)
    names = import_coax_names(read_seed(ARTICLES_FILE), ACTOR, NOW, uow)
    assert (cards["created"], cards["updated"], cards["removed"]) == (0, 0, 0)
    assert (names["cables_changed"], names["aliases_added"], names["aliases_removed"], names["full_names_changed"]) == (0, 0, 0, 0)
    # a card load keeps the names the supplier file gave, and a changed card is rewritten
    changed = read_seed(DEFAULT_FILE)
    changed["cables"][0] = {**changed["cables"][0], "notes_ru": "другое"}
    assert import_coax_cards(changed, ACTOR, NOW, uow)["updated"] == 1
    with uow:
        assert {card.name: card.full_name for card in uow.cable_structure.cards()}["RG-316"] == "Кабель RG-316 50 Ом"


def test_a_duplicate_alias_is_refused_by_the_base(uow):  # noqa: F811
    import_coax_cards({"cables": [_cable("RG-316"), _cable("RG-58")]}, ACTOR, NOW, uow)
    import_coax_names(ARTICLES, ACTOR, NOW, uow)
    with uow:
        rg58 = next(card for card in uow.cable_structure.cards() if card.name == "RG-58")
        uow.session.add(CoaxCableAliasRow(id=uuid4(), cable_id=rg58.id, alias="1951", alias_key="1951",
                                          kind="supplier_article", supplier="Крокс"))
        with pytest.raises(IntegrityError):
            uow.session.flush()


def test_aliases_move_between_cards_and_go_with_their_card(uow):  # noqa: F811
    import_coax_cards({"cables": [_cable("RG-316"), _cable("RG-58")]}, ACTOR, NOW, uow)
    import_coax_names(ARTICLES, ACTOR, NOW, uow)
    moved = {"cables": [{"mark": "RG-58", "aliases": [], "offers": [_offer("1951", "Кабель RG-316 50 Ом")]}]}
    report = import_coax_names(moved, ACTOR, NOW, uow)
    with uow:
        cards = {card.name: card for card in uow.cable_structure.cards()}
    assert [alias.alias for alias in cards["RG-58"].aliases] == ["Кабель RG-316 50 Ом", "1951"] and cards["RG-316"].aliases == ()
    assert report["full_names_changed"] == 2
    import_coax_names(ARTICLES, ACTOR, NOW, uow)  # back to RG-316
    import_coax_cards({"cables": [_cable("RG-58")]}, ACTOR, NOW, uow)  # RG-316 leaves the file with its names
    with uow:
        assert {row.alias for row in uow.session.query(CoaxCableAliasRow)} == {"Кабель коаксиальный RG-58 50 Ом", "384"}
    assert import_coax_names(moved, ACTOR, NOW, uow)["aliases_added"] == 2  # its key is free again


def test_dry_run_of_the_names_writes_nothing(uow):  # noqa: F811
    import_coax_cards({"cables": [_cable("RG-316")]}, ACTOR, NOW, uow)
    report = import_coax_names(ARTICLES, ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(uow.session_factory), dry_run=True)
    assert report["aliases_added"] == 2
    with uow:
        assert uow.cable_structure.cards()[0].aliases == ()


def test_generator_and_harness_read_d_through_an_alias(uow):  # noqa: F811
    _load_all(uow)
    factory = lambda: SqlAlchemyReferencesUnitOfWork(uow.session_factory)  # noqa: E731

    class Templates:
        def __enter__(self):
            self.operation_cards = type("Cards", (), {"list": lambda self: []})()
            return self

        def __exit__(self, *exc):
            return False

    refs = generator_refs(Templates, factory)
    assert _generator_lookup(refs["kab_coax"])["1951"] == _generator_lookup(refs["kab_coax"])["rg316"]
    _metadata, tables = reference_tables(factory)
    catalog = build_harness_catalog(tables, captured_utc="")
    coax = next(snapshot for snapshot in catalog["snapshots"] if snapshot["sourceId"] == COAX_CABLES)
    by_key = {record["sourceKey"]: record["payload"] for record in coax["records"]}
    assert by_key["Кабель RG-316 50 Ом"]["layers"] == by_key["RG-316"]["layers"]


def test_cables_screen_shows_the_card_of_an_alias_mark(uow):  # noqa: F811
    _load_all(uow)
    row = next(row for row in cable_catalog(uow).rows if row.cable == "RG1.37")
    assert row.card.name == "RF 1.37" and row.card_exact


# --- «Совместимые разъёмы»: БД.КОАКС rows made for the cable (owner 19.10.2026) ---

def test_compatible_connectors_come_from_the_coax_base_by_mark_card_and_alias(uow):  # noqa: F811
    _load_all(uow)
    catalog = cable_catalog(uow)
    with uow:
        coax = uow.tech_operations.list(tab=TechOperationTab.COAX, query="", limit=10_000)
    wires = lambda key: {record.id for record in coax if cable_key(connector_cable(record)) == key}  # noqa: E731
    row = {item.cable: item for item in catalog.rows}
    assert wires("5dfb") and {item.id for item in compatible_connectors(row["5D-FB"], catalog.connectors)} == wires("5dfb")
    # «RG316» of БД.ПРОВ finds the rows written «RG-316» (one key), and those of the card's other spellings
    rg316 = compatible_connectors(row["RG316"], catalog.connectors)
    assert {item.id for item in rg316} == wires("rg316") and len(rg316) == len({item.id for item in rg316})
    # a family card lends nothing; a row of another list has none
    assert compatible_connectors(replace(row["RG316"], cable="RG-316 Z", card_exact=False), catalog.connectors) == []
    wire = next(item for item in catalog.rows if item.kind == CableKind.WIRE)
    assert compatible_connectors(wire, {wire.key: coax}) == []
    by_alias = replace(row["RG316"], cable="Кабель RG-316 50 Ом")  # a mark the card knows as its alias
    assert {item.id for item in compatible_connectors(by_alias, catalog.connectors)} == wires("rg316")


# --- `--coax-vocabulary` ---

def test_vocabulary_rewrites_the_coax_cells_once_and_a_mirror_reload_keeps_them(uow):  # noqa: F811
    load_mirror(BUNDLED_MIRROR, uow, dry_run=False, now=NOW)
    document = read_seed(VOCABULARY_FILE)
    dry = apply_coax_vocabulary(document, ACTOR, NOW, SqlAlchemyReferencesUnitOfWork(uow.session_factory), dry_run=True)
    first = apply_coax_vocabulary(document, ACTOR, NOW, uow)
    again = apply_coax_vocabulary(document, ACTOR, NOW, uow)
    assert dry["rewritten"] == first["rewritten"] and first["problems"] == [] and first["missing"] == []
    assert "RG316, Изоляция: «ПТФЭ (диэлектрик) / ФЭП (оболочка)» → «ПТФЭ / оболочка ФЭП»" in first["rewritten"]
    assert again["rewritten"] == [] and again["problems"] == []
    load_mirror(BUNDLED_MIRROR, uow, dry_run=False, now=NOW)
    with uow:
        rg316 = next(record for record in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="RG316", limit=50)
                     if record.fields.get("Марка") == "RG316")
    assert rg316.edited_in_erp and rg316.fields["Экран"] == "оплётка медь посеребрённая"
    assert rg316.fields["Тип провода"] == "коаксиальный"


def test_load_cables_cli_loads_names_and_applies_the_vocabulary_on_request(monkeypatch, tmp_path, capsys):
    database = tmp_path / "erp.db"
    url = f"sqlite+pysqlite:///{database.as_posix()}"
    from sqlalchemy import create_engine

    from app.shared.persistence import create_schema
    create_schema(create_engine(url))
    monkeypatch.setenv("ERP_DATABASE_URL", url)
    assert load_cables.main([]) == 0
    first = capsys.readouterr().out
    assert load_cables.main([]) == 0
    second = capsys.readouterr().out
    assert "Названия и артикулы коаксиальных кабелей (записано)" in first and "добавлено 0, убрано 0" in second
    assert load_cables.main(["--coax-vocabulary", "--dry-run"]) == 0
    assert "марок нет в БД.ПРОВ" in capsys.readouterr().out  # no mirror here: every mark is listed


# --- HTTP ---

@pytest.fixture()
def api(sessions):  # noqa: F811
    app.state.references_uow_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=ACTOR, email="admin@example.test", roles=frozenset({"admin"}), permissions={"erpref": "edit"})
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 10, 12, 10, 0)
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)
    app.dependency_overrides.pop(moscow_now, None)
    del app.state.references_uow_factory


def test_api_import_loads_names_and_coax_dia_serves_alias_rows(sessions, api):  # noqa: F811
    load_mirror(BUNDLED_MIRROR, SqlAlchemyReferencesUnitOfWork(sessions), dry_run=False, now=NOW)
    imported = api.post("/api/references/coax-cards/import").json()
    assert imported["names"]["aliases"] > 20 and imported["names"]["refused"] == []
    rows = api.get("/api/references/coax-dia").json()["records"]
    by_name = {row["fields"]["Кабель"]: row["fields"] for row in rows}
    assert by_name["1951"]["D1"] == by_name["RG-316"]["D1"]
    listed = api.get("/api/references/cables").json()["rows"]
    # one request: every coax row carries its БД.КОАКС connectors, only the cells the card shows
    fdfb = next(row for row in listed if row["cable"] == "5D-FB")["connectors"]
    assert fdfb and {item["fields"]["coaxWire"] for item in fdfb} == {"5D-FB"}
    assert all(set(item["fields"]) <= set(CONNECTOR_FIELDS) and item["fields"].get("coaxType") for item in fdfb)
    assert all("connectors" not in row for row in listed if row["kind"] != "coax")
    card = next(row for row in listed if row["cable"] == "RG316")["card"]
    assert card["full_name"] == "Кабель RG-316 50 Ом"
    assert {"alias": "1951", "kind": "supplier_article", "supplier": "ГСМ Репитеры"}.items() <= next(
        alias for alias in card["aliases"] if alias["alias"] == "1951").items()
