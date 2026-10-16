"""713К.4 КБ1 «Жгут НВ4» built by the generator «Сложная сборка» (owner 16.10): crimp at cut in batches per terminal
(SYM and SHF on side А, T-DS1071 on side В, SYM + T-DS1071 on both ends of the A6 wires), marking tubes cut / put on /
shrunk as their own operations, ultrasonic splices. The config is the generator's own output for the НВ4 fixture
(frontend tcGenRealHarness.fixture.ts `fullHarness`, kept equal by frontend/src/tcGenCrimpBatches.test.ts)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.modules.techcards.etk_builder import _chain_results, build_etk
from app.modules.techcards.etk_marking import mark_kind
from app.modules.techcards.operation_cards import parse_operation_library
from app.modules.techcards.project_cards import ProjectTechcard


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
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
    assert [row["name"] for row in cuts[2]["result"]] == [
        "НВ4 0,12 белый 158мм, обжатый терминал SHF-001T-0.8BS", "НВ4 0,12 синий 158мм, обжатый терминал SHF-001T-0.8BS"]
    assert cuts[0]["result"][2] == {"name": "НВ4 0,12 белый 158мм, обжатый терминал SYM-001T-P0.6", "norm": "500", "unit": "Шт."}
    # Хвосты и сростки станок не обжимает: их провода — простой резкой.
    plain = op_by_key(document, "cutWire2")
    assert (plain["code"], [row["name"] for row in plain["result"]]) == ("CUT_WIRE_auto", [
        "НВ4 0,2 красный 60мм", "НВ4 0,2 черный 60мм", "НВ4 0,12 белый 60мм", "НВ4 0,12 синий 60мм"])
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


def test_marking_tubes_are_cut_put_on_before_the_connectors_and_shrunk_with_the_shrinks():
    document = build()
    cut, put_on, shrink = (op_by_key(document, key) for key in ("cutMark", "onMark", "heatMark"))
    assert (cut["code"], cut["connector"]) == ("CUT_TUT_short", "маркировка")
    assert cut["components"] == [
        {"art": f'Маркировка "{text}" 4/2мм', "name": f'Маркировка "{text}" 4/2мм', "norm": "500", "unit": "Шт."}
        for text in ("5", "I3", "C", "A6")]
    assert cut["result"][0]["name"].startswith('Маркировка нарезана: Маркировка "5" 4/2мм — 1 шт по 15 мм;')
    # Надевают только маркировку: трубка сростков и ТТК хвостовиков ушли в свои операции.
    assert (put_on["code"], put_on["connector"]) == ("INS_TUT", "маркировка")
    assert put_on["semis"][1]["name"] == cut["result"][0]["name"]
    assert op_by_key(document, "insSpl")["semis"][1]["name"] == "Трубка ТТК 3/1мм, черная нарезана: 4 шт по 20 мм"
    assert op_by_key(document, "insSleeves")["semis"][1]["name"] == "Трубка ТТК 8/2мм, черная нарезана: 4 шт по 15 мм"
    assert put_on["result"][0]["name"].endswith("; маркировка надета")
    assert (shrink["code"], shrink["connector"]) == ("HEAT_TYT_short", "маркировка")
    assert shrink["result"][0]["name"].endswith("; маркировка усажена")
    positions = [op["position"] for op in (put_on, shrink)]
    mounts = [op["position"] for op in document["operations"] if op["type"] == "insTerm" and op["side"] == "A"]
    assert positions[0] < min(mounts) < max(mounts) < positions[1]
    # «Маркировка "5" 4/2мм» — термоусадочная трубка и для маркировки по КД (вид метки).
    assert mark_kind('Маркировка "5" 4/2мм') == "heat"


def test_ultrasonic_splices_take_the_weld_card_and_no_sleeves():
    config = deepcopy(CONFIG)
    splice = next(op for op in config["ops"] if op["opKey"] == "splice")
    splice.update(method="ultrasonic", templateId="us-weld-уз-сварка", sleeves=[])
    for op in config["ops"]:
        if op["opKey"] in {"insSpl", "heatSpl"}:
            op["spliceWeld"] = True
    document = build(config)
    operation = op_by_key(document, "splice")
    assert (operation["code"], operation["components"]) == ("US_WELD", [])
    assert operation["result"][0]["name"].endswith("; провода сварены ультразвуком")
    assert [op_by_key(document, key)["connector"] for key in ("insSpl", "heatSpl")] == ["на сростки", "на сростках"]


def test_the_card_is_accepted_with_unique_etk_keys():
    document = build()
    keys = [(op["code"], op["connector"], op["side"]) for op in document["operations"]]
    assert len(keys) == len(set(keys))
    now = datetime.now(timezone.utc)
    card = ProjectTechcard.create(project="713", item_index="К.4 КБ1", product_name="Жгут НВ4", source_calculation="",
                                  deadline=None, now=now)
    card.rebuild(document, now)
