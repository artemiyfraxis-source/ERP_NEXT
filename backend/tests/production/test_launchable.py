from decimal import Decimal

from app.modules.production.launchable import operation_code, route_from_document, stage_name


def test_route_names_stages_in_russian_instead_of_generator_types():
    route = route_from_document({"build_config": {"ops": [
        {"type": "cutWireStrip", "label": "Резка с разделкой (3 ст.)", "opNumber": "БЕЗ КОДА", "tOp": 10},
        {"type": "prsTerm", "opNumber": "OP-2", "tOp": 5},
    ]}})

    assert [(step.stage, step.code) for step in route] == [("Резка с разделкой (3 ст.)", ""), ("Опрессовка терминалов", "OP-2")]


def test_already_launched_stages_and_codes_read_in_russian():
    assert stage_name("insWrap") == "Обмотка"
    assert stage_name("Пайка") == "Пайка"
    assert operation_code("без кода") == ""
    assert operation_code("OP-01") == "OP-01"


def _inputs(step):
    return [(item.block, int(item.per_unit)) for item in step.inputs]


def test_route_without_a_scheme_is_a_linear_chain_of_one_block_per_step():
    route = route_from_document({"build_config": {"ops": [
        {"type": "cutWire", "tOp": 4}, {"type": "prsTerm", "tOp": 5}, {"type": "insHousing", "tOp": 6},
    ]}})

    assert [step.position for step in route] == [1, 2, 3]
    assert [step.block for step in route] == ["b1", "b2", "b3"]
    assert [_inputs(step) for step in route] == [[], [("b1", 1)], [("b2", 1)]]
    assert [step.final for step in route] == [False, False, True]
    assert [step.block_name for step in route] == ["Резка", "Опрессовка терминалов", "Монтаж в корпус"]


def test_branched_route_two_blocks_feed_the_final_assembly_block():
    document = {
        "build_config": {"ops": [
            {"type": "cutWire", "key": "cutA", "tOp": 4},
            {"type": "cutWire", "key": "cutB", "tOp": 5},
            {"type": "insHousing", "key": "house", "tOp": 6},
            {"type": "insMark", "key": "mark", "tOp": 7},
        ]},
        "route": {
            "blocks": [
                {"key": "wireA", "name": "Провод А", "ops": ["cutA"], "final": False, "components": []},
                {"key": "wireB", "name": "Провод Б", "ops": ["cutB"], "final": False, "components": []},
                {"key": "harness", "name": "Жгут", "ops": ["house", "mark"], "final": True, "components": []},
            ],
            "edges": [
                {"from": "wireA", "to": "harness", "per_unit": 2},
                {"from": "wireB", "to": "harness", "per_unit": 1},
            ],
        },
    }

    route = route_from_document(document)

    assert [step.position for step in route] == [1, 2, 3, 4]
    assert [step.block for step in route] == ["wireA", "wireB", "harness", "harness"]
    assert [step.block_name for step in route] == ["Провод А", "Провод Б", "Жгут", "Жгут"]
    assert [step.final for step in route] == [False, False, True, True]
    assert [_inputs(step) for step in route] == [[], [], [("wireA", 2), ("wireB", 1)], [("wireA", 2), ("wireB", 1)]]
    # Шаги и их времена те же, что и без схемы: цех идёт по позициям.
    flat = route_from_document({"build_config": document["build_config"]})
    assert [(s.position, s.code, s.stage, s.description, s.seconds_per_unit, s.setup_seconds) for s in route] == [
        (s.position, s.code, s.stage, s.description, s.seconds_per_unit, s.setup_seconds) for s in flat
    ]


def test_route_blocks_find_ops_by_position_when_ops_carry_no_key_and_read_fractional_per_unit():
    route = route_from_document({
        "build_config": {"ops": [{"type": "cutWire"}, {"type": "cutWireStrip"}, {"type": "insTerm"}]},
        "route": {
            "blocks": [
                {"key": "x", "ops": [1], "final": False},
                {"key": "y", "ops": ["2"], "final": False},
                {"key": "z", "ops": [3], "final": True},
            ],
            "edges": [{"from": "x", "to": "z", "per_unit": "0,5"}, {"from": "y", "to": "z", "per_unit": 0}],
        },
    })

    assert [step.block for step in route] == ["x", "y", "z"]
    assert [step.block_name for step in route] == ["Резка", "Резка с разделкой", "Монтаж терминалов"]
    assert [(item.block, item.per_unit) for item in route[2].inputs] == [("x", Decimal("0.5")), ("y", Decimal(1))]


def test_route_that_does_not_fit_the_steps_falls_back_to_the_linear_chain():
    ops = [{"type": "cutWire", "key": "a"}, {"type": "prsTerm", "key": "b"}]
    broken = [
        {"blocks": [{"key": "x", "ops": ["a", "nope"], "final": True}], "edges": []},  # неизвестная операция
        {"blocks": [{"key": "x", "ops": ["a"]}], "edges": []},  # операция b ни в одном блоке
        {"blocks": [{"key": "x", "ops": ["a"]}, {"key": "y", "ops": ["a", "b"]}], "edges": []},  # a дважды
        {"blocks": [{"key": "x", "ops": ["a"]}, {"key": "x", "ops": ["b"]}], "edges": []},  # ключ блока дважды
        {"blocks": [{"key": "x", "ops": ["a"]}, {"key": "y", "ops": ["b"]}], "edges": [{"from": "x", "to": "q"}]},
        {"blocks": "oops", "edges": []},
    ]

    for route in broken:
        steps = route_from_document({"build_config": {"ops": ops}, "route": route})
        assert [(s.position, s.block, _inputs(s)) for s in steps] == [(1, "b1", []), (2, "b2", [("b1", 1)])], route


def test_route_from_a_document_without_operations_is_empty():
    assert route_from_document(None) == ()
    assert route_from_document({"route": {"blocks": [{"key": "x", "ops": []}], "edges": []}}) == ()
