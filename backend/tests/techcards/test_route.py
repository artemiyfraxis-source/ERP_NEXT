"""Block scheme of semi-finished products (ARCH-TRACK-1, Vasily Kostromin's model): derived from the generator
operations or hand-edited, validated, with block totals; written by build_etk and derived on read for older ETKs."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from app.modules.techcards.domain.route import RouteError, RouteOp, derive_route, parse_route, route_json, route_totals
from app.modules.techcards.etk_builder import ETK_VERSION, attach_route, build_etk, document_route
from app.modules.techcards.operation_cards import parse_operation_library


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
OPS = (RouteOp(1, name="Резка"), RouteOp(2, name="Обжим"), RouteOp(3, name="Монтаж"))


def kit(name: str) -> dict:
    return deepcopy(FIXTURES["files"][f"kit-{name}.json"]["payload"]["config"])


def build(config: dict) -> dict:
    return build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


def edges(route: dict) -> list[tuple]:
    return [(edge["from"], edge["to"], edge["per_unit"]) for edge in route["edges"]]


def blocks(route: dict) -> dict[str, dict]:
    return {block["key"]: block for block in route["blocks"]}


def minutes(document: dict) -> list[Decimal]:
    return [
        sum((Decimal(row["norm"].replace(",", ".")) for row in operation["time"]), Decimal(0))
        for operation in document["operations"]
    ]


def test_linear_card_is_a_chain_of_one_operation_blocks_ending_in_the_product():
    config = kit("720К.-КБ1")
    document = build(config)
    route = document["route"]

    assert document["schema_version"] == ETK_VERSION == 4
    assert route["source"] == "derived" and route["final"] == "b5"
    assert [(block["key"], block["ops"], block["final"]) for block in route["blocks"]] == [
        ("b1", [1], False), ("b2", [2], False), ("b3", [3], False), ("b4", [4], False), ("b5", [5], True),
    ]
    assert edges(route) == [("b1", "b2", 1), ("b2", "b3", 1), ("b3", "b4", 1), ("b4", "b5", 1)]
    final = blocks(route)["b5"]
    assert final["name"] == config["assemblyName"] == document["operations"][-1]["result"][0]["name"]
    assert blocks(route)["b1"]["inputs"] == 0 and final["inputs"] == 1 and final["outputs"] == 1
    # Per one product the chain adds up to every operation's time norm over the batch.
    total = sum(minutes(document), Decimal(0)) / Decimal(config["partQty"])
    assert Decimal(str(final["rolled_minutes"])) == total.quantize(Decimal("0.0001"))
    assert final["rolled_ops"] == 5


def test_tut_pieces_are_a_side_branch_into_the_operation_that_puts_them_on():
    """345К.2: ТУТ cut → put on the wires before soldering; ТТК cut → put on the harness. Both are real branches."""
    route = build(kit("345К.2-КБ1"))["route"]

    assert edges(route) == [
        ("b1", "b3", 1), ("b2", "b3", 1), ("b3", "b4", 1), ("b4", "b6", 1), ("b5", "b6", 1),
        ("b6", "b7", 1), ("b7", "b8", 1), ("b8", "b9", 1),
    ]
    by_key = blocks(route)
    assert by_key["b2"]["name"] == "Трубка ТУТ 10/5мм, черная нарезана: 2 шт по 30 мм" and by_key["b2"]["inputs"] == 0
    assert by_key["b3"]["name"].endswith("; трубка надета на провода") and by_key["b3"]["inputs"] == 2
    assert by_key["b6"]["inputs"] == 2 and by_key["b9"]["final"]


def test_two_cuts_and_the_tut_pieces_meet_in_the_next_operation():
    route = build(kit("591К.5-КБ2"))["route"]
    assert edges(route)[:4] == [("b1", "b4", 1), ("b2", "b4", 1), ("b3", "b4", 1), ("b4", "b5", 1)]
    assert blocks(route)["b4"]["rolled_ops"] == 4


def test_strip_and_splice_stay_in_the_chain_they_work_on():
    config = {
        "assemblyName": "Жгут", "partQty": 1, "wires": [{"art": "ПВ3", "qty": 2, "length": 300}],
        "ops": [
            {"type": "cutWire", "opNum": "CUT_WIRE_auto"}, {"type": "stripCable", "opNum": "STRIP_WIRE", "tutCount": 2},
            {"type": "splice", "opNum": "US_WELD", "units": 1}, {"type": "insTerm", "opNum": "INS_terminal", "side": "A", "ep": {"connArt": "XHP-2"}},
        ],
    }
    route = build(config)["route"]
    assert edges(route) == [("b1", "b2", 1), ("b2", "b3", 1), ("b3", "b4", 1)]
    assert blocks(route)["b2"]["name"] == "Провода нарезаны: ПВ3 — 2 шт по 300 мм; концы разделаны"


def test_tut_pieces_nobody_puts_on_and_a_late_cut_still_reach_the_product():
    route = derive_route([
        RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провода"), RouteOp(2, "cutTut", "CUT_TUT_short", "ТУТ"),
        RouteOp(3, "twist", "TWIST", "Свивка"), RouteOp(4, "cutWire", "CUT_WIRE_auto", "Провода 2"),
        RouteOp(5, "insWrap", "INS_WRAP", "Обмотка"),
    ], "Изделие")
    assert [(edge.source, edge.target) for edge in route.edges] == [("b1", "b3"), ("b2", "b5"), ("b3", "b5"), ("b4", "b5")]
    assert route.final.key == "b5" and route.final.name == "Изделие"


def test_untyped_supplymap_stages_chain_and_a_cut_code_starts_a_branch():
    route = derive_route([
        RouteOp(1, code="CUT_WIRE_auto", name="Резка"), RouteOp(2, code="CUT_TUT_short", name="Резка ТУТ"),
        RouteOp(3, code="INS_TUT", name="Надеть ТУТ"), RouteOp(4, code="PRS_TERMINAL_auto", name="Опрессовка"),
    ])
    assert [(edge.source, edge.target) for edge in route.edges] == [("b1", "b3"), ("b2", "b3"), ("b3", "b4")]
    assert route.final.name == "Опрессовка"


def test_hand_edited_scheme_groups_operations_into_blocks_and_rolls_up_per_unit():
    config = kit("720К.-КБ1")
    plain = build(config)
    config["route"] = {
        "blocks": [
            {"key": "cut", "ops": [1]},
            {"key": "A", "name": "Сторона А", "ops": ["3", 2], "components": ["Molex 39012140", {"art": "x"}]},
            {"key": "B", "ops": [4, 5], "final": True},
        ],
        "edges": [{"from": "cut", "to": "A", "per_unit": "2"}, {"from": "A", "to": "B"}],
    }
    document = build(config)
    route = document["route"]

    assert document["operations"] == plain["operations"] and document["topology"] == plain["topology"]
    assert route["source"] == "manual" and route["final"] == "B"
    # A hand-made block keeps its order of work (ПФ, 13.10): «3, 2» stays «3, 2».
    assert [(block["key"], block["ops"]) for block in route["blocks"]] == [("cut", [1]), ("A", [3, 2]), ("B", [4, 5])]
    by_key = blocks(route)
    assert by_key["cut"]["name"] == blocks(plain["route"])["b1"]["name"]
    assert by_key["A"]["name"] == "Сторона А" and by_key["B"]["name"] == config["assemblyName"]
    assert by_key["A"]["components"] == ["Molex 39012140", {"art": "x"}] and by_key["cut"]["components"] == []
    norms = minutes(plain)
    quantity = Decimal(config["partQty"])
    assert Decimal(str(by_key["A"]["minutes"])) == norms[1] + norms[2]
    unit = [value / quantity for value in norms]
    rolled_a = unit[1] + unit[2] + 2 * unit[0]
    assert by_key["A"]["inputs"] == 2
    assert Decimal(str(by_key["A"]["rolled_minutes"])) == rolled_a.quantize(Decimal("0.0001"))
    assert Decimal(str(by_key["B"]["rolled_minutes"])) == (unit[3] + unit[4] + rolled_a).quantize(Decimal("0.0001"))
    assert edges(route) == [("cut", "A", 2), ("A", "B", 1)]


def test_totals_multiply_inputs_per_unit_and_count_shared_operations_once():
    ops = [RouteOp(1, minutes=Decimal(10)), RouteOp(2, minutes=Decimal(20)), RouteOp(3, minutes=Decimal(30)),
           RouteOp(4, minutes=Decimal(40))]
    route = parse_route({
        "blocks": [{"key": "W", "ops": [1]}, {"key": "X", "ops": [2]}, {"key": "Y", "ops": [3]},
                   {"key": "Z", "ops": [4], "final": True}],
        "edges": [{"from": "W", "to": "X"}, {"from": "W", "to": "Y", "per_unit": 2},
                  {"from": "X", "to": "Z", "per_unit": 3}, {"from": "Y", "to": "Z", "per_unit": "1,5"}],
    }, ops)
    totals = route_totals(route, {op.position: op.minutes for op in ops}, Decimal(10))

    assert totals["W"].unit_minutes == 1 and totals["W"].rolled_minutes == 1
    assert totals["X"].rolled_minutes == 2 + 1
    assert totals["Y"].rolled_minutes == 3 + 2 * 1
    # Z = 4 + 3 × X + 1,5 × Y: W is needed on both paths, its time counts per piece of each.
    assert totals["Z"].rolled_minutes == 4 + 3 * 3 + Decimal("1.5") * 5
    assert totals["Z"].inputs == Decimal("4.5") and totals["Z"].rolled_ops == 4
    payload = route_json(route, totals)
    assert blocks(payload)["Z"]["rolled_minutes"] == 20.5 and blocks(payload)["Z"]["inputs"] == 4.5
    assert edges(payload)[-1] == ("Y", "Z", 1.5)


def _scheme(*items, edges=()):
    """Blocks as given; an edge is (from, to) or (from, to, extra fields)."""
    return {"blocks": list(items), "edges": [
        {"from": edge[0], "to": edge[1], **(edge[2] if len(edge) > 2 else {})} for edge in edges
    ]}


@pytest.mark.parametrize(("raw", "message"), [
    ([], "схема должна быть объектом с блоками и стрелками"),
    ({"blocks": []}, "нет ни одного блока"),
    (_scheme({"ops": [1, 2, 3], "final": True}), "у блока №1 нет ключа"),
    (_scheme({"key": "a", "ops": [1]}, {"key": "a", "ops": [2, 3], "final": True}), "ключ блока «a» повторяется"),
    (_scheme({"key": "a", "ops": []}, {"key": "b", "ops": [1, 2, 3], "final": True}), "в блоке «a» нет операций"),
    (_scheme({"key": "a", "ops": ["x"], "final": True}), "«x» — укажите номер операции"),
    (_scheme({"key": "a", "ops": [1, 2, 3, 4], "final": True}), "операции 4 нет в техкарте (операций 3)"),
    (_scheme({"key": "a", "ops": [1, 2]}, {"key": "b", "ops": [2, 3], "final": True}, edges=[("a", "b")]),
     "операция 2 входит в несколько блоков — «Обжим» и «Монтаж»"),
    (_scheme({"key": "a", "ops": [1]}, {"key": "b", "ops": [3], "final": True}, edges=[("a", "b")]),
     "операция 2 не входит ни в один блок"),
    (_scheme({"key": "a", "ops": [1], "final": True}), "операции 2, 3 не входят ни в один блок"),
    (_scheme({"key": "a", "ops": [1, 2, 3]}), "не отмечен итоговый блок (изделие)"),
    (_scheme({"key": "a", "ops": [1, 2], "final": True}, {"key": "b", "ops": [3], "final": True}),
     "итоговый блок должен быть один, отмечено: «Обжим», «Монтаж»"),
    (_scheme({"key": "a", "ops": [1, 2, 3], "final": True}, edges=[("x", "a")]), "стрелка ведёт из неизвестного блока «x»"),
    (_scheme({"key": "a", "ops": [1, 2]}, {"key": "b", "ops": [3], "final": True}, edges=[("a", "a"), ("a", "b")]),
     "стрелка из блока «Обжим» в него же"),
    (_scheme({"key": "a", "ops": [1, 2]}, {"key": "b", "ops": [3], "final": True}, edges=[("a", "b"), ("a", "b")]),
     "стрелка «Обжим» → «Монтаж» повторяется"),
    (_scheme({"key": "a", "ops": [1, 2]}, {"key": "b", "ops": [3], "final": True}, edges=[("a", "b", {"per_unit": 0})]),
     "количество на стрелке «Обжим» → «Монтаж» должно быть числом больше нуля"),
    (_scheme({"key": "a", "ops": [1, 2]}, {"key": "b", "ops": [3], "final": True}, edges=[("a", "b", {"per_unit": "два"})]),
     "количество на стрелке «Обжим» → «Монтаж» должно быть числом больше нуля"),
    (_scheme({"key": "a", "ops": [1, 2]}, {"key": "b", "ops": [3], "final": True}, edges=[("b", "a")]),
     "из итогового блока «Монтаж» не может выходить стрелка"),
    (_scheme({"key": "a", "ops": [1]}, {"key": "b", "ops": [2]}, {"key": "c", "ops": [3], "final": True},
             edges=[("a", "b"), ("b", "a"), ("b", "c")]),
     "схема зациклена — «Резка» → «Обжим» → «Резка»"),
    (_scheme({"key": "a", "ops": [1]}, {"key": "b", "ops": [2]}, {"key": "c", "ops": [3], "final": True},
             edges=[("a", "c")]),
     "блок «Обжим» не доходит до итогового блока"),
    (_scheme({"key": "a", "ops": [1]}, {"key": "b", "ops": [2]}, {"key": "c", "ops": [3], "final": True}),
     "блоки «Резка», «Обжим» не доходят до итогового блока"),
    (_scheme({"key": "a", "ops": [1, 2, 3], "final": True, "components": "Molex"}), "комплектующие блока «Монтаж» должны быть списком"),
])
def test_hand_edited_scheme_errors_are_explained_in_russian(raw, message):
    with pytest.raises(RouteError) as error:
        parse_route(raw, OPS)
    assert str(error.value) == f"Схема полуфабрикатов: {message}"


def test_build_stops_on_a_scheme_that_does_not_fit_the_operations():
    config = kit("720К.-КБ1")
    config["route"] = {"blocks": [{"key": "all", "ops": [1, 2, 3, 4], "final": True}]}
    with pytest.raises(ValueError, match="^Схема полуфабрикатов: операция 5 не входит ни в один блок$"):
        build(config)
    config["route"] = None
    assert build(config)["route"]["source"] == "derived"


def test_older_etk_gets_the_same_route_on_read_without_a_rebuild():
    document = build(kit("708К.2-КБ1"))
    old = {key: value for key, value in deepcopy(document).items() if key != "route"} | {"schema_version": 2}

    assert document_route(old) == document["route"]
    shown = attach_route(old)
    assert shown["route"] == document["route"] and "route" not in old
    assert {key: value for key, value in shown.items() if key != "route"} == old


def test_read_keeps_a_stored_hand_edited_scheme_with_fresh_totals_and_drops_one_that_no_longer_fits():
    config = kit("720К.-КБ1")
    config["route"] = {"blocks": [{"key": "A", "ops": [1, 2, 3]}, {"key": "B", "ops": [4, 5], "final": True}],
                       "edges": [{"from": "A", "to": "B"}]}
    document = build(config)
    document["operations"][0]["time"] = [{"name": "Резка", "norm": "100"}]
    route = document_route(document)
    assert route["source"] == "manual" and [block["key"] for block in route["blocks"]] == ["A", "B"]
    assert blocks(route)["A"]["minutes"] != blocks(document["route"])["A"]["minutes"]

    trimmed = deepcopy(document)
    trimmed["operations"] = trimmed["operations"][:4]
    trimmed["build_config"]["ops"] = trimmed["build_config"]["ops"][:4]
    route = document_route(trimmed)
    assert route["source"] == "derived" and route["final"] == "b4"


def test_supplymap_etk_without_generator_types_reads_as_a_chain_timed_from_seconds():
    document = {
        "operations": [
            {"position": "1", "code": "CUT_WIRE_auto", "connector": "", "title": "Резка",
             "times": {"operation_seconds": 12.5, "setup_seconds": 60}, "card": None},
            {"position": "2", "code": "PRS_TERMINAL_auto", "connector": "", "title": "Опрессовка",
             "times": {"operation_seconds": 8, "setup_seconds": 0}, "card": None},
        ],
        "build_config": {"source": "SUPPLYMAP", "ops": [
            {"stage": "Резка", "opNumber": "CUT_WIRE_auto", "tOp": 12.5, "tPrep": 60},
            {"stage": "Опрессовка", "opNumber": "PRS_TERMINAL_auto", "tOp": 8, "tPrep": 0},
        ]},
    }
    route = document_route(document)
    assert [(block["key"], block["name"]) for block in route["blocks"]] == [("b1", "Резка"), ("b2", "Опрессовка")]
    assert edges(route) == [("b1", "b2", 1)]
    # Без норм-строк время = секунды на изделие × партия, подготовка (setup_seconds) не считается (ETK 4).
    assert blocks(route)["b1"]["minutes"] == 0.2083 and blocks(route)["b2"]["rolled_minutes"] == 0.3417


def test_read_survives_documents_without_operations_or_with_odd_time():
    assert document_route({"operations": []}) is None and attach_route(None) is None
    assert attach_route({"warnings": []}) == {"warnings": []}
    odd = {"operations": [{"code": "10", "time": 10}, {"code": "20", "time": 20, "result": "x"}]}
    route = document_route(odd)
    assert [block["name"] for block in route["blocks"]] == ["10", "20"] and route["blocks"][1]["minutes"] == 0


# ── route v2 («Сложная сборка», 16.10): what flows on an edge, kit blocks, level labels ────────────────────────────

V2_OPS = [RouteOp(position, name=f"Операция {position}") for position in range(1, 6)]
V2 = {
    "blocks": [
        {"key": "W", "ops": [1], "kit": True}, {"key": "V", "ops": [2]}, {"key": "X", "ops": [3]},
        {"key": "Y", "ops": [4]}, {"key": "Z", "ops": [5], "final": True},
    ],
    "edges": [
        {"from": "W", "to": "X", "item": " НВ4 0,12 белый ×2 "},
        {"from": "W", "to": "Y", "item": "НВ4 0,12 белый ×2; ТТК 8/2 ×1"},
        {"from": "V", "to": "Y"}, {"from": "X", "to": "Z"}, {"from": "Y", "to": "Z", "item": ""},
    ],
}


def v2_json(raw: dict) -> dict:
    route = parse_route(raw, V2_OPS)
    return route_json(route, route_totals(route, {}, Decimal(1)))


def test_edge_items_kit_blocks_and_level_labels_round_trip():
    payload = v2_json(V2)

    assert [(block["key"], block.get("kit"), block["label"]) for block in payload["blocks"]] == [
        ("W", True, "1.1"), ("V", None, "1.2"), ("X", None, "2.1"), ("Y", None, "2.2"), ("Z", None, "3"),
    ]
    assert [(edge["from"], edge["to"], edge.get("item")) for edge in payload["edges"]] == [
        ("W", "X", "НВ4 0,12 белый ×2"), ("W", "Y", "НВ4 0,12 белый ×2; ТТК 8/2 ×1"),
        ("V", "Y", None), ("X", "Z", None), ("Y", "Z", None),
    ]
    # Read back (the stored route, «Сохранить ПФ»): the same JSON byte for byte.
    assert json.dumps(v2_json(payload), ensure_ascii=False) == json.dumps(payload, ensure_ascii=False)


def test_a_level_with_one_block_is_its_number_and_the_longest_path_decides_the_level():
    route = parse_route(_scheme(
        {"key": "a", "ops": [1]}, {"key": "b", "ops": [2]}, {"key": "c", "ops": [3], "final": True},
        edges=[("a", "b"), ("a", "c"), ("b", "c")],
    ), OPS)
    payload = route_json(route, route_totals(route, {}, Decimal(1)))
    assert [block["label"] for block in payload["blocks"]] == ["1", "2", "3"]


def test_a_route_without_items_or_kits_keeps_its_old_shape_and_reads_back_byte_equal():
    document = build(kit("345К.2-КБ1"))
    old = document["route"]
    assert all(set(block) == {"key", "name", "ops", "final", "components", "outputs", "inputs", "minutes",
                              "unit_minutes", "rolled_minutes", "rolled_ops", "label"} for block in old["blocks"])
    assert all(set(edge) == {"from", "to", "per_unit"} for edge in old["edges"])
    stored = deepcopy(old) | {"source": "manual"}
    document["route"] = stored
    assert json.dumps(document_route(document), ensure_ascii=False) == json.dumps(stored, ensure_ascii=False)
    # A route stored before the labels reads with them added and nothing else changed.
    unlabelled = deepcopy(stored)
    for block in unlabelled["blocks"]:
        del block["label"]
    document["route"] = unlabelled
    assert document_route(document) == stored


def test_one_edge_per_pair_still_holds_with_items_and_an_item_must_be_text():
    doubled = deepcopy(V2)
    doubled["edges"].append({"from": "W", "to": "X", "item": "ТТК 8/2 ×1"})
    with pytest.raises(RouteError, match="стрелка «Операция 1» → «Операция 3» повторяется"):
        parse_route(doubled, V2_OPS)
    numbered = deepcopy(V2)
    numbered["edges"][0]["item"] = 2
    with pytest.raises(RouteError, match="подпись стрелки №1 должна быть текстом"):
        parse_route(numbered, V2_OPS)
    # Only `true` makes a kit (a string «false» is not one).
    loose = deepcopy(V2)
    loose["blocks"][0]["kit"] = "false"
    assert "kit" not in v2_json(loose)["blocks"][0]
