"""Метка на проводе (генератор «Сложная сборка», владелец 20.11): лазер ОБ.019 при резке (`laserMark` у cutWire /
cutWireStrip — в `fields.laserMark` как есть и строкой хода операции) и носитель печатной маркировки проводов
(`markMedia`: трубка Ø / лента шириной — в `fields.markMedia` и строкой хода печати). Без полей — ЭТК как до 20.11."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from tests.techcards.test_etk_crimp_batches import CONFIG, build, op_by_key
from tests.techcards.test_etk_derive import CONFIG_713, current_path, run

ENDS = {"mode": "ends", "front": "XS1.1 to X2.2", "back": "X2.2 to XS1.1", "middle": "", "frontMm": 20, "backMm": 20, "stepMm": 100}
FULL = {"mode": "full", "front": "XS1.1 to X2.2", "back": "X2.2 to XS1.1", "middle": "ZHGUT-7", "frontMm": 30, "backMm": 30, "stepMm": 150}
TEXTS = ["XS1.1 to X2.2", "X2.2 to XS1.1"]


def with_op(config: dict[str, Any], key: str, **fields: Any) -> dict[str, Any]:
    config = deepcopy(config)
    next(op for op in config["ops"] if op["opKey"] == key).update(fields)
    return config


def step_texts(operation: dict[str, Any]) -> list[str]:
    return [step["text"] for step in operation["card"]["steps"]]


def wire_mark_config(media: dict[str, Any] | None) -> dict[str, Any]:
    extra = {"markMedia": media} if media else {}
    return with_op(CONFIG, "cutMark", wireMarks=True, markTexts=TEXTS,
                   tutParts=[{"art": "ТУТ 4/2мм", "name": "ТУТ 4/2мм", "count": 2, "len": "15"}], **extra)


def test_laser_ends_is_copied_to_fields_and_told_in_the_steps_before_the_checks():
    plain = op_by_key(build(), "cutWire2")
    operation = op_by_key(build(with_op(CONFIG, "cutWire2", laserMark=ENDS)), "cutWire2")
    assert operation["fields"]["laserMark"] == ENDS
    line = "Маркировка лазером: по краям «XS1.1 to X2.2» / «X2.2 to XS1.1», 20 мм от торца"
    texts = step_texts(operation)
    assert texts.count(line) == 1
    assert [text for text in texts if text != line] == step_texts(plain)
    kinds = [step["kind"] for step in operation["card"]["steps"]]
    if "Контроль" in kinds:
        assert texts.index(line) < kinds.index("Контроль")
    assert [step["number"] for step in operation["card"]["steps"]] == [str(i) for i in range(1, len(texts) + 1)]
    assert {key: value for key, value in operation.items() if key not in {"fields", "card"}} == \
        {key: value for key, value in plain.items() if key not in {"fields", "card"}}


def test_laser_full_length_and_different_offsets():
    operation = op_by_key(build(with_op(CONFIG, "cutWire2", laserMark=FULL)), "cutWire2")
    assert operation["fields"]["laserMark"] == FULL
    assert "Маркировка лазером: по всей длине «ZHGUT-7» шаг 150 мм" in step_texts(operation)
    uneven = op_by_key(build(with_op(CONFIG, "cutWire2", laserMark={**ENDS, "backMm": 25.5})), "cutWire2")
    assert any(text.endswith("20 / 25,5 мм от торца") for text in step_texts(uneven))


def test_laser_on_a_crimping_cut_is_ignored():
    operation = op_by_key(build(with_op(CONFIG, "cutWire", laserMark=ENDS)), "cutWire")
    assert "laserMark" not in operation["fields"]
    assert not any("лазером" in text for text in step_texts(operation))


def test_mark_media_tube_names_the_size_and_the_texts():
    document = build(wire_mark_config({"kind": "tube", "sizeMm": 3.2}))
    cut = op_by_key(document, "cutMark")
    assert cut["fields"]["markMedia"] == {"kind": "tube", "sizeMm": 3.2}
    assert "Маркировка: трубка Ø 3,2 «XS1.1 to X2.2»; «X2.2 to XS1.1»" in step_texts(cut)
    assert cut["components"] == op_by_key(build(wire_mark_config(None)), "cutMark")["components"]


def test_mark_media_tape_is_a_printer_consumable():
    cut = op_by_key(build(wire_mark_config({"kind": "tape", "sizeMm": 9})), "cutMark")
    assert cut["fields"]["markMedia"] == {"kind": "tape", "sizeMm": 9}
    assert any(text.startswith("Маркировка: лента 9 мм «XS1.1 to X2.2»") for text in step_texts(cut))
    assert cut["components"] == []


def test_without_the_fields_the_document_is_as_before():
    text = json.dumps(build(wire_mark_config(None)), ensure_ascii=False)
    assert "laserMark" not in text and "markMedia" not in text and "Маркировка лазером" not in text
    assert "Маркировка: трубка" not in text and "Маркировка: лента" not in text


def test_derive_is_deterministic_with_the_fields():
    config = with_op(with_op(CONFIG_713, "cutWire2", laserMark=FULL), "cutMark", wireMarks=True, markTexts=TEXTS,
                     markMedia={"kind": "tube", "sizeMm": 4.8})
    document, conflicts = run(config)
    assert conflicts == []
    assert document == current_path(config)
    assert run(config)[0] == document
    assert op_by_key(document, "cutWire2")["fields"]["laserMark"] == FULL
