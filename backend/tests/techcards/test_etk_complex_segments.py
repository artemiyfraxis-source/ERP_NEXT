"""The opt-in C1 contract describes physical wire segments, never inferred electrical shortcuts."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library


ROOT = Path(__file__).resolve().parents[2]
CARDS = parse_operation_library(json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))["templates"])
WIRE = {"idx": 7, "art": "НВ4 0,5", "name": "НВ4 0,5 белый", "qty": 1, "length": 250}
LUG = {"kind": "lug", "material": {"art": "НКИ 1.5-4", "name": "Наконечник кольцевой"}, "crimp": "lug"}
SPLICE = {"kind": "splice", "spliceId": "s1", "label": "С1"}


def end(idx=7, unit=0, side="A"):
    return {"idx": idx, "unit": unit, "side": side}


def pin(ep_id="x1", number=1):
    # Same article and displayed label deliberately belong to distinct connector instances.
    return {"kind": "pin", "epId": ep_id, "pin": number, "conn": "XHP-2", "label": "Разъём"}


def segment(start=None, finish=None, *, idx=7, unit=0, shield=False):
    return {"idx": idx, "unit": unit, "wire": WIRE["art"],
            "from": {**(start or pin()), "end": end(idx, unit, "A")},
            "to": {**(finish or pin("x2")), "end": end(idx, unit, "B")},
            **({"shield": True} if shield else {})}


def config(segments=None):
    return {"topologyVersion": 1, "tab": "complex", "assemblyName": "Сложный жгут", "partQty": 3,
            "wires": [deepcopy(WIRE)], "physicalConnections": segments if segments is not None else [segment()],
            "ops": [{"type": "cutWire", "opKey": "cut", "dependsOn": [],
                     "templateId": "cut-wire-auto-резка", "wires": [deepcopy(WIRE)]}]}


def mount(ep_id="x1", *, key="mount", depends=None):
    return {"type": "insTerm", "opKey": key, "dependsOn": ["cut"] if depends is None else depends,
            "side": "A", "epId": ep_id, "ep": {"connArt": "XHP-2", "label": "Разъём"},
            "templateId": "ins-terminal-2-54-6-3-монтаж-терминалов-a"}


def build(source):
    return build_etk(source, CARDS)


def by_key(document, key):
    return next(op for op in document["operations"] if op["opKey"] == key)


def test_splice_keeps_each_branch_in_connections_and_in_the_physical_graph():
    segments = [segment(pin(), SPLICE), segment(SPLICE, pin("x2"), idx=20),
                segment(SPLICE, {"kind": "free"}, idx=30), segment(SPLICE, LUG, idx=40)]
    source = config(segments)
    source["wires"] += [{**WIRE, "idx": idx, "length": idx * 10} for idx in (20, 30, 40)]
    # A stale legacy pin-to-pin row must never bypass the splice.
    source["connections"] = [{"wire": "ложный провод", "from": {"end": "А1", "pin": 1}, "to": {"end": "В1", "pin": 1}}]
    document = build(source)
    assert document["connections"] == segments
    graph = document["topology"]
    assert len(graph["nodes"]) == 5
    assert len(graph["edges"]) == 4
    assert all("splice:s1" in (edge["from"], edge["to"]) for edge in graph["edges"])
    assert [edge["length"] for edge in graph["edges"]] == [250, 200, 300, 400]
    assert {node["kind"] for node in graph["nodes"]} == {"pin", "splice", "free", "lug"}


def test_mount_links_stop_at_the_splice_and_match_connector_identity_without_ep_key():
    source = config([segment(pin(), SPLICE), segment(pin("x2", 2), {"kind": "free"}, idx=20)])
    source["ops"] += [mount(), mount("x2", key="mount2")]
    document = build(source)
    assert by_key(document, "mount")["fields"]["links"] == [
        {"pin": 1, "wire": WIRE["art"], "dir": "to", "peer": {**SPLICE, "end": end(side="B")}}]
    assert by_key(document, "mount2")["fields"]["links"][0]["peer"]["kind"] == "free"


def test_same_connector_jumper_keeps_two_distinct_pins_and_both_links():
    source = config([segment(pin("x1", 1), pin("x1", 2))])
    source["ops"].append(mount())
    document = build(source)
    links = by_key(document, "mount")["fields"]["links"]
    assert [(link["pin"], link["peer"]["pin"]) for link in links] == [(1, 2), (2, 1)]
    assert len(document["topology"]["nodes"]) == 1
    edge = document["topology"]["edges"][0]
    assert edge["from"] == edge["to"] == "pin:x1"
    assert document["topology"]["nodes"][0]["depth"] == 0


def test_shield_and_same_article_free_ends_remain_separate_segments():
    segments = [segment(LUG, {"kind": "free"}, unit=unit, shield=unit == 2) for unit in range(3)]
    document = build(config(segments))
    assert document["connections"] == segments
    assert len(document["topology"]["nodes"]) == 6
    assert [edge.get("shield", False) for edge in document["topology"]["edges"]] == [False, False, True]


def test_empty_physical_connections_do_not_fall_back_to_legacy_rows():
    source = config([])
    source["connections"] = [{"wire": "устаревший", "from": {"end": "А1", "pin": 1}, "to": {"end": "В1", "pin": 1}}]
    document = build(source)
    assert "connections" not in document
    assert document["topology"] == {"nodes": [], "edges": []}


@pytest.mark.parametrize("bad", [None, [None], [segment(finish={"kind": "pin", "epId": "x2", "pin": 0})],
                                [segment(finish={"kind": "splice"})], [segment(), segment()]])
def test_invalid_physical_connections_are_rejected_instead_of_silently_losing_wires(bad):
    source = config()
    source["physicalConnections"] = bad
    with pytest.raises(ValueError, match="[Фф]изическ"):
        build(source)


def test_a_segment_cannot_borrow_another_wires_end():
    source = config()
    source["physicalConnections"][0]["to"]["end"]["idx"] = 99
    with pytest.raises(ValueError, match="концы физического отрезка"):
        build(source)


def test_operation_dependencies_win_over_input_order_and_remap_route_positions_without_mutation():
    source = config()
    source["ops"] = [mount(), source["ops"][0]]
    source["route"] = {"source": "generated", "blocks": [
        {"key": "cut-pf", "ops": [2], "kit": True}, {"key": "mount-pf", "ops": [1], "final": True}],
        "edges": [{"from": "cut-pf", "to": "mount-pf"}]}
    original = deepcopy(source)
    document = build(source)
    assert [op["opKey"] for op in document["operations"]] == ["cut", "mount"]
    assert [op["opKey"] for op in document["build_config"]["ops"]] == ["cut", "mount"]
    assert [block["ops"] for block in document["route"]["blocks"]] == [[1], [2]]
    assert source == original
    assert build(document["build_config"]) == document


@pytest.mark.parametrize("dependencies", [["missing"], ["mount"], "cut", None])
def test_missing_self_or_malformed_dependencies_stop_the_build(dependencies):
    source = config()
    source["ops"].append({**mount(), "dependsOn": dependencies})
    with pytest.raises(ValueError, match="зависимост"):
        build(source)


def test_multi_operation_cycle_is_rejected():
    source = config()
    source["ops"][0]["dependsOn"] = ["mount"]
    source["ops"].append(mount())
    with pytest.raises(ValueError, match="цикл"):
        build(source)


def test_duplicate_operation_keys_are_rejected():
    source = config()
    source["ops"].append(mount(key="cut"))
    with pytest.raises(ValueError, match="уникальные ключи"):
        build(source)


def test_independent_operations_keep_input_order():
    source = config()
    source["ops"] += [mount(key="second"), mount(key="first")]
    assert [op["opKey"] for op in build(source)["operations"]] == ["cut", "second", "first"]


def test_same_article_lugs_on_both_ends_are_issued_once_per_end_and_keep_their_own_strip():
    source = config([segment(LUG, LUG)])
    # Physical end count must not multiply by all cores or all segments of the wire entry.
    source["wires"][0].update(qty=4, cores=3)
    for side, length in (("A", "5"), ("B", "8")):
        source["ops"] += [
            {"type": "stripCable", "opKey": f"end.7.0.{side}.strip", "dependsOn": ["cut"],
             "templateId": "strip-wire-разделка", "physicalEnds": [end(side=side)], "tutCount": 1,
             "strip": {"a": side == "A", "b": side == "B", "lenA": length if side == "A" else "", "lenB": length if side == "B" else ""}},
            {"type": "prsTerm", "opKey": f"end.7.0.{side}.crimp", "dependsOn": [f"end.7.0.{side}.strip"],
             "templateId": "prs-hkи-нви-рпи-опрессовка-наконечников", "side": side, "physicalEnds": [end(side=side)],
             "label": f"Опрессовка наконечника, конец {side}",
             "ep": {"termArt": LUG["material"]["art"], "termName": LUG["material"]["name"]}},
        ]
    document = build(source)
    for side, length in (("A", "5"), ("B", "8")):
        crimp = by_key(document, f"end.7.0.{side}.crimp")
        assert crimp["components"] == [{**LUG["material"], "norm": "3", "unit": "Шт."}]
        assert crimp["fields"]["physicalEnds"] == [end(side=side)]
        assert by_key(document, f"end.7.0.{side}.strip")["fields"]["strip"][f"len{side}"] == length
    keys = [(op["code"], op["connector"], op["side"]) for op in document["operations"]]
    assert len(keys) == len(set(keys))


def test_legacy_documents_ignore_new_metadata_without_the_version_flag():
    source = config()
    del source["topologyVersion"]
    source["ops"].append(mount())
    source["connections"] = [{"wire": "провод", "from": {"end": "А1", "pin": 1}, "to": {"end": "В1", "pin": 2}}]
    original = build(source)
    source["physicalConnections"] = [segment(LUG, LUG)]
    source["ops"][0].update(dependsOn=["missing"], physicalEnds=[end()], tubePlacement="unused")
    changed = build(source)
    for document in (original, changed):
        del document["build_config"]
    assert json.dumps(changed, ensure_ascii=False) == json.dumps(original, ensure_ascii=False)


def test_reordering_same_side_lugs_keeps_saved_edits_on_their_original_physical_end():
    from app.modules.techcards.etk_derive import derive
    from app.modules.techcards.project_cards import operation_key

    source = config([segment(LUG, pin(), unit=unit) for unit in range(2)])
    for unit in range(2):
        source["ops"].append({"opKey": f"end.7.{unit}.A.crimp", "type": "prsTerm", "side": "A",
                              "templateId": "prs-hkи-нви-рпи-опрессовка-наконечников", "dependsOn": ["cut"],
                              "physicalEnds": [end(unit=unit)], "wires": [deepcopy(WIRE)],
                              "ep": {"termArt": LUG["material"]["art"], "termName": LUG["material"]["name"]}})
    initial, conflicts = derive(source, {}, lambda value: value, CARDS)
    assert not conflicts
    original_keys = {op["opKey"]: operation_key(op) for op in initial["operations"]}
    edited = deepcopy(by_key(initial, "end.7.0.A.crimp"))
    edited["title"] = "Проверено для первой жилы"
    overrides = {"operations": {operation_key(edited): {"operation": edited}}}
    source["ops"][1:] = reversed(source["ops"][1:])
    rebuilt, conflicts = derive(source, overrides, lambda value: value, CARDS)
    assert not conflicts
    assert {op["opKey"]: operation_key(op) for op in rebuilt["operations"]} == original_keys
    assert by_key(rebuilt, "end.7.0.A.crimp")["title"] == edited["title"]
    assert by_key(rebuilt, "end.7.1.A.crimp")["title"] != edited["title"]
    assert [op["fields"]["physicalEnds"] for op in rebuilt["operations"][1:]] == [[end(unit=1)], [end(unit=0)]]


def test_large_connector_keeps_all_ends_without_exceeding_the_persisted_sketch_key_limit():
    from app.modules.techcards.project_cards import operation_key

    source = config([segment(pin(number=unit + 1), {"kind": "free"}, unit=unit) for unit in range(64)])
    ends = [end(unit=unit) for unit in range(64)]
    source["ops"].append({**mount(), "physicalEnds": ends})
    operation = by_key(build(source), "mount")
    assert len(operation_key(operation)) <= 1024
    assert operation["fields"]["physicalEnds"] == ends
    assert len(operation["fields"]["links"]) == 64
