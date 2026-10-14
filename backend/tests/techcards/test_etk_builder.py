import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import _result_name, _suffix, _term_count, _topology, build_etk
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard, TechcardValidationError
from app.modules.references.technology import TerminalRecord
from app.workflows.techcard_build import enrich_terminal_norms


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
ERP_LIBRARY = json.loads((ROOT / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))


def test_welded_splice_tubes_have_their_own_sheet_suffixes():
    assert _suffix({"type": "insTut", "spliceWeld": True}) == "на сростки"
    assert _suffix({"type": "heatTut", "spliceWeld": True}) == "на сростках"
    assert _suffix({"type": "cutWireCrimp", "tin": True}) == "обжим + лужение"


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
    assert operations[0]["fields"]["cutTolerance"] == "±3 мм"
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


def test_connections_pair_both_ends_and_show_the_other_end_in_mounting_operations():
    config = deepcopy(kit("720К.-КБ1")["config"])
    plain = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    assert "connections" not in plain
    assert not any("links" in item["fields"] for item in plain["operations"])

    mounts = [index for index, op in enumerate(config["ops"]) if op.get("type") == "insTerm"]
    assert len(mounts) == 2
    config["ops"][mounts[0]]["epKey"], config["ops"][mounts[1]]["epKey"] = "А1", "В1"
    config["connections"] = [
        {"wire": "черный", "from": {"end": "А1", "conn": "XHP-2", "pin": 1}, "to": {"end": "В1", "conn": "XHP-2", "pin": "3"}},
        {"wire": "черный", "from": {"end": "А1", "pin": 2}, "to": {"end": "", "pin": 1}},
        "мусор",
    ]
    document = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    assert document["connections"] == [{
        "wire": "черный",
        "from": {"end": "А1", "conn": "XHP-2", "label": "", "pin": 1},
        "to": {"end": "В1", "conn": "XHP-2", "label": "", "pin": 3},
    }]
    side_a, side_b = (document["operations"][index]["fields"]["links"] for index in mounts)
    assert side_a == [{"pin": 1, "wire": "черный", "dir": "to", "peer": {"end": "В1", "conn": "XHP-2", "label": "", "pin": 3}}]
    assert side_b == [{"pin": 3, "wire": "черный", "dir": "from", "peer": {"end": "А1", "conn": "XHP-2", "label": "", "pin": 1}}]


def test_harness_links_the_source_connector_named_by_its_bom_name_not_a_phantom_node():
    ends = {"connArt": "SHR-06V-S-B", "connName": "Корпус SH 6"}
    config = {"ops": [
        {"type": "insTerm", "side": "A", "ep": ends, "wires": [{"art": "черный", "qty": 6}], "wireSources": [""]},
        {"type": "insTerm", "side": "B", "ep": ends, "wires": [{"art": "черный", "qty": 6}], "wireSources": ["Корпус SH 6"]},
    ]}
    topology = _topology(config)
    assert [node["id"] for node in topology["nodes"]] == ["SHR-06V-S-B", "SHR-06V-S-B (В)"]
    assert [(edge["from"], edge["to"], edge["count"]) for edge in topology["edges"]] == [("SHR-06V-S-B", "SHR-06V-S-B (В)", 6)]


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
    # SUPPLYMAP etkResult_: резка с обжимом отдаёт каждый провод отдельной строкой, количество = провода × партия.
    assert operation["result"] == [{"name": "ПВ3 250мм, обжатый терминал SXH-001T-P0.6", "norm": "200", "unit": "Шт."}]
    assert operation["time"] == [{"name": "ПВ3 250мм, обжатый терминал SXH-001T-P0.6", "norm": "7,67"}]
    # Комбинированная карта несёт и дефекты опрессовки, а не только резки и зачистки.
    defects = operation["card"]["defects"]
    assert "Подрезка жил:" in defects
    assert "Опрессовка — нарушение геометрии зоны обжатия жилы:" in defects
    assert "Несоответствие высоты опрессовки или усилия на разрыв:" in defects


def _both_sides_config(term_b: str) -> dict:
    """Generator «Обе стороны»: one CUT_WIRE+2crimp op with `ep` (А) and `epB` (В), no prsTerm — mountings follow."""
    wire = {"name": "Провод НВ4 0,2", "art": "НВ4 0,2", "qty": 4, "length": 160}
    return {
        "assemblyName": "Кабель USB", "partQty": 100, "wires": [dict(wire)],
        "ops": [{
            "type": "cutWireCrimp", "templateId": "cut-wire-2crimp-автомат-резка-опрессовка",
            "opNum": "CUT_WIRE+2crimp", "side": "A", "tPrep": 3600, "tOp": 4.8, "wires": [dict(wire)],
            "strip": {"a": True, "lenA": "2", "b": True, "lenB": "2"},
            "ep": {"connArt": "I-DS1071-SCB02X5", "termArt": "SXH-001T-P0.6N", "termName": "Контакт XH"},
            "epB": {"connArt": "I-DS1071-SCB02X5", "termArt": term_b, "termName": "Контакт"},
        }, *({"type": "insTerm", "opNum": "INS_terminal_2,54-6,3", "side": side, "wires": [dict(wire)], "wireSources": [""],
              "ep": {"connArt": "I-DS1071-SCB02X5", "termArt": "SXH-001T-P0.6N" if side == "A" else term_b}} for side in ("A", "B"))],
    }


def test_cut_crimping_both_sides_carries_both_terminals_once_and_no_manual_crimp():
    records = (
        TerminalRecord(article="SXH-001T-P0.6N", applicator="S5/M5/A2", crimp_height="0,60 - 0,75", awg_from="28", awg_to="22"),
        TerminalRecord(article="SPH-002T-P0.5S", applicator="S7/A7", crimp_height="0,48 - 0,52", awg_from="28", awg_to="24"),
    )
    same = build_etk(enrich_terminal_norms(_both_sides_config("SXH-001T-P0.6N"), records), CARDS, operation_library_commit="x")
    cut = same["operations"][0]
    assert (cut["code"], cut["connector"]) == ("CUT_WIRE+2crimp", "обжим А и В")
    # Терминал обеих сторон один: строка комплектующих одна, на каждый конец — 4 провода × 2 конца × 100 = 800.
    assert [row for row in cut["components"] if row["unit"] == "Шт."] == [
        {"art": "SXH-001T-P0.6N", "name": "Контакт XH", "norm": "800", "unit": "Шт."}]
    assert cut["result"] == [{"name": "НВ4 0,2 160мм, обжатый терминал SXH-001T-P0.6N с двух сторон", "norm": "400", "unit": "Шт."}]
    assert cut["fields"]["applicator"] == "A2"
    assert cut["fields"]["strip"] == {"a": True, "b": True, "lenA": "2", "lenB": "2"}
    # Ручной опрессовки нет, монтаж в разъёмы терминалов не списывает: всего терминалов в ЭТК — 800, без двойного счёта.
    assert [operation["type"] for operation in same["operations"]] == ["cutWireCrimp", "insTerm", "insTerm"]
    terminals = sum(float(row["norm"].replace(",", ".")) for operation in same["operations"]
                    for row in operation["components"] if row["art"].startswith("SXH"))
    assert terminals == 800

    mixed = build_etk(enrich_terminal_norms(_both_sides_config("SPH-002T-P0.5S"), records), CARDS, operation_library_commit="x")
    cut = mixed["operations"][0]
    assert [(row["art"], row["norm"]) for row in cut["components"] if row["unit"] == "Шт."] == [
        ("SXH-001T-P0.6N", "400"), ("SPH-002T-P0.5S", "400")]
    assert cut["result"][0]["name"] == "НВ4 0,2 160мм, обжатый терминал SXH-001T-P0.6N (А) / SPH-002T-P0.5S (В)"
    # Два аппликатора на станке, высота обжима — по сторонам.
    assert cut["fields"]["applicator"] == "A2/A7"
    assert cut["fields"]["crimpHeight"] == "0,60 - 0,75 (А) / 0,48 - 0,52 (В)"
    assert _result_name(_both_sides_config("SPH-002T-P0.5S")["ops"][0], {}, "") == (
        "Провода нарезаны: НВ4 0,2 — 4 шт по 160 мм; сторона А: обжаты терминалы SXH-001T-P0.6N; сторона В: обжаты терминалы SPH-002T-P0.5S")


def test_cut_crimping_two_contacts_names_each_ends_crimp_spec_and_missing_norms():
    """Owner 12.10: the fitter must see which contact is end A / B and whose applicator, height and pull force it is."""
    records = (TerminalRecord(
        article="SXH-001T-P0.6N", applicator="S5/M5/A2", crimp_height="0,60 - 0,75", pull_force_min="9,8",
        pull_force_max="39,2", strip="2,5", awg_from="28", awg_to="22",
    ),)
    config = _both_sides_config("T-DS1071-SC600")
    config["ops"][0]["strip"] = {"a": True, "lenA": "", "b": True, "lenB": "3,5"}
    for wire in (config["wires"][0], config["ops"][0]["wires"][0]):
        wire.update(art="НВ4 0,2 белый")
    config["ops"][0]["wires"].append({**config["ops"][0]["wires"][0], "art": "НВ4 0,2 зелёный"})
    cut = build_etk(enrich_terminal_norms(config, records), CARDS, operation_library_commit="x")["operations"][0]

    assert cut["fields"]["crimpEnds"] == [
        {"ends": ["A"], "art": "SXH-001T-P0.6N", "name": "Контакт XH", "wire": "НВ4 0,2", "section": "24 AWG",
         "applicator": "A2", "crimpHeight": "0,60 - 0,75", "pullForce": "9,8 - 39,2", "strip": "2,5"},
        {"ends": ["B"], "art": "T-DS1071-SC600", "name": "Контакт", "wire": "НВ4 0,2", "section": "24 AWG",
         "applicator": "", "crimpHeight": "", "pullForce": "", "strip": "3,5"},
    ]
    # Шапка старого вида (шаг ход операции, допуски) — без «?»: нет нормы — «не задано».
    assert cut["fields"]["crimpHeight"] == "0,60 - 0,75 (А) / не задано (В)"

    same = build_etk(enrich_terminal_norms(_both_sides_config("SXH-001T-P0.6N"), records), CARDS, operation_library_commit="x")
    assert [(item["ends"], item["art"], item["strip"]) for item in same["operations"][0]["fields"]["crimpEnds"]] == [
        (["A", "B"], "SXH-001T-P0.6N", "2")]
    # Без снимка БД.ТЕР норм нет — и строк спецификации нет (выданные ЭТК показываются как были).
    plain = build_etk(_both_sides_config("SXH-001T-P0.6N"), CARDS, operation_library_commit="x")
    assert "crimpEnds" not in plain["operations"][0]["fields"]


def test_one_side_cut_crimp_ignores_side_b_norms_and_keeps_its_etk():
    """An op without `epB` (old configs, «Сторона А/В») builds exactly as before even if a stray `terminalNormB` sits on it."""
    config = _both_sides_config("SXH-001T-P0.6N")
    config["ops"][0].pop("epB")
    config["ops"][0]["opNum"] = "CUT_WIRE+1crimp"
    config["ops"][0]["templateId"] = "cut-wire-1crimp-автомат-резка-опрессовка"
    before = build_etk(deepcopy(config), CARDS, operation_library_commit="x")
    config["ops"][0]["terminalNormB"] = {"applicator": "A9", "crimpHeight": "1"}
    assert build_etk(config, CARDS, operation_library_commit="x")["operations"] == before["operations"]
    cut = before["operations"][0]
    assert cut["connector"] == "обжим"
    assert [row["norm"] for row in cut["components"] if row["unit"] == "Шт."] == ["400"]
    assert cut["result"][0]["name"] == "НВ4 0,2 160мм, обжатый терминал SXH-001T-P0.6N"


def test_drawing_cut_length_with_end_allowances_reaches_the_etk_unchanged():
    """Owner 06.10: the TECHMAP-GRAPHER end corrections are the contact allowance. The generator passes the export's
    cut_mm as the wire length (measure mode «длина реза», no L+/L− on top), and the ETK cuts exactly that."""
    from app.modules.techcards.grapher_import import read_export
    from . import grapher_fixture as fx

    wire_view = read_export(json.dumps(fx.snapshot()).encode(), "snapshot.json")["harnesses"][0]["wires"][0]
    assert (wire_view["length_mm"], wire_view["correction_from_mm"], wire_view["correction_to_mm"]) == (300, 8, 4)
    wire = {"name": "ПВАМ 0,35", "art": "ПВАМ 0,35", "qty": 1, "length": wire_view["cut_mm"]}
    config = {"assemblyName": "Жгут", "partQty": 1, "wires": [wire],
              "ops": [{"type": "cutWire", "opNum": "CUT_WIRE_auto", "wires": [wire]}]}
    operation = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])["operations"][0]
    assert operation["result"][0]["name"].startswith("ПВАМ 0,35 312мм")


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

    assert document["operations"][1]["result"][0]["name"].endswith("; концы разделаны")
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


def test_workshop_gost_awg_map_picks_crimp_row_but_1mm2_crimp_override_wins():
    records = (
        TerminalRecord(article="TEST-22", crimp_height="0,84 - 0,89", awg_from="22", awg_to="22"),
        TerminalRecord(article="TEST-18", crimp_height="1,10 - 1,20", awg_from="18", awg_to="18"),
    )

    def section(article: str, wire: str, gost_awg: dict[str, int] | None) -> dict:
        config = {"ops": [{"type": "prsTerm", "ep": {"termArt": article}, "wires": [{"art": wire}]}]}
        return enrich_terminal_norms(config, records, gost_awg)["ops"][0]["terminalNorm"]["bySection"][0]

    assert section("TEST-22", "ПВАМ 0,5", None)["found"] is False
    mapped = section("TEST-22", "ПВАМ 0,5", {"0.5": 22})
    assert mapped["section"] == "0,5 мм² (22 AWG)"
    assert mapped["crimpHeight"] == "0,84 - 0,89"
    overridden = section("TEST-18", "ПВ-3 1,0 мм²", {"1": 17})
    assert overridden["section"] == "1 мм² (18 AWG)"
    assert overridden["found"] is True


def test_coax_strip_carries_side_dimensions_for_the_sketch():
    # SUPPLYMAP TcBuild.gs etkFields_ f.coax — the data etkDrawCoax_ draws the strip steps from.
    config = {"mode": "coax", "coax": {
        "cableArt": "RG-174", "sideA": {"article": "SMA-M", "l1": "3", "l2": "5", "l3": "9", "d1": "0,5"},
        "sideB": {"article": "BNC-F", "l1": "4"},
    }, "ops": [{"type": "coaxStrip", "side": "A"}, {"type": "coaxStrip", "side": "B"}]}
    operations = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])["operations"]

    assert operations[0]["fields"]["coax"] == {
        "cable": "RG-174", "connector": "SMA-M", "d1": "0,5", "d2": "", "d3": "",
        "l1": "3", "l2": "5", "l3": "9", "lPlus": "", "lMinus": "",
    }
    assert operations[1]["fields"]["coax"]["connector"] == "BNC-F"


def test_720_result_and_input_carry_quantities_like_supplymap_etk_result():
    """SUPPLYMAP etkResult_/etkSemis_: сторона B опрессовки отдаёт одну заготовку на изделие, её норма = партия."""
    config = kit("720К.-КБ1")["config"]
    batch = str(config["partQty"])
    operations = build("720К.-КБ1")["operations"]
    side_b = operations[3]
    assert side_b["code"] == "PRS_TERMINAL_auto" and side_b["side"] == "B"
    assert [row["norm"] for row in side_b["result"]] == [batch]
    assert [row["norm"] for row in operations[4]["semis"]] == [batch]
    assert all(row["norm"] for item in operations for row in item["result"] + item["semis"])
    # Время одной строкой — время операции на партию, без повтора наименования в интерфейсе.
    assert side_b["time"][0]["name"] == side_b["result"][0]["name"]


def test_combined_marking_and_coax_cards_add_their_transition_defects():
    from app.modules.techcards.etk_builder import _combined_defects

    by_code = {}
    for card in CARDS:
        by_code.setdefault(card.code, card)
    marking = _combined_defects("CUT_WIRE+1strip+1mark", by_code["CUT_WIRE+1strip+1mark"].defects, by_code)
    assert marking.endswith("Маркировка:\nпропуск/неверная маркировка; нечитаемый текст; смещение; прожог ТУТ-бирки.")
    coax = _combined_defects("CUT_COAX+1strip", by_code["CUT_COAX+1strip"].defects, by_code)
    assert "Разделка коаксиала — " in coax
    plain = by_code["CUT_WIRE_auto"].defects
    assert _combined_defects("CUT_WIRE_auto", plain, by_code) == plain.rstrip()


def test_cut_crimp_and_tin_uses_the_erp_template_with_tin_defects_and_the_machine_applicator():
    cards = CARDS + parse_operation_library(ERP_LIBRARY["templates"])
    wire = {"name": "Силиконовый 28AWG черный", "art": "ПВ28", "qty": 2, "length": 100}
    config = {
        "assemblyName": "Кабельная сборка", "partQty": 50, "wires": [wire],
        "ops": [{
            "type": "cutWireCrimp", "templateId": "cut-wire-1crimp-tin-автомат-резка-опрессовка-лужение",
            "opNum": "CUT_WIRE+1crimp+tin", "side": "A", "tin": True, "tPrep": 60, "tOp": 4, "wires": [wire],
            "strip": {"a": True, "lenA": "1,2", "b": True, "lenB": "3"},
            "ep": {"connArt": "SHR-02V-S-B", "termArt": "SSH-003T-P0.2-H", "termName": "Терминал"},
            "terminalNorm": {"applicator": "S9/M1/A19"},
        }, {"type": "insTerm", "opNum": "INS_terminal", "side": "A", "ep": {"connArt": "SHR-02V-S-B"}}],
    }
    operation = build_etk(config, cards, operation_library_commit=LIBRARY["sourceCommit"])["operations"][0]

    assert operation["card"]["name"] == "Автомат резка + опрессовка + лужение"
    assert operation["result"][0]["name"] == "ПВ28 100мм, обжатый терминал SSH-003T-P0.2-H, другой конец облужен"
    assert operation["time"][0]["name"] == operation["result"][0]["name"]
    # Станку нужен аппликатор автомата, а не ручные S9/M1 из того же списка БД.ТЕР.
    machine = [tool for tool in operation["card"]["tooling"] if tool["section"] == "Оборудование"]
    assert [(tool["inventory_number"], tool["program"]) for tool in machine] == [("ОБ.098", "A19")]
    assert operation["fields"]["applicator"] == "A19"
    defects = operation["card"]["defects"]
    assert "Подрезка жил:" in defects
    assert "Опрессовка — " in defects
    assert "Лужение — " in defects
    assert operation["fields"]["strip"] == {"a": True, "b": True, "lenA": "1,2", "lenB": "3"}
    assert operation["fields"]["cutTolerance"] == "±3 мм"


def _crimp_tin_config(op_strip: dict, **config: object) -> dict:
    wire = {"name": "Силиконовый 28AWG черный", "art": "ПВ28", "qty": 2, "length": 104}
    return {
        "assemblyName": "Кабельная сборка", "partQty": 50, "wires": [wire], **config,
        "ops": [{
            "type": "cutWireCrimp", "opNum": "CUT_WIRE+1crimp+tin", "side": "A", "tin": True, "wires": [wire],
            "strip": op_strip, "ep": {"connArt": "SHR-02V-S-B", "termArt": "SSH-003T-P0.2-H"},
        }],
    }


def test_tinned_free_end_of_the_cut_crimp_takes_the_tin_strip_length():
    # 719К.2 КБ1: лужёный конец В без своей длины у операции («B ?») — длина зачистки под лужение из конфигурации.
    def strip_of(config: dict) -> dict:
        return build_etk(config, CARDS, operation_library_commit="x")["operations"][0]["fields"]["strip"]

    empty_b = {"a": True, "lenA": "1,2", "b": True, "lenB": ""}
    assert strip_of(_crimp_tin_config(empty_b, strip={"a": True, "lenA": "1,2", "b": True, "lenB": "3"}))["lenB"] == "3"
    assert strip_of(_crimp_tin_config(empty_b, tin={"enabled": True, "stripLen": "2,5"}))["lenB"] == "2,5"
    # Конец В лудится всегда — он зачищен, даже если генератор не отметил зачистку.
    no_b = {"a": True, "lenA": "1,2", "b": False, "lenB": ""}
    assert strip_of(_crimp_tin_config(no_b, tin={"stripLen": "3"})) == {"a": True, "b": True, "lenA": "1,2", "lenB": "3"}
    # Своя длина операции главнее; без лужения ничего не подставляется.
    assert strip_of(_crimp_tin_config({**empty_b, "lenB": "4"}, tin={"stripLen": "3"}))["lenB"] == "4"
    plain = _crimp_tin_config(empty_b, tin={"stripLen": "3"})
    plain["ops"][0].pop("tin")
    assert strip_of(plain)["lenB"] == ""
    assert strip_of(_crimp_tin_config(empty_b))["lenB"] == ""


@pytest.mark.parametrize(("length", "tolerance"), [
    (104, "±3 мм"), (428, "±3 мм"), (429, "±4 мм"), (500, "±4 мм"), (501, "±5 мм"), (714, "±5 мм"), (715, "±6 мм"), (1300, "±10 мм"), (2000, "±14 мм"), (0, ""),
])
def test_cut_length_tolerance_is_at_least_3_mm_up_to_500_mm_and_5_mm_above_else_0_7_percent(length: int, tolerance: str):
    config = _crimp_tin_config({"a": True, "lenA": "1,2", "b": True, "lenB": "3"})
    config["ops"][0]["wires"][0]["length"] = length
    assert build_etk(config, CARDS, operation_library_commit="x")["operations"][0]["fields"]["cutTolerance"] == tolerance


def test_cut_length_tolerance_entered_by_hand_stays_as_entered():
    config = _crimp_tin_config({"a": True, "lenA": "1,2", "b": True, "lenB": "3"})
    config["ops"][0]["cutTolerance"] = "(+/-)1мм"
    assert build_etk(config, CARDS, operation_library_commit="x")["operations"][0]["fields"]["cutTolerance"] == "(+/-)1мм"


def test_cut_cards_list_the_measuring_tools_their_steps_use():
    config = {
        "assemblyName": "Жгут", "partQty": 10,
        "wires": [{"name": "Провод", "art": "ПВ3", "qty": 1, "length": 250}],
        "ops": [{
            "type": "cutWireCrimp", "templateId": "cut-wire-1crimp-автомат-резка-опрессовка",
            "opNum": "CUT_WIRE+1crimp", "side": "A", "tPrep": 60, "tOp": 2,
            "wires": [{"name": "Провод", "art": "ПВ3", "qty": 1, "length": 250}],
            "strip": {"a": True, "lenA": "3,5", "b": False, "lenB": ""},
            "ep": {"connArt": "XHP-2", "termArt": "SXH-001T-P0.6", "termName": "Терминал"},
            "terminalNorm": {"applicator": "A16"},
        }, {"type": "insTerm", "opNum": "INS_terminal", "side": "A", "ep": {"connArt": "XHP-2"}}],
    }
    tooling = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])["operations"][0]["card"]["tooling"]
    tools = [tool["name"] for tool in tooling if tool["section"] == "Инструмент"]
    assert "Штангенциркуль ШЦ-I (ГОСТ 166-89)" in tools
    assert any("микрометр" in name for name in tools)
    assert any("разрывная" in name for name in tools)
    numbers = [tool["number"] for tool in tooling if tool["section"] == "Инструмент"]
    assert numbers == [str(index) for index in range(1, len(numbers) + 1)]


def _mark_op(document: dict) -> dict:
    return next(item for item in document["operations"] if item["type"] == "insMark")


def test_bare_marking_toggle_keeps_the_old_etk_composition_tags_without_a_mark_list():
    op = _mark_op(build("708К.2-КБ1"))
    assert op["components"] == [
        {"art": "Маркировка проводов у разъёмов", "name": "Маркировка проводов у разъёмов", "norm": "760", "unit": "Шт."},
        {"art": "Маркировка отводов", "name": "Маркировка отводов", "norm": "114", "unit": "Шт."},
        {"art": "Децимальный номер изделия", "name": "Децимальный номер изделия", "norm": "38", "unit": "Шт."},
    ]
    assert "marks" not in op["fields"]
    # 20 с на метку × (20 + 3 + 1) метки × 38 шт + 120 с подготовки = 306 мин.
    assert [row["norm"] for row in op["time"]] == ["306"]


def test_marks_by_kd_list_every_mark_group_materials_and_scale_the_norm_by_their_count():
    config = deepcopy(kit("708К.2-КБ1")["config"])
    mark = next(item for item in config["ops"] if item["type"] == "insMark")
    tag = "Бирка маркировочная TMS-SCE-1/4-2.0-4 жёлтая"
    mark["marks"] = [
        {"art": tag, "name": tag, "qty": 1, "target": "Разъём X1 (174047-2)", "text": "X1", "place": "у разъёма", "placeKind": "conn", "placeMm": ""},
        {"art": tag, "name": tag, "qty": 1, "target": "Разъём X2", "text": "X2", "place": "у разъёма", "placeKind": "conn", "placeMm": ""},
        {"art": "Маркировка отводов", "name": "Маркировка отводов", "qty": 2, "target": "Провод W1", "text": "708К.4КБ1",
         "place": "50 мм от конца", "placeKind": "dist", "placeMm": "50"},
        {"art": "", "name": "", "qty": 1, "target": "Жгут целиком", "text": "КБ1 №1", "place": "на оплётке", "placeKind": "free", "placeMm": ""},
    ]
    op = _mark_op(build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"]))
    assert op["fields"]["marks"] == [
        {"target": "Разъём X1 (174047-2)", "text": "X1", "material": tag, "place": "у разъёма", "qty": "1", "batch": "38"},
        {"target": "Разъём X2", "text": "X2", "material": tag, "place": "у разъёма", "qty": "1", "batch": "38"},
        {"target": "Провод W1", "text": "708К.4КБ1", "material": "Маркировка отводов", "place": "50 мм от конца", "qty": "2", "batch": "76"},
        {"target": "Жгут целиком", "text": "КБ1 №1", "material": "", "place": "на оплётке", "qty": "1", "batch": "38"},
    ]
    # Материал складывается по артикулу на партию; метка без материала строки не даёт.
    assert op["components"] == [
        {"art": tag, "name": tag, "norm": "76", "unit": "Шт."},
        {"art": "Маркировка отводов", "name": "Маркировка отводов", "norm": "76", "unit": "Шт."},
    ]
    # Норма БД.ОП на штуку × 5 меток на изделие × 38 шт + подготовка: 20 × 5 × 38 / 60 + 2 = 65,33 мин.
    assert [row["norm"] for row in op["time"]] == ["65,33"]
    assert op["result"][0]["name"].endswith("; маркировка нанесена")
    assert op["card"]["steps"]


def test_product_mark_with_a_tab_goes_to_the_etk_in_russian_and_feeds_the_materials():
    config = deepcopy(kit("708К.2-КБ1")["config"])
    mark = next(item for item in config["ops"] if item["type"] == "insMark")
    mark["marks"] = [
        {"art": "Термотрубка", "name": "Термотрубка", "qty": 1, "target": "Разъём X1", "text": "X1", "place": "у разъёма", "placeKind": "conn", "placeMm": ""},
        {"art": "Хлястик", "name": "Хлястик", "qty": 1, "target": "Изделие КБ1", "text": "КБ1", "place": "на изделии", "placeKind": "item", "placeMm": ""},
    ]
    op = _mark_op(build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"]))
    assert [(m["target"], m["material"], m["place"]) for m in op["fields"]["marks"]] == [
        ("Разъём X1", "Термотрубка", "у разъёма"), ("Изделие КБ1", "Хлястик", "на изделии"),
    ]
    assert {c["art"] for c in op["components"]} == {"Термотрубка", "Хлястик"}

def test_wires_text_uses_full_word_with_russian_plural():
    from app.modules.techcards.etk_builder import _wires_text

    assert [_wires_text(n) for n in (1, 2, 4, 5, 11, 12, 21, 22, 25, 111)] == [
        "1 провод", "2 провода", "4 провода", "5 проводов", "11 проводов", "12 проводов",
        "21 провод", "22 провода", "25 проводов", "111 проводов"]


def test_input_of_every_operation_is_the_previous_result_word_for_word():
    """Резка → опрессовка А → монтаж А → опрессовка В → монтаж В: «Вход» n-й операции — «Результат» (n−1)-й, то же
    наименование и то же количество; ЭТК и печать берут обе строки из одних полей документа."""
    from tests.techcards.test_etk_golden import CARDS, LIBRARY, _kit

    document = build_etk(_kit("720К.-КБ1"), CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    operations = document["operations"]
    assert len(operations) == 5
    for before, after in zip(operations, operations[1:]):
        assert [(row["name"], row["norm"]) for row in after["semis"]] == [(row["name"], row["norm"]) for row in before["result"]]
    assert operations[2]["result"][0]["name"] == "Сторона А собрана: провода вставлены в разъём Molex 39012140"


def test_terminal_norm_counts_one_terminal_per_double_crimped_contact():
    """Генератор 14.10: два провода в одном контакте (`doublePins`) — один терминал на двоих; весь разъём двойным обжимом —
    как раньше, по `perTerm`."""
    wires = [{"name": f"НВ4 0,2 {color}", "qty": 1} for color in ("красный", "черный", "белый", "зеленый")]
    op = {"type": "prsTerm", "wires": wires, "ep": {"double": False}}
    assert _term_count(op, {}) == 4
    assert _term_count({**op, "ep": {"double": False, "doublePins": [3]}}, {}) == 3
    assert _term_count({**op, "ep": {"double": False, "doublePins": [1, 3]}}, {}) == 2
    assert _term_count({**op, "ep": {"double": False, "doublePins": [3, 3, "x", True, 0]}}, {}) == 3
    assert _term_count({**op, "ep": {"double": True, "perTerm": 2, "doublePins": [3]}}, {}) == 2
