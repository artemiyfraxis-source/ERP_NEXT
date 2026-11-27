"""Short common coax cards (свод инструкций 708К., 17.11.2026): `coax.shortCards` replaces the recipe lines + generic
card of the common operations by one short resolved list with exactly one control; nothing changes without the flag."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.operation_cards import parse_operation_library

ROOT = Path(__file__).resolve().parents[3]
LIBRARY = [*json.loads((ROOT / "backend/data/techcard-library.json").read_text(encoding="utf-8"))["templates"],
           *json.loads((ROOT / "backend/data/techcard-library-erp.json").read_text(encoding="utf-8"))["templates"]]
CARDS = parse_operation_library(LIBRARY)
BY_CODE = {card.code: card for card in CARDS}
BY_ID = {card.source_id: card for card in CARDS}
FIXTURES = ROOT / "frontend/src/__fixtures__"
FRESH = {row["kb"]: row["config"] for row in json.loads((FIXTURES / "fakra1116Configs.json").read_text(encoding="utf-8"))}
BATCH = {row["kb"]: row["config"] for row in json.loads((FIXTURES / "fakraBatchAConfigs.json").read_text(encoding="utf-8"))}
LEGACY = {row["kb"]: row["config"] for row in json.loads((FIXTURES / "fakra708Configs.json").read_text(encoding="utf-8"))}
COMMON = {"coaxCut", "coaxCutTut", "coaxInsTut", "coaxHeat", "coaxVswr"}


def _build(config):
    cards = [replace(BY_CODE.get(op["templateId"]) or BY_ID[op["templateId"]], source_id=op["templateId"])
             for op in config["ops"]]
    return build_etk(config, cards)


def _common(doc):
    return [op for op in doc["operations"] if op["type"] in COMMON and not op["side"]]


def _straight_program(config):
    # Production БД.КОАКС for 59K13O/59K25H: X = 9,7, Y = 4,6, Z = 3 (L3/L2/L1) — a computed full-strip program.
    for side in ("sideA", "sideB"):
        config["coax"][side].update(l1="3", l2="4,6", l3="9,7")
    return config


@pytest.mark.parametrize("config", [FRESH["КБ1"], FRESH["КБ2"], BATCH["КБ28"]], ids=["КБ1", "КБ2", "КБ28"])
def test_common_operations_are_short_with_exactly_one_control(config):
    doc = _build(deepcopy(config))
    common = _common(doc)
    assert {op["type"] for op in common} >= {"coaxCut", "coaxVswr"}
    tubes = config["coax"].get("markMaterial") == "tube"
    for op in common:
        if tubes and op["type"] not in {"coaxCut", "coaxVswr"}:
            continue  # printed marking tubes keep their own cards
        steps = op["card"]["steps"]
        assert 3 <= len(steps) <= 8, op["code"]
        assert [step["kind"] for step in steps].count("Контроль") == 1, op["code"]
        assert [step["number"] for step in steps] == [str(index) for index in range(1, len(steps) + 1)]
        text = " ".join(step["text"] for step in steps)
        assert not any(step["text"].startswith(("Вход:", "Результат:")) for step in steps), op["code"]
        assert "«Полуфабрикат 2»" not in text and "согласно КД" not in text
    assert _build(doc["build_config"])["operations"] == doc["operations"]


def test_without_the_flag_every_card_is_exactly_as_before():
    config = deepcopy(FRESH["КБ1"])
    del config["coax"]["shortCards"]
    doc = _build(config)
    cut = doc["operations"][0]
    assert cut["card"]["steps"][0]["text"].startswith("Сторона А:")
    assert len(cut["card"]["steps"]) > 8
    label = next(op for op in doc["operations"] if op["code"] == "INS_MARK_LABEL")
    assert any(step["text"].startswith("Вход:") for step in label["card"]["steps"])


def test_tooling_is_never_lost():
    short = _build(deepcopy(FRESH["КБ1"]))
    config = deepcopy(FRESH["КБ1"])
    del config["coax"]["shortCards"]
    legacy = _build(config)
    for new, old in zip(short["operations"], legacy["operations"]):
        assert new["code"] == old["code"]
        names = {(tool["section"], tool["name"], tool["inventory_number"]) for tool in new["card"]["tooling"]}
        assert names >= {(tool["section"], tool["name"], tool["inventory_number"]) for tool in old["card"]["tooling"]}, new["code"]
        assert (new["components"], new["semis"], new["result"], new["time"]) == (
            old["components"], old["semis"], old["result"], old["time"])


def test_cut_tolerance_binds_length_and_strip_program_of_each_side():
    cut = _build(_straight_program(deepcopy(FRESH["КБ1"])))["operations"][0]
    steps = cut["card"]["steps"]
    control = next(step for step in steps if step["kind"] == "Контроль")
    assert control["tolerance"] == ("Lрез 4855 мм 0…+5 мм; "
                                    "А: диэлектрик 3 ±0,2; оплётка 7,6 ±0,4; оболочка 12,7 ±0,4 мм от торца жилы; "
                                    "В: диэлектрик 3 ±0,2; оплётка 7,6 ±0,4; оболочка 12,7 ±0,4 мм от торца жилы")
    assert "по КД" not in control["tolerance"]
    first = steps[0]["text"]
    assert "Lрез = 4855 мм" in first and "L = 4800 мм" in first and "0…+10 мм" in first and "А 30, В 25" in first
    assert not any(step["text"].startswith("Сторона") for step in steps)  # sizes are in the sketch and the tolerance
    assert "штангенциркул" in control["text"].casefold()


def test_cut_keeps_blocking_notes_for_unconfirmed_strip_programs():
    hfm = _build(deepcopy(BATCH["КБ3"]))["operations"][0]
    text = " ".join(step["text"] for step in hfm["card"]["steps"])
    assert text.count("размер обрезки прямой оплётки до отворота не задан") == 2
    assert "Опорную гильзу обжать после полной разделки" in text
    assert "4 кабеля на сборку" in text
    unknown = _build(deepcopy(FRESH["КБ1"]))["operations"][0]  # fixture X/Y/Z cannot form a program
    control = next(step for step in unknown["card"]["steps"] if step["kind"] == "Контроль")
    assert "А: программа от торца не рассчитана" in control["tolerance"]
    assert any(step["text"].startswith("Сторона А: программа от торца не рассчитана") for step in unknown["card"]["steps"])


def test_labels_and_protective_tube_are_resolved_once():
    config = deepcopy(FRESH["КБ1"])
    ops = {op["code"]: op for op in _build(config)["operations"]}
    printed = ops["PRINT_MARK_LABEL"]["card"]["steps"]
    # The texts live once, in the result table (fields.marks); the step refers to it (ETK audit 23.11, B3).
    assert [row["text"] for row in ops["PRINT_MARK_LABEL"]["fields"]["marks"]] == [mark["text"] for mark in config["coax"]["marks"]]
    assert "по таблице «Результат»" in printed[0]["text"]
    assert not any(mark["text"] in printed[0]["text"] for mark in config["coax"]["marks"])
    assert "Номер программы и параметры печати — не задано" in printed[0]["text"]
    assert printed[-1]["tolerance"] == "СТО ФРАК-001-2026, раздел 4.6"
    label = ops["INS_MARK_LABEL"]["card"]["steps"]
    assert "у разъёма А, не ближе 100 мм от разъёма" in label[0]["text"]
    assert "прозрачную ТУТ" in label[1]["text"]
    assert label[-1]["tolerance"] == "не ближе 100 мм от разъёма; СТО ФРАК-001-2026, раздел 4.6"
    put_on = ops["INS_TUT"]["card"]["steps"]
    assert put_on[-1]["tolerance"] == ("ТУТ 4/2мм, прозрачная: 2 отрезка по 35 мм (концевые), "
                                       "1 отрезок по 55 мм (центральные) на сборку")
    cut = ops["CUT_TUT_short"]["card"]["steps"]
    assert [step["text"] for step in cut[1:3]] == ["Нарезать ТУТ 4/2мм, прозрачная: 30 отрезков по 35 мм (концевые).",
                                                    "Нарезать ТУТ 4/2мм, прозрачная: 15 отрезков по 55 мм (центральные)."]
    assert "допуск длины не задан" in cut[3]["tolerance"]


def test_heat_keeps_the_material_requirement_and_splits_label_and_joint():
    label_heat = next(op for op in _build(deepcopy(FRESH["КБ1"]))["operations"] if op["type"] == "coaxHeat")
    steps = label_heat["card"]["steps"]
    assert steps[0]["text"].startswith("Температура усадки материала: ТУТ 4/2мм, прозрачная")
    assert "согласовать до работы" in steps[0]["text"]  # material temperature is never the heat-gun setting
    assert label_heat["fields"]["shrinkRequirements"]
    assert "этикетк" in steps[1]["text"] and "соединени" not in " ".join(step["text"] for step in steps)
    ordinary = deepcopy(LEGACY["КБ11"])
    ordinary["coax"]["shortCards"] = True
    joint = next(op for op in _build(ordinary)["operations"] if op["type"] == "coaxHeat")
    steps = joint["card"]["steps"]
    assert steps[0]["text"].startswith("Температура усадки материала:")
    assert "на соединение с нахлёстом" in steps[1]["text"]
    assert "ГОСТ 23592-96, п. 4.4.7" in steps[-1]["tolerance"]


def test_plain_tube_uses_the_article_and_the_saved_length():
    config = deepcopy(LEGACY["КБ11"])
    config["coax"]["shortCards"] = True
    ops = _build(config)["operations"]
    put_on = next(op for op in ops if op["type"] == "coaxInsTut")
    assert put_on["card"]["steps"][0]["text"] == "Надеть ТУТ 4/2мм, прозрачная до монтажа разъёмов: 2 отрезка по 20 мм на сборку."
    assert put_on["card"]["steps"][-1]["tolerance"] == "ТУТ 4/2мм, прозрачная: 20 мм ×2"
    steps = json.dumps([op["card"]["steps"] for op in ops if op["type"] in COMMON], ensure_ascii=False)
    assert "купить" not in steps and "Доставка" not in steps
    heat = next(op for op in ops if op["type"] == "coaxHeat")
    assert heat["card"]["steps"][0]["text"].startswith("Температура усадки материала: ТУТ 4/2мм, прозрачная · ТУТ (HF)-4/2 прозр КВТ:")


LISTING = ("ТУТ (HF)-4/2 прозр КВТ- Трубка термоусадочная ТУТ (HF)-4/2 прозр (КВТ) купить - цена, фото, характеристики - "
           "Доставка по Москве и РФ")


def test_protective_tube_never_shows_the_shop_listing():
    config = deepcopy(FRESH["КБ1"])
    config["coax"]["tutName"] = LISTING
    ops = {op["code"]: op for op in _build(config)["operations"]}
    for code in ("CUT_TUT_short", "INS_TUT", "HEAT_TYT_short"):
        text = json.dumps(ops[code]["card"]["steps"], ensure_ascii=False)
        assert "купить" not in text and "Доставка" not in text, code
    assert ops["CUT_TUT_short"]["card"]["steps"][1]["text"] == "Нарезать ТУТ 4/2мм, прозрачная: 30 отрезков по 35 мм (концевые)."
    assert all("купить" not in item["name"] for item in ops["HEAT_TYT_short"]["fields"]["shrinkMaterials"])
    assert _build(_build(config)["build_config"])["operations"] == _build(config)["operations"]


def test_cut_keeps_the_bend_radius_rule():
    steps = _build(deepcopy(FRESH["КБ1"]))["operations"][0]["card"]["steps"]
    assert "радиус изгиба не менее двух наружных диаметров кабеля (ГОСТ 23592-96, п. 4.2.4)" in steps[-1]["text"]


@pytest.mark.parametrize("norm,tolerance,text", [
    ({"status": "ok", "vswrMax": 1.15, "bandMhz": [1, 6000],
      "ends": [{"article": "59K13o-1M4A4", "vswr": 1.07}, {"article": "59K13o-1M4A4", "vswr": 1.07}]},
     "≤ 1,15", "полосу 1…6000 МГц"),
    ({"source": "customer", "vswrMax": 1.3, "bandMhz": [1, 6000], "basis": "ТЗ заказчика",
      "components": {"status": "ok", "vswrMax": 1.19}}, "≤ 1,3 (норма заказчика, ТЗ заказчика)", "полосу 1…6000 МГц"),
    ({"status": "undefined", "reason": "у 73403-6270 нет КСВ в БД.КОАКС", "bandMhz": [1, 6000]},
     "решение ОТК", "результат не определён"),
    ({}, "решение ОТК", "нет данных справочников"),
])
def test_vswr_resolves_band_limit_and_coverage_without_provenance(norm, tolerance, text):
    config = deepcopy(BATCH["КБ3"])
    config["coax"]["vswrNorm"] = norm
    vswr = next(op for op in _build(config)["operations"] if op["type"] == "coaxVswr")
    steps = vswr["card"]["steps"]
    control = next(step for step in steps if step["kind"] == "Контроль")
    assert control["tolerance"] == tolerance
    joined = " ".join(step["text"] for step in steps)
    assert text in joined
    assert "каждого из 4 каналов сборки" in control["text"]
    assert "59K13o" not in joined and "по комплектующим" not in joined
    assert vswr["fields"]["coaxVswrNorm"] == norm  # provenance stays in the structured field
    tools = {tool["name"] for tool in vswr["card"]["tooling"]}
    assert "Ключ динамометрический" in tools


def test_short_vswr_measures_both_ends_after_warm_up_with_numeric_torques():
    """СТО ФРАК-001-2026, 4.8.16 «с двух концов»; СТО ФРАК-002-2026: прогрев 40 мин, полная двухпортовая калибровка,
    моменты затяжки по прил. Б. Однопорт с нагрузкой — только запасной, по согласованию ОТК."""
    vswr = next(op for op in _build(deepcopy(BATCH["КБ3"]))["operations"] if op["type"] == "coaxVswr")
    steps = vswr["card"]["steps"]
    assert [step["kind"] for step in steps].count("Контроль") == 1
    warm, connect, control, record = steps
    assert warm["text"].startswith("Прогреть векторный анализатор цепей не менее 40 мин, задать полосу ")
    assert "полную двухпортовую калибровку на концах обоих измерительных кабелей; метод — по СТО ФРАК-002-2026" in warm["text"]
    assert connect["text"].startswith("Подключить сторону А к порту 1, сторону В — к порту 2;")
    assert "с согласования ОТК" in connect["text"] and "документации производителя" not in connect["text"]
    assert connect["tolerance"] == "N — 1,1–1,5 Н·м; 3,5 мм и SMA — 0,8–1,0 Н·м"
    assert control["text"].startswith("Измерить КСВН с двух концов (S11 и S22) каждого из 4 каналов сборки")
    assert "S11 и S22" in record["text"]
    joined = " ".join(step["text"] for step in steps)
    assert "30 мин" not in joined and "OSL" not in joined


def test_printed_marking_tubes_keep_their_own_cards():
    config = deepcopy(BATCH["КБ3"])
    short = {op["code"]: op["card"]["steps"] for op in _build(config)["operations"] if not op["side"]}
    del config["coax"]["shortCards"]
    legacy = {op["code"]: op["card"]["steps"] for op in _build(config)["operations"] if not op["side"]}
    assert short["PRINT_MARK_TUBE"] == legacy["PRINT_MARK_TUBE"]
    assert short["INS_TUT"] == legacy["INS_TUT"]
    assert short["HEAT_TYT_short"] == legacy["HEAT_TYT_short"]
    assert short["CUT_COAX+2strip"] != legacy["CUT_COAX+2strip"]
