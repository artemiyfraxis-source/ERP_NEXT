"""«Кабели»: серия наследует общие значения только в пустые ячейки, у каждого значения — источник."""

from datetime import datetime, timezone

import pytest

from app.workflows.cable_series import SeriesFileError, format_plan, inherited, load_series, run, series_of
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)

BASE = "/api/references/tech-operations"
SERIES = load_series()


def test_every_value_of_the_file_has_a_source():
    for item in SERIES:
        assert item["key"]
    with pytest.raises(SeriesFileError):
        from app.workflows.cable_series import _given
        _given({"value": "ПВХ", "source": ""}, "x")


@pytest.mark.parametrize("mark, key", [
    ("LiYCY 4x0,5", "LiYCY"), ("UNITRONIC LiYCY 2x0,34", "LiYCY"), ("H05RN-F 3x0,75", "H05RN-F"), ("Li2YCY 2x0,34", None), ("CCCU-4G", None),
])
def test_series_by_the_start_of_the_mark(mark, key):
    found = series_of(mark, SERIES)
    assert (found["key"] if found else None) == key


def test_inheritance_fills_only_empty_cells_and_matches_the_size():
    item = series_of("LiYCY 2x0,34", SERIES)
    wanted, sources = inherited({"Число жил": "2", "Сечение, мм2": "0,34", "Изоляция": "", "Экран": "сетка", "Производители": "Elektra",
                                 "Наружный диаметр, мм": ""}, item)
    assert wanted["Изоляция"] == "ПВХ" and wanted["Напряжение, В"] == "250" and wanted["Наружный диаметр, мм"] == "4,9"
    assert wanted["AWG"] == "22" and wanted["Производители"] == "Elektra, LAPP" and "Экран" not in wanted
    assert all(url.startswith("http") for url in sources) and sources
    other, _ = inherited({"Число жил": "2", "Сечение, мм2": "0,5", "Наружный диаметр, мм": "9"}, item)
    assert "Наружный диаметр, мм" not in other and "AWG" not in other  # another size, and a filled cell


def _wire(client, mark, **cells):
    assert client.post(BASE, json={"tab": "prov", "fields": {"Марка": mark, **cells}}).status_code == 201


def _fields(client):
    return {item["fields"]["Марка"]: item["fields"] for item in client.get(BASE, params={"tab": "prov"}).json()["records"]}


def test_dry_run_apply_and_idempotence(references_api):
    _wire(references_api, "LiYCY 2x0,34", **{"Число жил": "2", "Сечение, мм2": "0,34", "Источник данных": "магазин"})
    _wire(references_api, "LiYCY 4x0,5", **{"Число жил": "4", "Сечение, мм2": "0,5", "Изоляция": "ПВХ-пластикат"})
    _wire(references_api, "Другой кабель")
    factory = references_api.app.state.references_uow_factory
    actor, now = Actor(name="Тест", department="Цех"), datetime(2026, 10, 12, tzinfo=timezone.utc)

    plan = run(factory(), actor, now, apply=False)
    text = format_plan(plan, SERIES, applied=False)
    assert plan.seen == {"LiYCY": 2} and len(plan.rows) == 2 and "Наружный диаметр, мм: «» → «4,9»" in text
    assert "Изоляция" not in _fields(references_api)["LiYCY 2x0,34"] or _fields(references_api)["LiYCY 2x0,34"]["Изоляция"] == ""

    run(factory(), actor, now, apply=True)
    fields = _fields(references_api)
    assert fields["LiYCY 2x0,34"]["Наружный диаметр, мм"] == "4,9" and fields["LiYCY 2x0,34"]["Изоляция"] == "ПВХ"
    assert fields["LiYCY 2x0,34"]["Источник данных"].startswith("магазин; https://")
    assert fields["LiYCY 4x0,5"]["Изоляция"] == "ПВХ-пластикат"  # a filled cell stays
    assert "Изоляция" not in fields["Другой кабель"] or fields["Другой кабель"]["Изоляция"] == ""
    assert run(factory(), actor, now, apply=True).empty
