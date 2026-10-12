"""БД.ПРОВ: «Стандарт / производитель» → «Стандарт» (один) + «Производители» (список)."""

from datetime import datetime, timezone

import pytest

from app.modules.references.wire_makers import join_makers, parse_makers, split_fields, split_legacy
from app.workflows.wire_standard_makers import format_plan, run
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)

BASE = "/api/references/tech-operations"


@pytest.mark.parametrize("value, standard, makers", [
    ("ГОСТ 17515-72", "ГОСТ 17515-72", []),
    ("ТУ 16-505.185-71", "ТУ 16-505.185-71", []),
    ("UL2464", "UL2464", []),
    ("UL AWM Style 1015, PVC, 600V/105°C", "UL AWM Style 1015", ["PVC", "600V/105°C"]),
    ("Паритет (Подольск), ГОСТ", "ГОСТ", ["Паритет (Подольск)"]),
    ("Breks (Брянск), ГОСТ", "ГОСТ", ["Breks (Брянск)"]),
    ("ГОСТ 7399-97 (BREKS, Брянск)", "ГОСТ 7399-97 (BREKS, Брянск)", []),
    ("ГОСТ 31947-2012, ТУ 16-705.501-2010", "ГОСТ 31947-2012; ТУ 16-705.501-2010", []),
    ("ГОСТ 31565-2012; VEGATEL RG-58 A/U Cu", "ГОСТ 31565-2012", ["VEGATEL RG-58 A/U Cu"]),
    ("генерик (китайское производство), prodiel.ru", "", ["генерик (китайское производство)", "prodiel.ru"]),
    ("Cabeus / ParLan; Teldor", "", ["Cabeus", "ParLan", "Teldor"]),
    ("Cabeus, cabeus", "", ["Cabeus"]),
    ("LAPP/Helukabel PAAR-TRONIC-CY 4x2x0,5", "", ["LAPP", "Helukabel PAAR-TRONIC-CY 4x2x0,5"]),
    ("ГОСТ 3574-010 (Паритет ParLan); также IEC 61156-6, TIA/EIA-568-A", "ГОСТ 3574-010 (Паритет ParLan); IEC 61156-6; TIA/EIA-568-A", []),
    ("ParLan U/UTP Cat5e 4x2x0,52 (Паритет)", "", ["ParLan U/UTP Cat5e 4x2x0,52 (Паритет)"]),
    ("Cabeus; ГОСТ 31565, ТР ТС 004/2011", "ГОСТ 31565; ТР ТС 004/2011", ["Cabeus"]),
    ("UL2464 (маркировка продавца)", "UL2464 (маркировка продавца)", []),
    ("OCT 11 0398-84", "OCT 11 0398-84", []),
    ("IEC 61156-6", "IEC 61156-6", []),
    ("DIN VDE 0812", "DIN VDE 0812", []),
    ("MIL-W-16878", "MIL-W-16878", []),
    ("JIS C 3306", "JIS C 3306", []),
    ("Reliable Ensign", "", ["Reliable Ensign"]),
    ("", "", []),
])
def test_legacy_value_split_rule(value, standard, makers):
    assert split_legacy(value) == (standard, makers)


def test_makers_list_round_trip():
    assert parse_makers("LAPP, Helukabel; Паритет (Подольск, РФ)") == ["LAPP", "Helukabel", "Паритет (Подольск, РФ)"]
    assert join_makers(["LAPP", " Helukabel ", "lapp", ""]) == "LAPP, Helukabel"
    assert parse_makers(join_makers(["Cabeus", "ParLan"])) == ["Cabeus", "ParLan"]


def test_split_fields_keeps_order_and_own_values():
    fields = {"Марка": "M", "Стандарт / производитель": "Cabeus, ГОСТ 1-2", "Цвета": "белый"}
    assert split_fields(fields) == {"Марка": "M", "Стандарт": "ГОСТ 1-2", "Производители": "Cabeus", "Цвета": "белый", "Категория": ""}
    kept = {"Марка": "M", "Стандарт / производитель": "Cabeus, ГОСТ 1-2", "Стандарт": "ТУ 5", "Производители": "LAPP"}
    assert split_fields(kept)["Стандарт"] == "ТУ 5" and split_fields(kept)["Производители"] == "LAPP, Cabeus"
    # the cells the record lacks are added empty (the server refuses an edit of a cell a record has not)
    assert split_fields({"Марка": "M"}) == {"Марка": "M", "Стандарт": "", "Производители": "", "Категория": ""}


def _wire(client, mark, **cells):
    response = client.post(BASE, json={"tab": "prov", "fields": {"Марка": mark, **cells}})
    assert response.status_code == 201


def _fields(client):
    return {item["fields"]["Марка"]: item["fields"] for item in client.get(BASE, params={"tab": "prov"}).json()["records"]}


def test_command_dry_run_apply_and_idempotence(references_api):
    _wire(references_api, "A", **{"Стандарт / производитель": "Паритет (Подольск), ГОСТ 7399-97"})
    _wire(references_api, "B", **{"Стандарт / производитель": "Cabeus"})
    _wire(references_api, "C", **{"Стандарт": "ТУ 1", "Производители": "LAPP"})
    uow_factory = references_api.app.state.references_uow_factory
    actor, now = Actor(name="Тест", department="Цех"), datetime(2026, 10, 12, tzinfo=timezone.utc)

    plan = run(uow_factory(), actor, now, apply=False)
    text = format_plan(plan, applied=False)
    assert len(plan.rows) == 3 and "Стандарт «ГОСТ 7399-97» · Производители «Паритет (Подольск)»" in text
    assert "Стандарт / производитель" in _fields(references_api)["A"]  # a dry run writes nothing

    run(uow_factory(), actor, now, apply=True)
    fields = _fields(references_api)
    assert fields["A"]["Стандарт"] == "ГОСТ 7399-97" and fields["A"]["Производители"] == "Паритет (Подольск)"
    assert "Стандарт / производитель" not in fields["A"] and fields["B"]["Производители"] == "Cabeus"
    assert fields["C"]["Стандарт"] == "ТУ 1"
    assert run(uow_factory(), actor, now, apply=True).empty  # idempotent
