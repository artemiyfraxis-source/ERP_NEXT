"""Базовый план и «план / факт» (идея PRODUCTION_PLANNER Василия; владелец, 13.10.2026)."""

import sqlite3
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.main import app
from app.modules.production.baseline import (
    TOLERANCE, BaselineLine, PlanBaseline, TaskFact, deviation, first_start, take_baseline, task_window,
)
from app.modules.production.domain import ProductionOperation, ProductionStatus, ProductionTask, ProductionValidationError
from app.shared.clock import moscow_now
from tests.production.test_api import MASTER_ID, _as, launch_from_card, production_api  # noqa: F401 — фикстура

NOW = datetime(2026, 10, 13, 12, 0)
START = datetime(2026, 10, 13, 9, 0)
FINISH = datetime(2026, 10, 14, 17, 30)


def _operation(position=1, window=(START, FINISH)) -> ProductionOperation:
    return ProductionOperation(
        id=uuid4(), position=position, code="OP-01", stage="Резка", description="", seconds_per_unit=Decimal(60),
        setup_seconds=Decimal(0), norm_seconds=600, operator_ids=(uuid4(),) if window else (),
        planned_start=window[0] if window else None, planned_finish=window[1] if window else None,
    )


def _task(number="ПЗ-0001", status=ProductionStatus.RUNNING, window=(START, FINISH), quantity=10, operations=None) -> ProductionTask:
    task = ProductionTask.create(
        number=number, source_idempotency_key=f"key-{number}", source_fingerprint="fp", project="591К.3",
        item="591К.3.КБ1", name="Жгут", quantity=quantity, fulfillment_request_id=None, deadline=None,
        launched_by="Мастер", operations=operations or (_operation(1, window),), created_at=START,
    )
    return replace(task, status=status)


def _line(quantity=10) -> BaselineLine:
    return BaselineLine(uuid4(), "ПЗ-0001", "591К.3", "591К.3.КБ1", START, FINISH, quantity)


def _fact(status=ProductionStatus.RUNNING, started=None, finished=None, done="0") -> TaskFact:
    return TaskFact(status, started, finished, Decimal(done))


# --- снимок -------------------------------------------------------------------------------------------------------


def test_snapshot_takes_running_tasks_with_a_window_and_counts_the_rest():
    tasks = [
        _task("ПЗ-0002"),
        _task("ПЗ-0001", quantity=5),
        _task("ПЗ-0003", window=None),
        _task("ПЗ-0004", status=ProductionStatus.DONE),
        _task("ПЗ-0005", status=ProductionStatus.CANCELLED),
    ]

    baseline = take_baseline(tasks, number=3, now=NOW, actor_id=MASTER_ID, actor_name=" Мастер ")

    assert [line.task_number for line in baseline.lines] == ["ПЗ-0001", "ПЗ-0002"]
    assert baseline.skipped == 1 and baseline.number == 3 and baseline.created_by == "Мастер"
    first = baseline.lines[0]
    assert (first.planned_start, first.planned_finish, first.quantity) == (START, FINISH, 5)


def test_snapshot_window_spans_all_operations():
    two = _task(operations=(_operation(1), _operation(2, (FINISH, FINISH + timedelta(days=1))), _operation(3, None)))

    assert task_window(two) == (START, FINISH + timedelta(days=1))


def test_nothing_to_fix_without_planned_tasks():
    with pytest.raises(ProductionValidationError, match="Фиксировать нечего"):
        take_baseline([_task(window=None)], number=1, now=NOW, actor_id=MASTER_ID, actor_name="Мастер")


def test_snapshot_refuses_a_broken_line_or_header():
    with pytest.raises(ProductionValidationError):
        BaselineLine(uuid4(), "ПЗ-1", "П", "И", FINISH, START, 1)
    with pytest.raises(ProductionValidationError):
        BaselineLine(uuid4(), "ПЗ-1", "П", "И", START, FINISH, 0)
    line = _line()
    with pytest.raises(ProductionValidationError, match="дважды"):
        PlanBaseline(uuid4(), 1, NOW, MASTER_ID, "Мастер", 0, (line, line))
    with pytest.raises(ProductionValidationError):
        PlanBaseline(uuid4(), 0, NOW, MASTER_ID, "Мастер", 0, (line,))


def test_snapshot_is_frozen():
    baseline = take_baseline([_task()], number=1, now=NOW, actor_id=MASTER_ID, actor_name="Мастер")
    with pytest.raises(AttributeError):
        baseline.lines[0].quantity = 1  # type: ignore[misc]


# --- отклонения ---------------------------------------------------------------------------------------------------


def test_started_late_against_the_snapshot():
    item = deviation(_line(), _fact(started=START + timedelta(hours=3)), NOW)

    assert item.late_start and item.start_delay == timedelta(hours=3)
    assert not item.late_finish and not item.short


def test_start_within_tolerance_is_on_plan():
    item = deviation(_line(), _fact(started=START + TOLERANCE), NOW)

    assert not item.late_start and not item.deviates


def test_not_started_after_planned_start_is_late_until_now():
    item = deviation(_line(), _fact(), NOW)

    assert item.late_start and item.start_delay == NOW - START and not item.started


def test_not_started_before_planned_start_is_nothing_yet():
    item = deviation(_line(), _fact(), START - timedelta(days=1))

    assert item.start_delay is None and item.finish_delay is None and not item.deviates


def test_finished_late_and_short_on_a_done_task():
    item = deviation(_line(10), _fact(ProductionStatus.DONE, START, FINISH + timedelta(days=2), "8"), NOW + timedelta(days=5))

    assert item.late_finish and item.finish_delay == timedelta(days=2)
    assert item.short and item.shortfall == Decimal(2) and item.finished


def test_finished_early_with_full_quantity_is_on_plan():
    item = deviation(_line(10), _fact(ProductionStatus.DONE, START, FINISH - timedelta(hours=5), "10"), NOW + timedelta(days=5))

    assert item.finish_delay == timedelta(hours=-5) and not item.deviates


def test_running_task_past_its_planned_finish_is_late_and_short():
    later = FINISH + timedelta(hours=4)
    item = deviation(_line(10), _fact(started=START, done="6"), later)

    assert item.late_finish and item.finish_delay == timedelta(hours=4)
    assert item.shortfall == Decimal(4)


def test_running_task_before_its_planned_finish_is_not_short_yet():
    item = deviation(_line(10), _fact(started=START, done="3"), NOW)

    assert item.shortfall == 0 and not item.deviates


def test_cancelled_task_is_short_by_what_was_not_made():
    item = deviation(_line(10), _fact(ProductionStatus.CANCELLED, done="0"), NOW)

    assert item.short and item.shortfall == Decimal(10)
    assert item.start_delay is None and item.finish_delay is None


def test_overdelivery_is_not_negative_shortfall():
    item = deviation(_line(10), _fact(ProductionStatus.DONE, START, FINISH, "12"), NOW + timedelta(days=5))

    assert item.shortfall == 0


def test_aware_and_naive_moments_compare():
    from app.modules.production.baseline import MOSCOW
    item = deviation(_line(), _fact(started=(START + timedelta(hours=2)).replace(tzinfo=MOSCOW)), NOW)

    assert item.start_delay == timedelta(hours=2)


def test_first_start_skips_cancelled_starts():
    cancelled, kept = uuid4(), uuid4()
    starts = [(cancelled, START), (kept, START + timedelta(hours=2))]

    assert first_start(starts, {cancelled}) == START + timedelta(hours=2)
    assert first_start([(cancelled, START)], {cancelled}) is None


# --- миграция -----------------------------------------------------------------------------------------------------

REVISION = "0118_plan_baseline"


def _config(path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    return config


def test_baseline_tables_keep_rows_and_refuse_bad_ones_and_updates(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    path = tmp_path / "baseline.db"
    config = _config(path)
    command.upgrade(config, REVISION)
    header = "INSERT INTO production_plan_baselines (id, number, created_at, created_by_id, created_by, skipped) VALUES (?, ?, ?, ?, ?, ?)"
    line = (
        "INSERT INTO production_plan_baseline_lines (baseline_id, task_id, task_number, project, item, planned_start,"
        " planned_finish, quantity) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    baseline_id, task_id = uuid.uuid4().hex, uuid.uuid4().hex
    with sqlite3.connect(path) as connection:
        connection.execute(header, (baseline_id, 1, "2026-10-13 12:00:00", uuid.uuid4().hex, "Мастер", 0))
        connection.execute(line, (baseline_id, task_id, "ПЗ-1", "591К.3", "КБ1", "2026-10-13 09:00:00", "2026-10-14 17:30:00", 10))
        for bad_header in (
            (uuid.uuid4().hex, 1, "2026-10-13 12:00:00", uuid.uuid4().hex, "Мастер", 0),  # тот же номер
            (uuid.uuid4().hex, 0, "2026-10-13 12:00:00", uuid.uuid4().hex, "Мастер", 0),  # номер 0
            (uuid.uuid4().hex, 2, "2026-10-13 12:00:00", uuid.uuid4().hex, "", 0),  # без автора
            (uuid.uuid4().hex, 3, "2026-10-13 12:00:00", uuid.uuid4().hex, "Мастер", -1),
            (uuid.uuid4().hex, 4, None, uuid.uuid4().hex, "Мастер", 0),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(header, bad_header)
        for bad_line in (
            (baseline_id, task_id, "ПЗ-1", "591К.3", "КБ1", "2026-10-13 09:00:00", "2026-10-14 17:30:00", 10),  # дубль
            (baseline_id, uuid.uuid4().hex, "ПЗ-2", "591К.3", "КБ1", "2026-10-15 09:00:00", "2026-10-14 17:30:00", 10),
            (baseline_id, uuid.uuid4().hex, "ПЗ-3", "591К.3", "КБ1", "2026-10-13 09:00:00", "2026-10-14 17:30:00", 0),
            (baseline_id, uuid.uuid4().hex, "ПЗ-4", "591К.3", "КБ1", None, "2026-10-14 17:30:00", 1),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(line, bad_line)
        with pytest.raises(sqlite3.DatabaseError, match="не меняется"):
            connection.execute("UPDATE production_plan_baselines SET created_by = 'Другой'")
        with pytest.raises(sqlite3.DatabaseError, match="не меняется"):
            connection.execute("UPDATE production_plan_baseline_lines SET quantity = 1")
        assert connection.execute("SELECT COUNT(*) FROM production_plan_baseline_lines").fetchone() == (1,)

    command.downgrade(config, "-1")  # на родителя, каким бы он ни был после перестановки цепочки
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"production_plan_baselines", "production_plan_baseline_lines"} & tables


# --- API ----------------------------------------------------------------------------------------------------------


def _at(day: int, hour: int, minute: int = 0) -> None:
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, day, hour, minute)


def _planned_task(client, operator_id, key="baseline-v1", quantity=10, index="КБ1"):
    task = launch_from_card(client, key, "591К.3", index, quantity, [("CUT_WIRE+2crimp", "Резка", 60, 0)]).json()["task"]
    operation = task["operations"][0]
    assigned = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id)]},
    )
    assert assigned.status_code == 200, assigned.text
    return assigned.json()["task"]


def test_api_without_a_baseline_says_so(production_api):
    client, _operator_id = production_api

    result = client.get("/api/production/plan/baseline")

    assert result.status_code == 200, result.text
    assert result.json()["baseline"] is None and result.json()["rows"] == []


def test_api_fixes_a_baseline_and_shows_a_late_start_and_short_delivery(production_api):
    client, operator_id = production_api
    task = _planned_task(client, operator_id)
    operation = task["operations"][0]

    fixed = client.post("/api/production/plan/baseline")

    assert fixed.status_code == 201, fixed.text
    body = fixed.json()
    assert body["baseline"]["number"] == 1 and body["baseline"]["tasks"] == 1 and body["baseline"]["created_by"] == "Мастер"
    row = body["rows"][0]
    assert row["task_number"] == task["number"] and row["quantity"] == 10
    assert row["planned_start"].startswith(operation["planned_start"][:16])
    assert body["summary"]["on_plan"] == 1

    # План сдвигается после фиксации — снимок нет.
    planned_start = datetime.fromisoformat(row["planned_start"]).replace(tzinfo=None)
    start_at = planned_start + timedelta(hours=3)
    app.dependency_overrides[moscow_now] = lambda: start_at
    _as(MASTER_ID, "Артемий", {"admin"}, {})
    behalf = {"on_behalf_of": str(operator_id)}
    started = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", params=behalf, json={})
    assert started.status_code == 200, started.text
    session_id = started.json()["session"]["id"]
    finish_at = start_at + timedelta(hours=1)
    app.dependency_overrides[moscow_now] = lambda: finish_at
    submitted = client.post(f"/api/production/work/{session_id}/submit", params=behalf, json={"good_quantity": "7"})
    assert submitted.status_code == 200, submitted.text

    view = client.get("/api/production/plan/baseline").json()

    row = view["rows"][0]
    assert row["late_start"] is True and row["start_delay_minutes"] == 180 and row["started"] is True
    assert row["done_quantity"] == "7" and row["finished"] is False
    assert view["summary"]["late_start"] == 1

    # Плановый конец прошёл, сдано 7 из 10 — недодано 3 и конец позже.
    later = datetime.fromisoformat(row["planned_finish"]).replace(tzinfo=None) + timedelta(days=1)
    app.dependency_overrides[moscow_now] = lambda: later
    row = client.get("/api/production/plan/baseline").json()["rows"][0]
    assert row["short"] is True and row["shortfall"] == "3" and row["late_finish"] is True


def test_api_cancelled_start_does_not_count_as_started(production_api):
    client, operator_id = production_api
    task = _planned_task(client, operator_id)
    operation = task["operations"][0]
    assert client.post("/api/production/plan/baseline").status_code == 201
    _as(MASTER_ID, "Артемий", {"admin"}, {})
    behalf = {"on_behalf_of": str(operator_id)}
    session_id = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", params=behalf, json={},
    ).json()["session"]["id"]
    cancelled = client.post(f"/api/production/work/{session_id}/cancel", params=behalf)
    assert cancelled.status_code == 200, cancelled.text

    row = client.get("/api/production/plan/baseline").json()["rows"][0]

    assert row["started"] is False and row["started_at"] is None


def test_api_each_fix_is_a_new_numbered_snapshot_and_new_tasks_show_as_outside(production_api):
    client, operator_id = production_api
    _planned_task(client, operator_id)
    assert client.post("/api/production/plan/baseline").json()["baseline"]["number"] == 1
    _planned_task(client, operator_id, key="baseline-v2", index="КБ2")

    view = client.get("/api/production/plan/baseline").json()
    assert view["baseline"]["outside"] == 1 and view["baseline"]["tasks"] == 1

    second = client.post("/api/production/plan/baseline").json()
    assert second["baseline"]["number"] == 2 and second["baseline"]["tasks"] == 2 and second["baseline"]["outside"] == 0


def test_api_refuses_an_empty_fix_and_a_viewer(production_api):
    client, _operator_id = production_api

    empty = client.post("/api/production/plan/baseline")
    assert empty.status_code == 422 and "Фиксировать нечего" in empty.json()["detail"]

    _as(MASTER_ID, "Наблюдатель", {"foreman"}, {"production": "view"})
    assert client.post("/api/production/plan/baseline").status_code == 403
    assert client.get("/api/production/plan/baseline").status_code == 200
