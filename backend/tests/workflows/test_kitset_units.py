from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.modules.fulfillment.domain import FulfillmentKind, FulfillmentLine, FulfillmentStatus
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.references.infrastructure import SqlAlchemyReferencesUnitOfWork
from app.modules.requests.domain import parse_zpk, unit_for_type
from app.modules.techcards.infrastructure import SqlAlchemyTechcardsUnitOfWork
from app.modules.techcards.kitsets import (
    KitAssembly, KitComponent, KitLot, KitsetValidationError, SupplyRow, SupplyStock, issue_tasks, supply_lines,
)
from app.modules.warehouse.domain import StockLot
from app.workflows.project_kitset import generator_bom, issue_from_kitset, material_units
from app.workflows.units_report import read_report, report_lines
from tests.techcards.test_kitset_api import kitset_api  # noqa: F401
from tests.workflows.test_kitset_issue_once import (
    ACTOR, DEADLINE, NOW, factory, kitset, requests,  # noqa: F401
)


def card_with(*components, run="15"):
    return replace(kitset("708К.", 1), assemblies=(KitAssembly(
        index="КБ1", name="Коаксиал", run=Decimal(run), components=components,
    ),))


def component(article, kind, per):
    return KitComponent(article=article, type=kind, per_unit=Decimal(per))


@pytest.mark.parametrize("article,kind,per,run,quantity,unit", [
    ("ТТК 4/1", "ТТК", "0.06", "15", "0.90", "м"),
    ("XS-8", "Др", "1.05", "15", "15.75", "м"),
    ("ТУТ 6/3", "ТУТ", "4", "2.7", "10.80", "м"),
    ("Лента Brady", "Др", "0.06", "15", "0.90", "м"),
    ("Оплётка XS-8", "Др", "1.05", "15", "15.75", "м"),
    ("43025-0400", "Разъём", "0.06", "15", "1", "шт"),
])
def test_consumption_reaches_fulfillment_without_piece_rounding(factory, article, kind, per, run, quantity, unit):  # noqa: F811
    card = card_with(component(article, kind, per), run=run)
    issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)
    (request,) = requests(factory)
    (line,) = request.lines
    assert (line.requirement, line.unit) == (Decimal(quantity), unit)
    assert line.reserved == 0


def test_explicit_source_and_reference_units_win_without_pack_conversion():
    items = parse_zpk([
        ["Тип", "Обозначение", "На проект", "Без НДС", "Ед. изм."],
        ["ТТК", "ТТК 4/1", "2", "10", "рул."],
        ["Др", "XS-8", "15,75", "10", ""],
    ])
    assert [item.unit for item in items] == ["рул", "м"]
    assert items[0].source_unit == "рул."
    assert unit_for_type("Провод", unit="шт", reference_unit="м") == "шт"
    assert unit_for_type("Др", reference_unit="м") == "м"
    (task,) = issue_tasks(card_with(component("REF-1", "Др", "0.06")), set(), reference_units={"ref-1": "м"})
    assert (task.items[0].need, task.items[0].unit) == (Decimal("0.90"), "м")


def test_reference_units_are_unambiguous_and_preserve_rolls():
    def record(unit):
        return SimpleNamespace(tab=TechOperationTab.MATERIAL, fields={"Наименование": "REF-1", "Ед. изм.": unit})
    assert material_units([record("м"), record("m")]) == {"ref-1": "м"}
    assert material_units([record("м"), record("шт")]) == {}
    assert material_units([record("рул")]) == {"ref-1": "рул"}


def test_incompatible_stock_procurement_and_kits_do_not_cover_metres():
    card = card_with(component("XS-8", "Др", "1.05"))
    rows = [SupplyRow("XS-8", "Запрос", "Новая", open_quantity=Decimal("50"), unit="шт")]
    packs = [SupplyStock("XS-8", "", Decimal("100"), unit="упак")]
    (line,) = supply_lines(card, rows, stock=packs, kitted=packs, off_book=packs, absent=packs)
    assert (line.need, line.short, line.unit) == (Decimal("15.75"), Decimal("15.75"), "м")
    metres = [SupplyStock("XS-8", "", Decimal("5"), unit="m")]
    (line,) = supply_lines(card, rows, stock=metres)
    assert (line.free, line.short) == (Decimal("5"), Decimal("10.75"))


def test_duplicate_article_with_incompatible_units_is_split_not_summed():
    card = card_with(component("REF-1", "ТУТ", "1"), component("REF-1", "Разъём", "1"))
    lines = supply_lines(card, [])
    assert sorted(line.unit for line in lines) == ["м", "шт"]
    assert all(line.need == Decimal("15") for line in lines)


@pytest.mark.parametrize("lot_unit,reserved", [("шт", "0"), ("м", "0.90")])
def test_lot_binding_requires_compatible_units(factory, lot_unit, reserved):  # noqa: F811
    lot = StockLot.create(
        warehouse="ЦАР", grn="FRX.26.0000001", package="", name="ТТК 4/1", item_type="ТТК",
        purpose="Склад", unit=lot_unit, quantity=Decimal("10"), storage_location="1F4",
        source_receiving_ids=("receiving-one",), article="ТТК 4/1",
    )
    with factory() as uow:
        uow.warehouse.add(lot)
        uow.commit()
    part = replace(component("ТТК 4/1", "ТТК", "0.06"), lot=KitLot(lot.id, lot.warehouse, lot.grn))
    result = issue_from_kitset(card_with(part), DEADLINE, ACTOR, NOW, factory)
    (request,) = requests(factory)
    assert sum(line.reserved for line in request.lines) == Decimal(reserved)
    assert all(line.unit == "м" for line in request.lines)
    assert bool(result.created[0]["shortfall"]) is (lot_unit == "шт")


def test_report_uses_consumption_and_never_writes(factory):  # noqa: F811
    card = card_with(component("ТТК 4/1", "ТТК", "0.06"))
    issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)
    (request,) = requests(factory)
    old = replace(request, lines=(replace(request.lines[0], requirement=Decimal("1"), unit="шт"),))
    with factory() as uow:
        uow.fulfillment.add(old)
        uow.commit()
    sessions = factory().session_factory
    with SqlAlchemyTechcardsUnitOfWork(sessions) as uow:
        uow.kitsets.save(card)
        uow.commit()
    engine = sessions.kw["bind"]
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        (row,) = read_report(engine, "708К.")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert (Decimal(row["current_quantity"]), row["current_unit"]) == (Decimal("1"), "шт")
    assert (row["correct_quantity"], row["correct_unit"]) == ("0.90", "м")
    assert row["line_id"] == str(old.lines[0].id)
    assert not any(sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) for sql in statements)
    assert requests(factory)[0].lines == old.lines


def test_report_marks_split_and_ambiguous_requirements_and_ignores_cancelled(factory):  # noqa: F811
    card = card_with(component("XS-8", "Др", "1.05"))
    issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)
    (request,) = requests(factory)
    first = replace(request.lines[0], requirement=Decimal("8"), unit="шт")
    other = FulfillmentLine.create(article=first.article, name=first.name, unit="шт", requirement=Decimal("8"))
    split = replace(request, lines=(first, replace(other, requirement_id=first.requirement_id)))
    rows = report_lines([card], [split])
    assert len(rows) == 2
    assert {row["confidence"] for row in rows} == {"split_requirement"}
    assert all(row["correct_quantity"] is None and row["bom_requirement_quantity"] == "15.75" for row in rows)
    ambiguous = replace(request, lines=(first, other))
    assert {row["confidence"] for row in report_lines([card], [ambiguous])} == {"ambiguous"}
    assert report_lines([card], [replace(request, status=FulfillmentStatus.CANCELLED)]) == []


def test_extra_issue_quantity_is_not_compared_with_entire_bom(factory):  # noqa: F811
    card = card_with(component("XS-8", "Др", "1.05"))
    issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)
    (request,) = requests(factory)
    extra = replace(request, kind=FulfillmentKind.EXTRA, purpose=f"{request.purpose} · Довыдача: повреждение",
                    lines=(replace(request.lines[0], requirement=Decimal("1")),))
    assert report_lines([card], [extra]) == []
    wrong_unit = replace(extra, lines=(replace(extra.lines[0], unit="шт"),))
    (row,) = report_lines([card], [wrong_unit])
    assert (row["correct_unit"], row["correct_quantity"], row["confidence"]) == ("м", None, "extra_issue")


def test_supply_api_and_order_missing_keep_metres_and_repeat_is_empty(kitset_api):
    from app.main import app
    from app.shared.http_auth import authenticated_principal
    from tests.techcards.test_kitset_api import _principal

    client, _lot = kitset_api
    card = card_with(component("XS-8", "Др", "1.05"), component("ТТК 4/1", "ТТК", "0.06"))
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(card)
        uow.commit()
    app.dependency_overrides[authenticated_principal] = _principal({"projects": "edit", "newrequest": "edit"})
    response = client.get("/api/techcards/kitsets/708К./supply")
    assert response.status_code == 200
    assert {(line["art"], line["need"], line["unit"]) for line in response.json()["lines"]} == {
        ("XS-8", "15.75", "м"), ("ТТК 4/1", "0.9", "м"),
    }
    response = client.post("/api/techcards/kitsets/708К./order-missing", json={"deadline": "2026-10-20"})
    assert response.status_code == 200
    assert {(row["art"], row["quantity"], row["unit"]) for row in response.json()["created"]} == {
        ("XS-8", "15.75", "м"), ("ТТК 4/1", "0.9", "м"),
    }
    response = client.post("/api/techcards/kitsets/708К./order-missing", json={"deadline": "2026-10-20"})
    assert response.status_code == 200 and response.json()["created"] == []


def test_report_incompatible_bom_units_are_unresolved_without_summing(factory):  # noqa: F811
    card = card_with(component("REF-1", "ТУТ", "1"), component("REF-1", "Разъём", "1"))
    issue_from_kitset(card, DEADLINE, ACTOR, NOW, factory)
    rows = report_lines([card], requests(factory))
    assert len(rows) == 2
    assert all(row["confidence"] == "incompatible_bom_units" for row in rows)
    assert all(row["candidate_units"] == ["м", "шт"] for row in rows)
    assert all(row["correct_quantity"] is None and row["correct_unit"] is None for row in rows)


@pytest.mark.parametrize("article,kind,tab,reference_unit,expected_unit,quantity", [
    ("ТТК 4/1", "ТТК", None, None, "м", "0.9"),
    ("XS-8", "Др", None, None, "м", "0.9"),
    ("Оплётка XS-8", "Др", None, None, "м", "0.9"),
    ("Лента Brady", "Др", None, None, "м", "0.9"),
    ("REF-1", "Др", TechOperationTab.MATERIAL, "m", "м", "0.9"),
    ("REF-1", "Др", TechOperationTab.COVERING, "м", "м", "0.9"),
    ("REF-1", "Др", TechOperationTab.HEAT_SHRINK, "м", "м", "0.9"),
    ("ТТК 4/1", "ТТК", TechOperationTab.MATERIAL, "рул.", "рул", "0.9"),
    ("XS-8", "Др", TechOperationTab.COVERING, "шт", "шт", "1"),
    ("43025-0400", "Разъём", None, None, "шт", "1"),
])
def test_effective_unit_reaches_card_generator_supply_and_issue_api(
    kitset_api, monkeypatch, article, kind, tab, reference_unit, expected_unit, quantity,
):
    from app.main import app

    client, _lot = kitset_api
    sessions = app.state.techcards_uow_factory().session_factory
    reference_factory = lambda: SqlAlchemyReferencesUnitOfWork(sessions)
    monkeypatch.setattr(app.state, "references_uow_factory", reference_factory, raising=False)
    if reference_unit is not None:
        with reference_factory() as uow:
            uow.tech_operations.save(TechOperationRecord(
                id=uuid4(), tab=tab, display_text=article, normalized_search=article.casefold(),
                source_sheet="test", sort_key=article,
                fields={"Артикул": article, "Наименование": article, "Ед. изм.": reference_unit},
            ))
            uow.commit()
    card = card_with(component(article, kind, "0.06"))
    card = replace(card, assemblies=(replace(card.assemblies[0], calc_ops=()),))
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(card)
        uow.commit()

    response = client.get("/api/techcards/kitsets/708К.")
    assert response.status_code == 200, response.text
    (row,) = response.json()["assemblies"][0]["components"]
    assert (row["unit"], row["batch"]) == (expected_unit, quantity)
    response = client.get("/api/techcards/kitsets/708К./bom/КБ1")
    assert response.status_code == 200, response.text
    (row,) = response.json()["components"]
    assert (row["unit"], row["qty"]) == (expected_unit, "0.06")
    response = client.get("/api/techcards/kitsets/708К./supply")
    assert response.status_code == 200, response.text
    (row,) = response.json()["lines"]
    assert (row["unit"], row["need"]) == (expected_unit, quantity)

    response = client.post("/api/techcards/kitsets/708К./issue", json={"deadline": "2026-10-20"})
    assert response.status_code == 200, response.text
    with app.state.fulfillment_uow_factory() as uow:
        (request,) = uow.fulfillment.for_project(card.project)
    assert {(line.unit, line.requirement) for line in request.lines} == {(expected_unit, Decimal(quantity))}
    assert client.post(
        "/api/techcards/kitsets/708К./issue", json={"deadline": "2026-10-20"},
    ).status_code == 409


def test_generator_bom_carries_units_without_api_or_references():
    card = card_with(component("XS-8", "Др", "1.05"), component("ТТК 4/1", "ТТК", "0.06"))
    rows = generator_bom(card, card.assemblies[0], [])["components"]
    assert [(row["unit"], row["qty"]) for row in rows] == [("м", "1.05"), ("м", "0.06")]
