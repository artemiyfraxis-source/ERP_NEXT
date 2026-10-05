"""Harness domain model: TECHMAP-GRAPHER rules re-checked (EX-01 and edge cases), the snapshot (de)serializer,
the operations a wire implies and the check against «Данные из листа»."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.techcards.harness import (
    HarnessRuleError, SheetRow, cut_length, dump_design, match_sheet, parse_design, route_ops, wire_ops,
)
from app.modules.techcards.harness.model import Cable, Covering, CoveringSpan, Dimension
from app.modules.techcards.harness.rules import (
    covering_kind, covering_length, normalize_strip_layers, sheath_strip, strip_steps, terminal_label, to_decimal,
)

from . import grapher_fixture as fx

D = Decimal


def _design():
    design, warnings = parse_design(fx.design())
    return design, warnings


# --- rules ----------------------------------------------------------------------------------------------------
def test_cut_length_is_drawing_length_plus_signed_allowances_rounded_up_once():
    # EX-01 (TECHMAP-GRAPHER docs/acceptance-examples.md): 1000 + 10 + 20 − 2 + 3 = 1031.
    assert cut_length(D(1000), D(10 - 2), D(20 + 3)).cut_mm == D(1031)
    # Corrections add up exactly, only the total is rounded: 150.2 + 0.4 = 150.6 → 151, not 150.2→151 + 0.4.
    cut = cut_length(D("150.2"), D("0.4"), D(0))
    assert (cut.unrounded_mm, cut.cut_mm) == (D("150.6"), D(151))
    assert cut_length(D(100), D(0), D(-2)).cut_mm == D(98)
    assert cut_length(D(101), rounding_step_mm=D(5)).cut_mm == D(105)
    assert cut_length(D(100), rounding_step_mm=D(5)).cut_mm == D(100)


def test_cut_length_edges():
    assert cut_length(None, D(5), D(5)).cut_mm is None
    with pytest.raises(HarnessRuleError):
        cut_length(D(5), D(-10), D(0))
    with pytest.raises(HarnessRuleError):
        cut_length(D("1.0005"))
    with pytest.raises(HarnessRuleError):
        cut_length(D(10), rounding_step_mm=D(0))


def test_sheath_strip_grows_by_the_end_allowance_and_zero_means_no_strip():
    cable = Cable("c", ("a", "b"), length_mm=D(1000), correction_a_mm=D(8), correction_b_mm=D(23),
                  has_sheath_strip=True, sheath_a_mm=D(30), sheath_b_mm=D(20))
    strip = sheath_strip(cable)
    assert (strip.cut_a_mm, strip.cut_b_mm) == (D(38), D(43))
    zero = sheath_strip(Cable("c", (), length_mm=D(100), correction_a_mm=D(5), has_sheath_strip=True,
                              sheath_a_mm=D(0), sheath_b_mm=None))
    assert (zero.cut_a_mm, zero.cut_b_mm) == (D(0), None)
    assert sheath_strip(Cable("c", (), length_mm=D(100))) is None
    with pytest.raises(HarnessRuleError):
        sheath_strip(Cable("c", (), length_mm=D(40), has_sheath_strip=True, sheath_a_mm=D(30), sheath_b_mm=D(20)))


def test_strip_layers_skip_zero_empty_dash_as_no_layer():
    # Bug in the original (normalizeWireStripProfileLayers refuses L = 0); its own contract says «no layer».
    layers, warnings = normalize_strip_layers([
        {"index": 1, "diameterMm": 0.5, "stripLengthMm": 2},
        {"index": 2, "diameterMm": 1.6, "stripLengthMm": "5,5"},
        {"index": 3, "diameterMm": 2.0, "stripLengthMm": 0},
        {"index": 4, "diameterMm": 2.2, "stripLengthMm": ""},
        {"index": 5, "diameterMm": 2.4, "stripLengthMm": "-"},
        {"index": 6, "diameterMm": 2.6, "stripLengthMm": None},
        {"index": 7, "diameterMm": 3.0, "stripLengthMm": 9},
    ])
    assert warnings == []
    assert [(layer.index, layer.length_mm) for layer in layers] == [(1, D(2)), (2, D("5.5")), (7, D(9))]
    assert [step for _layer, step in strip_steps(layers)] == [D(2), D("3.5"), D("3.5")]


def test_strip_layers_out_of_order_warns_instead_of_dropping_the_profile():
    layers, warnings = normalize_strip_layers([{"index": 1, "stripLengthMm": 5}, {"index": 2, "stripLengthMm": 3}])
    assert len(layers) == 2
    assert warnings == ["L2: слои должны идти по возрастанию номера, длины и диаметра"]


def test_terminal_label_decodes_only_a_complete_catalog_key():
    assert terminal_label(fx.SHF_KEY) == "SHF-001T-0.8BS"
    assert terminal_label("3:JST|0:|8:BL2018TS|3:SHF") == "BL2018TS"
    assert terminal_label("T-DS1069-SC600") == "T-DS1069-SC600"
    assert terminal_label("3:JST|99:x") == "3:JST|99:x"


def test_covering_length_manual_dimensions_and_missing():
    dims = (Dimension("s", 0, 1, D(30)), Dimension("s", 1, 2, D(50)), Dimension("s", 0, 2, D(999), auxiliary=True))
    span = CoveringSpan("s", D(0), D(1), 0, 2)
    assert covering_length(Covering("c", "x", "braid", (span,), length_mode="manual", length_mm=D(250)), dims) == D(250)
    assert covering_length(Covering("c", "x", "braid", (span,), length_mode="auto"), dims) == D(80)
    assert covering_length(Covering("c", "x", "braid", (CoveringSpan("s", D(0), D(1)),), length_mode="auto"), dims) is None
    # Bug in the original (coveringMeasuredLength returns 0 for a covering without spans): no length, not 0.
    assert covering_length(Covering("c", "x", "braid", (), length_mode="auto"), dims) is None
    # Legacy document without lengthMode: a stored length is manual.
    assert covering_length(Covering("c", "x", "braid", (span,), length_mm=D(12)), dims) == D(12)


def test_covering_kind_from_name_when_missing():
    assert covering_kind("", "Термоусадка 6/3") == "heat-shrink"
    assert covering_kind("", "Лента ПВХ") == "tape"
    assert covering_kind("", "Что-то") == "braid"
    assert covering_kind("nylon", "Термоусадка") == "nylon"


def test_to_decimal_reads_comma_and_treats_dash_as_empty():
    assert to_decimal("1,5") == D("1.5")
    assert to_decimal("-") is None
    assert to_decimal(0.1) == D("0.1")
    assert to_decimal("abc") is None


# --- snapshot (de)serializer --------------------------------------------------------------------------------------
def test_parse_reads_every_section_of_the_export():
    design, warnings = _design()
    assert warnings == ["Маршрут «Провода нарезанные»: операция без кода БД.ОП"]
    assert design.writer_contract == 2
    assert [w.id for w in design.wires] == [f"w{n}" for n in range(1, 11)]
    w1 = design.wire("w1")
    assert (w1.end_a.kind, w1.end_a.connector_id, w1.length_mm, w1.correction_a_mm, w1.correction_b_mm) == (
        "contact", "x1", D(300), D(8), D(4))
    contact = design.contact_at(w1.end_a)[1]
    assert (contact.number, contact.name, contact.terminal.l_plus_mm, contact.terminal.l_minus_mm) == (
        1, "Питание", D("1.5"), D("-0.8"))
    assert design.wire("w7").end_a.kind == "screen" and design.wire("w7").end_a.screen_side == "below"
    assert design.wire("w8").end_b.kind == "free"
    assert [layer.index for layer in design.wire("w8").strip_a.layers] == [1, 2, 3]
    assert design.junctions[0].wire_ids == ("w2", "w3", "w4") and design.junctions[0].label == "Сросток 1"
    assert design.diff_pairs[0].wire_ids == ("w5", "w6")
    assert design.cables[0].sheath_a_mm == D(30)
    assert [c.kind for c in design.topology.coverings] == ["heat-shrink", "braid"]
    assert design.topology.wire_segments["w1"] == ("s1", "s2")
    assert [row.id for row in design.route.rows] == ["r1", "r2"]
    assert design.route.rows[0].operations[0].operation.source_key == "CUT_WIRE_auto"
    assert design.route.rows[0].terminal_requirements[0].side == "A"


def test_older_export_without_sections_reads_as_empty_with_warnings():
    design, warnings = parse_design({"schemaVersion": 1, "connectors": [], "wires": [
        {"id": "w", "from": {"connectorId": "", "contactId": ""}, "to": {"connectorId": "", "contactId": ""},
         "lengthMm": 10},
    ]})
    assert (design.cables, design.junctions, design.screens, design.diff_pairs) == ((), (), (), ())
    assert design.topology is None and design.route is None
    assert warnings == [
        "Экспорт из старой версии TECHMAP-GRAPHER (без версии контракта) — часть разделов может отсутствовать",
        "В чертеже нет раздела «Кабели» (старый экспорт) — раздел пуст",
        "В чертеже нет раздела «Сростки» (старый экспорт) — раздел пуст",
        "В чертеже нет раздела «Дифпары» (старый экспорт) — раздел пуст",
        "В чертеже нет раздела «Экраны» (старый экспорт) — раздел пуст",
        "В чертеже нет физической топологии — оболочки и трассы проводов не прочитаны",
    ]
    assert design.wire("w").correction_a_mm == 0 and design.wire("w").rounding_step_mm == 1


def test_unrecognised_things_become_russian_warnings_not_errors():
    content = fx.design()
    content["futureSection"] = {}
    content["requiredWriterContractVersion"] = 3
    content["diffPairs"].append({"id": "p2", "wireIds": ["w1"]})
    content["wires"][0]["to"] = {"portId": "q"}
    content["wires"][1]["lengthMm"] = "abc"
    content["manufacturingRoute"]["rows"][0]["operations"][0]["mode"] = "laser"
    content["physicalTopology"]["coverings"][1]["kind"] = "kevlar"
    design, warnings = parse_design(content)
    assert design.wire("w1").end_b.kind == "free"
    assert design.wire("w2").length_mm is None
    assert len(design.diff_pairs) == 1
    for expected in (
        "Экспорт новее, чем знает ERP (контракт 3) — незнакомые поля пропущены",
        "Незнакомый раздел чертежа «futureSection» пропущен",
        "Дифпара p2: нужны два разных провода — пропущена",
        "Провод +5V: незнакомый тип конца (portId) — конец свободный",
        "Провод GND: длина «abc» не число — считается неизвестной",
        "Маршрут «Провода нарезанные»: незнакомый режим операции «laser»",
        "Оболочка Оплётка: незнакомый вид «kevlar»",
    ):
        assert expected in warnings


def test_broken_links_are_reported():
    content = fx.design()
    content["junctions"][0]["wireIds"] = ["w2"]
    content["wires"][0]["from"] = {"connectorId": "x1", "contactId": "nope"}
    content["wires"][6]["from"]["screenId"] = "s9"
    _design_, warnings = parse_design(content)
    assert "Провод +5V, конец А: контакт не найден в соединителе" in warnings
    assert "Сросток 1: соединяет меньше двух проводов" in warnings
    assert "Сросток 1: в нём кончается провод GND, но сросток его не содержит" in warnings
    assert "Провод Экран, конец А: экран не найден" in warnings


def test_dump_writes_the_same_document_back_and_keeps_unmodelled_fields():
    design, _warnings = _design()
    dumped = dump_design(design)
    again, _ = parse_design(dumped)
    assert again == design
    assert dumped["views"] == fx.DESIGN["views"]
    assert dumped["wires"][0]["e4Route"] == [{"x": 1, "y": 2}]
    assert dumped["wires"][7]["stripProfiles"]["from"]["layers"][-1]["index"] == 3
    assert dumped["physicalTopology"]["segments"] == fx.DESIGN["physicalTopology"]["segments"]
    assert dumped["manufacturingRoute"]["rows"][0]["presentation"] == {"backgroundOpacity": 1, "objects": []}
    # L− is written back signed.
    assert dumped["connectors"][0]["contacts"][0]["terminalDetails"]["lengthMinusMm"] == -0.8


def test_legacy_route_row_with_components_is_split_per_component():
    content = fx.design()
    content["manufacturingRoute"]["rows"] = [{
        "id": "g", "kind": "semiFinished", "title": "Группа", "comment": "", "dependsOn": [], "operations": [],
        "prepared": False, "presentation": {"backgroundOpacity": 1, "objects": []},
        "sourceObjects": [{"kind": "wire", "id": "w1"}, {"kind": "wire", "id": "w2"}],
        "components": [{"ref": {"kind": "wire", "id": "w1"}, "title": "Провод 1"},
                       {"ref": {"kind": "wire", "id": "w2"}, "title": "Провод 2"}],
    }]
    design, warnings = parse_design(content)
    assert [(row.id, row.kind, row.depends_on) for row in design.route.rows] == [
        ("g-pf-1", "semiFinished", ()), ("g-pf-2", "semiFinished", ()), ("g", "assembly", ("g-pf-1", "g-pf-2"))]
    assert "Маршрут в старом формате (полуфабрикаты внутри строки) — разложен по строкам" in warnings


# --- derivations ----------------------------------------------------------------------------------------------
def test_wire_ops_map_ends_onto_generator_operations():
    design, _ = _design()
    ops, notes = wire_ops(design, design.wire("w1"))
    assert notes == []
    assert [(op.type, op.side, op.shared) for op in ops] == [
        ("cutWireStrip", "", ""), ("prsTerm", "A", ""), ("insTerm", "A", ""), ("prsTerm", "B", ""), ("insTerm", "B", ""),
        ("cutTut", "", "covering:cv1"), ("insTut", "", "covering:cv1"), ("heatTut", "", "covering:cv1"),
        ("insWrap", "", "covering:cv2"),
    ]
    cut, crimp_a = ops[0].data, ops[1].data
    assert (cut["length_mm"], cut["correction_a_mm"], cut["correction_b_mm"], cut["cut_mm"]) == (300, 8, 4, 312)
    assert cut["strip_sides"] == ["A", "B"] and ops[0].block == "r1"
    assert (crimp_a["terminal"], crimp_a["terminal_l_plus_mm"], crimp_a["terminal_l_minus_mm"],
            crimp_a["allowance_mm"], crimp_a["strip_length_mm"]) == ("T-DS1069-SC600", D("1.5"), D("-0.8"), 8, D("3.5"))
    assert ops[3].data["terminal"] == "SHF-001T-0.8BS"
    assert ops[-1].data["wrap_kind"] == "braid"


def test_splice_of_three_wires_screen_end_free_end_and_coax():
    design, _ = _design()
    splices = [next(op for op in wire_ops(design, design.wire(w))[0] if op.type == "splice") for w in ("w2", "w3", "w4")]
    assert {op.shared for op in splices} == {"junction:j1"}
    assert splices[0].data["wire_ids"] == ["w2", "w3", "w4"]
    drain = wire_ops(design, design.wire("w7"))[0]
    assert (drain[1].type, drain[1].shared, drain[1].data["target"]) == ("splice", "screen:s1", "screen")
    coax, notes = wire_ops(design, design.wire("w8"))
    assert [(op.type, op.side) for op in coax] == [("cutWire", ""), ("coaxStrip", "A")]
    assert [layer["step_mm"] for layer in coax[1].data["layers"]] == [2, 3, 4]
    assert notes == []
    pair = [op for op in wire_ops(design, design.wire("w5"))[0] if op.type == "twist"]
    assert pair[0].shared == "pair:p1"


def test_cable_core_is_cut_and_stripped_with_its_cable():
    design, _ = _design()
    ops, _notes = wire_ops(design, design.wire("w9"))
    assert [(op.type, op.shared) for op in ops[:2]] == [("cutWire", "cable:c1"), ("stripCable", "cable:c1")]
    assert ops[0].data["cut_mm"] == 1031 and ops[0].data["cores"] == 2
    assert (ops[1].data["sheath_cut_a_mm"], ops[1].data["sheath_cut_b_mm"]) == (38, 43)


def test_contact_without_terminal_becomes_soldering_with_a_note():
    content = fx.design()
    content["connectors"][1]["contacts"][0]["terminalArticle"] = ""
    design, _ = parse_design(content)
    ops, notes = wire_ops(design, design.wire("w1"))
    assert ("solderConn", "B") in [(op.type, op.side) for op in ops]
    assert notes == ["Провод +5V, конец Б (X2:1): терминал не задан — выведена пайка, проверь"]


def test_wire_without_length_has_no_cut_and_a_note():
    content = fx.design()
    content["wires"][0]["lengthMm"] = None
    design, _ = parse_design(content)
    ops, notes = wire_ops(design, design.wire("w1"))
    assert ops[0].data["cut_mm"] is None
    assert notes == ["Провод +5V: нет длины в чертеже — длина резки не посчитана"]


def test_route_ops_carry_operation_codes_of_the_block():
    design, _ = _design()
    ops = route_ops(design, design.wire("w1"))
    assert [(op.type, op.data["code"], op.block) for op in ops] == [("cutWireCrimp", "CUT_WIRE_auto", "r1"), ("tin", "", "r1")]
    assert route_ops(design, design.wire("w5")) == []


def test_sheet_match_by_article_kd_and_quantity_warnings():
    design, _ = _design()
    rows = [SheetRow(r["id"], r["type"], r["name"], r["article"], r["kd"], to_decimal(r["qty"])) for r in fx.SHEET]
    matches, warnings = match_sheet(design, rows)
    by_sheet = {m.sheet_id: m for m in matches if m.sheet_id}
    assert (by_sheet["s-x1"].matched_by, by_sheet["s-x1"].status) == ("article", "ok")
    assert (by_sheet["s-x2"].matched_by, by_sheet["s-x2"].status) == ("kd", "ok")
    assert (by_sheet["s-t2"].quantity, by_sheet["s-t2"].status) == (D(6), "quantity")
    assert (by_sheet["s-tut"].quantity, by_sheet["s-tut"].status) == (D("0.08"), "ok")
    assert by_sheet["s-wire"].status == "not_in_drawing"
    assert warnings == [
        "Провод ПВАМ 0,35 есть в чертеже, но нет в листе",
        "Провод ПВВС 2х0,35 есть в чертеже, но нет в листе",
        "Соединитель SMA-J есть в чертеже, но нет в листе",
        "Соединитель PLS-2 есть в чертеже, но нет в листе",
        "Терминал PLS-T есть в чертеже, но нет в листе",
        "Оболочка Оплётка есть в чертеже, но нет в листе",
        "Провод ПВВС 4х0,35мм есть в листе, но нет в чертеже",
        "Терминал SHF-001T-0.8BS: в чертеже 6 шт, в листе 4",
    ]


def test_sheet_match_by_name_and_wire_metres():
    design, _ = _design()
    rows = [SheetRow("w", "Провод", "ПВАМ 0,35 красный", "", "", D("2.071")),
            SheetRow("x", "", "Разъём DS1069", "", "", D(1))]
    matches, warnings = match_sheet(design, rows)
    by_sheet = {m.sheet_id: m for m in matches if m.sheet_id}
    # 312 + 151 + 98 + 120 + 400 + 400 + 90 + 500 mm cut = 2.071 m
    assert (by_sheet["w"].matched_by, by_sheet["w"].quantity, by_sheet["w"].status) == ("name", D("2.071"), "ok")
    assert by_sheet["x"].matched_by == "name"
    assert not any("ПВАМ" in item for item in warnings)


def test_harness_package_stays_pure():
    root = Path(__file__).resolve().parents[2] / "app" / "modules" / "techcards" / "harness"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("fastapi", "sqlalchemy", "googleapiclient", "pydantic"):
            assert forbidden not in text, f"{path.name} imports {forbidden}"
