from io import BytesIO
import json
from zipfile import ZipFile

from app.modules.techcards.grapher_import import (
    GrapherComponent,
    cut_length,
    match_component,
    read_export,
)


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
