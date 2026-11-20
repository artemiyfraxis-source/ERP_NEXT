"""A ПФ per preparation operation (owner 20.11: «задание на печать маркировок, маркировку и на нарезку почему-то в
одном, трубки тоже в одном»): cutting the cable, printing labels and cutting tubes are separate input-less ПФ, each
feeding the stage that takes its pieces. The ETK's operation order (and so the printed label keys
`{task}:{op_index}:{mark_index}`) does not change — only the grouping into ПФ."""

from decimal import Decimal

from app.modules.techcards.domain.pf_split import generator_split, op_stages, same_split
from app.modules.techcards.domain.route import RouteOp, parse_route


def blocks(route) -> list:
    return [(block.key, block.ops, block.name) for block in route.blocks]


def edges(route) -> list:
    return sorted((edge.source, edge.target) for edge in route.edges)


# 708К. КБ1 (fakra1116Configs.json): FAKRA cut-strip on the machine, tape labels, transparent protective TUT.
FAKRA_TAPE = [
    RouteOp(1, "coaxCut", "CUT_COAX+2strip", "Кабель", key="coaxFakraCutStrip"),
    RouteOp(2, "coaxCutTut", "PRINT_MARK_LABEL", "Этикетки", key="coaxPrintLabel"),
    RouteOp(3, "coaxCutTut", "CUT_TUT_short", "ТУТ", key="coaxProtectCut"),
    RouteOp(4, "coaxInsTut", "INS_TUT", "ТУТ надета", key="coaxProtectIns"),
    RouteOp(5, "coaxInsSleeve", "INS_FAKRA_ST_PREP", "Детали А", side="A", key="coaxInsSleeveA"),
    RouteOp(6, "coaxInsSleeve", "INS_FAKRA_RA_PREP", "Детали В", side="B", key="coaxInsSleeveB"),
    RouteOp(7, "coaxInsTut", "INS_MARK_LABEL", "Этикетки наклеены", key="coaxInsLabel"),
    RouteOp(8, "coaxPinCrimp", "PRS_FAKRA_ST_PIN", "Контакт А", side="A", key="coaxPinCrimpA"),
    RouteOp(9, "coaxHousing", "INS_FAKRA_ST_HOUSING", "Корпус А", side="A", key="coaxBodyA"),
    RouteOp(10, "coaxPinCrimp", "PRS_FAKRA_RA_PIN", "Контакт В", side="B", key="coaxPinCrimpB"),
    RouteOp(11, "coaxHousing", "INS_FAKRA_RA_HOUSING", "Корпус В", side="B", key="coaxBodyB"),
    RouteOp(12, "coaxHeat", "HEAT_TYT_short", "Усадка", key="coaxHeat"),
    RouteOp(13, "coaxVswr", "CTRL_VSWR_coax", "КСВН", key="coaxVswr"),
]


def test_fakra_cable_labels_and_tubes_are_three_input_less_pf_feeding_the_marked_cable():
    stages = op_stages(FAKRA_TAPE)
    assert [stages[position] for position in (1, 2, 3, 4, 7)] == ["wires", "labels", "tubes", "marked", "marked"]
    route = generator_split(FAKRA_TAPE, "Кабельная сборка FAKRA")

    assert blocks(route) == [
        ("wires", (1,), "Нарезанный и разделанный кабель"), ("labels", (2,), "Этикетки"),
        ("tubes", (3,), "Нарезанные трубки"), ("marked", (4, 5, 6, 7), "Кабель с метками и деталями"),
        ("A", (8, 9), "Сторона А собрана"), ("B", (10, 11), "Сторона В собрана"),
        ("final", (12, 13), "Кабельная сборка FAKRA"),
    ]
    assert edges(route) == [("A", "B"), ("B", "final"), ("labels", "marked"), ("marked", "A"), ("tubes", "marked"),
                            ("wires", "marked")]
    assert not any(edge.target in {"wires", "labels", "tubes"} for edge in route.edges)
    assert not any(block.kit for block in route.blocks)


def test_a_fakra_etk_stored_with_one_blanks_pf_is_proposed_split_without_links_between_the_preparations():
    # The scheme of 708К. ETKs issued before 20.11: «Заготовки» [1, 2, 3] as a kit, per unit carried over.
    old = parse_route({
        "source": "generated",
        "blocks": [{"key": "blanks", "ops": [1, 2, 3], "kit": True}, {"key": "marked", "ops": [4, 5, 6, 7]},
                   {"key": "A", "ops": [8, 9]}, {"key": "B", "ops": [10, 11]}, {"key": "final", "ops": [12, 13], "final": True}],
        "edges": [{"from": "blanks", "to": "marked", "per_unit": 2}, {"from": "marked", "to": "A"}, {"from": "A", "to": "B"},
                  {"from": "B", "to": "final"}],
    }, FAKRA_TAPE, manual=True)
    proposed = generator_split(FAKRA_TAPE, "", old)

    assert [block.ops for block in proposed.blocks] == [(1,), (2,), (3,), (4, 5, 6, 7), (8, 9), (10, 11), (12, 13)]
    assert edges(proposed) == edges(generator_split(FAKRA_TAPE))
    assert not same_split(proposed, old)
    assert same_split(generator_split(FAKRA_TAPE, "", proposed), proposed)


# A «КОАКС» tab cable with tape labels (not FAKRA): cut, strip, print, protective TUT, pins, «Монтаж ТУТ», housings,
# «Наклейка этикеток». Labels went to «Нарезанные трубки» with the TUT cut (coaxCutTut is a tube-cut type).
COAX_TAPE = [
    RouteOp(1, "coaxCut", "CUT_COAX_auto", "Кабель", key="coaxCut"),
    RouteOp(2, "coaxStrip", "STRIP_COAX_auto", "Разделка А", side="A", key="coaxStripA"),
    RouteOp(3, "coaxStrip", "STRIP_COAX_auto", "Разделка В", side="B", key="coaxStripB"),
    RouteOp(4, "coaxCutTut", "PRINT_MARK_LABEL", "Этикетки", key="coaxPrintLabel"),
    RouteOp(5, "coaxCutTut", "CUT_TUT_short", "ТУТ", key="coaxProtectCut"),
    RouteOp(6, "coaxPin", "SDR_COAX_pin", "Пайка А", side="A", key="coaxPinA"),
    RouteOp(7, "coaxPin", "SDR_COAX_pin", "Пайка В", side="B", key="coaxPinB"),
    RouteOp(8, "coaxInsTut", "INS_TUT", "ТУТ надеты", key="coaxProtectIns"),
    RouteOp(9, "coaxInsSleeve", "INS_SLEEVE_coax", "Втулка", key="coaxInsSleeve"),
    RouteOp(10, "coaxHousing", "INS_housing_COAX", "Корпус А", side="A", key="coaxHousingA"),
    RouteOp(11, "coaxHousing", "INS_housing_COAX", "Корпус В", side="B", key="coaxHousingB"),
    RouteOp(12, "coaxInsTut", "INS_MARK_LABEL", "Этикетки наклеены", key="coaxInsLabel"),
    RouteOp(13, "coaxShield", "PRS_SLEEVE_coax", "Опрессовка втулки", key="coaxShield"),
    RouteOp(14, "coaxHeat", "HEAT_TYT_short", "Усадка", key="coaxHeat"),
]


def test_coax_tape_labels_are_their_own_pf_apart_from_the_tube_cut_and_go_to_the_label_mount():
    route = generator_split(COAX_TAPE, "Кабельная сборка")

    assert blocks(route) == [
        ("wires", (1, 2, 3), "Нарезанный и разделанный кабель"), ("labels", (4,), "Этикетки"),
        ("tubes", (5,), "Нарезанные трубки"),
        ("crimpedA", (6,), "Кабель с центральным контактом, сторона А"),
        ("crimpedB", (7,), "Кабель с центральным контактом, сторона В"),
        ("A", (8, 9, 10), "Сторона А собрана"), ("B", (11,), "Сторона В собрана"),
        ("final", (12, 13, 14), "Кабельная сборка"),
    ]
    # The labels wait for «Наклейка этикеток» (12, the product's), the TUT for «Монтаж ТУТ» (8, side A).
    assert edges(route) == [("A", "B"), ("B", "final"), ("crimpedA", "crimpedB"), ("crimpedB", "A"),
                            ("labels", "final"), ("tubes", "A"), ("wires", "crimpedA")]


def test_a_harness_prints_its_marking_tubes_apart_from_cutting_the_shrink_tubes():
    # «Печать и резка маркировки» (cutMark, PRINT_MARK_TUBE on the marker) and the jacket shrink cut on the tube cutter.
    ops = [
        RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провод", key="cutWire"),
        RouteOp(2, "cutTut", "PRINT_MARK_TUBE", "Маркировка", key="cutMark"),
        RouteOp(3, "cutTut", "CUT_TUT_short", "ТУТ", key="cutJs1"),
        RouteOp(4, "insTut", "INS_TUT", "Трубки надеты", key="insSleeves"),
        RouteOp(5, "prsTerm", "PRS_TERMINAL_auto", "Терминалы А", side="A", key="prsTermA1"),
        RouteOp(6, "insTerm", "INS_terminal_2,54-6,3", "Сторона А", side="A", key="insTermA1"),
        RouteOp(7, "insTut", "INS_TUT", "Маркировка надета", key="onMark"),
        RouteOp(8, "prsTerm", "PRS_TERMINAL_auto", "Терминалы В", side="B", key="prsTermB1"),
        RouteOp(9, "insTerm", "INS_terminal_2,54-6,3", "Сторона В", side="B", key="insTermB1"),
    ]
    route = generator_split(ops, "Жгут")
    # The derived scheme gives every cut tube to the first «Надеть ТУТ» (4) — as before this split.

    assert blocks(route) == [
        ("wires", (1,), "Нарезанные провода"), ("labels", (2,), "Напечатанная маркировка"),
        ("tubes", (3,), "Нарезанные трубки"), ("crimpedA", (4, 5), "Обжатые провода, сторона А"),
        ("A", (6,), "Сторона А собрана"), ("crimpedB", (7, 8), "Обжатые провода, сторона В"), ("B", (9,), "Жгут"),
    ]
    assert edges(route) == [("A", "crimpedB"), ("crimpedA", "A"), ("crimpedB", "B"), ("labels", "crimpedA"),
                            ("tubes", "crimpedA"), ("wires", "crimpedA")]


def test_a_plain_tube_cut_stays_cut_tubes_and_carries_its_per_unit():
    ops = [
        RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провод", key="cutWire"),
        RouteOp(2, "cutTut", "CUT_TUT_short", "ТУТ", key="cutJs1"),
        RouteOp(3, "insTut", "INS_TUT", "Трубки надеты", key="insSleeves"),
        RouteOp(4, "prsTerm", "PRS_TERMINAL_auto", "Терминалы А", side="A", key="prsTermA1"),
        RouteOp(5, "insTerm", "INS_terminal_2,54-6,3", "Кабель", side="A", key="insTermA1"),
    ]
    assert op_stages(ops)[2] == "tubes"
    current = parse_route({
        "blocks": [{"key": "cut", "ops": [1, 2]}, {"key": "rest", "ops": [3, 4, 5], "final": True}],
        "edges": [{"from": "cut", "to": "rest", "per_unit": 2}],
    }, ops, manual=True)
    route = generator_split(ops, "", current)
    assert blocks(route)[:2] == [("wires", (1,), "Нарезанные провода"), ("tubes", (2,), "Нарезанные трубки")]
    assert {(edge.source, edge.target): edge.per_unit for edge in route.edges}[("tubes", "crimpedA")] == Decimal(2)
    assert ("wires", "tubes") not in edges(route)


# Owner 20.11: the wire marks are printed on the TP80E (ОБ.057, card PRINT_MARK), the marking tubes on a Godex marker
# (PRINT_MARK_TUBE, ОБ.102) — two printers, two operators in parallel, so two ПФ (two tasks). The clear cover over the
# printed mark is cut on the tube cutter with the other tubes.
WIRE_MARKS_AND_MARKING_TUBES = [
    RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провод", key="cutWire"),
    RouteOp(2, "cutTut", "PRINT_MARK_TUBE", "Маркировка", key="cutMark"),
    RouteOp(3, "cutTut", "PRINT_MARK", "Маркировка проводов", key="cutWireMark"),
    RouteOp(4, "cutTut", "CUT_TUT_short", "Прозрачная ТУТ", key="cutWireMarkCover"),
    RouteOp(5, "insTut", "INS_TUT", "Маркировка на проводах", key="onWireMark"),
    RouteOp(6, "insTut", "INS_TUT", "Прозрачная ТУТ надета", key="onWireMarkCover"),
    RouteOp(7, "heatTut", "HEAT_TYT_short", "Прозрачная ТУТ усажена", key="heatWireMarkCover"),
    RouteOp(8, "prsTerm", "PRS_TERMINAL_auto", "Терминалы А", side="A", key="prsTermA1"),
    RouteOp(9, "insTerm", "INS_terminal_2,54-6,3", "Сторона А", side="A", key="insTermA1"),
    RouteOp(10, "insTut", "INS_TUT", "Маркировка надета", key="onMark"),
    RouteOp(11, "prsTerm", "PRS_TERMINAL_auto", "Терминалы В", side="B", key="prsTermB1"),
    RouteOp(12, "insTerm", "INS_terminal_2,54-6,3", "Сторона В", side="B", key="insTermB1"),
]


def test_a_godex_mark_and_a_tp80e_wire_mark_are_two_print_pf():
    stages = op_stages(WIRE_MARKS_AND_MARKING_TUBES)
    assert [stages[position] for position in (1, 2, 3, 4)] == ["wires", "labels", "printed", "tubes"]

    route = generator_split(WIRE_MARKS_AND_MARKING_TUBES, "Жгут")

    assert blocks(route)[:4] == [
        ("wires", (1,), "Нарезанные провода"), ("labels", (2,), "Маркировочные трубки"),
        ("printed", (3,), "Напечатанная маркировка"), ("tubes", (4,), "Нарезанные трубки"),
    ]
    # The derived scheme gives every cut piece to the first «Надеть ТУТ» (5): the crimp of side A.
    assert edges(route) == [("A", "crimpedB"), ("crimpedA", "A"), ("crimpedB", "B"), ("labels", "crimpedA"),
                            ("printed", "crimpedA"), ("tubes", "crimpedA"), ("wires", "crimpedA")]


def test_a_tp80e_mark_alone_is_the_printed_pf_and_godex_labels_keep_their_names():
    tp80e_only = [op for op in WIRE_MARKS_AND_MARKING_TUBES if op.key != "cutMark"]
    assert ("printed", (3,), "Напечатанная маркировка") in blocks(generator_split(tp80e_only, "Жгут"))
    godex_only = [op for op in WIRE_MARKS_AND_MARKING_TUBES if op.key not in {"cutWireMark", "onWireMark"}]
    assert ("labels", (2,), "Напечатанная маркировка") in blocks(generator_split(godex_only, "Жгут"))


def test_flag_marks_printed_apart_go_to_the_godex_pf():
    ops = [
        RouteOp(1, "cutWire", "CUT_WIRE_auto", "Провод", key="cutWire"),
        RouteOp(2, "insMark", "INS_MARK_AUTO", "Флажки", key="printMarks"),
        RouteOp(3, "cutTut", "PRINT_MARK", "Маркировка проводов", key="cutWireMark"),
        RouteOp(4, "insTut", "INS_TUT", "Маркировка на проводах", key="onWireMark"),
        RouteOp(5, "prsTerm", "PRS_TERMINAL_auto", "Терминалы А", side="A", key="prsTermA1"),
        RouteOp(6, "insTerm", "INS_terminal_2,54-6,3", "Сторона А", side="A", key="insTermA1"),
        RouteOp(7, "insMark", "INS_MARK", "Флажки на жгуте", key="insMark"),
    ]
    stages = op_stages(ops)
    assert (stages[2], stages[3], stages[7]) == ("labels", "printed", "final")
    route = generator_split(ops, "Жгут")
    assert [block.name for block in route.blocks if block.key in {"labels", "printed"}] == ["Этикетки", "Напечатанная маркировка"]
