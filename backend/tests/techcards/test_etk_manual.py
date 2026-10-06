"""«Ручная сборка» (mode manual, owner 04.10): the technologist types the operation list himself — operation cards
from the library and own operations — and the block scheme is always his own (manual), so production follows it."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from app.modules.production.launchable import route_from_document
from app.modules.techcards.etk_builder import build_etk, document_route
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard, operation_key


NOW = datetime(2026, 10, 4, 12, 0)
ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
CUT = "cut-wire-hand-резка"
CRIMP = "prs-terminal-hand-опрессовка-терминалов"

CONFIG = {
    "mode": "manual", "projectNo": "P-300", "assemblyIndex": "КБ7", "assemblyName": "Провод нетиповой 300мм",
    "partQty": 10,
    "ops": [
        {"type": "libraryOp", "templateId": CUT, "opNum": "CUT_WIRE_hand", "label": "Резка", "qty": 2,
         "tPrep": "120", "tOp": "6", "params": "L = 300 мм", "note": "провод из бухты",
         "components": [{"id": "kit-0", "art": "МГТФ 0,35", "name": "Провод МГТФ 0,35", "qty": "0,6", "unit": "М."}]},
        {"type": "customOp", "label": "Пайка экрана к лепестку", "tPrep": "300", "tOp": "45",
         "steps": [
             {"kind": "Операция", "text": "Зачистить экран на 8 мм"},
             {"kind": "Операция", "text": "Облудить экран и лепесток"},
             {"kind": "Контроль", "text": "Пайка без наплывов и непропая", "tolerance": "IPC-A-620 раздел 4"},
         ],
         "tooling": [
             {"section": "Оборудование", "name": "Паяльная станция, жало клин 2,4 мм"},
             {"section": "Инструмент", "name": "Пинцет"},
             {"section": "Расходники", "name": "Припой ПОС-61"},
             {"section": "", "name": "Бокорезы"},
         ],
         "result": "Провод с припаянным лепестком"},
        {"type": "libraryOp", "templateId": CRIMP, "opNum": "PRS_TERMINAL_hand", "label": "Опрессовка терминалов",
         "qty": 1, "tPrep": "", "tOp": "20"},
    ],
}


def build(config: dict) -> dict:
    return build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


def manual(**changes) -> dict:
    config = deepcopy(CONFIG)
    config.update(changes)
    return config


def test_library_and_custom_operations_build_an_etk_chained_into_a_manual_scheme():
    document = build(manual())
    cut, solder, crimp = document["operations"]

    assert document["kit"]["mode"] == "manual"
    assert [item["type"] for item in document["operations"]] == ["libraryOp", "customOp", "libraryOp"]
    assert [item["code"] for item in document["operations"]] == ["CUT_WIRE_hand", "", "PRS_TERMINAL_hand"]
    # Library card: its own steps, the operation's name from the list.
    assert cut["card"]["name"] == "Резка"
    assert cut["card"]["steps"][3]["kind"] == "Контроль"
    assert crimp["card"]["steps"][0]["text"].startswith("Подобрать ручной кримпер")
    # Own operation: numbered steps, КОНТРОЛЬ with its допуск, tooling by section numbered within the section.
    assert solder["code"] == "" and solder["title"] == "Пайка экрана к лепестку"
    assert solder["card"]["name"] == "Пайка экрана к лепестку"
    assert solder["card"]["steps"] == [
        {"number": "1", "kind": "Операция", "text": "Зачистить экран на 8 мм", "tolerance": ""},
        {"number": "2", "kind": "Операция", "text": "Облудить экран и лепесток", "tolerance": ""},
        {"number": "3", "kind": "Контроль", "text": "Пайка без наплывов и непропая", "tolerance": "IPC-A-620 раздел 4"},
    ]
    assert [(tool["section"], tool["number"], tool["name"]) for tool in solder["card"]["tooling"]] == [
        ("Оборудование", "1", "Паяльная станция, жало клин 2,4 мм"),
        ("Инструмент", "1", "Пинцет"),
        ("Расходники", "1", "Припой ПОС-61"),
        ("Инструмент", "2", "Бокорезы"),
    ]
    # Вход: kit lines per item × batch; the chain hands each product on.
    assert cut["components"] == [{"art": "МГТФ 0,35", "name": "Провод МГТФ 0,35", "norm": "6", "unit": "М."}]
    assert cut["semis"] == []
    assert solder["semis"] == [{"name": "Полуфабрикат после «Резка»", "norm": "10", "unit": "Шт."}]
    assert crimp["semis"] == [{"name": "Провод с припаянным лепестком", "norm": "10", "unit": "Шт."}]
    # Результат: the last operation hands over the product; time = (sec × qty × batch + setup) / 60.
    assert [item["result_label"] for item in document["operations"]] == ["Полуфабрикат", "Полуфабрикат", "Изделие"]
    assert crimp["result"] == [{"name": "Провод нетиповой 300мм", "norm": "10", "unit": "Шт."}]
    assert cut["time"] == [{"name": "Полуфабрикат после «Резка»", "norm": "4"}]
    assert solder["time"][0]["norm"] == "12,5"
    assert crimp["time"][0]["norm"] == "3,33"
    assert cut["times"] == {"operation_seconds": "6", "setup_seconds": "120", "machine_seconds": ""}
    assert cut["fields"] == {"perItem": "2", "params": "L = 300 мм", "note": "провод из бухты"}
    assert solder["fields"] == {}
    assert document["warnings"] == []

    route = document["route"]
    assert route["source"] == "manual"
    assert [(block["key"], block["ops"], block["final"]) for block in route["blocks"]] == [
        ("b1", [1], False), ("b2", [2], False), ("b3", [3], True),
    ]
    assert route["blocks"][2]["name"] == "Провод нетиповой 300мм"
    assert [(edge["from"], edge["to"]) for edge in route["edges"]] == [("b1", "b2"), ("b2", "b3")]
    assert route["blocks"][2]["rolled_minutes"] == pytest.approx((4 + 12.5 + 3.33) / 10)
    # The read path gives back the same scheme.
    assert document_route(document) == route


def test_hand_drawn_scheme_feeds_blocks_with_their_quantities_and_names_the_products():
    route = {
        "blocks": [
            {"key": "cut", "name": "Мерный провод", "ops": [1], "final": False},
            {"key": "lug", "name": "Лепесток с проводом", "ops": [2], "final": False,
             "components": [{"id": "kit-2", "art": "ЛП-3", "name": "Лепесток", "qty": 1}]},
            {"key": "end", "ops": [3], "final": True},
        ],
        "edges": [{"from": "cut", "to": "end", "per_unit": 2}, {"from": "lug", "to": "end", "per_unit": 1}],
    }
    document = build(manual(route=route))
    cut, solder, crimp = document["operations"]

    assert cut["result"] == [{"name": "Мерный провод", "norm": "10", "unit": "Шт."}]
    assert solder["semis"] == []
    assert solder["result"][0]["name"] == "Лепесток с проводом"
    assert crimp["semis"] == [
        {"name": "Мерный провод", "norm": "20", "unit": "Шт."},
        {"name": "Лепесток с проводом", "norm": "10", "unit": "Шт."},
    ]
    stored = document["route"]
    assert stored["source"] == "manual"
    assert stored["blocks"][1]["components"] == [{"id": "kit-2", "art": "ЛП-3", "name": "Лепесток", "qty": 1}]
    final = next(block for block in stored["blocks"] if block["final"])
    assert final["name"] == "Провод нетиповой 300мм" and final["inputs"] == 3

    # Production follows the technologist's scheme: block of each step and its inputs.
    steps = route_from_document(document)
    assert [(step.stage, step.code, step.block) for step in steps] == [
        ("Резка", "CUT_WIRE_hand", "cut"), ("Пайка экрана к лепестку", "", "lug"),
        ("Опрессовка терминалов", "PRS_TERMINAL_hand", "end"),
    ]
    assert [(item.block, str(item.per_unit)) for item in steps[2].inputs] == [("cut", "2"), ("lug", "1")]
    # The ETK norm: 6 s × 2 «на изделие» per item + 120 s setup per batch (was 6 s per item: «на изделие» dropped).
    assert steps[0].seconds_per_unit == 12 and steps[0].setup_seconds == 120


def test_simultaneous_operations_are_branches_the_next_operation_joins():
    # Link «одновременно» (owner 05.10): the generator marks the op and draws it as its own branch.
    config = manual(route={
        "blocks": [{"key": "b1", "ops": [1], "final": False}, {"key": "b2", "ops": [2], "final": False},
                   {"key": "b3", "ops": [3], "final": True}],
        "edges": [{"from": "b1", "to": "b3", "per_unit": 1}, {"from": "b2", "to": "b3", "per_unit": 1}],
    })
    config["ops"][1]["link"] = "parallel"
    document = build(config)
    cut, solder, crimp = document["operations"]
    assert solder["semis"] == []
    assert [item["name"] for item in crimp["semis"]] == ["Полуфабрикат после «Резка»", "Провод с припаянным лепестком"]
    steps = route_from_document(document)
    assert [item.block for item in steps[2].inputs] == ["b1", "b2"]


def test_operations_merged_into_one_block_pass_the_product_inside_it():
    route = {
        "blocks": [{"key": "b1", "ops": [1, 2], "final": False}, {"key": "b3", "ops": [3], "final": True}],
        "edges": [{"from": "b1", "to": "b3", "per_unit": 1}],
    }
    cut, solder, crimp = build(manual(route=route))["operations"]
    # Inside the block the cut hands its own product on; the block's product is the last operation's one.
    assert cut["result"][0]["name"] == "Полуфабрикат после «Резка»"
    assert solder["semis"] == [{"name": "Полуфабрикат после «Резка»", "norm": "10", "unit": "Шт."}]
    assert crimp["semis"] == [{"name": "Провод с припаянным лепестком", "norm": "10", "unit": "Шт."}]


def test_a_reorder_keeps_a_hand_edited_etk_operation_with_its_own_operation_by_seq():
    config = manual()
    for seq, op in zip((4, 7, 9), config["ops"]):
        op["seq"] = seq
    document = build(config)
    # An own operation has no card code: its key is its number; a numeric connector is not shown in the ETK title.
    assert [operation_key(item) for item in document["operations"]] == [
        "CUT_WIRE_hand::4::", "::7::", "PRS_TERMINAL_hand::9::",
    ]
    card = ProjectTechcard.create(
        project="P-300", item_index="КБ7", product_name="Провод нетиповой 300мм", source_calculation="",
        deadline=None, now=NOW,
    ).rebuild(document, NOW).issue(NOW)
    edited = deepcopy(document["operations"][1])
    edited["card"]["steps"][0]["text"] = "Зачистить экран на 10 мм"
    card = card.edit_operation("::7::", edited, NOW)

    config["ops"] = [config["ops"][1], config["ops"][0], config["ops"][2]]
    rebuilt = card.rebuild(build(config), NOW).document["operations"]
    assert [item["card"]["name"] for item in rebuilt] == ["Пайка экрана к лепестку", "Резка", "Опрессовка терминалов"]
    assert rebuilt[0]["card"]["steps"][0]["text"] == "Зачистить экран на 10 мм"
    assert rebuilt[1]["card"]["steps"][0]["text"].startswith("Подготовить ручной")


def test_without_distinct_seq_numbers_the_operations_are_keyed_by_position():
    config = manual()
    config["ops"][0]["seq"] = config["ops"][1]["seq"] = 3
    assert [item["connector"] for item in build(config)["operations"]] == ["1", "2", "3"]


def test_a_card_gone_from_the_library_and_an_own_operation_without_steps_are_warnings():
    config = manual()
    config["ops"][0]["templateId"] = "нет-такой"
    config["ops"][1]["steps"] = []
    config["ops"][2].update(tOp="", tPrep="0")
    document = build(config)
    assert document["operations"][0]["card"] is None
    assert document["operations"][0]["code"] == "CUT_WIRE_hand"
    assert document["warnings"] == [
        "Операция 1 (Резка): нет карточки CUT_WIRE_hand в библиотеке",
        "Операция 2 (Пайка экрана к лепестку): не заполнен ход операции",
        "Операция 3 (Опрессовка терминалов): не задана норма времени",
    ]
    assert document["operations"][2]["time"][0]["norm"] == "0"


@pytest.mark.parametrize(("change", "message"), [
    (lambda config: config.update(ops=[]), "Ручная сборка: добавьте хотя бы одну операцию — из библиотеки или свою"),
    (lambda config: config["ops"][0].update(tOp="шесть"),
     "Ручная сборка: операция 1 «Резка»: время на штуку должно быть числом не меньше нуля"),
    (lambda config: config["ops"][1].update(tPrep="-5"),
     "Ручная сборка: операция 2 «Пайка экрана к лепестку»: время подготовки должно быть числом не меньше нуля"),
    (lambda config: config["ops"][2].update(qty="0"),
     "Ручная сборка: операция 3 «Опрессовка терминалов»: количество на изделие должно быть числом больше нуля"),
    (lambda config: config["ops"][1].update(label=" "), "Ручная сборка: операция 2: укажите название своей операции"),
    (lambda config: config["ops"][0].update(templateId=""), "Ручная сборка: операция 1: не выбрана карточка из библиотеки"),
    # A generator type (cutWire, insTerm…) is a generator operation here (test_assembly_schema.py); an unknown one is not.
    (lambda config: config["ops"][2].update(type="pressFit"),
     "Ручная сборка: операция 3: непонятный вид операции — выберите карточку из библиотеки или «Своя операция»"),
    (lambda config: config["ops"][0]["components"][0].update(qty="много"),
     "Ручная сборка: операция 1 «Резка»: количество «МГТФ 0,35» должно быть числом больше нуля"),
    (lambda config: config.update(route={"blocks": [{"key": "b1", "ops": [1, 2], "final": True}], "edges": []}),
     "Схема полуфабрикатов: операция 3 не входит ни в один блок"),
])
def test_refuses_a_manual_list_it_cannot_build_in_russian(change, message):
    config = manual()
    change(config)
    with pytest.raises(ValueError) as error:
        build(config)
    assert str(error.value) == message


def test_generator_operations_of_existing_schemes_keep_their_etk_keys():
    """Every golden kit opened in «Ручная сборка» builds exactly the ETK keys it built before colliding generator
    operations were told apart (fixtures/manual-etk-keys.json): saved techcards and issued ETKs keep theirs."""
    from app.modules.techcards.assembly_schema import generator_to_assembly
    from tests.techcards import test_etk_golden as golden

    expected = json.loads((Path(__file__).parent / "fixtures" / "manual-etk-keys.json").read_text(encoding="utf-8"))
    assert set(expected) == set(golden.KITS)
    for kit, keys in expected.items():
        document = build_etk(generator_to_assembly(golden._kit(kit)), golden.CARDS,
                             operation_library_commit=golden.LIBRARY["sourceCommit"])
        assert [operation_key(operation) for operation in document["operations"]] == keys, kit
