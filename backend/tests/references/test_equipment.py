"""БД.ОБ: inventory numbers, «Ещё такой же», refusals, junk rows of the loader, «что обжимает» tokens and links."""

import pytest

from app.modules.references.domain import ReferenceValidationError
from app.modules.references.equipment import (
    add_code, has_code, next_instance_inventory, next_model_inventory, next_row_number, parse_inventory, remove_code, tokens,
)
from app.modules.references.technology import sheet_snapshot_records
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
    assert next_model_inventory("Матрицы", existing) == "ОСН.027"  # most matrices are ОСН.
    assert next_model_inventory("Оборудование", []) == "ОБ.001"
    assert next_model_inventory("Обжимной инструмент", existing) == "ОБ.105"


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
