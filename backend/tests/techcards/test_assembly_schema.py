"""One schema for the generator and «Ручная сборка» (owner, 05.10): a generator config converts into a manual config
(blocks with operations), generator operations keep their type and fields, and the manual build makes each of them with
the generator's own typed code — so the converted assembly builds the same ETK, except the input rows (semis), which
come from the block scheme. Every such difference is listed here with its reason."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.production.launchable import route_from_document
from app.modules.techcards.assembly_schema import generator_to_assembly
from app.modules.techcards.domain.route import RouteError
from app.modules.techcards.etk_builder import build_etk, document_route
from app.modules.techcards.operation_cards import parse_operation_library
from app.shared.http_auth import Principal, authenticated_principal
from app.workflows.techcard_build import enrich_terminal_norms
from tests.factories import Actor
from tests.techcards import test_etk_golden as golden
from tests.techcards.test_etk_builder import _crimp_tin_config
from tests.techcards.test_etk_manual import CRIMP, CUT


CARDS = golden.CARDS
ERP_CARDS = CARDS + parse_operation_library(golden.ERP_LIBRARY["templates"])
COMPARED = ("code", "type", "side", "connector", "connector_article", "template_id", "title", "card", "components",
            "result", "result_label", "time", "times", "fields")


def _harness_topology() -> dict[str, Any]:
    ends = {"connArt": "SHR-06V-S-B", "connName": "Корпус SH 6"}
    return {"ops": [
        {"type": "insTerm", "side": "A", "ep": ends, "wires": [{"art": "черный", "qty": 6}], "wireSources": [""]},
        {"type": "insTerm", "side": "B", "ep": ends, "wires": [{"art": "черный", "qty": 6}], "wireSources": ["Корпус SH 6"]},
    ]}


def _crimp_tin_free_end() -> dict[str, Any]:
    return _crimp_tin_config({"a": True, "lenA": "1,2", "b": True, "lenB": ""}, tin={"enabled": True, "stripLen": "2,5"})


# Every generator config the builder tests build: current-etk.json kits (etk-golden.json cases) and the hand-made ones
# of test_etk_builder.py. test_coax.py / test_harness.py test the route generators (domain/coax.py, domain/harness.py),
# which give route steps, not build_etk configs — nothing of theirs goes through this path.
CASES: dict[str, tuple[Callable[[], dict[str, Any]], tuple]] = {
    **{name: (lambda name=name: golden._kit(name), CARDS) for name in golden.KITS},
    **{f"{name} нормы терминалов": (lambda name=name: enrich_terminal_norms(golden._kit(name), golden._records()), CARDS)
       for name in ("720К.-КБ1", "720К.1.2-КБ1", "708К.5-КБ1")},
    "720К.-КБ1 распиновка": (golden._pins, CARDS),
    "720К.-КБ1 соединения": (golden._connections, CARDS),
    "720К.-КБ1 нет шаблона": (golden._missing_template, CARDS),
    "708К.2-КБ1 без длины": (golden._no_length, CARDS),
    "резка с обжимом": (lambda: golden._cut_crimp(100, 2), CARDS),
    "резка с обжимом, мерительный инструмент": (lambda: golden._cut_crimp(10, 1), CARDS),
    "сростки и разделка": (golden._splice_strip, CARDS),
    "резка без длины": (lambda: {"wires": [{"art": "ПВ3", "length": 0}], "ops": [{"type": "cutWire", "opNum": "CUT_WIRE"}]}, CARDS),
    "коаксиал": (golden._coax, CARDS),
    "резка, обжим, лужение": (golden._cut_crimp_tin, ERP_CARDS),
    "резка, обжим, лужение — свободный конец": (_crimp_tin_free_end, CARDS),
    "жгут: источник по имени разъёма": (_harness_topology, CARDS),
}

# Why the input rows of a converted assembly differ from the typed ETK. Everything else per operation is equal.
REASONS = {
    "провода": "типовая ЭТК даёт на входе опрессовки / монтажа / пайки каждый провод своей строкой с его количеством; "
               "в схеме вход — полуфабрикат блока-источника, одна штука на изделие (время блока считается на изделие)",
    "ТУТ": "надевание ТУТ: типовая — провода (количество проводов × партия) и отрезки ТУТ (отрезков × партия); "
           "в схеме — блоки-входы: провода, все открытые ветви и резка ТУТ, по штуке на изделие",
    "количество": "тот же полуфабрикат, но типовая оставляет количество пустым или считает провода/отрезки, когда "
                  "предыдущая операция не даёт штучного полуфабриката; у стрелки схемы количество всегда (на изделие × партия)",
    "цепочка": "схема ведёт продукт через каждую операцию, и через те, что типовая цепочка пропускает (ТУТ на провода, "
               "разделка концов); типовая называет вход последним продвигающим продуктом",
    "первая": "у операции нет предыдущего продукта: типовая оставляет вход пустым, в схеме вход — блок-источник",
    "без входа": "операция начинает ветвь схемы (до неё нет резки): типовая перечисляет её провода, у блока-источника "
                 "схемы входа нет",
}

# case → {position: reason}: every operation whose semis differ. Cases not listed have equal semis everywhere.
_WIRES_720 = {2: "провода", 3: "провода"}
_708 = {3: "ТУТ", 4: "цепочка", 5: "количество", 6: "провода", 7: "провода"}
SEMIS_DIFFERENCES: dict[str, dict[int, str]] = {
    "720К.-КБ1": _WIRES_720, "720К.-КБ1 нормы терминалов": _WIRES_720, "720К.-КБ1 распиновка": _WIRES_720,
    "720К.-КБ1 соединения": _WIRES_720, "720К.-КБ1 нет шаблона": _WIRES_720,
    "720К.1.2-КБ1": {2: "провода"}, "720К.1.2-КБ1 нормы терминалов": {2: "провода"},
    "708К.2-КБ1": _708, "708К.2-КБ1 без длины": _708, "708К.5-КБ1": _708, "708К.5-КБ1 нормы терминалов": _708,
    "591К.5-КБ2": {4: "ТУТ", 5: "провода", 6: "провода", 7: "провода"},
    "729К.2-КБ1": {3: "ТУТ", 4: "провода"},
    "345К.2-КБ1": {3: "ТУТ"},
    "резка с обжимом": {2: "провода"}, "резка с обжимом, мерительный инструмент": {2: "провода"},
    "резка, обжим, лужение": {2: "провода"},
    "сростки и разделка": {2: "количество", 3: "цепочка", 4: "количество"},
    "коаксиал": {2: "первая"},
    "жгут: источник по имени разъёма": {1: "без входа"},
}


def _kind(semis_typed: list[dict], semis_manual: list[dict], operation: dict) -> str:
    if not semis_typed:
        return "первая"
    if not semis_manual:
        return "без входа"
    if operation["type"] in {"insTut", "endCap"}:
        return "ТУТ"
    if [row["name"] for row in semis_typed] == [row["name"] for row in semis_manual]:
        return "количество"
    if operation["type"] in {"prsTerm", "insTerm", "solderConn"}:
        return "провода"
    return "цепочка"


def _build(config: dict[str, Any], cards) -> dict[str, Any]:
    return build_etk(config, cards, operation_library_commit=golden.LIBRARY["sourceCommit"])


def _pair(case: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    factory, cards = CASES[case]
    converted = generator_to_assembly(factory())
    return _build(factory(), cards), _build(converted, cards), converted


@pytest.mark.parametrize("case", sorted(CASES))
def test_converted_assembly_builds_the_same_operations_through_the_manual_path(case: str):
    typed, manual, converted = _pair(case)
    assert manual["kit"]["mode"] == "manual" and converted["mode"] == "manual"
    assert len(manual["operations"]) == len(typed["operations"])
    for expected, built in zip(typed["operations"], manual["operations"]):
        for key in COMPARED:
            assert built[key] == expected[key], (case, expected["position"], key)
    assert manual["topology"] == typed["topology"]
    assert manual.get("connections") == typed.get("connections")
    assert manual["warnings"] == typed["warnings"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_every_difference_in_the_input_rows_is_a_listed_one(case: str):
    typed, manual, _ = _pair(case)
    found = {
        expected["position"]: _kind(expected["semis"], built["semis"], expected)
        for expected, built in zip(typed["operations"], manual["operations"])
        if expected["semis"] != built["semis"]
    }
    assert found == SEMIS_DIFFERENCES.get(case, {})
    assert set(found.values()) <= set(REASONS)
    # The manual input is always the scheme's: the blocks feeding the operation, per_unit × batch.
    route = manual["route"]
    names = {block["key"]: block["name"] for block in route["blocks"]}
    batch = Decimal(manual["kit"]["part_quantity"].replace(",", "."))
    for block in route["blocks"]:
        first = manual["operations"][min(block["ops"]) - 1]
        assert [(row["name"], row["norm"]) for row in first["semis"]] == [
            (names[edge["from"]], str(Decimal(str(edge["per_unit"])) * batch).replace(".", ","))
            for edge in route["edges"] if edge["to"] == block["key"]
        ]


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_scheme_is_the_generators_and_production_gets_the_same_steps(case: str):
    typed, manual, converted = _pair(case)
    derived, drawn = typed["route"], manual["route"]
    assert (derived["source"], drawn["source"]) == ("derived", "manual")
    shape = lambda route: (
        [(block["key"], block["ops"], block["final"], block["minutes"], block["rolled_minutes"]) for block in route["blocks"]],
        route["edges"],
    )
    assert shape(drawn) == shape(derived)
    # Block names are the generator's product names; where the generator names none (coax), the typed scheme shows
    # «Операция N» / the card title, the converted one leaves the name empty for the editor to title — and the read
    # path (document_route) titles it the typed way. The ETK rows keep the generator's (empty) names either way.
    read = document_route(manual)
    for typed_block, block, read_block in zip(derived["blocks"], drawn["blocks"], read["blocks"]):
        assert block["name"] in {typed_block["name"], ""}, (case, block["key"])
        assert read_block == {**block, "name": block["name"] or typed_block["name"]}
    assert {**read, "blocks": []} == {**drawn, "blocks": []}
    # Same steps with the same codes, stages and norms; production follows the scheme's blocks now (manual source).
    core = lambda steps: [(s.position, s.code, s.stage, s.description, s.seconds_per_unit, s.setup_seconds) for s in steps]
    typed_steps, manual_steps = route_from_document(typed), route_from_document(manual)
    assert core(manual_steps) == core(typed_steps)
    owner = {position: block["key"] for block in drawn["blocks"] for position in block["ops"]}
    assert [step.block for step in manual_steps] == [owner[step.position] for step in manual_steps]
    assert [step.block for step in typed_steps] == [f"b{step.position}" for step in typed_steps]


def test_conversion_keeps_the_generator_data_and_operations_as_they_are():
    config = golden._connections()
    config.update(coax={"cableArt": "RG-174"}, mode="wire")
    converted = generator_to_assembly(config)
    assert converted["mode"] == "manual"
    for key in ("assemblyIndex", "assemblyName", "partQty", "bigAssembly", "wires", "twist", "tin", "strip",
                "connections", "coax"):
        assert converted[key] == config[key], key
    assert [{k: v for k, v in op.items() if k != "seq"} for op in converted["ops"]] == config["ops"]
    assert [op["seq"] for op in converted["ops"]] == [1, 2, 3, 4, 5]
    route = converted["route"]
    assert [(block["key"], block["ops"], block["final"]) for block in route["blocks"]] == [
        ("b1", [1], False), ("b2", [2], False), ("b3", [3], False), ("b4", [4], False), ("b5", [5], True),
    ]
    assert route["blocks"][1]["name"].startswith("Заготовка: 2 пров. с обжатым терм.")
    assert route["blocks"][-1]["name"] == config["assemblyName"]
    assert all(edge["per_unit"] == 1 for edge in route["edges"])
    assert "seq" not in config["ops"][0], "the source config is not changed"


def test_a_hand_drawn_generator_scheme_is_kept_with_its_quantities():
    config = golden._cut_crimp(10, 1)
    config["route"] = {"blocks": [{"key": "cut", "name": "Мерный провод", "ops": [1]},
                                  {"key": "end", "ops": [2], "final": True}],
                       "edges": [{"from": "cut", "to": "end", "per_unit": "2,5"}]}
    converted = generator_to_assembly(config)
    assert converted["route"] == {
        "blocks": [{"key": "cut", "name": "Мерный провод", "ops": [1], "final": False, "components": []},
                   {"key": "end", "name": "Жгут", "ops": [2], "final": True, "components": []}],
        "edges": [{"from": "cut", "to": "end", "per_unit": 2.5}],
    }
    cut, mount = _build(converted, CARDS)["operations"]
    assert cut["result"] == [{"name": "ПВ3 250мм, обжатый терминал SXH-001T-P0.6", "norm": "10", "unit": "Шт."}]
    assert cut["time"][0]["name"] == "ПВ3 250мм, обжатый терминал SXH-001T-P0.6"
    assert mount["semis"] == [{"name": "Мерный провод", "norm": "25", "unit": "Шт."}]


def test_a_manual_config_comes_back_as_it_is_and_an_empty_one_is_refused():
    manual = {"mode": "manual", "ops": [{"type": "customOp", "label": "Пайка"}]}
    assert generator_to_assembly(manual) == manual
    with pytest.raises(ValueError, match="Нет операций для сборки"):
        generator_to_assembly({"ops": ["мусор"]})
    with pytest.raises(RouteError, match="операция 2 не входит ни в один блок"):
        generator_to_assembly({**golden._cut_crimp(1, 1), "route": {"blocks": [{"key": "b1", "ops": [1], "final": True}]}})


def test_generator_library_and_own_operations_mix_in_one_manual_assembly():
    """A converted assembly edited in the diagram editor: a library card and an own operation join the generator ones,
    a block is renamed — the generator operations keep their typed build and take the block's name."""
    config = generator_to_assembly(golden._cut_crimp(10, 2))
    config["ops"][0].update(label="Резка с обжимом на автомате", params="L = 250 мм",
                            components=[{"art": "Маркер", "name": "Бирка", "qty": 1}])
    config["ops"].insert(1, {"type": "customOp", "label": "Скрутка вручную", "tPrep": "60", "tOp": "10", "seq": 3,
                             "steps": ["Скрутить провода"], "result": "Скрученная пара"})
    config["ops"].append({"type": "libraryOp", "templateId": CRIMP, "opNum": "PRS_TERMINAL_hand", "label": "Опрессовка",
                          "tOp": "20", "seq": 4})
    config["route"] = {
        "blocks": [{"key": "b1", "name": "Провода с терминалом", "ops": [1, 2]}, {"key": "b2", "ops": [3]},
                   {"key": "b3", "ops": [4], "final": True}],
        "edges": [{"from": "b1", "to": "b2"}, {"from": "b2", "to": "b3"}],
    }
    cut, twist, mount, crimp = _build(config, CARDS)["operations"]

    assert [item["type"] for item in (cut, twist, mount, crimp)] == ["cutWireCrimp", "customOp", "insTerm", "libraryOp"]
    assert cut["card"]["name"] == "Резка с обжимом на автомате"
    assert cut["components"][-1] == {"art": "Маркер", "name": "Бирка", "norm": "10", "unit": "Шт."}
    assert cut["fields"]["params"] == "L = 250 мм" and cut["fields"]["applicator"] == "A16"
    # Inside the block the cut hands its typed product on; the block's product is named by the block.
    assert cut["result"] == [{"name": "ПВ3 250мм, обжатый терминал SXH-001T-P0.6", "norm": "20", "unit": "Шт."}]
    assert twist["semis"] == [{"name": "ПВ3 250мм; ст. А: обжат терм. SXH-001T-P0.6", "norm": "10", "unit": "Шт."}]
    assert twist["result"][0]["name"] == "Провода с терминалом"
    assert mount["semis"] == [{"name": "Провода с терминалом", "norm": "10", "unit": "Шт."}]
    assert mount["result"][0]["name"] == "Разъём XHP-2 смонтирован (ст. А, 1 пров.)"
    assert mount["connector"] == "XHP-2"
    assert crimp["result"] == [{"name": "Жгут", "norm": "10", "unit": "Шт."}]
    assert crimp["result_label"] == "Изделие" and mount["result_label"] == "Полуфабрикат"


def test_a_generator_operation_without_its_card_is_a_warning_not_an_error():
    config = generator_to_assembly(golden._cut_crimp(10, 1))
    config["ops"][0]["templateId"] = "нет-такой"
    document = _build(config, CARDS)
    assert document["operations"][0]["card"] is None
    assert document["warnings"] == [
        "Операция 1 (cutWireCrimp): нет карточки CUT_WIRE+1crimp", "Операция 2 (insTerm): нет карточки INS_terminal",
    ]


# ── POST /api/techcards/assembly/from-generator ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(authenticated_principal, None)


def _as(permissions: dict[str, str]) -> None:
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Технолог", department="КБ"), email="tech@example.test", roles=frozenset({"staff"}),
        permissions=permissions,
    )


def test_endpoint_returns_the_manual_config_without_saving(client):
    _as({"tcgen": "view"})
    config = golden._kit("720К.-КБ1")
    response = client.post("/api/techcards/assembly/from-generator", json={"config": config})
    assert response.status_code == 200, response.text
    assert response.json() == generator_to_assembly(config)


def test_endpoint_refuses_in_russian_and_asks_the_generator_section(client):
    _as({"tcgen": "view"})
    response = client.post("/api/techcards/assembly/from-generator", json={"config": {"ops": []}})
    assert response.status_code == 422 and response.json()["detail"] == "Нет операций для сборки"
    _as({"techcards": "edit"})
    response = client.post("/api/techcards/assembly/from-generator", json={"config": golden._kit("720К.-КБ1")})
    assert response.status_code == 403
