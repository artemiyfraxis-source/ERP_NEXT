"""Tube cuts supply each placement once; dependency edges protect access before closing a joint."""

from copy import deepcopy

import pytest

from tests.techcards.test_etk_complex_segments import SPLICE, build, by_key, config, end, mount, pin, segment


def add_tube(source, identifier, target, *, phase="preload", count=2, length=25, closure="join"):
    material = {"art": f"ТУТ-{identifier}", "name": f"Трубка {identifier}"}
    target_ends = [end(side="B")] if target["kind"] == "splice" else [target["end"]]
    placement = {"id": identifier, "material": material, "lengthMm": length, "count": count,
                 "target": target, "phase": phase}
    if phase == "preload":
        placement["preloadEnd"] = target_ends[0]
    source.setdefault("tubePlacements", []).append(placement)
    prefix = f"tube.{identifier}"
    common = {"tubePlacement": identifier, "physicalEnds": target_ends, "tutCount": count}
    source["ops"].append({**common, "opKey": f"{prefix}.cut", "type": "cutTut", "dependsOn": [],
                          "templateId": "cut-tut-short-резка", "tutArt": material["art"], "tutName": material["name"],
                          "tutSegs": [{"len": str(length), "count": count}],
                          "tutParts": [{**material, "len": str(length), "count": count}]})
    ready = f"{prefix}.cut"
    if phase == "preload":
        ready = f"{prefix}.preload"
        source["ops"].append({**common, "opKey": ready, "type": "insTut", "dependsOn": [f"{prefix}.cut", "cut"],
                              "templateId": "ins-tut-монтаж", "label": "Предварительно надеть трубку"})
        next(op for op in source["ops"] if op["opKey"] == closure)["dependsOn"].append(ready)
    source["ops"] += [
        {**common, "opKey": f"{prefix}.position", "type": "insTut", "dependsOn": [ready, closure, "cut"],
         "templateId": "ins-tut-монтаж", "label": "Установить трубку на место"},
        {**common, "opKey": f"{prefix}.shrink", "type": "heatTut", "dependsOn": [f"{prefix}.position"],
         "templateId": "heat-tyt-short-усадка", "label": "Усадка трубки"},
    ]


def splice_config():
    source = config([segment(pin(), SPLICE), segment(SPLICE, pin("x2"), idx=8)])
    source["ops"].append({"opKey": "join", "type": "splice", "dependsOn": ["cut"],
                          "templateId": "sdr-w-w-пайка", "method": "solder", "units": 1, "spliceIds": ["s1"]})
    return source


@pytest.mark.parametrize("target", [{"kind": "branch", "end": end(side="B")}, {"kind": "splice", "spliceId": "s1"}])
def test_branch_and_through_splice_tubes_preload_before_join_then_position_and_shrink(target):
    source = splice_config()
    add_tube(source, "branch", target)
    # Simulates a saved/user order that cannot override physical access requirements.
    source["ops"].reverse()
    original = deepcopy(source)
    document = build(source)
    order = [op["opKey"] for op in document["operations"]]
    assert order.index("tube.branch.cut") < order.index("tube.branch.preload") < order.index("join")
    assert order.index("join") < order.index("tube.branch.position") < order.index("tube.branch.shrink")
    assert by_key(document, "join")["fields"]["spliceIds"] == ["s1"]
    assert by_key(document, "tube.branch.preload")["title"] == "Предварительно надеть трубку"
    assert by_key(document, "tube.branch.position")["title"] == "Установить трубку на место"
    assert source == original
    assert build(document["build_config"]) == document


def test_cup_shrink_follows_soldering_and_precedes_housing():
    source = config()
    source["ops"].append({**mount(key="join"), "type": "solderConn", "templateId": "sdr-db-пайка"})
    add_tube(source, "cup", {"kind": "cup", "end": end()}, count=1, length=12)
    source["ops"].insert(0, {"opKey": "housing", "type": "insHousing", "epId": "x1", "units": 1,
                             "dependsOn": ["tube.cup.shrink"], "opNum": "INS_HOUSING"})
    document = build(source)
    order = [op["opKey"] for op in document["operations"]]
    assert order.index("tube.cup.preload") < order.index("join") < order.index("tube.cup.position")
    assert order.index("tube.cup.shrink") < order.index("housing")
    assert by_key(document, "tube.cup.position")["fields"] == {
        "tubePlacement": "cup", "physicalEnds": [end()]}


@pytest.mark.parametrize("phase", ["preload", "afterJoin"])
def test_tube_material_is_issued_only_by_the_cut_with_batch_scaled_tut_parts(phase):
    source = splice_config()
    add_tube(source, "splice", {"kind": "splice", "spliceId": "s1"}, phase=phase)
    document = build(source)
    uses = [(op["opKey"], part) for op in document["operations"] for part in op["components"] if part["art"] == "ТУТ-splice"]
    assert uses == [("tube.splice.cut", {"art": "ТУТ-splice", "name": "Трубка splice", "norm": "6", "unit": "Шт."})]
    consumer = by_key(document, "tube.splice.preload" if phase == "preload" else "tube.splice.position")
    assert any(row["name"] == "ТУТ-splice — 25 мм" and row["norm"] == "6" for row in consumer["semis"])
    for suffix in (["preload", "position", "shrink"] if phase == "preload" else ["position", "shrink"]):
        assert by_key(document, f"tube.splice.{suffix}")["components"] == []


def test_two_placements_take_only_their_own_tube_cut_even_when_both_are_ready():
    source = splice_config()
    add_tube(source, "branch", {"kind": "branch", "end": end()}, count=2)
    add_tube(source, "splice", {"kind": "splice", "spliceId": "s1"}, count=1)
    # Put both tube cuts before both preloads, the old prefix fallback consumed both at the first preload.
    source["ops"].sort(key=lambda op: 0 if op["type"] == "cutTut" else 1)
    document = build(source)
    for identifier, other, count in (("branch", "splice", "6"), ("splice", "branch", "3")):
        semis = by_key(document, f"tube.{identifier}.preload")["semis"]
        assert any(row["name"] == f"ТУТ-{identifier} — 25 мм" and row["norm"] == count for row in semis)
        assert not any(f"ТУТ-{other}" in row["name"] for row in semis)


def test_generated_c1_route_preserves_tube_cut_piece_quantities():
    source = splice_config()
    add_tube(source, "s1", {"kind": "splice", "spliceId": "s1"}, count=4)
    # C1 exports a block per operation, with empty items on its dependency edges.
    source["route"] = {"source": "generated", "blocks": [
        {"key": f"pf-{op['opKey']}", "name": op.get("label", op["opKey"]), "ops": [index],
         **({"kit": True} if op["type"].startswith("cut") else {}),
         **({"final": True} if op["opKey"] == "tube.s1.shrink" else {})}
        for index, op in enumerate(source["ops"], 1)],
        "edges": [{"from": f"pf-{key}", "to": f"pf-{op['opKey']}", "item": ""}
                  for op in source["ops"] for key in op["dependsOn"]]}
    document = build(source)
    assert not document["warnings"]
    assert by_key(document, "tube.s1.cut")["result"] == [{"name": "ТУТ-s1 — 25 мм", "norm": "12", "unit": "Шт."}]
    assert any(row["name"] == "ТУТ-s1 — 25 мм" and row["norm"] == "12"
               for row in by_key(document, "tube.s1.preload")["semis"])
    positions = {op["opKey"]: op["position"] for op in document["operations"]}
    assert all(block["ops"] == [positions[block["key"][3:]]] for block in document["route"]["blocks"])


def test_placement_operations_do_not_reissue_material_even_with_a_custom_template():
    source = splice_config()
    add_tube(source, "custom", {"kind": "splice", "spliceId": "s1"})
    for op in source["ops"]:
        if op.get("tubePlacement") and op["type"] != "cutTut":
            op["templateId"] = "cut-tut-short-резка"
    document = build(source)
    assert sum(len(op["components"]) for op in document["operations"] if op.get("opKey", "").startswith("tube.")) == 1


@pytest.mark.parametrize("with_route", [False, True])
def test_dependency_diamonds_count_each_operation_time_once(with_route):
    source = splice_config()
    source["partQty"] = 1
    add_tube(source, "s1", {"kind": "splice", "spliceId": "s1"}, count=1)
    for op in source["ops"]:
        op["tOp"] = 60
    if with_route:
        source["route"] = build(source)["build_config"]["route"]
    document = build(source)
    blocks = document["route"]["blocks"]
    assert len(blocks) == 6
    assert sum(block["minutes"] for block in blocks) == 6
    assert next(block for block in blocks if block["final"])["rolled_minutes"] == 6


def test_manual_route_keeps_explicit_per_unit_multipliers():
    source = splice_config()
    source["partQty"] = 1
    add_tube(source, "s1", {"kind": "splice", "spliceId": "s1"}, count=1)
    for op in source["ops"]:
        op["tOp"] = 60
    ordered = build(source)["build_config"]
    ordered["route"]["source"] = "manual"
    # A genuinely repeated input stays multiplied on a hand-edited route.
    edge = next(edge for edge in ordered["route"]["edges"] if edge["from"] == "tube.s1.cut")
    edge["per_unit"] = 2
    blocks = build(ordered)["route"]["blocks"]
    assert next(block for block in blocks if block["final"])["rolled_minutes"] == 13


def test_preload_and_positioning_keys_survive_reordering_independent_tubes():
    from app.modules.techcards.project_cards import operation_key

    source = splice_config()
    for identifier in ("first", "second"):
        add_tube(source, identifier, {"kind": "splice", "spliceId": "s1"})
    initial = {op["opKey"]: operation_key(op) for op in build(source)["operations"]}
    source["ops"].reverse()
    reordered = {op["opKey"]: operation_key(op) for op in build(source)["operations"]}
    assert initial == reordered
    assert len(set(initial.values())) == len(initial)
