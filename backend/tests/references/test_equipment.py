"""БД.ОБ: inventory numbers, «Ещё такой же», refusals, junk rows of the loader, «что обжимает» tokens and links."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from app.modules.references.domain import ReferenceValidationError, TechOperationRecord, TechOperationTab
from app.modules.references.equipment import (
    add_code, has_code, next_instance_inventory, next_model_inventory, next_row_number, parse_inventory,
    physical_equipment, remove_code, tokens,
)
from app.modules.references.importers import import_rows
from app.modules.references.technology import parse_mirror_rows, sheet_snapshot_records
from app.shared.bulk_import import ImportRowIn
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401  (fixture)

BASE = "/api/references/tech-operations"


def rows(*items):
    return [{"Инв. номер": inv, "Тип": kind} for inv, kind in items]


# --- numbering ---

def test_parses_models_instances_and_legacy_outliers():
    assert parse_inventory("ОБ.070.8").instance == 8 and parse_inventory("ОБ.070.8").base == 70
    assert parse_inventory("ОБ.044").instance == 1
    assert parse_inventory("ОБ.79.9").base == 79 and parse_inventory("ОБ.81").base_text == "81"
    assert parse_inventory("ОБ.070.X").instance == 0
    assert parse_inventory("ОСН.012").prefix == "ОСН."
    assert parse_inventory("станок") is None


def test_next_model_number_is_one_above_the_highest_of_the_prefix():
    existing = rows(("ОБ.104", "Оборудование"), ("ОБ.79.9", "Оборудование"), ("ОБ.048", "Инструмент"),
                    ("ОСН.026", "Матрицы"), ("ОСН.001", "Матрицы"), ("ОБ.069.7", "Матрицы"))
    assert next_model_inventory("Оборудование", existing) == "ОБ.105"
    assert next_model_inventory("Инструмент", existing) == "ОБ.105"  # one ОБ. sequence for equipment and tools
    assert next_model_inventory("Матрицы", existing) == "ОБ.105"  # legacy ОСН. remains readable, new numbers use ОБ.
    assert next_model_inventory("Оборудование", []) == "ОБ.001"
    assert next_model_inventory("Обжимной инструмент", existing) == "ОБ.105"


@pytest.mark.parametrize("code", ["A1", "M10", "S3"])
def test_applicator_inventory_is_its_code(references_api, code):
    created = _create(references_api, **{"Тип": "Аппликаторы", "Наменование": "Аппликатор",
                                        "Инв. номер": "ОБ.106", "Код в базе терминалов": f" {code.lower()} "})
    assert created.status_code == 201, created.text
    assert created.json()["fields"]["Инв. номер"] == code
    assert created.json()["fields"]["Код в базе терминалов"] == code
    assert parse_inventory(code).prefix == code[0]
    assert parse_inventory(code).base == int(code[1:])
    assert references_api.post(f"{BASE}/{created.json()['id']}/duplicate").status_code == 422
    assert references_api.get(f"{BASE}/equipment-number", params={"type": "Аппликаторы"}).json()["inventory"] == ""


def test_applicators_need_a_real_code_and_reject_collisions(references_api):
    for code in ("", "X1", "A1/M2"):
        response = _create(references_api, **{"Тип": "Аппликаторы", "Наменование": "Аппликатор", "Код в базе терминалов": code})
        assert response.status_code == 422
        assert "код" in response.json()["detail"].lower()
    assert _create(references_api, **{"Тип": "Инструмент", "Наменование": "Занятый номер", "Инв. номер": "A1"}).status_code == 201
    collision = _create(references_api, **{"Тип": "Аппликаторы", "Наменование": "Аппликатор", "Код в базе терминалов": "A1"})
    assert collision.status_code == 409


def test_applicator_code_edits_keep_inventory_in_sync(references_api):
    item = _create(references_api, **{"Тип": "Аппликаторы", "Наменование": "Аппликатор", "Код в базе терминалов": "M10"}).json()
    url = f"{BASE}/{item['id']}"
    changed = references_api.patch(url, json={"edits": [{"field": "Код в базе терминалов", "old": "M10", "value": "M11"}]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["fields"]["Инв. номер"] == "M11"
    missing = references_api.patch(url, json={"edits": [{"field": "Код в базе терминалов", "old": "M11", "value": ""}]})
    assert missing.status_code == 422

def test_changing_a_tool_into_an_applicator_requires_its_code(references_api):
    item = _create(references_api, **{"Тип": "Инструмент", "Наменование": "Бывший инструмент"}).json()
    url = f"{BASE}/{item['id']}"
    for code in ("", "X1"):
        edits = [{"field": "Тип", "old": "Инструмент", "value": "Аппликаторы"}]
        if code:
            edits.append({"field": "Код в базе терминалов", "old": "", "value": code})
        response = references_api.patch(url, json={"edits": edits})
        assert response.status_code == 422, (code, response.text)
    ok = references_api.patch(url, json={"edits": [{"field": "Тип", "old": "Инструмент", "value": "Аппликаторы"},
                                                   {"field": "Код в базе терминалов", "old": "", "value": "S7"}]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["fields"]["Инв. номер"] == "S7"


def test_measuring_instruments_have_a_separate_sequence(references_api):
    existing = rows(("ОБ.197", "Аппликаторы"), ("СИ.002", "Средства измерений"), ("A999", "Аппликаторы"))
    assert next_model_inventory("Средства измерений", existing) == "СИ.003"
    assert next_model_inventory("Оборудование", existing) == "ОБ.198"  # do not reuse occupied legacy numbers
    assert next_model_inventory("Аппликаторы", existing) == ""
    first = _create(references_api, **{"Тип": "Средства измерений", "Наменование": "Штангенциркуль"}).json()
    assert first["fields"]["Инв. номер"] == "СИ.001"
    assert references_api.post(f"{BASE}/{first['id']}/duplicate").json()["fields"]["Инв. номер"] == "СИ.001.2"
    assert _create(references_api, **{"Тип": "Средства измерений", "Наменование": "Микрометр"}).json()["fields"]["Инв. номер"] == "СИ.002"


def test_supporting_equipment_keeps_ob_prefix_and_is_not_a_machine(references_api):
    existing = rows(("ОБ.200", "Обеспечивающее оборудование"), ("СИ.999", "Средства измерений"))
    assert next_model_inventory("Обеспечивающее оборудование", existing) == "ОБ.201"
    item = _create(references_api, **{"Тип": "Обеспечивающее оборудование", "Наменование": "Компрессор",
                                    "Инв. номер": "ОБ.200"})
    assert item.status_code == 201, item.text
    assert item.json()["fields"]["Инв. номер"] == "ОБ.200"
    assert references_api.get(f"{BASE}/equipment-number", params={"type": "Обеспечивающее оборудование"}).json()["inventory"] == "ОБ.201"
    record = TechOperationRecord.create(tab=TechOperationTab.EQUIPMENT,
        fields=item.json()["fields"], display_text="Компрессор", source_sheet="БД.ОБ", search_text="Компрессор", sort_key="ОБ.200")
    assert physical_equipment([record]) == ()


def test_next_instance_number_keeps_existing_records():
    existing = rows(*[(f"ОБ.070.{k}", "Оборудование") for k in range(1, 9)], ("ОБ.070.X", "Оборудование"),
                    ("ОБ.044", "Оборудование"), ("ОБ.79.1", "Оборудование"), ("ОБ.79.9", "Оборудование"))
    assert next_instance_inventory("ОБ.070.3", existing) == "ОБ.070.9"
    assert next_instance_inventory("ОБ.044", existing) == "ОБ.044.2"
    assert next_instance_inventory("ОБ.79.1", existing) == "ОБ.79.10"
    with pytest.raises(ReferenceValidationError):
        next_instance_inventory("", existing)


def test_next_row_number():
    assert next_row_number([{"№": "5"}, {"№": "17"}, {"№": ""}]) == "18" and next_row_number([]) == "1"


def test_number_generation_counts_inventory_aliases():
    existing = [{"Инв. номер": " ", "inventoryNumber": number}
                for number in ("ОБ.94.1", "ОБ.94.3", "СИ.095.1")]
    assert next_model_inventory("Оборудование", existing) == "ОБ.095"
    assert next_model_inventory("Средства измерений", existing) == "СИ.096"
    assert next_instance_inventory("ОБ.94.1", existing) == "ОБ.94.4"


@pytest.mark.parametrize("kind, number", [
    ("Оборудование", "ОБ.94.1"), ("Средства измерений", "ОБ.94.1"), ("Средства измерений", "СИ.094.1"),
])
@pytest.mark.parametrize("canonical", [{}, {"Инв. номер": " "}])
def test_repository_save_keeps_inventory_and_type_aliases(references_api, kind, number, canonical):
    factory = references_api.app.state.references_uow_factory
    record = TechOperationRecord.create(
        tab=TechOperationTab.EQUIPMENT, display_text="Прибор", search_text="", source_sheet="БД.ОБ", sort_key="1",
        fields={"Тип": " ", "obType": kind, "inventoryNumber": number, **canonical},
    )
    with factory() as uow:
        uow.tech_operations.save(record)
        uow.commit()
    with factory() as uow:
        stored = uow.tech_operations.get(record.id)
        assert stored.fields["Инв. номер"] == stored.fields["inventoryNumber"] == number
        assert stored.fields["obType"] == kind
        uow.tech_operations.save(replace(stored, fields={**stored.fields, "Расположение": "ЖУК"}))
        uow.commit()
    with factory() as uow:
        assert uow.tech_operations.get(record.id).fields["Инв. номер"] == number


@pytest.mark.parametrize("kind, number", [("Оборудование", "ОБ.94.1"), ("Средства измерений", "ОБ.94.1")])
def test_bulk_import_keeps_aliased_inventory_on_create_and_update(references_api, kind, number):
    factory = references_api.app.state.references_uow_factory
    now = datetime(2026, 11, 12, 12)
    record = TechOperationRecord.create(
        tab=TechOperationTab.EQUIPMENT, display_text="Прибор", search_text="", source_sheet="БД.ОБ", sort_key="1",
        fields={"Тип": " ", "obType": "Инструмент", "Инв. номер": " ", "inventoryNumber": number, "Расположение": "ЦАР"},
    )
    with factory() as uow:
        uow.tech_operations.replace((record,), schema_version=14, source_updated_at=None, imported_at=now)
        uow.commit()
    rows = [
        ImportRowIn(fields={"baseValue": "Прибор", "obType": kind, "Расположение": "ЖУК"}),
        ImportRowIn(fields={"baseValue": "Другой прибор", "obType": kind, "inventoryNumber": "ОБ.95.1"}),
    ]
    actor = Actor(name="Тест", department="ОГТ")
    preview = import_rows("ob", factory(), rows, actor, now, dry_run=True)
    assert preview.refused == []
    with factory() as uow:
        assert uow.tech_operations.get(record.id).fields == record.fields
    applied = import_rows("ob", factory(), rows, actor, now, dry_run=False)
    assert applied.refused == []
    assert len(applied.created) == len(applied.updated) == 1
    with factory() as uow:
        stored = uow.tech_operations.get(record.id)
        assert stored.fields["Инв. номер"] == stored.fields["inventoryNumber"] == number
        assert stored.fields["obType"] == kind and stored.fields["Расположение"] == "ЖУК"
        [created] = [row for row in uow.tech_operations.list(tab=TechOperationTab.EQUIPMENT, query="", limit=10)
                     if row.id != record.id]
        assert created.fields["Инв. номер"] == created.fields["inventoryNumber"] == "ОБ.95.1"
        assert created.fields["obType"] == kind


@pytest.mark.parametrize("code", ["", "M10"])
def test_saving_legacy_applicator_does_not_renumber_it_or_require_a_new_code(references_api, code):
    factory = references_api.app.state.references_uow_factory
    record = TechOperationRecord.create(
        tab=TechOperationTab.EQUIPMENT, display_text="Аппликатор", search_text="", source_sheet="БД.ОБ", sort_key="1",
        fields={"Тип": " ", "obType": "Аппликаторы", "inventoryNumber": "ОБ.106", "Код в базе терминалов": code},
    )
    with factory() as uow:
        uow.tech_operations.replace((record,), schema_version=14, source_updated_at=None,
                                    imported_at=datetime(2026, 11, 12, 12))
        uow.commit()
    with factory() as uow:
        stored = uow.tech_operations.get(record.id)
        uow.tech_operations.save(replace(stored, fields={**stored.fields, "Расположение": "ЖУК"}))
        uow.commit()
    with factory() as uow:
        fields = uow.tech_operations.get(record.id).fields
        assert fields["Инв. номер"] == fields["inventoryNumber"] == "ОБ.106"
        assert fields["Код в базе терминалов"] == code


# --- physical equipment catalog ---

def _machine(number="ОБ.099.1", *, name="Станок для обжима терминалов без зачистки", model="BZW-SF4040T", site="Цар", **extra):
    return TechOperationRecord.create(
        tab=TechOperationTab.EQUIPMENT, display_text=f"{model}/{name}", search_text="", source_sheet="БД.ОБ",
        sort_key=number, fields={"Тип": "Оборудование", "Инв. номер": number, "Наменование": name,
                                 "Артикул": model, "Расположение": site, **extra},
    )


def test_physical_catalog_keeps_actual_instances_and_models_from_reference_mirror():
    source = Path(__file__).resolve().parents[2] / "data" / "reference-mirror" / "БД.ОБ.tsv"
    sheet = parse_mirror_rows(source.read_text(encoding="utf-8"))
    records = [TechOperationRecord.create(
        tab=TechOperationTab.EQUIPMENT, display_text=str(index), search_text="", source_sheet="БД.ОБ",
        sort_key=str(index), fields=dict(zip(sheet[1], row)),
    ) for index, row in enumerate(sheet[2:])]
    catalog = {item.inventory_number: item for item in physical_equipment(records)}
    assert len(catalog) == 130
    assert catalog["ОБ.044"].name == "Источник питания постоянного тока"
    assert catalog["ОБ.044"].model == "APS3010H"
    assert catalog["ОБ.027.1"].model == "EW-50A"
    assert catalog["ОБ.027.4"].model == "WL-2.0ВТ"
    assert {key for key in catalog if key.startswith("ОБ.099.")} == {"ОБ.099.1", "ОБ.099.2"}
    assert {key for key in catalog if key.startswith("ОБ.100.")} == {"ОБ.100.1", "ОБ.100.2", "ОБ.100.3"}
    assert {key for key in catalog if key.startswith("ОБ.070.")} == {f"ОБ.070.{i}" for i in range(1, 9)}
    assert {item.site for item in catalog.values()} == {"ЦАР"}
    assert not any(key.endswith(".X") for key in catalog)


def test_physical_catalog_identity_survives_new_reference_uuids_and_deduplicates_full_numbers():
    one, duplicate, two = _machine(), _machine(" об.099.1 "), _machine("ОБ.099.2", site="Жук")
    assert one.id != duplicate.id
    catalog = physical_equipment([two, duplicate, one])
    assert [(item.id, item.inventory_number, item.site) for item in catalog] == [
        ("ОБ.099.1", "ОБ.099.1", "ЦАР"), ("ОБ.099.2", "ОБ.099.2", "ЖУК"),
    ]
    assert physical_equipment([_machine(), _machine("ОБ.099.2", site="Жук")]) == catalog


@pytest.mark.parametrize("site", ["", "Склад", "ЦАР / ЖУК"])
def test_physical_catalog_keeps_valid_equipment_with_unknown_site(site):
    (item,) = physical_equipment([_machine(site=site)])
    assert item.inventory_number == "ОБ.099.1" and item.site == ""


def test_physical_catalog_accepts_reference_field_aliases_and_retains_inventory_spelling():
    record = replace(_machine(), fields={
        "obType": "Оборудование (ПР)", "inventoryNumber": " OБ.79.01 ", " Наименование ": "Мини ПК",
        " РАСПОЛОЖЕНИЕ ": " жУк ",
    })
    (item,) = physical_equipment([record])
    assert item.id == "ОБ.79.01" and item.inventory_number == "ОБ.79.01"
    assert item.name == "Мини ПК" and item.model == "" and item.site == "ЖУК"


@pytest.mark.parametrize("record", [
    _machine(""), _machine("пример"), _machine("ОБ.099.X"), _machine("ОБ.099.Х"),
    _machine("ОБ.099.0"), _machine("ОБ.000"), _machine("ОБ.099.1.2"), _machine("ОСН.099.1"),
    _machine(name=""), _machine(model="BZW-SF4040T(R)"),
    _machine(**{"Тип": "Матрицы"}), _machine(**{"Тип": "Инструмент"}),
    replace(_machine(), tab=TechOperationTab.OPERATION),
])
def test_physical_catalog_excludes_placeholders_models_without_inventory_and_invalid_rows(record):
    assert physical_equipment([record]) == ()


@pytest.mark.parametrize("difference", [{"site": "Жук"}, {"name": "Другой станок"}, {"model": "Другая модель"}])
def test_physical_catalog_refuses_conflicting_duplicate_inventory(difference):
    with pytest.raises(ReferenceValidationError, match="ОБ.099.1"):
        physical_equipment([_machine(), _machine(**difference)])


def test_physical_catalog_preserves_bare_single_unit_and_does_not_invent_units_for_missing_families():
    (item,) = physical_equipment([_machine("ОБ.044", name="Блок питания", model="")])
    assert item.id == "ОБ.044" and item.model == ""
    assert physical_equipment([]) == ()


# --- tokens ---

def test_token_matching_is_whole_token_and_marks_remarks():
    value = "S10/M10/M19/M49/A8"
    assert has_code(value, "S10") and not has_code(value, "S1") and not has_code(value, "M1")
    assert has_code("S1/A13", "S1") and not has_code("S1/A13", "S10")
    assert has_code("S1, M2 A3", "M2") and has_code("S1, M2 A3", "A3")
    doubtful = [(t.code, t.doubtful) for t in tokens("возможно S12/M12")]
    assert doubtful == [("S12", True), ("M12", True)]
    assert not has_code("совмест. S2", "S2") and has_code("совмест. S2", "S2", sure_only=False)
    assert [(t.code, t.doubtful) for t in tokens("S5 возможно M2")] == [("S5", False), ("M2", True)]


def test_add_and_remove_touch_only_that_token():
    assert add_code("", "M31") == "M31"
    assert add_code("S8/M8", "A5") == "S8/M8/A5"
    assert add_code("S8/M8", "S8") == "S8/M8"
    assert add_code("возможно S12/M12", "A1") == "A1/возможно S12/M12"
    with pytest.raises(ReferenceValidationError):
        add_code("возможно S12/M12", "S12")
    assert remove_code("S10/M10/M19/M49/A8", "M10") == "S10/M19/M49/A8"
    assert remove_code("S1/S10", "S1") == "S10"
    assert remove_code("S10/S1", "S1") == "S10"
    assert remove_code("S10", "S1") == "S10"
    assert remove_code("M31", "M31") == ""
    assert remove_code("S5 возможно M2", "S5") == "возможно M2"
    with pytest.raises(ReferenceValidationError):
        remove_code("возможно S12/M12", "S12")


# --- loader ---

def test_loader_skips_rows_with_only_a_number_and_the_base_formula():
    header = ["№", "Тип", "Инв. номер", "Артикул", "Наменование", "Для базы"]
    sheet = [[""], header,
             ["1", "Оборудование", "ОБ.001", "EW", "Паяльник", "EW/Паяльник"],
             ["190", "", "", "", "", "/"], ["191", "", "", "", "", "/"],
             ["2", "", "ОБ.002", "", "", "/"]]
    _headers, records = sheet_snapshot_records("ob", sheet)
    assert [item["displayText"] for item in records] == ["EW/Паяльник", "/"]
    assert records[1]["exportValues"][2] == "ОБ.002"


# --- API ---

def _create(client, **fields):
    return client.post(BASE, json={"tab": "ob", "fields": fields})


def _seed(client):
    for inv, name in (("ОБ.070.1", "Станция"), ("ОБ.070.2", "Станция"), ("ОБ.044", "Блок питания")):
        assert _create(client, **{"Тип": "Оборудование", "Инв. номер": inv, "Наменование": name, "Артикул": "A1"}).status_code == 201


def test_create_gives_next_number_and_row_and_refuses_duplicates_and_nameless(references_api):
    _seed(references_api)
    created = _create(references_api, **{"Тип": "Оборудование", "Наменование": "Новый станок", "Артикул": "N1"})
    assert created.status_code == 201
    fields = created.json()["fields"]
    assert fields["Инв. номер"] == "ОБ.071" and fields["№"] == "4" and fields["Для базы"] == "N1/Новый станок"

    taken = _create(references_api, **{"Тип": "Оборудование", "Инв. номер": "об.044", "Наменование": "Другой"})
    assert taken.status_code == 409 and "уже есть" in taken.json()["detail"]
    nameless = _create(references_api, **{"Тип": "Оборудование", "Артикул": "X"})
    assert nameless.status_code == 422 and "Наименование" in nameless.json()["detail"]
    preview = references_api.get(f"{BASE}/equipment-number", params={"type": "Оборудование"}).json()
    assert preview["inventory"] == "ОБ.072" and preview["row"] == "5"


def test_another_one_copies_fields_and_numbers_the_instance(references_api):
    _seed(references_api)
    records = {item["fields"]["Инв. номер"]: item for item in references_api.get(BASE, params={"tab": "ob"}).json()["records"]}

    more = references_api.post(f"{BASE}/{records['ОБ.070.1']['id']}/duplicate")
    single = references_api.post(f"{BASE}/{records['ОБ.044']['id']}/duplicate")

    assert more.status_code == 201 and more.json()["fields"]["Инв. номер"] == "ОБ.070.3"
    assert more.json()["fields"]["Наменование"] == "Станция" and more.json()["fields"]["Артикул"] == "A1"
    assert single.json()["fields"]["Инв. номер"] == "ОБ.044.2"
    after = {item["fields"]["Инв. номер"] for item in references_api.get(BASE, params={"tab": "ob"}).json()["records"]}
    assert after == {"ОБ.070.1", "ОБ.070.2", "ОБ.070.3", "ОБ.044", "ОБ.044.2"}


def test_edit_refuses_a_taken_number_and_an_emptied_name(references_api):
    _seed(references_api)
    records = {item["fields"]["Инв. номер"]: item for item in references_api.get(BASE, params={"tab": "ob"}).json()["records"]}
    url = f"{BASE}/{records['ОБ.044']['id']}"

    taken = references_api.patch(url, json={"edits": [{"field": "Инв. номер", "old": "ОБ.044", "value": "ОБ.070.1"}]})
    nameless = references_api.patch(url, json={"edits": [{"field": "Наменование", "old": "Блок питания", "value": ""}]})
    renamed = references_api.patch(url, json={"edits": [{"field": "Наменование", "old": "Блок питания", "value": "Блок"}]})

    assert taken.status_code == 409 and nameless.status_code == 422
    assert renamed.json()["fields"]["Для базы"] == "A1/Блок"


def _terminal(client, applicator, article):
    client.post(BASE, json={"tab": "ter", "fields": {"Артикул контакта (REEL)": article, "Аппликатор": applicator, "Product Name": article}})


def test_crimp_tool_links_terminals_through_the_code(references_api):
    tool = _create(references_api, **{"Тип": "Обжимной инструмент", "Наменование": "Пресс", "Код в базе терминалов": "s1"}).json()
    assert tool["fields"]["Код в базе терминалов"] == "S1"
    clash = _create(references_api, **{"Тип": "Обжимной инструмент", "Наменование": "Другой", "Код в базе терминалов": "S1"})
    bad = _create(references_api, **{"Тип": "Обжимной инструмент", "Наменование": "Другой", "Код в базе терминалов": "1S"})
    assert clash.status_code == 409 and bad.status_code == 422
    for applicator, article in (("S10/M10", "T-10"), ("S1/A13", "T-1"), ("возможно S1", "T-Q"), ("", "T-0")):
        _terminal(references_api, applicator, article)
    terminals = {item["fields"]["Артикул контакта (REEL)"]: item for item in references_api.get(BASE, params={"tab": "ter"}).json()["records"]}

    overview = references_api.get(f"{BASE}/{tool['id']}/crimps").json()
    assert {item["article"]: item["doubtful"] for item in overview["terminals"]} == {"T-1": False, "T-Q": True}

    added = references_api.post(f"{BASE}/{tool['id']}/crimps/terminals/{terminals['T-0']['id']}")
    assert added.status_code == 200 and added.json()["applicator"] == "S1"
    references_api.post(f"{BASE}/{tool['id']}/crimps/terminals/{terminals['T-10']['id']}")
    refused = references_api.post(f"{BASE}/{tool['id']}/crimps/terminals/{terminals['T-Q']['id']}")
    assert refused.status_code == 422
    removed = references_api.delete(f"{BASE}/{tool['id']}/crimps/terminals/{terminals['T-1']['id']}")
    assert removed.json()["applicator"] == "A13"

    now = {item["fields"]["Артикул контакта (REEL)"]: item for item in references_api.get(BASE, params={"tab": "ter"}).json()["records"]}
    assert now["T-10"]["fields"]["Аппликатор"] == "S10/M10/S1" and now["T-10"]["fields"]["terApplicator"] == "S10/M10/S1"
    codes = references_api.get(f"{BASE}/crimp-codes").json()
    assert codes["owners"] == {"S1": tool["id"]} and {item["code"] for item in codes["missing"]} == {"M10", "S10", "A13"}


def test_targets_are_kept_on_the_tool_record(references_api):
    tool = _create(references_api, **{"Тип": "Матрицы", "Наменование": "Губки", "Инв. номер": "ОСН.001"}).json()
    target = {"kind": "series", "key": "GHR", "label": "GHR"}
    assert references_api.post(f"{BASE}/{tool['id']}/crimps/targets", json=target).json()["targets"] == [target]
    assert references_api.get(f"{BASE}/{tool['id']}/crimps").json()["targets"] == [target]
    assert references_api.post(f"{BASE}/{tool['id']}/crimps/targets/remove", json=target).json()["targets"] == []
