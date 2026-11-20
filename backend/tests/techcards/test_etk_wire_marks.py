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


# «Покрыть прозрачной трубкой» (владелец 20.11): прозрачная ТУТ поверх напечатанной метки, длина = метка + 10 мм;
# резка на ОБ.018 (CUT_TUT_short), надеть (INS_TUT), усадить (HEAT_TYT_short).
COVER = {"art": "ТУТ 6/3 прозрачная", "name": "ТУТ 6/3 прозрачная"}


def cover_config(media: dict | None = None, *, length: str = "25") -> dict:
    config = wire_mark_config()
    cut = next(op for op in config["ops"] if op["opKey"] == "cutMark")
    cut.update(templateId="print-mark-печать-маркировки-на-трубке-и-ленте", opNum="PRINT_MARK")  # TP80E ОБ.057
    if media:
        cut["markMedia"] = media
    at = config["ops"].index(cut) + 1
    common = {"shrinkMaterials": [COVER], "tutArt": COVER["art"], "tutName": COVER["name"], "tutCount": 4, "coverMm": 6}
    config["ops"][at:at] = [
        {**common, "type": "cutTut", "opKey": "cutWireMarkCover", "templateId": "cut-tut-short-резка", "label": "Резка прозрачной ТУТ",
         "tutSegs": [{"len": length, "count": 4}], "tutParts": [{**COVER, "count": 4, "len": length}]},
        {**common, "type": "insTut", "opKey": "onWireMarkCover", "templateId": "ins-tut-монтаж", "label": "Надеть прозрачную ТУТ"},
        {**common, "type": "heatTut", "opKey": "heatWireMarkCover", "templateId": "heat-tyt-short-усадка", "label": "Усадка прозрачной ТУТ"},
    ]
    return config


def test_mark_cover_is_cut_in_metres_put_on_and_shrunk():
    document = build(cover_config({"kind": "tape", "sizeMm": 9}))
    cut = op_by_key(document, "cutWireMarkCover")
    assert cut["components"] == [{"art": COVER["art"], "name": COVER["name"], "norm": "50", "unit": "М."}]  # 4 × 25 мм × 500 шт
    assert (cut["code"], cut["connector"]) == ("CUT_TUT_short", "прозрачная трубка поверх маркировки")
    assert [row["name"] for row in cut["result"]] == [f"Прозрачная трубка {COVER['art']} нарезана: 4 шт по 25 мм"]
    put_on, shrink = op_by_key(document, "onWireMarkCover"), op_by_key(document, "heatWireMarkCover")
    assert (put_on["code"], shrink["code"]) == ("INS_TUT", "HEAT_TYT_short")
    assert put_on["result"][0]["name"].endswith("прозрачная трубка надета поверх маркировки")
    assert shrink["result"][0]["name"].endswith("прозрачная трубка поверх маркировки усажена")
    assert put_on["components"] == [] and shrink["components"] == []


def test_mark_cover_is_told_on_the_print_card_after_the_media_line():
    print_card = op_by_key(build(cover_config({"kind": "tape", "sizeMm": 9})), "cutMark")
    assert print_card["code"] == "PRINT_MARK"
    assert [(tool["name"], tool["inventory_number"]) for tool in print_card["card"]["tooling"] if tool["section"] == "Оборудование"] == [
        ("TP80E | Принтер кабельных маркеров и термоусаживаемых трубок", "ОБ.057")]
    texts = [step["text"] for step in print_card["card"]["steps"]]
    media = next(index for index, text in enumerate(texts) if text.startswith("Маркировка: лента 9 мм"))
    assert texts[media + 1] == "Поверх метки — прозрачная термоусадочная трубка ТУТ 6/3 прозрачная Ø 6, L 25 мм"
    assert [step["number"] for step in print_card["card"]["steps"]] == [str(i) for i in range(1, len(texts) + 1)]


def test_mark_cover_without_its_length_takes_the_mark_plus_10_mm():
    config = cover_config({"kind": "tube", "sizeMm": 3.2})
    cover = next(op for op in config["ops"] if op["opKey"] == "cutWireMarkCover")
    cover["tutParts"] = [{**COVER, "count": 4, "len": ""}]
    texts = [step["text"] for step in op_by_key(build(config), "cutMark")["card"]["steps"]]
    assert "Поверх метки — прозрачная термоусадочная трубка ТУТ 6/3 прозрачная Ø 6, L 25 мм" in texts  # 15 + 10


def test_mark_cover_shrink_takes_the_clear_tube_material():
    from app.modules.techcards.etk_heat_shrink import material_association

    config = cover_config()
    for op in config["ops"]:
        if op.get("opKey") in {"onWireMarkCover", "heatWireMarkCover"}:
            op.pop("shrinkMaterials")
    shrink = next(op for op in config["ops"] if op["opKey"] == "heatWireMarkCover")
    assert material_association(shrink, config) == [{"article": COVER["art"], "name": COVER["name"]}]
