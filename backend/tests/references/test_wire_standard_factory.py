"""БД.ПРОВ: завод из «Стандарт» → «Производители», только пока в ячейке известный текст."""

from datetime import datetime, timezone

from app.modules.references.domain import TechOperationTab
from app.modules.references.load_mirror import default_source, read_mirror
from app.modules.references.wire_makers import split_fields
from app.workflows.wire_standard_factory import KNOWN, format_plan, moved, run
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)

BASE = "/api/references/tech-operations"


def test_every_known_text_is_what_the_split_left_on_the_mirror():
    standards = {split_fields(item.fields)["Стандарт"] for item in read_mirror(default_source()).tables["prov"]}
    assert set(KNOWN) <= standards
    for text, (standard, factories) in KNOWN.items():
        assert "(" not in standard and factories and all(name not in standard for name in factories)


def test_only_the_known_text_moves_and_makers_merge():
    fields = moved({"Стандарт": " ГОСТ 7399-97 (BREKS, Брянск) ", "Производители": "breks (брянск), Электрокабель"})
    assert fields["Стандарт"] == "ГОСТ 7399-97" and fields["Производители"] == "breks (брянск), Электрокабель"
    fields = moved({"Стандарт": "ГОСТ-based (Камкабель/ОКБ КП)", "Производители": ""})
    assert fields["Стандарт"] == "ГОСТ" and fields["Производители"] == "Камкабель, ОКБ КП"
    assert moved({"Стандарт": "ГОСТ 7399-97 (BREKS)", "Производители": ""}) is None  # edited by hand — left alone
    assert moved({"Производители": "Паритет"}) is None


def _wire(client, mark, **cells):
    assert client.post(BASE, json={"tab": "prov", "fields": {"Марка": mark, **cells}}).status_code == 201


def _records(client):
    return {item["fields"]["Марка"]: item for item in client.get(BASE, params={"tab": "prov"}).json()["records"]}


def test_dry_run_apply_and_idempotence(references_api):
    _wire(references_api, "ШВВП 2x0,5", **{"Стандарт": "ГОСТ (Breks, Брянск)", "Производители": ""})
    _wire(references_api, "КСПВГ 4x0,50", **{"Стандарт": "ГОСТ 3581-011-39793330-2009 (Паритет)", "Производители": "Паритет"})
    _wire(references_api, "ШВВП 2x0,75", **{"Стандарт": "ГОСТ (Калужский завод)", "Производители": ""})  # not the known text
    factory = references_api.app.state.references_uow_factory
    actor, now = Actor(name="Тест", department="Цех"), datetime(2026, 10, 13, tzinfo=timezone.utc)

    plan = run(factory(), actor, now, apply=False)
    text = format_plan(plan, applied=False)
    assert len(plan.rows) == 2 and "ШВВП 2x0,5: Стандарт «ГОСТ (Breks, Брянск)» → «ГОСТ»" in text and "--apply" in text
    assert _records(references_api)["ШВВП 2x0,5"]["fields"]["Стандарт"] == "ГОСТ (Breks, Брянск)"  # dry run wrote nothing

    run(factory(), actor, now, apply=True)
    records = _records(references_api)
    assert records["ШВВП 2x0,5"]["fields"]["Стандарт"] == "ГОСТ"
    assert records["ШВВП 2x0,5"]["fields"]["Производители"] == "Breks (Брянск)"
    assert records["КСПВГ 4x0,50"]["fields"]["Производители"] == "Паритет"  # no repeat
    with factory() as uow:
        edited = {item.display_text: item.edited_in_erp for item in uow.tech_operations.list(tab=TechOperationTab.WIRE, query="", limit=100)}
    assert edited["ШВВП 2x0,5"] and edited["КСПВГ 4x0,50"]
    assert records["ШВВП 2x0,75"]["fields"]["Стандарт"] == "ГОСТ (Калужский завод)"
    again = run(factory(), actor, now, apply=True)
    assert again.empty and "Делать нечего" in format_plan(again, applied=True)
