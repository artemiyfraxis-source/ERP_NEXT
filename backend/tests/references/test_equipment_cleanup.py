"""БД.ОБ cleanup: ПР → Оборудование, «.X» rows, БД.ОП references; the loader keeps them from coming back."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.technology import sheet_snapshot_records
from app.workflows.equipment_cleanup import build_plan, format_plan, main, run
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

    with uow_factory() as uow:
        events = [(row[0], row[1] if isinstance(row[1], dict) else json.loads(row[1]))
                  for row in uow.session.execute(text("select event_type, details from audit_events"))]
    kinds = [kind for kind, _details in events]
    assert kinds.count("references.equipment_retyped") == 1 and kinds.count("references.operation_repointed") == 1
    assert kinds.count("references.equipment_removed") == 1 and kinds.count("references.equipment_cleanup") == 1
    removed = next(details for kind, details in events if kind == "references.equipment_removed")
    assert removed["inventory"] == "ОБ.071.X" and removed["fields"]["Артикул"] == "T21(R)"  # kept whole in the journal


def _record(tab, display, **fields):
    return TechOperationRecord(id=uuid4(), tab=tab, display_text=display, normalized_search="", source_sheet="",
                               sort_key="", fields=dict(fields))


def _ob(inv, article, name, kind="Оборудование", **extra):
    return _record(TechOperationTab.EQUIPMENT, f"{article}/{name}", **{
        "Тип": kind, "Инв. номер": inv, "Артикул": article, "Наменование": name, "Для базы": f"{article}/{name}", **extra})


def _op(number, **cells):
    return _record(TechOperationTab.OPERATION, number, **{"Номер": number, **cells})


def test_reference_found_regardless_of_case_and_spaces():
    unit, x = _ob("ОБ.071.1", "T21", "Станция"), _ob("ОБ.071.X", "T21(R)", "Станция")
    plan = build_plan([unit, x], [_op("SDR", **{"Машина": "  t21(R) /  станция ;ОБ.071.X"})])
    assert [after for *_rest, after in plan.repointed] == ["  T21/Станция ;ОБ.071.X"]
    assert plan.deleted == [] and "операция SDR · Машина" in plan.kept[0][1]  # its inventory number is still named


def test_x_row_named_by_a_matrix_is_kept_but_operations_still_move():
    unit, x = _ob("ОБ.069.1", "КВТ", "Пресс"), _ob("ОБ.069.X", "КВТ(R)", "Пресс")
    matrix = _ob("ОСН.001", "M1", "Матрица", "Матрицы", **{"Для инструмента": "ОБ.069.X"})
    plan = build_plan([unit, x, matrix], [_op("CRM", **{"Машина": "КВТ(R)/Пресс"})])
    assert len(plan.repointed) == 1 and plan.deleted == []
    assert "матрица ОСН.001" in plan.kept[0][1]


def test_x_row_alone_owning_a_crimp_code_or_linked_to_stock_is_kept():
    unit = _ob("ОБ.070.1", "S", "Клещи", "Обжимной инструмент")
    coded = _ob("ОБ.070.X", "S(R)", "Клещи", "Обжимной инструмент", **{"Код в базе терминалов": "S10"})
    assert "код обжима S10" in build_plan([unit, coded], []).kept[0][1]
    shared = _ob("ОБ.070.2", "S", "Клещи", "Обжимной инструмент", **{"Код в базе терминалов": "S10"})
    assert build_plan([unit, shared, coded], []).deleted == [coded]
    assert "привязок склада: 2" in build_plan([unit, coded, shared], [], {coded.id: 2}).kept[0][1]


def test_kept_x_row_of_the_retired_type_is_retyped():
    lonely = _ob("ОБ.099.X", "BZW(R)", "Стрипер", "Оборудование (ПР)")
    plan = build_plan([lonely], [])
    assert plan.retyped == [lonely] and plan.deleted == [] and not plan.empty


def test_x_row_whose_text_is_the_model_text_is_deleted():
    unit, x = _ob("ОБ.044.1", "BZW", "Стрипер"), _ob("ОБ.044.X", "BZW", "Стрипер")
    plan = build_plan([unit, x], [_op("STR", **{"Машина": "BZW/Стрипер"})])
    assert plan.deleted == [x] and plan.repointed == []


def test_cli_modes(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        main(["--dry-run", "--apply"])
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert main(["--dry-run"]) == 2 and "ERP_DATABASE_URL" in capsys.readouterr().err
