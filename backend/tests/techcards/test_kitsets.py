from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.techcards.kitset_calc import calc_project_number, parse_calc
from app.modules.techcards.kitsets import (
    KitLot,
    KitsetValidationError,
    SupplyRow,
    batch_quantity,
    issue_tasks,
    kit_number,
    project_key,
    sort_kitsets,
    supply_lines,
    write_from_calc,
)
from tests.techcards.xlsx_builder import build_xlsx, calc_workbook


NOW = datetime(2026, 9, 29, 12, 0)


def test_numbers_and_batch_follow_tc_kit_num_and_batch_qty():
    assert kit_number("1,56") == Decimal("1.56")
    assert kit_number(" 2 ") == Decimal("2")
    assert kit_number("") == 0
    assert kit_number("abc") == 0
    # 1,5 × 10 × 1,05 = 15,75; 0,333 × 3 × 1,05 = 1,04895 → 1,05 (вверх до сотых по правилам JS round)
    assert batch_quantity("1,5", 10, 5) == Decimal("15.75")
    assert batch_quantity("0.333", 3, 5) == Decimal("1.05")


def test_calc_number_takes_file_name_only_when_it_refines_the_cell():
    assert calc_project_number("720К.", "720К.1.2 Расчет") == "720К.1.2"
    assert calc_project_number("720К.1", "721К.1.2 Расчет") == "720К.1"
    assert calc_project_number("720К.1.2", "720К.1 Расчет") == "720К.1.2"
    assert project_key("720k.") == "720К.1"


def test_parse_calc_reads_zkz_assemblies_and_spa_composition():
    data = parse_calc(calc_workbook(), "708К.4 Расчет.xlsx")
    assert data.project == "708К.4"
    assert data.customer == "ООО Заказчик"
    assert [(a.index, a.name, a.run) for a in data.assemblies] == [
        ("КБ1", "Жгут питания", Decimal("10")), ("КБ2", "Жгут сигнальный", Decimal("4")),
    ]
    first = data.assemblies[0].components
    assert [(c.article, c.type, c.per_unit, c.scrap_pct) for c in first] == [
        ("UL1007 22AWG", "Провод", Decimal("1.5"), Decimal("5")),
        ("43025-0400", "Разъём", Decimal("2"), Decimal("5")),
    ]
    assert first[0].link == "https://snab.example/ul1007"


def test_parse_calc_refuses_foreign_files():
    with pytest.raises(KitsetValidationError, match="Не похоже"):
        parse_calc(b"not a zip", "x.xlsx")
    with pytest.raises(KitsetValidationError, match="нет листа «ЗКЗ»"):
        parse_calc(build_xlsx({"Лист1": [["a"]]}), "x.xlsx")
    with pytest.raises(KitsetValidationError, match="Вкл. в расчет"):
        parse_calc(build_xlsx({"ЗКЗ": [[], ["", "1К.1"], [], ["Индекс сборки", "Наименование сборки по чертежу", "Вкл. в расчет?"], ["КБ1", "x", 0]]}), "x.xlsx")


def test_refresh_keeps_person_edits_and_lot_binding():
    data = parse_calc(calc_workbook(), "708К.4.xlsx")
    first = write_from_calc(data, None, who="Технолог", now=NOW)
    lot = KitLot(lot_id=uuid4(), warehouse="ЦАР", grn="FRX.26.0000001")
    edited, count = first.with_edits([
        {"index": "КБ1", "art": "UL1007 22AWG", "per": "2", "scrap": "10", "name": "Провод UL1007 красный",
         "lot": {"lot_id": str(lot.lot_id), "warehouse": "ЦАР", "grn": lot.grn}},
    ], who="Технолог", now=NOW)
    assert count == 1
    wire = edited.assembly("КБ1").components[0]
    assert (wire.per_unit, wire.scrap_pct, wire.lot.grn) == (Decimal("2"), Decimal("10"), "FRX.26.0000001")
    assert wire.batch(Decimal("10")) == Decimal("22.00")

    refreshed = write_from_calc(data, edited, who="Технолог", now=NOW)
    wire = refreshed.assembly("КБ1").components[0]
    assert (wire.per_unit, wire.scrap_pct, wire.name, wire.lot.grn) == (
        Decimal("2"), Decimal("10"), "Провод UL1007 красный", "FRX.26.0000001",
    )
    assert refreshed.version == edited.version + 1
    assert refreshed.lines == 3

    cleared, _ = refreshed.with_edits([{"index": "КБ1", "art": "UL1007 22AWG", "per": "2", "scrap": "10", "lot": {}}],
                                      who="Технолог", now=NOW)
    assert cleared.assembly("КБ1").components[0].lot is None


def test_supply_takes_latest_stage_and_marks_stock_and_missing():
    kitset = write_from_calc(parse_calc(calc_workbook(), "708К.4.xlsx"), None, who="Т", now=NOW)
    kitset, _ = kitset.with_edits([{"index": "КБ2", "art": "43030-0007", "per": "8", "scrap": "5",
                                    "lot": {"warehouse": "ЦАР", "grn": "G-1"}}], who="Т", now=NOW)
    lines = {line.article: line for line in supply_lines(kitset, [
        SupplyRow("UL1007 22AWG", "Запрос", "Новая"),
        SupplyRow("ul1007  22awg", "Приёмка", "Готово", received=True),
    ])}
    assert lines["UL1007 22AWG"].stage == "Приёмка · Готово"
    assert lines["UL1007 22AWG"].received is True
    assert lines["UL1007 22AWG"].need == Decimal("15.75")
    assert lines["43025-0400"].stage == "не заведено"
    assert lines["43030-0007"].stage == "со склада"


def test_issue_tasks_one_per_assembly_and_waiting_notes():
    kitset = write_from_calc(parse_calc(calc_workbook(), "708К.4.xlsx"), None, who="Т", now=NOW)
    kitset, _ = kitset.with_edits([{"index": "КБ2", "art": "43030-0007", "per": "8", "scrap": "0",
                                    "lot": {"warehouse": "ЦАР", "grn": "G-1"}}], who="Т", now=NOW)
    tasks = issue_tasks(kitset, {("G-1", "")})
    assert [task.purpose for task in tasks] == ["708К.4 · КБ1 · Жгут питания", "708К.4 · КБ2 · Жгут сигнальный"]
    assert tasks[0].items[0].name == "UL1007 22AWG"
    assert tasks[0].waiting[0] == "UL1007 22AWG: ГРН не выбран (15.75)"
    assert tasks[1].waiting == ()
    assert tasks[1].items[0].need == Decimal("32.00")


def test_sort_kitsets_like_portal():
    rows = [
        {"project": "10К.1", "customer": "Б", "status": "В работе", "updated_at": datetime(2026, 9, 1)},
        {"project": "9К.1", "customer": "А", "status": "Новый", "updated_at": datetime(2026, 9, 3)},
    ]
    statuses = ["Новый", "ТК готова", "КБ набрана", "В работе"]
    assert [r["project"] for r in sort_kitsets(rows, "upd", statuses)] == ["9К.1", "10К.1"]
    assert [r["project"] for r in sort_kitsets(rows, "upd_asc", statuses)] == ["10К.1", "9К.1"]
    assert [r["project"] for r in sort_kitsets(rows, "no", statuses)] == ["9К.1", "10К.1"]
    assert [r["project"] for r in sort_kitsets(rows, "status", statuses)] == ["9К.1", "10К.1"]
    assert [r["project"] for r in sort_kitsets(rows, "customer", statuses)] == ["9К.1", "10К.1"]
