from decimal import Decimal
from io import BytesIO
import json
from zipfile import ZipFile

from app.modules.techcards.grapher_import import (
    GrapherComponent,
    cut_length,
    match_component,
    read_export,
)

from . import grapher_fixture as fx


def _zip(snapshot) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("project/snapshot.json", json.dumps(snapshot, ensure_ascii=False))
    return stream.getvalue()


def test_full_export_keeps_old_keys_and_adds_the_full_read():
    result = read_export(_zip(fx.snapshot()), "КБ1.techmap-project.zip", (GrapherComponent("kit-w", "ПВАМ 0,35", "красный"),))
    harness = result["harnesses"][0]
    for key in ("designation", "quantity", "groups", "connectors", "pairs", "warnings"):
        assert key in harness
    assert harness["pairs"] == 1
    assert [group["cut_mm"] for group in harness["groups"]][:4] == [Decimal(312), Decimal(151), Decimal(98), Decimal(120)]
    # A free end and a screen end are no longer called «сросток».
    ends = [end for group in harness["groups"] for end in group["ends"]]
    assert "X3:1 → свободный конец красный" in ends and "экран → X2:4 красный" in ends
    assert set(harness) >= {
        "id", "format", "wires", "junctions", "screens", "diff_pairs", "twisted_groups", "cables", "strip_profiles",
        "coverings", "route_status", "route_rows", "connector_details", "sheet_match", "sheet_warnings",
        "import_warnings",
    }
    wire = harness["wires"][0]
    assert (wire["length_mm"], wire["correction_from_mm"], wire["correction_to_mm"], wire["cut_mm"]) == (300, 8, 4, 312)
    assert (wire["from"]["connector"], wire["from"]["contact_number"], wire["from"]["terminal_l_minus_mm"]) == (
        "X1", 1, -0.8)
    assert wire["component_id"] == "kit-w" and wire["block"]["id"] == "r1"
    assert [op["type"] for op in wire["ops"]][:3] == ["cutWireStrip", "prsTerm", "insTerm"]
    assert harness["junctions"][0]["ending_wire_ids"] == ["w2", "w3", "w4"]
    assert harness["screens"][0]["drain_wire_ids"] == ["w7"]
    assert harness["twisted_groups"][0]["wire_ids"] == ["w5", "w6"]
    assert harness["cables"][0]["sheath_strip"] == {"from_mm": 30, "to_mm": 20, "cut_from_mm": 38, "cut_to_mm": 43}
    assert [layer["name"] for layer in harness["strip_profiles"][0]["layers"]] == ["L1", "L2", "L3"]
    assert harness["coverings"][0]["length_mm"] == 80
    assert harness["coverings"][0]["wire_ids"] == ["w1", "w5", "w6"]
    assert [row["id"] for row in harness["route_rows"]] == ["r1", "r2"]
    assert harness["route_rows"][1]["operations"][0]["code"] == "ASM_HARNESS"
    assert harness["sheet_match"] == [] and harness["sheet_warnings"] == []
    assert result["catalog"]["placements"][0]["component_code"] == "DS1069"
    assert result["import_warnings"] == [] and result["format"]["snapshot_format"] == 4


def test_old_export_without_catalog_sections_still_imports():
    result = read_export(json.dumps(_snapshot()).encode(), "snapshot.json")
    assert result["catalog"] == {"pinned": [], "components": [], "placements": []}
    assert len(result["import_warnings"]) == 3
    harness = result["harnesses"][0]
    assert harness["junctions"] == [] and harness["route_rows"] == []
    assert harness["groups"][0]["quantity"] == 2


def _snapshot():
    return {
        "project": {"designation": "708К.2", "name": "Контроллер"},
        "harnesses": [{
            "designation": "КБ1", "quantity": 10,
            "design": {"schemaVersion": 1, "content": {
                "connectors": [],
                "wires": [
                    {"id": "w1", "lengthMm": 100.2, "materialBinding": {"displayName": "ПВАМ 0,35 белый"}},
                    {"id": "w2", "lengthMm": 100.2, "materialBinding": {"displayName": "ПВАМ 0,35 белый"}},
                ],
                "cables": [], "diffPairs": [{"id": "p1"}],
            }},
        }],
    }


def test_cut_rounds_only_final_length_up_to_step():
    assert str(cut_length("100.2", "0.4", "0.5", "1")) == "102"
    assert cut_length(None) is None


def test_material_match_requires_unique_numeric_token_match():
    components = (
        GrapherComponent("a", "ПВАМ 0,35", "белый"),
        GrapherComponent("b", "ПВАМ 0,5", "красный"),
    )
    assert match_component("ПВАМ 0,35 белый", components) == "a"
    assert match_component("провод белый", components) == ""


def test_zip_snapshot_groups_equal_wires_and_matches_component():
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("project/snapshot.json", json.dumps(_snapshot(), ensure_ascii=False))
    result = read_export(stream.getvalue(), "drawing.techmap-project.zip", (
        GrapherComponent("kit-1", "ПВАМ 0,35", "белый"),
    ))
    harness = result["harnesses"][0]
    assert result["project"]["designation"] == "708К.2"
    assert harness["designation"] == "КБ1"
    assert harness["pairs"] == 1
    assert len(harness["groups"]) == 1
    assert harness["groups"][0]["quantity"] == 2
    assert harness["groups"][0]["component_id"] == "kit-1"
