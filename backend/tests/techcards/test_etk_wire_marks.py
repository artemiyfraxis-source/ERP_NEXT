"""Маркировка проводов «откуда → куда» (генератор «Сложная сборка», владелец 26.10, 708К.5 п.8): печать и резка метки
(`wireMarks`) выдаёт трубку в метрах (отрезков × длина × партия), а надписи для оператора идут в результат операции."""

from __future__ import annotations

from copy import deepcopy

from tests.techcards.test_etk_crimp_batches import CONFIG, build, op_by_key


TEXTS = ["XS1.1 to X2.2", "X2.2 to XS1.1", "XS1.2 to С1", "С1 to XS1.2"]


def wire_mark_config() -> dict:
    config = deepcopy(CONFIG)
    cut = next(op for op in config["ops"] if op["opKey"] == "cutMark")
    cut.update(wireMarks=True, markTexts=TEXTS, tutParts=[{"art": "ТУТ 4/2мм", "name": "ТУТ 4/2мм", "count": 4, "len": "15"}])
    return config


def test_wire_marks_cut_issues_metres_and_lists_the_texts():
    cut = op_by_key(build(wire_mark_config()), "cutMark")
    assert cut["components"] == [{"art": "ТУТ 4/2мм", "name": "ТУТ 4/2мм", "norm": "30", "unit": "М."}]  # 4 × 15 мм × 500 шт
    text = " ".join(row["name"] for row in cut["result"])
    assert [row["name"] for row in cut["result"]] == [f"{item} — 15 мм" for item in TEXTS]
    assert all(item in text for item in TEXTS)


def test_other_marking_cuts_keep_pieces():
    cut = op_by_key(build(), "cutMark")
    assert {row["unit"] for row in cut["components"]} == {"Шт."}
