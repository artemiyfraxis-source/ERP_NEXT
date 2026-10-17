"""713К.4 КБ1 «Жгут НВ4» built by the generator «Сложная сборка» (owner 16.10): crimp at cut in batches per terminal
(SYM and SHF on side А, T-DS1071 on side В, SYM + T-DS1071 on both ends of the A6 wires), splice ends stripped at the cut,
tails cut + stripped + tinned on one machine, one cut for every tube, one for every braid piece, the marking printed and
cut, and the ПФ graph the generator drew itself (`build_config.route`, source «generated»): kits → branches X1–X4 →
splice groups (solder first, then the tube on the finished splice, owner 17.10) → «ТУТ на месте Б» (the harness tubes)
→ the product (only the side-В connectors X5, X6). The config is the generator's own output for the НВ4 fixture (frontend
tcGenRealHarness.fixture.ts `fullHarness`, kept equal by frontend/src/tcGenCrimpBatches.test.ts)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.modules.techcards.etk_builder import _chain_results, _time, build_etk
from app.modules.techcards.etk_marking import mark_kind
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
ERP_LIBRARY = json.loads((ROOT / "data" / "techcard-library-erp.json").read_text(encoding="utf-8"))
# The ERP cards too: CUT_WIRE+2strip+tin (tails) and INS_MARK_TUBE_AUTO (marking) live there.
CARDS = parse_operation_library(LIBRARY["templates"] + ERP_LIBRARY["templates"])
CONFIG = json.loads((Path(__file__).with_name("generator_713k4_config.json")).read_text(encoding="utf-8"))


def build(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_etk(config or CONFIG, CARDS, operation_library_commit=LIBRARY["sourceCommit"])


def op_by_key(document: dict[str, Any], key: str) -> dict[str, Any]:
    keys = [op.get("opKey") for op in document["build_config"]["ops"]]
    return document["operations"][keys.index(key)]


def terminals(operation: dict[str, Any]) -> dict[str, str]:
    return {row["art"]: row["norm"] for row in operation["components"] if not row["art"].startswith("НВ4")}


def test_each_terminal_is_crimped_at_cut_in_its_own_batch_with_its_own_norm():
    document = build()
    cuts = [op for op in document["operations"] if op["type"] == "cutWireCrimp"]
    assert [(op["code"], op["connector"], op["side"]) for op in cuts] == [
        ("CUT_WIRE+1crimp", "обжим SYM-001T-P0.6", "A"),
        ("CUT_WIRE+1crimp", "обжим T-DS1071-SC600", "B"),
        ("CUT_WIRE+1crimp", "обжим SHF-001T-0.8BS", "A"),
        ("CUT_WIRE+2crimp", "обжим А и В SYM-001T-P0.6 (А) / T-DS1071-SC600 (В)", "A"),
    ]
    # 500 изделий: SYM 4 + 3, T-DS1071 4 + 3, SHF 2 на изделие — столько же, сколько в составе (7, 7, 2).
    assert [terminals(op) for op in cuts] == [
        {"SYM-001T-P0.6": "2000"}, {"T-DS1071-SC600": "2000"}, {"SHF-001T-0.8BS": "1000"},
        {"SYM-001T-P0.6": "1500", "T-DS1071-SC600": "1500"},
    ]
    # Провод ×2 «белый 0,12»: одна жила — к SYM («I3»), другая — к SHF («C»): в каждой партии по одной.
    # 157 мм: припуск на свивку по шагу 713К.4 — 20 мм (владелец 17.10).
    assert [row["name"] for row in cuts[2]["result"]] == [
        "НВ4 0,12 белый 157мм, обжатый терминал SHF-001T-0.8BS", "НВ4 0,12 синий 157мм, обжатый терминал SHF-001T-0.8BS"]
    assert cuts[0]["result"][2] == {"name": "НВ4 0,12 белый 157мм, обжатый терминал SYM-001T-P0.6", "norm": "500", "unit": "Шт."}
    # Хвосты «сросток → облуженный конец» станок не обжимает: резка, зачистка обоих концов и лужение хвоста — одной
    # операцией (BZW-5.0+Z), без отдельных зачистки и лужения хвостов и без зачистки концов под сростки.
    tails = op_by_key(document, "cutWire2")
    assert (tails["code"], [row["name"] for row in tails["result"]]) == ("CUT_WIRE+2strip+tin", [
        "НВ4 0,2 красный 60мм", "НВ4 0,2 черный 60мм", "НВ4 0,12 белый 60мм", "НВ4 0,12 синий 60мм"])
    # Конец под сросток — 5 мм по умолчанию (владелец 17.10), хвост — 3 мм.
    assert tails["fields"]["strip"] == {"a": True, "b": True, "lenA": "5", "lenB": "3"}
    assert _chain_results(CONFIG)[2].endswith("; концы зачищены, свободные концы облужены")
    keys = {op["opKey"] for op in document["build_config"]["ops"]}
    assert not keys & {"stripSpl", "stripTail", "tinTail"}
    # Концы под сростки зачищает станок: партия SYM — обжим А и зачистка В.
    assert op_by_key(document, "cutWire")["fields"]["strip"]["b"] is True
    # Ручной опрессовки нет — всё обжато при резке.
    assert not [op for op in document["operations"] if op["type"] == "prsTerm"]
    assert document["warnings"] == []


def test_batches_name_the_connectors_they_crimp():
    names = _chain_results(CONFIG)
    assert names[0].endswith("; 5, I3: обжаты терминалы SYM-001T-P0.6")
    assert names[1].endswith("; В1, В2: обжаты терминалы T-DS1071-SC600")
    assert names[4].endswith("; A6: обжаты терминалы SYM-001T-P0.6; I-DS1071-SCB02X3: обжаты терминалы T-DS1071-SC600")


def test_a_single_crimp_cut_keeps_its_old_etk_key():
    """Without `crimpBatch` (one cut with crimp, or a config before 16.10) the ETK key stays «обжим»."""
    config = deepcopy(CONFIG)
    for op in config["ops"]:
        op.pop("crimpBatch", None)
    assert build(config)["operations"][0]["connector"] == "обжим"


def test_marking_is_printed_and_cut_put_on_with_the_branch_tubes_and_shrunk_with_them():
    document = build()
    cut = op_by_key(document, "cutMark")
    assert (cut["code"], cut["connector"]) == ("INS_MARK_TUBE_AUTO", "маркировка")
    assert cut["components"] == [
        {"art": f'Маркировка "{text}" 4/2мм', "name": f'Маркировка "{text}" 4/2мм', "norm": "500", "unit": "Шт."}
        for text in ("5", "I3", "C", "A6")]
    # Комплект — по строке на отрезок: что нарезано и сколько на партию.
    assert cut["result"][0] == {"name": 'Маркировка "5" 4/2мм — 15 мм', "norm": "500", "unit": "Шт."}
    assert len(cut["result"]) == 4
    # Ветвь X1 (владелец 16.10): монтаж контактов → надеть оплётку → надеть ТТК → надеть маркировку → усадка ТТК на
    # разъёме → усадка маркировки — каждая своей операцией и картой.
    keys = ("insTermA1", "braidA1", "onTubesA1", "onMarkA1", "heatShrA1", "heatMarkA1")
    branch = [op_by_key(document, key) for key in keys]
    assert [(op["code"], op["connector"]) for op in branch] == [
        ("INS_terminal_2,54-6,3", "SMR-02V-B"), ("INS_BRAID", "X1 «5»"), ("INS_TUT", "на жгут X1 «5»"),
        ("INS_TUT", "маркировка X1 «5»"), ("HEAT_TTK_short", "поверх разъёма А1"), ("HEAT_TYT_short", "маркировка X1 «5»")]
    assert [op["position"] for op in branch] == list(range(9, 15))
    assert branch[1]["components"] == []
    # «Маркировка "5" 4/2мм» — термоусадочная трубка и для маркировки по КД (вид метки).
    assert mark_kind('Маркировка "5" 4/2мм') == "heat"


def test_every_tube_is_cut_by_one_operation_and_every_braid_piece_by_another():
    document = build()
    tubes, braid = op_by_key(document, "cutTubes"), op_by_key(document, "cutBraid")
    assert (tubes["code"], tubes["connector"]) == ("CUT_TUT_short", "трубки")
    assert tubes["components"] == [
        {"art": "ТТК 8/2мм, черная", "name": "ТТК 8/2мм, черная", "norm": "1500", "unit": "Шт."},
        {"art": "ТТК 12/4мм, черная", "name": "ТТК 12/4мм, черная", "norm": "1000", "unit": "Шт."},
        {"art": "ТТК 3/1мм, черная", "name": "ТТК 3/1мм, черная", "norm": "2000", "unit": "Шт."},
    ]
    assert [(row["name"], row["norm"]) for row in tubes["result"]] == [
        ("ТТК 8/2мм, черная — 15 мм", "1500"), ("ТТК 12/4мм, черная — 25 мм", "1000"), ("ТТК 3/1мм, черная — 20 мм", "2000")]
    # Название результата для карты — как раньше, одной фразой.
    assert _chain_results(CONFIG)[5] == ("Трубки нарезаны: ТТК 8/2мм, черная — 3 шт по 15 мм; ТТК 12/4мм, черная — 2 шт по 25 мм; "
                                         "ТТК 3/1мм, черная — 4 шт по 20 мм")
    # Оплётка — отрезок на ветвь (X1 120, X2 110, X3 110, X4 120 мм); материал в метрах — у резки, не у монтажа ветвей.
    assert (braid["code"], braid["connector"]) == ("CUT_NEYLON", "оплётка")
    assert braid["components"] == [{"art": "XP-3", "name": "XP-3", "norm": "230", "unit": "М."}]
    assert [(row["name"], row["norm"]) for row in braid["result"]] == [("XP-3 — 120 мм", "1000"), ("XP-3 — 110 мм", "1000")]
    assert _chain_results(CONFIG)[6] == "Оплётка XP-3 нарезана: 2 шт по 120 мм; 2 шт по 110 мм"
    assert [op_by_key(document, f"braidA{n}")["components"] for n in range(1, 5)] == [[], [], [], []]


def test_a_branch_twists_its_own_pairs_only():
    document = build()
    assert [op["opKey"] for op in document["build_config"]["ops"] if op["type"] == "twist"] == ["twistA2", "twistA3"]
    assert op_by_key(document, "twistA2")["connector"] == "X2 «I3»"
    # Owner 17.10: each branch twist carried both pairs of the form («1) … 2) …», count 2) — now its own pair, shared pitch.
    for key in ("twistA2", "twistA3"):
        assert op_by_key(document, key)["fields"]["twist"] == {
            "pitch": "20", "pairs": "1) НВ4 0,12 белый + НВ4 0,12 синий", "count": 1}
    both = deepcopy(CONFIG)
    both["twist"]["pairs"] = [[7, 9], [8, 10]]
    assert op_by_key(build(both), "twistA2")["fields"]["twist"]["count"] == 1
    for op in both["ops"]:
        op.pop("twistPairs", None)
    assert op_by_key(build(both), "twistA2")["fields"]["twist"]["count"] == 2
    # Время свивки — по своим парам (`units`), не по всем парам формы.
    config = {"partQty": 2, "wires": [{"qty": 1}] * 4, "twist": {"mode": "pairs", "pairs": [[0, 1], [2, 3]]}}
    assert _time({"type": "twist", "units": 1, "tOp": "60", "tPrep": "0"}, config, "x")[0]["norm"] == "2"
    assert _time({"type": "twist", "tOp": "60", "tPrep": "0"}, config, "x")[0]["norm"] == "4"


def test_ultrasonic_splices_take_the_weld_card_and_no_sleeves():
    config = deepcopy(CONFIG)
    for op in config["ops"]:
        if op["type"] == "splice":
            op.update(method="ultrasonic", templateId="us-weld-уз-сварка", sleeves=[])
        if op["opKey"].startswith(("insSpl", "heatSpl")):
            op["spliceWeld"] = True
    document = build(config)
    operation = op_by_key(document, "splice1")
    assert (operation["code"], operation["components"]) == ("US_WELD", [])
    assert operation["result"][0]["name"] == "Провода сварены ультразвуком"
    assert [op_by_key(document, key)["connector"] for key in ("insSpl1", "heatSpl1", "insSpl2")] == [
        "на сростки С1, С2", "на сростках С1, С2", "на сростки С3, С4"]


def test_the_card_is_accepted_with_unique_etk_keys():
    document = build()
    keys = [(op["code"], op["connector"], op["side"]) for op in document["operations"]]
    assert len(keys) == len(set(keys))
    now = datetime.now(timezone.utc)
    card = ProjectTechcard.create(project="713", item_index="К.4 КБ1", product_name="Жгут НВ4", source_calculation="",
                                  deadline=None, now=now)
    card.rebuild(document, now)


def test_the_generator_draws_the_semi_finished_graph_kits_branches_splice_groups_product():
    # Owner 16.10: «нам нужно понимать иерархию». The generator draws the ПФ graph itself (source «generated»): every
    # cut batch, the tube cut, the braid cut and the marking are kits (made once per product); the branches X1–X4 take
    # them; the splice groups take the branches and the cut wires of the other ends; owner 17.10: the harness tubes go
    # on at place Б as their own ПФ (splice groups, X4, the tubes), and the product only mounts X5, X6.
    document = build()
    route = document["route"]
    assert route["source"] == "generated"
    keys = [op["opKey"] for op in document["build_config"]["ops"]]
    blocks = {block["key"]: block for block in route["blocks"]}
    by_label = [(block["label"], block["name"], [keys[position - 1] for position in block["ops"]], block.get("kit", False))
                for block in route["blocks"]]

    def branch(n: int, *, tube: bool = True, twist: bool = True) -> list[str]:
        return [f"insTermA{n}", *([f"twistA{n}"] if twist else []), f"braidA{n}", *([f"onTubesA{n}"] if tube else []),
                f"onMarkA{n}", *([f"heatShrA{n}"] if tube else []), f"heatMarkA{n}"]

    # Короткие разные имена ПФ (владелец 16.10): партии резки — что делает станок и на какие разъёмы по КД.
    assert by_label == [
        ("1.1", "Резка + обжим SYM, зачистка — X1, X2", ["cutWire"], True),
        ("1.2", "Резка + обжим T-DS1071, зачистка — X5, X6", ["cutWire1"], True),
        ("1.3", "Резка хвостов + лужение", ["cutWire2"], True),
        ("1.4", "Резка + обжим SHF, зачистка — X3", ["cutWire3"], True),
        ("1.5", "Резка + обжим А и В — X4→X6", ["cutWire4"], True),
        ("1.6", "Резка трубок", ["cutTubes"], True), ("1.7", "Резка оплётки", ["cutBraid"], True),
        ("1.8", "Маркировка", ["cutMark"], True),
        ("2.1", "Ветвь X1 «5», сторона А", branch(1, twist=False), False),
        ("2.2", "Ветвь X2 «I3», сторона А", branch(2), False),
        ("2.3", "Ветвь X3 «C», сторона А", branch(3), False),
        ("2.4", "Ветвь X4 «A6», сторона А", branch(4, tube=False, twist=False), False),
        ("3.1", "Пайка С1, С2", ["splice1", "insSpl1", "heatSpl1"], False),
        ("3.2", "Пайка С3, С4", ["splice2", "insSpl2", "heatSpl2"], False),
        ("4", "ТУТ на месте Б", ["insSleeves", "heatHs"], False),
        ("5", "Кабельная сборка Жгут НВ4", ["insTermB1", "insTermB2"], False),
    ]
    assert blocks["pf-final"]["final"] and sum(len(block["ops"]) for block in route["blocks"]) == len(keys)
    edges = {(edge["from"], edge["to"]): edge.get("item", "") for edge in route["edges"]}
    assert edges[("pf-cutWire", "pf-A1")] == "НВ4 0,5 красный, черный — 2 шт"
    assert edges[("pf-cutWire1", "pf-spl1")] == "НВ4 0,2 красный ×2, черный ×2 — 4 шт"
    assert edges[("pf-tubes", "pf-A1")] == "ТТК 8/2 — 1 шт"
    assert edges[("pf-tubes", "pf-hs")] == "ТТК 12/4 — 2 шт"
    assert edges[("pf-tubes", "pf-spl1")] == "ТТК 3/1 — 2 шт"
    assert edges[("pf-braid", "pf-A2")] == "Оплётка XP-3 110 мм — 1 шт"
    assert edges[("pf-mark", "pf-A1")] == "Маркировка «5» — 1 шт"
    assert {pair for pair in edges if pair[0].startswith(("pf-A", "pf-spl", "pf-hs"))} == {
        ("pf-A1", "pf-spl1"), ("pf-A2", "pf-spl2"), ("pf-A3", "pf-spl2"), ("pf-A4", "pf-hs"),
        ("pf-spl1", "pf-hs"), ("pf-spl2", "pf-hs"), ("pf-hs", "pf-final")}
    assert document["warnings"] == []


def test_a_hand_edit_wins_and_a_generated_graph_that_no_longer_fits_falls_back_with_a_warning():
    broken = deepcopy(CONFIG)
    broken["route"]["blocks"][0]["ops"] = [1, 2]
    document = build(broken)
    assert any("схема генератора не подошла" in warning for warning in document["warnings"])
    manual = deepcopy(CONFIG)
    manual["route"].pop("source")
    assert build(manual)["route"]["source"] == "manual"


def rows(operation: dict[str, Any], key: str = "semis") -> list[tuple[str, str, str]]:
    return [(row["name"], row["norm"], row.get("source", "")) for row in operation[key]]


def test_the_first_operation_of_a_pf_takes_one_row_per_arrow_of_the_scheme():
    # Owner 16.10: «Вход» of a ПФ's first operation — one row per incoming arrow, × the batch (500), with the ПФ it comes
    # from; a kit's arrow by what it carries, a branch or a splice group by its name. Parts from stock stay parts.
    document = build()
    mount = op_by_key(document, "insTermA2")
    assert rows(mount) == [
        ("НВ4 0,12 белый, синий — 2 шт", "1000", "ПФ 1.1"), ("ТТК 8/2 — 1 шт", "500", "ПФ 1.6"),
        ("Оплётка XP-3 110 мм — 1 шт", "500", "ПФ 1.7"), ("Маркировка «I3» — 1 шт", "500", "ПФ 1.8")]
    assert all(row["unit"] == "Шт." for row in mount["semis"])
    assert mount["components"] == [{"art": "SMR-02V-B", "name": "SMR-02V-B", "norm": "500", "unit": "Шт."}]
    assert rows(op_by_key(document, "splice2")) == [
        ("НВ4 0,12 белый, синий — 2 шт", "1000", "ПФ 1.3"), ("ТТК 3/1 — 2 шт", "1000", "ПФ 1.6"),
        ("Ветвь X2 «I3», сторона А", "500", "ПФ 2.2"), ("Ветвь X3 «C», сторона А", "500", "ПФ 2.3")]
    assert rows(op_by_key(document, "insSleeves")) == [
        ("ТТК 12/4 — 2 шт", "1000", "ПФ 1.6"), ("Ветвь X4 «A6», сторона А", "500", "ПФ 2.4"),
        ("Пайка С1, С2", "500", "ПФ 3.1"), ("Пайка С3, С4", "500", "ПФ 3.2")]
    assert rows(op_by_key(document, "insTermB1")) == [("ТУТ на месте Б", "500", "ПФ 4")]
    # Комплекты ни из чего не делаются — входа нет.
    assert [op_by_key(document, key)["semis"] for key in ("cutWire", "cutTubes", "cutBraid", "cutMark")] == [[], [], [], []]


def test_inside_a_pf_each_operation_takes_the_previous_one_and_the_last_hands_on_the_pf():
    document = build()
    keys = ["insTermA2", "twistA2", "braidA2", "onTubesA2", "onMarkA2", "heatShrA2", "heatMarkA2"]
    branch = [op_by_key(document, key) for key in keys]
    results = ["Контакты установлены в разъём", "Пара свита", "Оплётка надета", "ТТК надета", "Маркировка надета",
               "ТТК усажена", "Ветвь X2 «I3», сторона А"]
    assert [rows(op, "result") for op in branch] == [[(name, "500", "")] for name in results]
    assert [rows(op) for op in branch[1:]] == [
        [(name, "500", f"операция {op['position'] - 1:02d}")] for name, op in zip(results, branch[1:])]
    assert [row["name"] for row in op_by_key(document, "heatSpl2")["result"]] == ["Пайка С3, С4"]
    # Owner 17.10: solder first, then the tube on the finished splice, then shrink.
    assert [row["name"] for row in op_by_key(document, "splice2")["result"]] == ["Провода спаяны"]
    assert rows(op_by_key(document, "insSpl2"), "result") == [("Трубка надета на сростки", "500", "")]
    assert [row["name"] for row in op_by_key(document, "heatHs")["result"]] == ["ТУТ на месте Б"]
    last = document["operations"][-1]
    assert (last["result"], last["result_label"]) == ([{"name": "Кабельная сборка Жгут НВ4", "norm": "500", "unit": "Шт."}], "Изделие")
    # Имена ПФ схемы — это и есть то, что отдают их последние операции.
    keys_all = [op["opKey"] for op in CONFIG["ops"]]
    assert _chain_results(CONFIG)[keys_all.index("heatMarkA2")] == "Ветвь X2 «I3», сторона А"


def test_a_scheme_drawn_before_reads_its_arrows_and_names_unnamed_blocks_by_their_product():
    # Схема #494: «ТТК 8/2 ×1», партии резки без имён.
    config = deepcopy(CONFIG)
    for block in config["route"]["blocks"]:
        if block["key"].startswith("pf-cutWire"):
            block.pop("name")
    for edge in config["route"]["edges"]:
        if edge["from"] == "pf-tubes":
            edge["item"] = edge["item"].replace(" — ", " ×").replace(" шт", "")
    document = build(config)
    assert rows(op_by_key(document, "insTermA2"))[:2] == [
        ("НВ4 0,12 белый, синий — 2 шт", "1000", "ПФ 1.1"), ("ТТК 8/2 — 1 шт", "500", "ПФ 1.6")]
    assert document["route"]["blocks"][0]["name"].startswith("Провода нарезаны: ")


def test_without_the_generators_scheme_the_chain_stays_linear():
    # Схема, правленная руками («Править ПФ»), или её нет — цепочка идёт по списку, как раньше.
    manual = deepcopy(CONFIG)
    manual["route"].pop("source")
    document = build(manual)
    assert all("source" not in row for row in op_by_key(document, "insTermA2")["semis"])
    assert op_by_key(document, "twistA2")["result"][0]["name"] != "Ветвь X2 «I3», сторона А"
