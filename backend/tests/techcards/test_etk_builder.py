import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import _suffix, build_etk
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard, TechcardValidationError
from app.modules.references.technology import TerminalRecord
from app.workflows.techcard_build import enrich_terminal_norms


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])


def test_welded_splice_tubes_have_their_own_sheet_suffixes():
    assert _suffix({"type": "insTut", "spliceWeld": True}) == "на сростки"
    assert _suffix({"type": "heatTut", "spliceWeld": True}) == "на сростках"


def kit(name: str) -> dict:
    return FIXTURES["files"][f"kit-{name}.json"]["payload"]


def build(name: str) -> dict:
    return build_etk(
        kit(name)["config"],
        CARDS,
        operation_library_commit=LIBRARY["sourceCommit"],
    )


def terminal_records() -> tuple[TerminalRecord, ...]:
    return tuple(
        TerminalRecord(
            article=item["article"], step=item["step"], strip=item["strip"],
            l_plus=item["lPlus"], l_minus=item["lMinus"], applicator=item["applicator"],
            crimp_height=item["crimpHeight"], pull_force_min=item["pullForceMin"],
            pull_force_max=item["pullForceMax"], awg_from=item["awgFrom"],
            awg_to=item["awgTo"], mm2_from=item["mm2From"], mm2_to=item["mm2To"],
        )
        for item in FIXTURES["files"]["db.json"]["payload"]["terRecords"]
    )


def test_current_supplymap_etk_fixtures_are_pinned_to_the_library_commit():
    assert FIXTURES["sourceCommit"] == LIBRARY["sourceCommit"]
    assert len(FIXTURES["files"]) == 8
    assert len(FIXTURES["files"]["db.json"]["payload"]["terRecords"]) == 280


def test_builds_all_current_supplymap_kits_with_cards_results_and_times():
    for name in ("720К.-КБ1", "720К.1.2-КБ1", "708К.2-КБ1", "708К.5-КБ1", "591К.5-КБ2", "729К.2-КБ1", "345К.2-КБ1"):
        document = build(name)
        expected = kit(name)["config"]["ops"]
        assert len(document["operations"]) == len(expected)
        assert all(item["result"] and item["time"] for item in document["operations"])
        missing = sum(item["card"] is None for item in document["operations"])
        assert len(document["warnings"]) == missing


def test_720_etk_keeps_supplymap_codes_material_norms_times_and_final_product():
    document = build("720К.-КБ1")
    operations = document["operations"]
    assert [item["code"] for item in operations] == [
        "CUT_WIRE_auto", "PRS_TERMINAL_auto", "INS_terminal_1.00",
        "PRS_TERMINAL_auto", "INS_terminal_1.00",
    ]
    assert [item["norm"] for item in operations[0]["components"]] == ["175", "175"]
    assert operations[0]["fields"]["cutTolerance"] == "(+/-)2мм"
    assert operations[0]["fields"]["lengthKd"] == "250мм"
    assert operations[2]["components"][0] == {
        "art": "Molex 39012140", "name": "Molex 39012140", "norm": "100", "unit": "Шт.",
    }
    assert operations[2]["time"][0]["norm"] == "235,33"
    assert operations[-1]["result_label"] == "Изделие"
    assert operations[-1]["result"][0]["name"] == kit("720К.-КБ1")["config"]["assemblyName"]


def test_720_etk_resolves_terminal_norms_from_current_supplymap_database():
    config = enrich_terminal_norms(kit("720К.-КБ1")["config"], terminal_records())
    document = build_etk(
        config, CARDS, operation_library_commit=LIBRARY["sourceCommit"],
        technology_source={"schema_version": 14},
    )

    first_crimp, second_crimp = document["operations"][1], document["operations"][3]
    assert first_crimp["fields"]["applicator"] == "S10/M10/M19"
    assert first_crimp["fields"]["crimpHeight"] == "1,14 - 1,24"
    assert first_crimp["fields"]["pullForce"] == "156"
    assert all(item["found"] for item in first_crimp["fields"]["crimpBySection"])
    assert second_crimp["fields"]["applicator"] == "S10/M10/M19"
    assert document["source"]["technology_snapshot"] == {"schema_version": 14}
    assert not [warning for warning in document["warnings"] if "нормы терминала" in warning]


def test_current_cut_and_crimp_fixture_includes_control_tolerances_and_materials():
    config = enrich_terminal_norms(kit("720К.1.2-КБ1")["config"], terminal_records())
    document = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    cut = document["operations"][0]

    assert [item["code"] for item in document["operations"]] == [
        "CUT_WIRE+1crimp", "INS_terminal_1.00", "PRS_TERMINAL_auto", "INS_terminal_1.00",
    ]
    assert cut["components"][-1] == {
        "art": "Molex 39000079", "name": "Molex 39000079", "norm": "1400", "unit": "Шт.",
    }
    assert all("обжатый терминал Molex 39000079" in item["name"] for item in cut["time"])
    assert cut["fields"]["strip"] == {"a": True, "b": False, "lenA": "3,00 - 3,50", "lenB": ""}
    assert len(cut["card"]["steps"]) == 12
    tolerances = {item["text"]: item["tolerance"] for item in cut["card"]["steps"]}
    assert next(value for text, value in tolerances.items() if "микрометром высоту опрессовки" in text) == f"{cut['fields']['crimpHeight']} мм"
    assert next(value for text, value in tolerances.items() if "Pull-test" in text).endswith(" Н")


def test_etk_surfaces_terminal_sections_missing_from_current_database():
    config = enrich_terminal_norms(kit("708К.5-КБ1")["config"], terminal_records())
    document = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])

    warnings = [warning for warning in document["warnings"] if "нормы терминала" in warning]
    assert any("16-02-0086" in warning for warning in warnings)
    assert any("T-DS1071-SC600" in warning for warning in warnings)
    resolved = next(
        operation for operation in document["operations"]
        if operation["components"] and operation["components"][0]["art"] == "Molex 43030-0001"
    )
    assert resolved["fields"]["applicator"] == "S8/M8"
    assert resolved["fields"]["crimpHeight"] == "0,91 - 1,09"


def test_build_uses_template_id_not_ambiguous_side_title_and_tracks_pinout():
    config = kit("720К.-КБ1")["config"]
    config["ops"][2]["pins"] = ["1", "2"]
    document = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    operation = document["operations"][2]
    assert operation["template_id"].endswith("-a")
    assert [item["pins"] for item in operation["fields"]["wires"]] == [[1], [2]]
    assert operation["fields"]["pinout"] is True


def test_project_document_accepts_repeated_code_when_connectors_are_distinct():
    now = datetime.now(timezone.utc)
    card = ProjectTechcard.create(
        project="720К.", item_index="КБ1", product_name="Жгут",
        source_calculation="", deadline=None, now=now,
    )
    rebuilt = card.rebuild(build("720К.-КБ1"), now)
    assert rebuilt.document is not None
    assert len(rebuilt.document["operations"]) == 5


def test_missing_template_is_visible_in_the_built_project_snapshot():
    config = kit("720К.-КБ1")["config"]
    config["ops"][0]["templateId"] = "missing"
    document = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    assert document["operations"][0]["card"] is None
    assert "нет карточки" in document["warnings"][0]

    card = ProjectTechcard.create(
        project="P", item_index="I", product_name="N",
        source_calculation="", deadline=None, now=datetime.now(timezone.utc),
    ).rebuild(document, datetime.now(timezone.utc))
    assert card.document["warnings"] == document["warnings"]


def test_cut_and_crimp_operation_tracks_terminal_material_strip_and_applicator():
    config = {
        "assemblyName": "Жгут", "partQty": 100,
        "wires": [{"name": "Провод", "art": "ПВ3", "qty": 2, "length": 250}],
        "ops": [{
            "type": "cutWireCrimp", "templateId": "cut-wire-1crimp-автомат-резка-опрессовка",
            "opNum": "CUT_WIRE+1crimp", "side": "A", "tPrep": 60, "tOp": 2,
            "wires": [{"name": "Провод", "art": "ПВ3", "qty": 2, "length": 250}],
            "strip": {"a": True, "lenA": "3,5", "b": False, "lenB": ""},
            "ep": {"connArt": "XHP-2", "termArt": "SXH-001T-P0.6", "termName": "Терминал"},
            "terminalNorm": {"applicator": "A16"},
        }, {"type": "insTerm", "opNum": "INS_terminal", "side": "A", "ep": {"connArt": "XHP-2"}}],
    }
    operation = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])["operations"][0]

    assert operation["components"][-1] == {"art": "SXH-001T-P0.6", "name": "Терминал", "norm": "200", "unit": "Шт."}
    assert operation["fields"]["strip"]["lenA"] == "3,5"
    assert operation["fields"]["applicator"] == "A16"
    assert "обжат терм. SXH-001T-P0.6" in operation["result"][0]["name"]


def test_splice_topology_and_strip_cable_do_not_advance_main_chain():
    config = {
        "assemblyName": "Жгут", "partQty": 1,
        "wires": [{"name": "Провод", "art": "ПВ3", "qty": 2, "length": 300}],
        "ops": [
            {"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [{"name": "Провод", "art": "ПВ3", "qty": 2, "length": 300}]},
            {"type": "stripCable", "opNum": "STRIP_WIRE", "tutCount": 2},
            {"type": "splice", "opNum": "US_WELD", "units": 1},
            {"type": "insTerm", "opNum": "INS_terminal_2.00", "side": "A", "ep": {"connArt": "XHP-2"}, "wires": [{"name": "Провод", "art": "ПВ3", "qty": 2, "length": 300}], "wireSources": [""]},
            {"type": "insTerm", "opNum": "INS_terminal_2.00", "side": "B", "ep": {"connArt": "PHR-2"}, "wires": [{"name": "Провод", "art": "ПВ3", "qty": 2, "length": 300}], "wireSources": [""]},
        ],
    }
    document = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])

    assert document["operations"][1]["result"][0]["name"].endswith("(концы разделаны)")
    assert any(node.get("kind") == "splice" for node in document["topology"]["nodes"])
    assert sum(edge["count"] for edge in document["topology"]["edges"]) == 4


def test_issue_rejects_cut_operations_without_wire_length():
    config = {"wires": [{"art": "ПВ3", "length": 0}], "ops": [{"type": "cutWire", "opNum": "CUT_WIRE"}]}
    now = datetime.now(timezone.utc)
    document = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    card = ProjectTechcard.create(
        project="P", item_index="КБ1", product_name="Жгут",
        source_calculation="", deadline=None, now=now,
    ).rebuild(document, now)
    with pytest.raises(TechcardValidationError, match="Не указана длина резки"):
        card.issue(now)


def test_folded_wire_resolves_crimp_norm_for_doubled_section():
    records = (TerminalRecord(
        article="TEST-24", applicator="A-24", crimp_height="0,80 - 0,90",
        awg_from="24", awg_to="24",
    ),)

    def norm(fold: bool) -> dict:
        config = {"ops": [{
            "type": "prsTerm",
            "ep": {"termArt": "TEST-24", "fold": fold},
            "wires": [{"art": "МГТФ 0,12 мм²"}],
        }]}
        return enrich_terminal_norms(config, records)["ops"][0]["terminalNorm"]

    folded = norm(True)
    assert folded["bySection"][0]["section"] == "0,24 мм² (24 AWG)"
    assert folded["bySection"][0]["found"] is True
    assert folded["crimpHeight"] == "0,80 - 0,90"
    plain = norm(False)
    assert plain["bySection"][0]["section"] == "0,12 мм² (26 AWG)"
    assert plain["bySection"][0]["found"] is False
