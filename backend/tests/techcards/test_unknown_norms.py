"""Missing ETK time is a lower bound, never an explicit zero norm."""

from copy import deepcopy
from decimal import Decimal

import pytest

from app.modules.techcards.domain.route import pf_documents
from app.modules.techcards.etk_builder import attach_route, build_etk, operation_norms, operation_time_summary


def document() -> dict:
    return {
        "schema_version": 4,
        "kit": {"part_quantity": "10"},
        "operations": [
            {"position": 1, "code": "KNOWN", "time": [{"norm": "2,5"}]},
            {"position": 2, "code": "UNKNOWN", "time": [{"norm": "1"}, {"norm": ""}],
             "times": {"operation_seconds": "999"}},
            {"position": 3, "code": "ZERO", "time": [{"norm": "0"}]},
        ],
        "route": {
            "source": "manual", "final": "finish",
            "blocks": [{"key": "source", "ops": [1, 2]}, {"key": "finish", "ops": [3], "final": True}],
            "edges": [{"from": "source", "to": "finish", "per_unit": 2}],
        },
    }


@pytest.mark.parametrize("missing", [None, "", "  ", "не задано", "NaN", "Infinity", "-1"])
def test_operation_norm_distinguishes_unknown_from_explicit_zero(missing):
    raw = document()
    raw["operations"][1]["time"][1]["norm"] = missing
    assert operation_norms(raw) == (Decimal(10), (Decimal("2.5"), None, Decimal(0)))
    assert operation_time_summary(raw["operations"][1], Decimal(10)) == (Decimal(1), True)
    assert operation_time_summary(raw["operations"][2], Decimal(10)) == (Decimal(0), False)


def test_route_totals_keep_numeric_lower_bound_and_count_unknown_operations_once():
    raw = document()
    original = deepcopy(raw)
    filled = attach_route(raw)
    source, finish = filled["route"]["blocks"]
    assert source["minutes"] == 3.5
    assert source["unit_minutes"] == .35
    assert source["unknown_operations"] == source["rolled_unknown_operations"] == 1
    assert finish["minutes"] == 0
    assert finish["rolled_minutes"] == .7
    assert finish.get("unknown_operations", 0) == 0
    assert finish["rolled_unknown_operations"] == 1
    assert pf_documents(filled)[0]["unknown_operations"] == 1
    assert raw == original


def test_legacy_time_without_rows_still_uses_its_explicit_seconds():
    old = {"times": {"operation_seconds": "12"}}
    assert operation_time_summary(old, Decimal(10)) == (Decimal(2), False)
    assert operation_time_summary({"times": {"operation_seconds": "0"}}, Decimal(10)) == (Decimal(0), False)


@pytest.mark.parametrize("seconds,unknown", [(None, True), ("", True), ("  ", True), ("0", False), (0, False)])
def test_stored_zero_uses_source_norm_provenance(seconds, unknown):
    operation = {"time": [{"norm": "2"}, {"norm": "0,00"}], "times": {"operation_seconds": seconds}}
    original = deepcopy(operation)
    assert operation_time_summary(operation, Decimal(10)) == (Decimal(2), unknown)
    # Editing instructions does not establish that the time norm was entered explicitly.
    operation["edited"] = {"by": "Technologist"}
    assert operation_time_summary(operation, Decimal(10)) == (Decimal(2), unknown)
    assert {key: value for key, value in operation.items() if key != "edited"} == original


@pytest.mark.parametrize("seconds,unknown", [(None, True), ("", True), ("0", False), (0, False)])
def test_manual_builder_preserves_explicit_zero_source(seconds, unknown):
    built = build_etk({
        "mode": "manual", "assemblyName": "Sample", "partQty": 10,
        "ops": [{"type": "customOp", "seq": 1, "label": "Check", "tOp": seconds,
                 "steps": [{"kind": "Контроль", "text": "Check result"}]}],
    }, [])
    [operation] = built["operations"]
    assert operation_time_summary(operation, Decimal(10)) == (Decimal(0), unknown)
    assert operation["times"]["operation_seconds"] == ("" if unknown else "0")
    assert bool(built["warnings"]) == unknown


@pytest.mark.parametrize("seconds", ["0", 0])
def test_wire_builder_does_not_replace_explicit_zero_with_default_minutes(seconds):
    built = build_etk({
        "partQty": 10, "assemblyName": "Sample", "wires": [{"name": "Wire", "qty": 1, "length": 100}],
        "ops": [{"type": "cutWire", "opNum": "CUT_WIRE", "opKey": "cut", "tOp": seconds}],
    }, [])
    [operation] = built["operations"]
    assert operation["time"][0]["norm"] == "0"
    assert operation_time_summary(operation, Decimal(10)) == (Decimal(0), False)
