"""Лист «ЗАГРУЗ» в производственном плане: колонки, ручные отметки, группы, календарь."""

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.modules.production.domain import (
    OperationShare,
    ProductionOperation,
    ProductionStatus,
    ProductionTask,
    ProductionValidationError,
)
from app.modules.production.plan import (
    PLAN_KINDS,
    KitStage,
    PlanEvent,
    ProjectStage,
    SupplySummary,
    normalize_shop_statuses,
    plan_mark_value,
    summarize_plan_marks,
    validate_plan_note,
)
from app.modules.production.plan_sheet import (
    calendar_span,
    ready_percent,
    sheet_calendar,
    sheet_cells,
    sheet_group,
    shop_status_auto,
)


def _operation(norm=60, start=None, finish=None, shares=()):
    return ProductionOperation(
        id=uuid4(), position=1, code="OP-1", stage="Резка", description="",
        seconds_per_unit=Decimal(1), setup_seconds=Decimal(0), norm_seconds=norm,
        planned_start=start, planned_finish=finish, shares=shares,
    )


def _task(operations, quantity=10, status=ProductionStatus.RUNNING, project="708К.4"):
    return ProductionTask(
        id=uuid4(), number="ПЗ-1", source_idempotency_key="k", source_fingerprint="f", version=1,
        created_at=datetime(2026, 10, 1), project=project, item="КБ1", name="Жгут", quantity=quantity,
        fulfillment_request_id=None, deadline=None, status=status, launched_by="Мастер",
        operations=tuple(operations),
    )


def _mark(field, value="", text="", project="708К.4"):
    return PlanEvent(project=project, occurred_at=datetime(2026, 10, 4), who="Мастер",
                     kind=PLAN_KINDS["mark"], field=field, value=value, text=text)


def test_old_status_typos_still_normalize_for_the_drift_report():
    assert normalize_shop_statuses("к запус, ОЖД ИНФ.,ОЖИД КОМП") == "К ЗАПУСК, ОЖИД ИНФ, ОЖИД КОМПЛ"
    assert normalize_shop_statuses("В РАБОТЕ, в работе") == "В РАБОТЕ"


@pytest.mark.parametrize("value", ["холд, в работе", "авто", ""])
def test_new_status_mark_is_refused_even_a_clearing_one(value):
    with pytest.raises(ProductionValidationError, match="берётся из этапа проекта — меняйте этап"):
        plan_mark_value("status", value)
    with pytest.raises(ProductionValidationError, match="меняйте этап"):
        validate_plan_note({"project": "708К.4", "kind": "mark", "field": "status", "value": value}, ["708К.4"])


@pytest.mark.parametrize(("field", "value", "expected"), [
    ("ready", "057", "57"),
    ("in_plan", "да", "да"),
    ("customer_supplied", "ч/т", "Ч/Т"),
    ("kit_arrival", "Склад", "Склад"),
    ("kit_arrival", "2026-10-09", "2026-10-09"),
    ("kit_arrival", "09.10.2026", "2026-10-09"),
    ("kit_arrival", "авто", ""),
    ("day:2026-10-06", "П", "П"),
])
def test_plan_mark_values_are_normalized(field, value, expected):
    assert plan_mark_value(field, value) == expected


@pytest.mark.parametrize(("field", "value"), [
    ("ready", "101"), ("ready", "много"), ("in_plan", "может"), ("customer_supplied", "чуть"),
    ("kit_arrival", "скоро"), ("unknown", "да"), ("day:2026-13-40", "П"), ("day:2026-10-06", "x" * 17),
])
def test_bad_plan_marks_are_refused(field, value):
    with pytest.raises(ProductionValidationError):
        validate_plan_note({"project": "708К.4", "kind": "mark", "field": field, "value": value}, ["708К.4"])


def test_latest_mark_wins_and_auto_or_empty_clears():
    marks = summarize_plan_marks([
        _mark("ready", "40"), _mark("ready", "70"), _mark("in_plan", "да"), _mark("in_plan", "авто"),
        _mark("comment", text="ждём кабель"), _mark("comment", text="ждём кабель 2"),
        _mark("test_sb", "да", project="591К.3"),
        _mark("status", "ХОЛД"),  # an old status mark stays in the journal but is ignored on reading
    ])
    assert marks == {"708К.4": {"ready": "70", "comment": "ждём кабель 2"}, "591К.3": {"test_sb": "да"}}
    assert summarize_plan_marks([_mark("comment", text="x"), _mark("comment", text="")]) == {"708К.4": {}}


def test_status_is_blocker_plus_erp_stage_name():
    assert shop_status_auto("КБ набрана") == "КБ набрана"
    assert shop_status_auto("В работе", "ОЖИД КОМПЛ") == "ОЖИД КОМПЛ, В работе"
    assert shop_status_auto("Готово к отгрузке") == "Готово к отгрузке"
    assert shop_status_auto("Отгружено") == "Отгружено"
    assert shop_status_auto("ТК готова") == "ТК готова"


def test_groups_come_from_stage_and_blocker():
    assert sheet_group("В работе", "ХОЛД") == "hold"
    assert sheet_group("Готово к отгрузке", "ПАУЗА") == "hold"
    assert sheet_group("Отказ", "СТОП", terminal=True) == "hold"
    assert sheet_group("В работе", "ОЖИД ИНФ") == "active"
    # Работа цеха кончена — ГП принята и дальше; ОТК ещё у цеха.
    assert sheet_group("ОТК") == "active"
    assert sheet_group("Готово к отгрузке") == "done"
    assert sheet_group("На отгрузке") == "done"
    assert sheet_group("Отгружено") == "done"
    assert sheet_group("Отгружено", "ОЖИД КОМПЛ") == "active"
    assert sheet_group("В работе") == "active"
    assert sheet_group("Отказ", terminal=True) == "closed"


def test_ready_percent_is_output_weighted_by_norm():
    heavy, light = _operation(norm=300), _operation(norm=100)
    task = _task([heavy, light], quantity=10)
    assert ready_percent("В работе", [task], {heavy.id: Decimal(5), light.id: Decimal(20)}) == "63"
    assert ready_percent("В работе", [task], {}) == "0"
    # Производство закончено (ОТК и дальше) — 100.
    assert ready_percent("ОТК", [task], {}) == "100"
    assert ready_percent("Готово к отгрузке", [task], {}) == "100"
    assert ready_percent("В работе", [_task([heavy], status=ProductionStatus.CANCELLED)], {}) == ""
    assert ready_percent("Новый", [], {}) == ""


def _stage(*kits, status="В работе"):
    return ProjectStage(status, tuple(kits))


def test_sheet_cells_compute_sheet_columns_from_erp_facts():
    stage = _stage(
        KitStage("КБ1", "В работе", tc=True, kit=True), KitStage("КБ2", "ТК готова", tc=True),
    )
    supply = SupplySummary(total=4, stock=2, transit=2, customer_supplied=1, eta="09.10.2026")
    cells = sheet_cells(stage=stage, blocker="ОЖИД КОМПЛ", supply=supply, assigned=2, ready="40", marks={})
    values = {name: cell.value for name, cell in cells.items()}
    assert values == {
        "comment": "", "status": "ОЖИД КОМПЛ, В работе", "ready": "40", "in_plan": "да",
        "customer_supplied": "Ч/Т", "kit_checked": "нет", "kit_present": "нет", "zkp": "да",
        "kit_arrival": "2026-10-09", "tc_ready": "да", "test_sb": "нет", "kit_picked": "нет",
    }
    assert not any(cell.manual for cell in cells.values())


def test_sheet_cells_edge_cases_without_supply_and_all_in_stock():
    bare = sheet_cells(stage=_stage(status="Новый"), blocker="", supply=None, assigned=0, ready="", marks={})
    assert (bare["customer_supplied"].value, bare["kit_arrival"].value, bare["zkp"].value) == ("НЕТ", "", "нет")
    assert (bare["tc_ready"].value, bare["kit_picked"].value, bare["in_plan"].value) == ("нет", "нет", "нет")
    stocked = sheet_cells(
        stage=_stage(KitStage("КБ1", "Готово к отгрузке", tc=True)), blocker="",
        supply=SupplySummary(total=3, stock=3, customer_supplied=3, customer_supplied_stock=3),
        assigned=0, ready="100", marks={},
    )
    assert stocked["kit_arrival"].value == "Склад"
    assert stocked["kit_present"].value == "да"
    assert stocked["customer_supplied"].value == "ДА"
    # A KB that has gone past «КБ набрана» counts as picked even without the fulfilment fact.
    assert stocked["kit_picked"].value == "да"
    waiting = sheet_cells(stage=_stage(), blocker="", supply=SupplySummary(total=2, buying=2),
                          assigned=0, ready="", marks={})
    assert waiting["kit_arrival"].value == "Ждем"


def test_manual_mark_overrides_and_keeps_the_auto_value():
    cells = sheet_cells(stage=_stage(), blocker="", supply=None, assigned=0, ready="",
                        marks={"in_plan": "да", "status": "ПРВ КОМПЛ", "comment": "срочно"})
    assert (cells["in_plan"].value, cells["in_plan"].auto, cells["in_plan"].manual) == ("да", "нет", True)
    # The status is derived only: even a mark that slipped in does not override it.
    assert (cells["status"].value, cells["status"].manual) == ("В работе", False)
    assert cells["comment"].value == "срочно"


def test_calendar_marks_planned_days_quantity_on_first_day_and_day_notes():
    first, last = calendar_span(date(2026, 10, 5))
    assert (first, last) == (date(2026, 9, 28), date(2026, 12, 4))
    worker = uuid4()
    cut = _operation(start=datetime(2026, 10, 5, 9), finish=datetime(2026, 10, 7, 0, 0))
    shared = _operation(shares=(OperationShare(worker, None, datetime(2026, 10, 9, 9), datetime(2026, 10, 9, 12)),))
    unplanned = _operation()
    calendar = sheet_calendar(
        [_task([cut, shared, unplanned], quantity=12), _task([_operation()], status=ProductionStatus.CANCELLED)],
        {"day:2026-10-08": "П", "day:2027-05-01": "далеко", "ready": "40"}, first, last,
    )
    # The window ending at midnight does not spill into 07.10.
    assert calendar.work == (date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 9))
    assert calendar.starts == ((date(2026, 10, 5), 12),)
    assert calendar.notes == ((date(2026, 10, 8), "П"),)


def test_plan_marks_migration_adds_field_and_keeps_events(tmp_path):
    backend_root = Path(__file__).parents[2]
    database_path = tmp_path / "plan-marks.db"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    command.upgrade(config, "0079_procurement_stock_covers")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO production_plan_events (id, project, sequence, occurred_at, who, kind, blocker, text,"
            " deadline, item_index, value) VALUES (?, '720К.1', 1, '2026-10-01 00:00:00', 'Тест', 'Комментарий',"
            " '', 'До миграции', NULL, '', '')", ("5" * 32,),
        )
    command.upgrade(config, "0080_production_plan_marks")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT text, field FROM production_plan_events").fetchone() == ("До миграции", "")
    command.downgrade(config, "0079_procurement_stock_covers")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(production_plan_events)")}
        assert "field" not in columns
        assert connection.execute("SELECT text FROM production_plan_events").fetchone() == ("До миграции",)
