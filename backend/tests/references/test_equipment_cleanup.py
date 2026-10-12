"""БД.ОБ cleanup: ПР → Оборудование, «.X» rows, БД.ОП references; the loader keeps them from coming back."""

from datetime import datetime, timezone

from app.modules.references.technology import sheet_snapshot_records
from app.workflows.equipment_cleanup import format_plan, run
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)

BASE = "/api/references/tech-operations"


def test_loader_maps_pr_to_equipment_skips_x_rows_and_repoints_operations():
    header = ["№", "Тип", "Инв. номер", "Артикул", "Наменование", "Для базы"]
    sheet = [[""], header,
             ["1", "Оборудование (ПР)", "ОБ.071.1", "T21", "Станция", "T21/Станция"],
             ["2", "Оборудование (ПР)", "ОБ.071.X", "T21(R)", "Станция", "T21(R)/Станция"],
             ["3", "Оборудование", "ОБ.099.X", "BZW", "Стрипер", "BZW/Стрипер"]]
    _headers, records = sheet_snapshot_records("ob", sheet)
    assert [item["displayText"] for item in records] == ["T21/Станция"]
    assert records[0]["obType"] == "Оборудование" and records[0]["exportValues"][1] == "Оборудование"
    _h, ops = sheet_snapshot_records("op", [[""], ["Номер", "Название", "Машина"], ["SDR", "Пайка", "T21(R)/Станция"]])
    assert ops[0]["exportValues"][2] == "T21/Станция"


def _equipment(client, inv, article, name, kind="Оборудование"):
    response = client.post(BASE, json={"tab": "ob", "fields": {
        "Тип": kind, "Инв. номер": inv, "Артикул": article, "Наменование": name}})
    assert response.status_code == 201


def _operation(client, number, **cells):
    assert client.post(BASE, json={"tab": "op", "fields": {"Номер": number, "Название": "Операция", **cells}}).status_code == 201


def test_cleanup_plan_apply_and_idempotence(references_api):
    uow_factory = references_api.app.state.references_uow_factory
    _equipment(references_api, "ОБ.071.1", "T21", "Станция паяльная")
    _equipment(references_api, "ОБ.071.2", "T21", "Станция паяльная")
    _equipment(references_api, "ОБ.071.X", "T21(R)", "Станция паяльная", "Оборудование (ПР)")
    _equipment(references_api, "ОБ.004", "WL-IDC", "Пресс", "Оборудование (ПР)")
    _equipment(references_api, "ОБ.099.X", "BZW(R)", "Стрипер")  # no numbered unit of the model: stays
    _operation(references_api, "SDR", **{"Машина": "T21(R)/Станция паяльная",
                                         "Инструмент": "WL-IDC/Пресс; T21(R)/Станция паяльная"})
    _operation(references_api, "ZZ", **{"Машина": "T21(R)/Станция паяльная ЛАЗЕР"})
    actor, now = Actor(name="Тест", department="Цех"), datetime(2026, 10, 12, tzinfo=timezone.utc)

    plan = run(uow_factory(), actor, now, apply=False)
    assert [item.fields["Инв. номер"] for item in plan.retyped] == ["ОБ.004"]
    assert [item.fields["Инв. номер"] for item in plan.deleted] == ["ОБ.071.X"]
    assert [item.fields["Инв. номер"] for item, _why in plan.kept] == ["ОБ.099.X"]
    assert len(plan.repointed) == 2 and "будет" in format_plan(plan, applied=False)
    before = {item["fields"]["Инв. номер"] for item in references_api.get(BASE, params={"tab": "ob"}).json()["records"]}
    assert "ОБ.071.X" in before  # a dry run writes nothing

    run(uow_factory(), actor, now, apply=True)
    equipment = {item["fields"]["Инв. номер"]: item for item in references_api.get(BASE, params={"tab": "ob"}).json()["records"]}
    assert set(equipment) == {"ОБ.071.1", "ОБ.071.2", "ОБ.004", "ОБ.099.X"}
    assert equipment["ОБ.004"]["fields"]["Тип"] == "Оборудование" and equipment["ОБ.004"]["fields"]["obType"] == "Оборудование"
    ops = {item["fields"]["Номер"]: item["fields"] for item in references_api.get(BASE, params={"tab": "op"}).json()["records"]}
    assert ops["SDR"]["Машина"] == "T21/Станция паяльная"
    assert ops["SDR"]["Инструмент"] == "WL-IDC/Пресс; T21/Станция паяльная"
    assert ops["ZZ"]["Машина"] == "T21(R)/Станция паяльная ЛАЗЕР"  # only a whole reference is re-pointed

    assert run(uow_factory(), actor, now, apply=True).empty
