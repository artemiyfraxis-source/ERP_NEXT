from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.techcards.kitset_calc import calc_project_number, parse_calc_sheets
from app.modules.techcards.kitsets import (
    KitComponent,
    KitLot,
    KitsetValidationError,
    SupplyRow,
    SupplyStock,
    batch_quantity,
    default_scrap,
    issue_tasks,
    kit_number,
    manual_kitset,
    project_key,
    sort_kitsets,
    supply_lines,
    write_from_calc,
)
from app.workflows.project_kitset import generator_bom
from tests.techcards.calc_sheets import calc_sheets, grid
from app.modules.techcards import kitsets


@pytest.fixture(autouse=True)
def scrap_allowance_on(monkeypatch):
    """These rules are written with the «% брака» allowance on; it is switched off for now («выдаём вровень», owner
    10.10.2026) and the formula stays behind `SCRAP_APPLIED` — `test_kit_need_has_no_scrap_allowance_for_now` checks
    the switched-off state."""
    monkeypatch.setattr(kitsets, "SCRAP_APPLIED", True)


def test_kit_need_has_no_scrap_allowance_for_now(monkeypatch):
    monkeypatch.setattr(kitsets, "SCRAP_APPLIED", False)
    wire = KitComponent(article="W", type="Провод", per_unit=Decimal("1.5"), scrap_pct=Decimal("5"))
    piece = KitComponent(article="P", type="Разъём", per_unit=Decimal("0.333"), scrap_pct=Decimal("5"))
    assert wire.scrap_pct == Decimal("5")  # the typed % is kept
    assert wire.batch(Decimal("10")) == Decimal("15.00")  # 1,5 × 10, nothing on top
    assert piece.batch(Decimal("3")) == Decimal("1")  # 0,999 → 1 шт
    assert batch_quantity(Decimal("1.5"), Decimal("10"), Decimal("5"), "м") == Decimal("15.75")  # the formula itself


def parse(sheets, title="708К.4 Расчет"):
    return parse_calc_sheets(sheets, title, "https://docs.google.com/spreadsheets/d/calc/edit")


NOW = datetime(2026, 9, 29, 12, 0)


def test_numbers_and_batch_follow_tc_kit_num_and_batch_qty():
    assert kit_number("1,56") == Decimal("1.56")
    assert kit_number(" 2 ") == Decimal("2")
    assert kit_number("") == 0
    assert kit_number("abc") == 0
    # Метры: 1,5 × 10 × 1,05 = 15,75; 0,333 × 3 × 1,05 = 1,04895 → 1,05 (вверх до сотых по правилам JS round)
    assert batch_quantity("1,5", 10, 5, "м") == Decimal("15.75")
    assert batch_quantity("0.333", 3, 5, "м") == Decimal("1.05")
    assert batch_quantity("1,5", 3, 5, "м") == Decimal("4.73")


def test_piece_batch_rounds_up_to_whole_pieces_after_scrap():
    """Василий, 04.10: «округлять шт, половинки быть не может» — после брака вверх до целого."""
    assert batch_quantity("1,5", 3, 5, "шт") == Decimal("5")  # 4,725
    assert batch_quantity("0.333", 3, 5, "шт") == Decimal("2")  # 1,05
    assert batch_quantity("1,5", 10, 5, "шт") == Decimal("16")  # 15,75
    # Целое остаётся целым: сначала до сотых, потом вверх, — 2 × 10 × 1,05 = 21, а 0,1 × 30 не станет 4.
    assert batch_quantity("2", 10, 5, "шт") == Decimal("21")
    assert batch_quantity("0.1", 30, 0, "шт") == Decimal("3")
    assert batch_quantity("", 10, 5, "шт") == Decimal("0")
    # Единица — по типу строки, как в импорте из расчёта: провод в метрах, разъём штучный.
    assert KitComponent(article="W", type="Провод", per_unit=Decimal("1.5"), scrap_pct=Decimal("5")).batch(
        Decimal("3")) == Decimal("4.73")
    assert KitComponent(article="X", type="Разъём", per_unit=Decimal("1.5"), scrap_pct=Decimal("5")).batch(
        Decimal("3")) == Decimal("5")


def test_scrap_default_is_zero_for_pieces_and_five_for_wire():
    assert [default_scrap(kind) for kind in ("Разъём", "Контакт", "", "Провод", "Кабель", "ТУТ", "Шлейф")] == [
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("5"), Decimal("5"), Decimal("5"), Decimal("5"),
    ]
    assert KitComponent(article="X", type="Разъём").scrap_pct == Decimal("0")
    assert KitComponent(article="W", type="Провод").scrap_pct == Decimal("5")
    assert KitComponent(article="W", type="Провод", scrap_pct=Decimal("0")).scrap_pct == Decimal("0")


def test_calc_number_takes_file_name_only_when_it_refines_the_cell():
    assert calc_project_number("720К.", "720К.1.2 Расчет") == "720К.1.2"
    assert calc_project_number("720К.1", "721К.1.2 Расчет") == "720К.1"
    assert calc_project_number("720К.1.2", "720К.1 Расчет") == "720К.1.2"
    assert project_key("720k.") == "720К.1"


def test_parse_calc_reads_zkz_assemblies_and_spa_composition():
    data = parse(calc_sheets())
    assert data.project == "708К.4"
    assert data.customer == "ООО Заказчик"
    assert [(a.index, a.name, a.run) for a in data.assemblies] == [
        ("КБ1", "Жгут питания", Decimal("10")), ("КБ2", "Жгут сигнальный", Decimal("4")),
    ]
    first = data.assemblies[0].components
    assert [(c.article, c.type, c.per_unit, c.scrap_pct) for c in first] == [
        ("UL1007 22AWG", "Провод", Decimal("1.5"), Decimal("5")),
        ("43025-0400", "Разъём", Decimal("2"), Decimal("0")),
    ]
    assert first[0].link == "https://snab.example/ul1007"


def test_parse_calc_refuses_foreign_files():
    with pytest.raises(KitsetValidationError, match="нет листа «ЗКЗ»"):
        parse({"Лист1": grid([["a"]])}, "x")
    with pytest.raises(KitsetValidationError, match="Вкл. в расчет"):
        parse({"ЗКЗ": grid([[], ["", "1К.1"], [], ["Индекс сборки", "Наименование сборки по чертежу", "Вкл. в расчет?"], ["КБ1", "x", 0]])}, "x")


def test_refresh_keeps_person_edits_and_lot_binding():
    data = parse(calc_sheets(), "708К.4")
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



def test_technologist_row_stays_at_the_end_until_the_calculation_lists_it():
    data = parse(calc_sheets(), "708К.4")
    first = write_from_calc(data, None, who="Технолог", now=NOW)
    tube = KitComponent(article="ТТК 4,8/1,6мм", type="ТТК", per_unit=Decimal("0.04"))
    added = first.with_component("кб1", tube, who="Технолог", now=NOW)
    assert added.version == first.version + 1
    assert [(item.article, item.manual) for item in added.assembly("КБ1").components][-1] == ("ТТК 4,8/1,6мм", True)

    refreshed = write_from_calc(data, added, who="Технолог", now=NOW)
    assert refreshed.assembly("КБ1").components[-1].article == "ТТК 4,8/1,6мм"
    assert refreshed.assembly("КБ1").components[-1].manual

    kb1 = data.assemblies[0]
    calc_tube = KitComponent(article="ТТК 4,8/1,6мм", type="ТТК")
    listed = replace(data, assemblies=(replace(kb1, components=(*kb1.components, calc_tube)), *data.assemblies[1:]))
    taken = write_from_calc(listed, refreshed, who="Технолог", now=NOW).assembly("КБ1").components
    assert [(item.article, item.manual, item.per_unit) for item in taken if item.article == "ТТК 4,8/1,6мм"] == [
        ("ТТК 4,8/1,6мм", False, Decimal("0.04")),
    ]

    with pytest.raises(KitsetValidationError, match="пришёл из расчёта"):
        refreshed.without_component("КБ1", "UL1007 22AWG", who="Технолог", now=NOW)
    gone = refreshed.without_component("КБ1", "ТТК 4,8/1,6мм", who="Технолог", now=NOW)
    assert gone.assembly("КБ1").components == first.assembly("КБ1").components

def test_refresh_keeps_hand_set_piece_scrap_and_defaults_only_new_lines():
    data = parse(calc_sheets(), "708К.4")
    first = write_from_calc(data, None, who="Технолог", now=NOW)
    edited, _ = first.with_edits([{"index": "КБ1", "art": "43025-0400", "per": "2", "scrap": "3"}],
                                 who="Технолог", now=NOW)
    refreshed = write_from_calc(data, edited, who="Технолог", now=NOW)
    assert refreshed.assembly("КБ1").components[1].scrap_pct == Decimal("3")
    assert refreshed.assembly("КБ2").components[0].scrap_pct == Decimal("0")  # «Контакт», не правили


def test_manual_card_defaults_scrap_by_type_and_keeps_typed_value():
    kitset = manual_kitset(project="708К.4", customer="", who="Т", now=NOW, assemblies=[{
        "index": "кб1", "run": "3", "components": [
            {"art": "W", "type": "Провод", "per": "1,5"},
            {"art": "X", "type": "Разъём", "per": "1,5", "scrap": ""},
            {"art": "Y", "type": "Разъём", "per": "1,5", "scrap": "5"},
        ],
    }])
    components = kitset.assembly("КБ1").components
    assert [(c.article, c.scrap_pct, c.batch(Decimal("3"))) for c in components] == [
        ("W", Decimal("5"), Decimal("4.73")), ("X", Decimal("0"), Decimal("5")), ("Y", Decimal("5"), Decimal("5")),
    ]


def test_supply_takes_latest_stage_and_marks_stock_and_missing():
    kitset = write_from_calc(parse(calc_sheets(), "708К.4"), None, who="Т", now=NOW)
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


def test_supply_line_without_purchase_is_from_stock_when_kitted_even_without_grn():
    kitset = _replaced_wire_kitset()
    kitted = [SupplyStock("GHR-04V-S", "Колодка 4pin 1.25мм", Decimal("20"))]
    lines = {line.article: line for line in supply_lines(kitset, [], kitted=kitted)}
    assert lines["GHR-04V-S"].stage == "со склада"
    assert lines["Силиконовый 28AWG черный"].stage == "не заведено"
    zero = {line.article: line for line in supply_lines(
        kitset, [], kitted=[SupplyStock("GHR-04V-S", "Колодка 4pin 1.25мм", Decimal("0"))])}
    assert zero["GHR-04V-S"].stage == "не заведено"
    # A purchase row still wins over the kit.
    bought = {line.article: line for line in supply_lines(
        kitset, [SupplyRow("GHR-04V-S", "Закупка", "В работе")], kitted=kitted)}
    assert bought["GHR-04V-S"].stage == "Закупка · В работе"


def _replaced_wire_kitset():
    return manual_kitset(project="720К.1", customer="З", who="Т", now=NOW, assemblies=[{
        "index": "КБ1", "name": "Жгут", "run": "10", "components": [
            {"art": "Силиконовый 28AWG черный", "type": "Провод", "name": "Силиконовый 28AWG черный", "per": "0,5"},
            {"art": "GHR-04V-S", "type": "Разъём", "name": "Колодка 4pin 1.25мм", "per": "2"},
        ],
    }])


def _replace_wire(kitset):
    changed, _ = kitset.with_edits([{"index": "КБ1", "art": "Силиконовый 28AWG черный", "per": "0,5", "scrap": "5",
                                     "name": "Силиконовый 30AWG черный"}], who="Т", now=NOW)
    return changed


def test_generator_bom_takes_the_wire_replaced_in_the_calc_and_keeps_the_kd_article():
    kitset = _replaced_wire_kitset()
    before = generator_bom(kitset, kitset.assembly("КБ1"), [])["components"]
    assert [(c["name"], c["art"], c["kd"]) for c in before] == [
        ("Силиконовый 28AWG черный", "Силиконовый 28AWG черный", ""),
        ("Колодка 4pin 1.25мм", "GHR-04V-S", ""),
    ]
    # The wire changed in «Наименование»: the saved card gives the generator the new one, nothing is rebuilt.
    kitset = _replace_wire(kitset)
    after = generator_bom(kitset, kitset.assembly("КБ1"), [])["components"]
    assert [(c["id"], c["name"], c["art"], c["kd"]) for c in after] == [
        ("kit-0", "Силиконовый 30AWG черный", "Силиконовый 30AWG черный", "Силиконовый 28AWG черный"),
        ("kit-1", "Колодка 4pin 1.25мм", "GHR-04V-S", ""),
    ]


def test_supply_counts_purchases_of_the_wire_replaced_in_the_calc():
    kitset = _replace_wire(_replaced_wire_kitset())
    assert {line.article: line.stage for line in supply_lines(kitset, [])}["Силиконовый 28AWG черный"] == "не заведено"
    lines = {line.article: line for line in supply_lines(kitset, [
        SupplyRow("Силиконовый 30AWG черный", "Закупка", "В работе"),
        SupplyRow("Силиконовый 28AWG черный", "Запрос", "Новая"),
        SupplyRow("Колодка 4pin 1.25мм", "Закупка", "В работе"),  # a description is not the connector's article
    ])}
    wire = lines["Силиконовый 28AWG черный"]
    assert (wire.stage, wire.name, len(wire.rows)) == ("Закупка · В работе", "Силиконовый 30AWG черный", 2)
    assert lines["GHR-04V-S"].stage == "не заведено"


def test_issue_tasks_one_per_assembly_and_waiting_notes():
    kitset = write_from_calc(parse(calc_sheets(), "708К.4"), None, who="Т", now=NOW)
    kitset, _ = kitset.with_edits([{"index": "КБ2", "art": "43030-0007", "per": "8", "scrap": "0",
                                    "lot": {"warehouse": "ЦАР", "grn": "G-1"}}], who="Т", now=NOW)
    tasks = issue_tasks(kitset, {("G-1", "")})
    assert [task.purpose for task in tasks] == ["708К.4 · КБ1 · Жгут питания", "708К.4 · КБ2 · Жгут сигнальный"]
    assert tasks[0].items[0].name == "UL1007 22AWG"
    assert tasks[0].waiting[0] == "UL1007 22AWG: ГРН не выбран (15.75)"
    assert tasks[1].waiting == ()
    assert tasks[1].items[0].need == Decimal("32.00")


def test_issue_and_supply_take_piece_batch_in_whole_pieces():
    kitset = write_from_calc(parse(calc_sheets(), "708К.4"), None, who="Т", now=NOW)
    kitset, _ = kitset.with_edits([{"index": "КБ1", "art": "43025-0400", "per": "1,5", "scrap": "5"}],
                                  who="Т", now=NOW)
    first = issue_tasks(kitset, set())[0]
    assert [(item.article, item.need) for item in first.items] == [
        ("UL1007 22AWG", Decimal("15.75")), ("43025-0400", Decimal("16")),
    ]
    assert first.waiting[1] == "Корпус Molex: ГРН не выбран (16)"
    need = {line.article: line.need for line in supply_lines(kitset, [])}
    assert (need["UL1007 22AWG"], need["43025-0400"]) == (Decimal("15.75"), Decimal("16"))


def test_supply_matches_a_row_without_article_by_its_name():
    """A manual request typed without АРТИКУЛ КД used to stay «не заведено»: its name finds the position."""
    kitset = write_from_calc(parse(calc_sheets(), "708К.4"), None, who="Т", now=NOW)
    lines = {line.article: line for line in supply_lines(kitset, [
        SupplyRow("", "Запрос", "Новый", name="  корпус   MOLEX "),  # the spec name, spacing and case aside
        SupplyRow("", "Закупка", "В работе", name="43030-0007"),  # the КД article typed as the name
        SupplyRow("", "Запрос", "Новый", name="Что-то другое"),
    ])}
    assert lines["43025-0400"].stage == "Запрос · Новый"
    assert lines["43030-0007"].stage == "Закупка · В работе"
    assert lines["UL1007 22AWG"].stage == "не заведено"


def test_supply_short_is_need_less_free_stock_open_purchases_and_kits():
    kitset = write_from_calc(parse(calc_sheets(), "708К.4"), None, who="Т", now=NOW)
    lines = {line.article: line for line in supply_lines(kitset, [
        SupplyRow("43030-0007", "Запрос", "Новый", open_quantity=Decimal("10")),
        SupplyRow("43030-0007", "Приёмка", "Готово", received=True),  # accepted: counted on the shelf, not here
        SupplyRow("UL1007 22AWG", "Закупка", "В работе", open_quantity=Decimal("20")),
    ], stock=[
        SupplyStock("43025-0400", "Корпус Molex", Decimal("15")),
        SupplyStock("43030-0007", "Контакт", Decimal("4")),
        SupplyStock("", "43030-0007", Decimal("2")),  # a lot accepted without an article, named by it
    ], kitted=[SupplyStock("43030-0007", "Контакт", Decimal("6"))])}
    molex, contact, wire = lines["43025-0400"], lines["43030-0007"], lines["UL1007 22AWG"]
    assert (molex.need, molex.free, molex.ordered, molex.short) == (Decimal("20"), Decimal("15"), 0, Decimal("5"))
    assert (contact.need, contact.free, contact.ordered, contact.kitted, contact.short) == (
        Decimal("32"), Decimal("6"), Decimal("10"), Decimal("6"), Decimal("10"),
    )
    assert (wire.need, wire.ordered, wire.short) == (Decimal("15.75"), Decimal("20"), Decimal("0"))


def test_supply_counts_stock_without_grn_once_less_what_kits_issued_off_book():
    """«Есть на складе, без ГРН» covers the need like a lot does; a kit line issued «есть, не заведено» takes those
    same unbooked goods, so it uses the «без ГРН» up instead of being counted next to it."""
    kitset = write_from_calc(parse(calc_sheets(), "708К.4"), None, who="Т", now=NOW)
    rows = [SupplyRow("43030-0007", "Закупка", "В работе", open_quantity=Decimal("18"))]
    lotless = [SupplyStock("43030-0007", "Контакт", Decimal("12")), SupplyStock("", "Корпус Molex", Decimal("5"))]

    lines = {line.article: line for line in supply_lines(kitset, rows, off_book=lotless)}

    contact, molex = lines["43030-0007"], lines["43025-0400"]
    assert (contact.need, contact.free, contact.off_book, contact.ordered, contact.short) == (
        Decimal("32"), 0, Decimal("12"), Decimal("18"), Decimal("2"),
    )
    assert (molex.off_book, molex.short) == (Decimal("5"), Decimal("15"))  # matched by the spec name

    issued = [
        SupplyStock("43030-0007", "Контакт", Decimal("8"), off_book=True),  # «есть, не заведено», issued
        SupplyStock("43030-0007", "Контакт", Decimal("3")),  # reserved on a lot
    ]
    kitted = {line.article: line for line in supply_lines(kitset, rows, kitted=issued, off_book=lotless)}
    contact = kitted["43030-0007"]
    assert (contact.kitted, contact.off_book, contact.short) == (Decimal("11"), Decimal("4"), Decimal("0"))
    # need 32: 18 bought + 11 kitted + 4 still unbooked on the shelf = 33 — the 8 issued off-book count once

    more = [SupplyStock("43030-0007", "Контакт", Decimal("20"), off_book=True)]
    over = {line.article: line for line in supply_lines(kitset, rows, kitted=more, off_book=lotless)}["43030-0007"]
    assert (over.kitted, over.off_book) == (Decimal("20"), Decimal("0"))  # never below zero


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


# ── «Стадия» on «Снабжение»: one word + tone (owner, 12.10.2026) ──


def _position(**values) -> kitsets.SupplyLine:
    base = {"article": "LP-12-C04PE-02-001", "name": "", "kbs": ("КБ1",), "need": Decimal("20"), "grn": "", "stage": "",
            "received": False}
    return kitsets.SupplyLine(**{**base, **values})


def _row(where: str, status: str, source: str, **values) -> SupplyRow:
    return SupplyRow("LP-12-C04PE-02-001", where, status, source, **values)


@pytest.mark.parametrize(("line", "key", "label", "tone"), [
    (_position(free=Decimal("25")), "stock", "Со склада", "info"),
    (_position(free=Decimal("5")), "short", "Не хватает 15", "danger"),
    (_position(free=Decimal("5"), ordered=Decimal("2.5")), "short", "Не хватает 12,5", "danger"),
    (_position(kitted=Decimal("20")), "kitting", "В комплектации", "ok"),
    (_position(kitted=Decimal("20"), issued=Decimal("12")), "kitting", "В комплектации", "ok"),
    # Everything the project needs is already issued from the warehouse (owner 17.10.2026): not «в комплектации».
    (_position(kitted=Decimal("20"), issued=Decimal("20")), "issued", "Выдано в производство", "ok"),
    (_position(ordered=Decimal("20"), rows=(_row("Закупка", "В работе", "ЗК-1", open_quantity=Decimal("20")),)),
     "procurement", "В закупке", "warn"),
    (_position(free=Decimal("20"), rows=(_row("Приёмка", "Готово", "ЗК-1", received=True),)),
     "received", "Принято на склад", "ok"),
    # A request entered without «В закупку»: no procurement line in ERP — bought outside the system.
    (_position(free=Decimal("20"), rows=(_row("Запрос", "Закрыто", "З-7"),)), "outside", "Куплено вне системы", "neutral"),
    (_position(ordered=Decimal("20"), rows=(_row("Запрос", "Новый", "З-8", open_quantity=Decimal("20")),)),
     "outside", "Куплено вне системы", "neutral"),
    # Procurement closed it from the shelf («Готово · без ГРН»): stock, no purchase.
    (_position(off_book=Decimal("20"), rows=(_row("Закупка", "Готово · без ГРН", "ЗК-2"),)), "stock", "Со склада", "info"),
    (_position(need=Decimal("0")), "none", "Нет потребности", "neutral"),
])
def test_supply_state(line, key, label, tone):
    state = kitsets.supply_state(line)
    assert (state.key, state.label, state.tone) == (key, label, tone)
    assert "Нужно" in state.hint


def test_supply_state_hint_carries_the_numbers_and_the_rows():
    short = kitsets.supply_state(_position(free=Decimal("5"), ordered=Decimal("2.5"), kitted=Decimal("1")))
    assert short.hint == "Нужно 20 · свободно 5 · без ГРН 0 · в закупке 2,5 · в комплектации 1 → не хватает 11,5"
    buying = kitsets.supply_state(_position(
        ordered=Decimal("20"), rows=(_row("Закупка", "В пути", "ЗК-1", open_quantity=Decimal("20")),),
    ))
    assert buying.hint.startswith("В закупке 20: ЗК-1 · Закупка · В пути")
    assert kitsets.supply_state(_position(free=Decimal("25"))).hint.startswith("Свободно на складе 25 из нужных 20")


def test_supply_lines_carry_the_first_link_of_the_position():
    kitset = manual_kitset(project="720К.1", customer="З", who="Т", now=NOW, assemblies=[
        {"index": "КБ1", "run": "1", "components": [{"art": "XHP-4", "per": "1"}]},
        {"index": "КБ2", "run": "1", "components": [{"art": "xhp-4", "per": "1", "link": "https://shop.example/xhp"}]},
    ])
    (line,) = supply_lines(kitset, [])
    assert line.link == "https://shop.example/xhp"


def test_supply_lines_count_the_issued_part_of_the_kits_apart_from_the_reserve():
    """A kit line holding 9, of it issued 5 (the rest reserved) — the position keeps both numbers (owner 17.10.2026)."""
    kitset = _replaced_wire_kitset()
    line = {item.article: item for item in supply_lines(kitset, [], kitted=[
        SupplyStock("GHR-04V-S", "Колодка 4pin 1.25мм", Decimal("9"), issued=Decimal("5"))])}["GHR-04V-S"]
    assert (line.kitted, line.issued) == (Decimal("9"), Decimal("5"))


def test_generator_bom_title_reads_like_the_kd_not_the_supplier_page():
    """Owner 22.10: «Данные из листа» showed supplier-page titles; `title` is the reference name, `name` stays as is."""
    from app.modules.references.load_mirror import BUNDLED_MIRROR, read_mirror
    from app.workflows.item_names import REF, ReferenceNames, StockNames

    snapshot = read_mirror(BUNDLED_MIRROR)
    book = ReferenceNames(record for records in snapshot.tables.values() for record in records)
    site = "Автомобильный провод ПВАМ (медный). Продажа от 5 м!"
    kitset = manual_kitset(project="720К.2", customer="З", who="Т", now=NOW, assemblies=[{
        "index": "КБ1", "name": "Жгут", "run": "10", "components": [
            {"art": "ПВАМ 0,35 красный", "type": "Провод", "name": site, "per": "0,5"},
            {"art": "ТУТ 3,0/1,5мм, чёрная", "type": "Трубка", "name": "Трубка термоусадочная ТУТ нг 3,0/1,5 черная, Rexant купить оптом", "per": "1"},
            {"art": "ТТК 6/2мм, чёрная", "type": "Трубка", "name": 'Трубка 85205 ТТК "Flex" 6/2 (КВТ)', "per": "1"},
            {"art": "SXH-001T-P0.6", "type": "Терминал", "name": "", "per": "2"},
            {"art": "XHP-4", "type": "Разъём", "name": "", "per": "1"},
            {"art": "ZZ-9", "type": "Разъём", "name": "", "per": "1"},
        ],
    }])
    rows = generator_bom(kitset, kitset.assembly("КБ1"), [], (book, StockNames()))["components"]
    assert [c["title"] for c in rows] == [
        "Провод ПВАМ 0,35 мм² красный",
        "Трубка термоусадочная ТУТ 3/1,5 мм, чёрная (Rexant)",
        "Трубка термоусадочная клеевая ТТК 6/2 мм, чёрная (КВТ Калуга)",
        "Контакт обжимной JST XH гнездо",
        "Корпус JST XH 4 конт.",
        "ZZ-9",  # the references do not know it: today's value
    ]
    assert rows[0]["title_source"] == REF and "справочника" in rows[0]["title_hint"]
    assert rows[0]["name"] == site  # the generator keeps recognising parts by `name`
    # Without resolvers the answer is what it was.
    assert "title" not in generator_bom(kitset, kitset.assembly("КБ1"), [])["components"][0]
