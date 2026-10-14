"""«Маркировка» ЭТК по видам меток (владелец 12.10.2026): ход операции, дефекты, оборудование и время — по видам из
таблицы «Метки по КД»; оборудование и нормы — из справочника (зеркало backend/data/reference-mirror/)."""

import csv
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.etk_marking import TYPES, mark_kind
from app.modules.techcards.operation_cards import parse_operation_library
from app.workflows.techcard_build import enrich_marking

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = json.loads((ROOT / "data" / "techcard-library.json").read_text(encoding="utf-8"))
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "current-etk.json").read_text(encoding="utf-8"))
CARDS = parse_operation_library(LIBRARY["templates"])
MIRROR = ROOT / "data" / "reference-mirror"


def _tsv(name: str, header_row: int) -> list[dict[str, str]]:
    lines = (MIRROR / name).read_text(encoding="utf-8").splitlines()[header_row - 1:]
    return list(csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE))


def _mirror_equipment() -> list[tuple[str, dict[str, str]]]:
    return [(row["Для базы"], {"Инв. номер": row["Инв. номер"]}) for row in _tsv("БД.ОБ.tsv", 2) if row.get("Для базы")]


def _mirror_operations() -> list[dict[str, str]]:
    return [{"opNumber": row["Номер"], "tOp": row["Время Операции"], "tPrep": row["Время подготовки, сек"]}
            for row in _tsv("БД.ОП.tsv", 2) if row.get("Номер")]


def _mark(material: str, text: str, qty: int = 1, target: str = "Провод W1") -> dict:
    return {"art": material, "name": material, "qty": qty, "target": target, "text": text, "place": "по КД",
            "placeKind": "kd", "placeMm": ""}


def _build(marks: list[dict], *, references: bool = True) -> tuple[dict, list[str]]:
    config = deepcopy(FIXTURES["files"]["kit-708К.2-КБ1.json"]["payload"]["config"])  # партия 38 шт.
    next(item for item in config["ops"] if item["type"] == "insMark")["marks"] = marks
    if references:
        config = enrich_marking(config, _mirror_equipment(), _mirror_operations())
    document = build_etk(config, CARDS, operation_library_commit=LIBRARY["sourceCommit"])
    return next(item for item in document["operations"] if item["type"] == "insMark"), document["warnings"]


def _equipment(op: dict) -> list[tuple[str, str]]:
    return [(tool["name"], tool["inventory_number"]) for tool in op["card"]["tooling"] if tool["section"] == "Оборудование"]


@pytest.mark.parametrize(("material", "kind"), [
    ("Хлястик", "flag"), ("Термотрубка", "heat"), ("ТУТнг 4/2 черная", "heat"), ("ТТК 6/2 черная", "heat"),
    ("Бирка маркировочная TMS-SCE-1/4-2.0-4 жёлтая", "heat"), ("Трубка ТВ-40 ПВХ 4мм (кембрик) чёрная", "pvc"),
    ("Бирка", "tag"), ("Маркировочные кольца", "ring"), ("Печать на изоляции", "print"),
    ("Маркировка отводов", "other"), ("", "other"),
])
def test_mark_kind_comes_from_the_material(material: str, kind: str):
    assert mark_kind(material) == kind


def test_mirror_has_the_marking_equipment_and_norms():
    config = {"ops": [{"type": "insMark", "marks": [_mark("Хлястик", "КБ1")]}]}
    kit = enrich_marking(config, _mirror_equipment(), _mirror_operations())["ops"][0]["markKit"]
    assert kit["equipment"] == {
        "flagPrinter": {"name": "ZCUT-RT430/Маркиратор проводов флашковый", "inventory": "ОБ.101"},
        "tubePrinter": {"name": "TP80E/Принтер кабельных маркеров и термоусаживаемых трубок", "inventory": "ОБ.057"},
        "tubeCutter": {"name": "/Автомат мерной резки гофрированных и термоусаживаемых трубок", "inventory": "ОБ.018"},
        "heater": {"name": "8620DX-Pro/Станция паяльная термовоздушная", "inventory": "ОБ.070.1"},
    }
    assert {key: (norm["code"], norm["operation_seconds"], norm["setup_seconds"]) for key, norm in kit["norms"].items()} == {
        "flagPrint": ("INS_MARK_AUTO", "4,00", "600"), "tubePrint": ("PRINT_MARK", "2,00", "600"),
        "tubeCut": ("CUT_TUT_short", "1,00", "300"), "heat": ("HEAT_TYT_short", "9,00", "120"),
    }


def test_flag_marking_prints_on_the_flag_machine_installs_fixes_and_checks():
    op, warnings = _build([_mark("Хлястик", "КБ1", target="Изделие КБ1")])
    steps = op["card"]["steps"]
    assert [step["kind"] for step in steps] == ["Операция", "Операция", "Операция", "Контроль"]
    assert steps[0]["text"] == "Напечатать хлястики — ZCUT-RT430 «Маркиратор проводов флашковый»: надписи по таблице «Метки по КД», 38 шт. на партию."
    assert steps[3]["tolerance"] == "Согласно КД"
    assert [step["number"] for step in steps] == ["1", "2", "3", "4"]
    assert not any("термо" in step["text"].lower() or "трубк" in step["text"] for step in steps)
    assert op["card"]["defects"].splitlines() == ["Хлястик:", *(f"{name}: {cause}" for name, cause in TYPES[0]["defects"])]
    assert _equipment(op) == [("ZCUT-RT430 | Маркиратор проводов флашковый", "ОБ.101")]
    # Монтаж 20 с × 1 × 38 + 120 с = 14,67 мин; печать INS_MARK_AUTO 4 с × 38 + 600 с = 12,53 мин.
    assert [(row["name"], row["norm"]) for row in op["time"][1:]] == [("Печать хлястиков", "12,53")]
    assert op["time"][0]["norm"] == "14,67"
    assert warnings == []


def test_heat_shrink_tube_is_printed_cut_put_on_before_termination_and_shrunk():
    op, _warnings = _build([_mark("Термотрубка", "X1"), _mark("ТУТнг 4/2 черная", "X2", qty=2)])
    texts = [step["text"] for step in op["card"]["steps"]]
    assert texts[0].startswith("Напечатать надписи на трубках — TP80E «Принтер кабельных маркеров и термоусаживаемых трубок»")
    assert "114 шт. на партию" in texts[0]
    assert texts[1] == "Нарезать трубки в размер — Автомат мерной резки гофрированных и термоусаживаемых трубок."
    assert op["card"]["steps"][1]["tolerance"] == "длина не задана"
    assert texts[2].startswith("Надеть трубку на провод до оконцовки")
    assert texts[3].startswith("Усадить трубку — 8620DX-Pro «Станция паяльная термовоздушная»")
    assert op["card"]["steps"][4]["kind"] == "Контроль"
    assert op["card"]["defects"].splitlines()[0] == "Термоусадочная трубка:"
    assert "Трубка не усажена или усажена не полностью: мало времени или температуры нагрева" in op["card"]["defects"]
    assert _equipment(op) == [
        ("TP80E | Принтер кабельных маркеров и термоусаживаемых трубок", "ОБ.057"),
        ("Автомат мерной резки гофрированных и термоусаживаемых трубок", "ОБ.018"),
        ("8620DX-Pro | Станция паяльная термовоздушная", "ОБ.070.1"),
    ]
    # 3 трубки на изделие × 38: печать 2 с + 600, резка 1 с + 300, усадка 9 с + 120.
    assert [(row["name"], row["norm"]) for row in op["time"][1:]] == [
        ("Печать на трубках", "13,8"), ("Нарезка трубок", "6,9"), ("Усадка трубок", "19,1"),
    ]


def test_pvc_tube_is_cut_but_not_shrunk_and_needs_no_heater():
    op, _warnings = _build([_mark("Трубка ТВ-40 ПВХ 4мм (кембрик) чёрная", "X1")])
    texts = " ".join(step["text"] for step in op["card"]["steps"])
    assert "Нарезать трубки в размер" in texts and "не усаживается" in texts
    assert "Усадить" not in texts
    assert [name for name, _ in _equipment(op)] == [
        "TP80E | Принтер кабельных маркеров и термоусаживаемых трубок",
        "Автомат мерной резки гофрированных и термоусаживаемых трубок",
    ]
    assert [row["name"] for row in op["time"][1:]] == ["Печать на трубках", "Нарезка трубок"]
    assert "не усажена" not in op["card"]["defects"] and op["card"]["defects"].startswith("ПВХ-трубка:")


def test_mixed_marking_lists_each_kind_block_once_in_the_order_of_its_first_mark():
    op, _warnings = _build([
        _mark("Термотрубка", "X1"), _mark("Хлястик", "КБ1"), _mark("Термотрубка", "X2"), _mark("Бирка", "W1"),
    ])
    steps = op["card"]["steps"]
    heads = list(dict.fromkeys(step["text"].split(" — ", 1)[0] for step in steps))
    assert heads == ["Термоусадочная трубка", "Хлястик", "Бирка"]
    assert [step["number"] for step in steps] == [str(index) for index in range(1, len(steps) + 1)]
    assert sum(step["kind"] == "Контроль" for step in steps) == 3
    groups = [line for line in op["card"]["defects"].splitlines() if line.endswith(":")]
    assert groups == ["Термоусадочная трубка:", "Хлястик:", "Бирка:"]
    assert "76 шт. на партию" in steps[0]["text"]  # две термотрубки на изделие
    assert steps[0]["text"].startswith("Термоусадочная трубка — напечатать надписи на трубках")
    assert any(step["text"].startswith("Хлястик — ВИЗУАЛЬНЫЙ КОНТРОЛЬ") for step in steps)


def test_without_references_the_flag_machine_is_named_generically_and_no_time_rows_are_added():
    op, warnings = _build([_mark("Хлястик", "КБ1")], references=False)
    assert "станок для хлястиков" in op["card"]["steps"][0]["text"]
    assert [(tool["name"], tool["parameters"]) for tool in op["card"]["tooling"] if tool["section"] == "Оборудование"] == [
        ("станок для хлястиков", "нет в справочнике «Оборудование»"),
    ]
    assert len(op["time"]) == 1 and warnings == []


def test_a_missing_norm_is_a_build_warning_not_an_invented_time():
    operations = [item for item in _mirror_operations() if item["opNumber"] != "CUT_TUT_short"]
    config = deepcopy(FIXTURES["files"]["kit-708К.2-КБ1.json"]["payload"]["config"])
    next(item for item in config["ops"] if item["type"] == "insMark")["marks"] = [_mark("Термотрубка", "X1")]
    document = build_etk(enrich_marking(config, _mirror_equipment(), operations), CARDS, operation_library_commit="x")
    op = next(item for item in document["operations"] if item["type"] == "insMark")
    assert [row["name"] for row in op["time"][1:]] == ["Печать на трубках", "Усадка трубок"]
    assert any("«Нарезка трубок» (CUT_TUT_short)" in warning for warning in document["warnings"])


def test_legacy_marking_without_a_kd_list_keeps_the_card_as_it_was():
    config = deepcopy(FIXTURES["files"]["kit-708К.2-КБ1.json"]["payload"]["config"])
    enriched = enrich_marking(config, _mirror_equipment(), _mirror_operations())
    assert "markKit" not in next(item for item in enriched["ops"] if item["type"] == "insMark")


def _tube(material: str, text: str, length, qty: int = 1) -> dict:
    return {**_mark(material, text, qty), **({"lengthMm": length} if length is not None else {})}


def test_tube_cutting_step_lists_the_lengths_from_the_marks_table():
    op, _warnings = _build([_tube("Термотрубка", "X1", 25), _tube("Термотрубка", "X2", "30,5")])
    step = op["card"]["steps"][1]
    assert step["text"] == "Нарезать трубки в размер: X1 — 25 мм, X2 — 30,5 мм — Автомат мерной резки гофрированных и термоусаживаемых трубок."
    assert step["tolerance"] == "Согласно КД"
    assert [row["length"] for row in op["fields"]["marks"]] == ["25", "30,5"]


def test_equal_tube_lengths_are_grouped_per_item():
    op, _warnings = _build([_tube("Термотрубка", "X1", 25), _tube("Термотрубка", "X2", 25), _tube("ТУТнг 4/2", "X3", 25, qty=2)])
    assert op["card"]["steps"][1]["text"].startswith("Нарезать трубки в размер: 25 мм — 4 шт. на изделие — ")


def test_tube_rows_without_length_keep_the_not_set_tolerance_only_for_them():
    op, _warnings = _build([_tube("Термотрубка", "X1", 25), _tube("Термотрубка", "X2", None), _tube("Термотрубка", "X3", 0)])
    step = op["card"]["steps"][1]
    assert step["text"].startswith("Нарезать трубки в размер: X1 — 25 мм — ")
    assert step["tolerance"] == "длина не задана: X2, X3"


def test_length_of_a_non_tube_mark_is_ignored_and_old_marks_stay_not_set():
    op, _warnings = _build([_tube("Хлястик", "КБ1", 25)])
    assert "length" not in op["fields"]["marks"][0]
    assert all("мм" not in step["text"] for step in op["card"]["steps"])
    op, _warnings = _build([_mark("Термотрубка", "X1")])
    assert op["card"]["steps"][1]["tolerance"] == "длина не задана"
    assert "length" not in op["fields"]["marks"][0]


def test_mixed_heat_and_pvc_blocks_get_their_own_lengths():
    op, _warnings = _build([_tube("Термотрубка", "X1", 25), _tube("Трубка ТВ-40 ПВХ 4мм (кембрик) чёрная", "W1", 40)])
    cuts = [step["text"] for step in op["card"]["steps"] if "арезать" in step["text"]]
    assert len(cuts) == 2
    assert cuts[0].startswith("Термоусадочная трубка — нарезать трубки в размер: X1 — 25 мм — ")
    assert cuts[1].startswith("ПВХ-трубка — нарезать трубки в размер: W1 — 40 мм — ")


def test_product_number_token_stays_in_the_inscription_and_changes_nothing_else():
    # «№ изделия» (`{№}`, `{№:3}`) разворачивает экран задания; ЭТК хранит надпись как есть, остальное — как у
    # надписи без номера: ход операции, материалы, время.
    numbered, _ = _build([_mark("Хлястик", "КБ1-{№:3}", target="Изделие КБ1"), _mark("Термотрубка", "X1-{№}")])
    plain, _ = _build([_mark("Хлястик", "КБ1-001", target="Изделие КБ1"), _mark("Термотрубка", "X1-1")])
    assert [mark["text"] for mark in numbered["fields"]["marks"]] == ["КБ1-{№:3}", "X1-{№}"]
    for mark in (*numbered["fields"]["marks"], *plain["fields"]["marks"]):
        mark.pop("text")
    assert numbered == plain


def test_tube_cut_step_names_a_numbered_inscription_by_its_template():
    op, _warnings = _build([_tube("Термотрубка", "X1", 25), _tube("Термотрубка", "W1/{№:3}", 20)])
    cut = next(step["text"] for step in op["card"]["steps"] if "арезать" in step["text"])
    assert "X1 — 25 мм, W1/{№} — 20 мм" in cut
