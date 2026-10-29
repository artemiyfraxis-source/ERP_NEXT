"""«Норма и факт» (владелец, 29.10.2026): факт против нормы ЭТК по шаблонам операций и монтажникам — только отчёт."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from app.main import app
from app.modules.production.infrastructure import ProductionWorkTimeLogRow
from app.modules.production.norm_fact import (
    MIN_FACTS,
    Submission,
    by_operator,
    by_template,
    facts,
    quartiles,
    relative_ratio,
    spread,
    template_key,
    template_medians,
)
from app.shared.clock import moscow_now
from tests.production.test_api import launch_from_card, production_api  # noqa: F401 — фикстура

T0 = datetime(2026, 10, 1, 9, 0)
ANNA, BORIS = UUID("20000000-0000-0000-0000-00000000000a"), UUID("20000000-0000-0000-0000-00000000000b")


def _sub(*, actual: int, norm: int, quantity: str = "10", code: str = "CUT", stage: str = "Резка", operator: UUID = ANNA,
         operation: UUID | None = None, unit: str = "6", hour: int = 0, name: str = "") -> Submission:
    return Submission(
        session_id=uuid4(), task_id=uuid4(), task_number="ПЗ-1", project="П1", item="КБ1",
        operation_id=operation or uuid4(), position=1, operation_code=code, stage=stage,
        operator_id=operator, operator_name=name or ("Анна" if operator == ANNA else "Борис"),
        started_at=T0 + timedelta(hours=hour), finished_at=T0 + timedelta(hours=hour, seconds=actual),
        actual_seconds=actual, quantity=Decimal(quantity), seconds_per_unit=Decimal(unit), norm_seconds=norm,
    )


# --- правила ---------------------------------------------------------------------------------------------------------

def test_one_fact_is_one_operation_by_one_fitter_summing_her_submissions():
    operation = uuid4()
    items = facts([
        _sub(actual=600, norm=500, quantity="10", operation=operation, hour=0),
        _sub(actual=300, norm=250, quantity="5", operation=operation, hour=2),
        _sub(actual=100, norm=100, quantity="2", operation=operation, operator=BORIS, hour=3),
    ])
    anna = next(fact for fact in items if fact.operator_id == ANNA)
    assert (anna.submissions, anna.actual_seconds, anna.norm_seconds, anna.quantity) == (2, 900, 750, Decimal(15))
    assert anna.ratio == pytest.approx(1.2)
    assert anna.actual_per_piece == pytest.approx(60) and anna.norm_per_piece == pytest.approx(50)
    assert anna.started_at == T0 and anna.finished_at == T0 + timedelta(hours=2, seconds=300)
    assert len(items) == 2


def test_fact_without_norm_or_pieces_or_time_is_not_counted():
    no_norm, no_pieces, no_time = facts([
        _sub(actual=600, norm=0, hour=0), _sub(actual=600, norm=500, quantity="0", hour=1), _sub(actual=0, norm=500, hour=2),
    ])
    assert [fact.ratio for fact in (no_norm, no_pieces, no_time)] == [None, None, None]
    assert no_norm.norm_per_piece is None and no_pieces.actual_per_piece is None
    value = spread([no_norm, no_pieces, no_time])
    assert (value.facts, value.no_norm, value.median, value.verdict) == (0, 3, None, "")


def test_template_is_the_operation_code_or_the_stage_without_one():
    assert template_key("CUT_WIRE+2crimp", "Резка") == "CUT_WIRE+2crimp"
    assert template_key(" ", "Пайка") == "Пайка"


def test_quartiles_interpolate_like_a_spreadsheet():
    assert quartiles([1.0, 2.0, 3.0, 4.0, 5.0]) == (2.0, 4.0)
    assert quartiles([1.0, 2.0]) == (1.25, 1.75)
    assert quartiles([0.7]) == (0.7, 0.7)


def test_few_facts_give_a_median_but_no_suggestion():
    rows = by_template(facts([_sub(actual=2000, norm=1000, hour=hour) for hour in range(MIN_FACTS - 1)]))
    assert rows[0].spread.facts == MIN_FACTS - 1 and not rows[0].spread.enough
    assert rows[0].spread.median == pytest.approx(2.0) and rows[0].spread.verdict == "медленнее"
    assert rows[0].suggestion == ""


def test_stably_slow_template_suggests_a_longer_piece_time():
    ratios = [1.4, 1.5, 1.5, 1.6, 1.3]
    rows = by_template(facts([_sub(actual=int(ratio * 1000), norm=1000, hour=hour, unit="60") for hour, ratio in enumerate(ratios)]))
    row = rows[0]
    assert row.spread.enough and row.spread.median == pytest.approx(1.5)
    assert row.suggested_seconds_per_unit == pytest.approx(90)
    assert row.suggestion == "Норма занижена: факт ×1,50 — предложение: штучное время 1 мин 30 с вместо 1 мин"


def test_stably_fast_template_suggests_a_shorter_piece_time():
    rows = by_template(facts([_sub(actual=700, norm=1000, hour=hour, unit="40") for hour in range(MIN_FACTS)]))
    assert rows[0].suggestion == "Норма завышена: факт ×0,70 — предложение: штучное время 28 с вместо 40 с"


def test_scattered_template_is_not_stable_even_with_a_high_median():
    # Медиана 1,3, но четверть фактов быстрее нормы — это разброс, а не заниженная норма.
    ratios = [0.6, 0.7, 0.9, 1.3, 1.4, 1.5, 1.6]
    rows = by_template(facts([_sub(actual=int(ratio * 1000), norm=1000, hour=hour) for hour, ratio in enumerate(ratios)]))
    assert rows[0].spread.median == pytest.approx(1.3) and rows[0].spread.low < 1
    assert rows[0].suggestion == ""


def test_in_norm_band_gives_no_suggestion():
    rows = by_template(facts([_sub(actual=1200, norm=1000, hour=hour) for hour in range(MIN_FACTS)]))
    assert rows[0].spread.verdict == "в норме" and rows[0].suggestion == ""


def test_fitter_is_measured_against_the_usual_for_the_template_not_a_wrong_norm():
    # Норма резки занижена для всех (×1,5); Анна режет как все — её скорость в норме, Борис вдвое медленнее обычного.
    subs = [_sub(actual=1500, norm=1000, hour=hour) for hour in range(MIN_FACTS)]
    subs += [_sub(actual=1500, norm=1000, hour=10 + hour) for hour in range(MIN_FACTS)]
    subs += [_sub(actual=3000, norm=1000, hour=20 + hour, operator=BORIS) for hour in range(MIN_FACTS)]
    items = facts(subs)
    templates = by_template(items)
    medians = template_medians(templates)
    assert medians == {"CUT": pytest.approx(1.5)}
    assert relative_ratio(items[0], medians) == pytest.approx(1.0)
    rows = {row.operator_name: row for row in by_operator(items, templates)}
    assert rows["Анна"].coefficient is None and rows["Анна"].suggestion == ""
    assert rows["Борис"].relative == pytest.approx(2.0)
    assert rows["Борис"].coefficient == 0.5
    assert rows["Борис"].suggestion == "Работает медленнее нормы — коэффициент скорости в «Компетенциях»: 0,50"


def test_fitter_without_enough_template_data_is_compared_to_the_norm_itself():
    items = facts([_sub(actual=800, norm=1000, hour=hour, code=f"OP{hour}") for hour in range(MIN_FACTS)])
    templates = by_template(items)
    assert template_medians(templates) == {}
    row = by_operator(items, templates)[0]
    assert row.relative == pytest.approx(0.8) and row.coefficient == 1.25
    assert row.suggestion.startswith("Работает быстрее нормы")


def test_fitter_coefficient_close_to_one_or_few_facts_is_not_suggested():
    near = by_operator(facts([_sub(actual=1050, norm=1000, hour=hour) for hour in range(MIN_FACTS)]), [])
    assert near[0].coefficient is None
    few = by_operator(facts([_sub(actual=3000, norm=1000, hour=hour) for hour in range(MIN_FACTS - 1)]), [])
    assert few[0].relative == pytest.approx(3.0) and few[0].coefficient is None


def test_empty_report():
    assert facts([]) == [] and by_template([]) == [] and by_operator([], []) == []


# --- HTTP ------------------------------------------------------------------------------------------------------------

def _at(day: int, hour: int, minute: int = 0) -> None:
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, day, hour, minute)


def _assigned(client, operator_id, key="norm-fact-1", code="CUT_WIRE+2crimp", quantity=100):
    task = launch_from_card(client, key, "П3-0007", "КБ1", quantity, [(code, "Автомат резка + опрессовка", 36, 0)]).json()["task"]
    operation = task["operations"][0]
    assigned = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id)]},
    )
    assert assigned.status_code == 200, assigned.text
    return assigned.json()["task"]


def _work_with_pause(client, task, good="98", defect="2"):
    operation = task["operations"][0]
    _at(22, 14, 5)
    started = client.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/work/start", json={})
    assert started.status_code == 200, started.text
    session_id = started.json()["session"]["id"]
    _at(22, 14, 30)
    assert client.post(f"/api/production/work/{session_id}/pause").status_code == 200
    _at(22, 14, 50)
    assert client.post(f"/api/production/work/{session_id}/resume").status_code == 200
    _at(22, 16, 3)
    submitted = client.post(f"/api/production/work/{session_id}/submit", json={
        "good_quantity": good, "defect_quantity": defect, "defect_character": "Обрыв", "defect_description": "Порван провод",
    })
    assert submitted.status_code == 200, submitted.text


def _as_operator(operator_id):
    from app.shared.http_auth import Principal, authenticated_principal
    from app.shared.identity import Actor
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset(), permissions={"production": "edit", "myops": "edit"},
    )


def test_report_is_honestly_empty_with_the_default_period(production_api):
    client, _operator_id = production_api

    report = client.get("/api/production/norm-fact")

    assert report.status_code == 200, report.text
    body = report.json()
    assert (body["date_from"], body["date_to"]) == ("2026-07-25", "2026-09-22")
    assert body["templates"] == [] and body["operators"] == [] and body["facts"] == []
    assert body["thresholds"] == {"min_facts": MIN_FACTS, "slow": 1.25, "fast": 0.8}


def test_report_takes_the_time_net_of_pauses_from_the_work_time_log(production_api):
    client, operator_id = production_api
    task = _assigned(client, operator_id)
    norm_unit = float(task["operations"][0]["seconds_per_unit"])
    _as_operator(operator_id)
    _work_with_pause(client, task)

    report = client.get("/api/production/norm-fact", params={"date_from": "2026-09-22", "date_to": "2026-09-22"})

    assert report.status_code == 200, report.text
    body = report.json()
    [fact] = body["facts"]
    # 14:05 → 16:03 = 118 мин, пауза 20 мин: 98 мин работы на 100 шт (годные + брак).
    assert fact["actual_seconds"] == 98 * 60 and fact["quantity"] == "100"
    assert fact["norm_seconds"] == pytest.approx(norm_unit * 100, abs=1)
    assert fact["ratio"] == pytest.approx(98 * 60 / fact["norm_seconds"], abs=0.001)
    assert fact["actual_per_piece"] == pytest.approx(58.8)
    assert fact["operator_name"] == "Оператор" and fact["operation_code"] == "CUT_WIRE+2crimp"
    [template] = body["templates"]
    assert template["template"] == "CUT_WIRE+2crimp" and template["facts"] == 1 and template["enough"] is False
    assert template["suggestion"] == ""
    [operator] = body["operators"]
    assert operator["operator_id"] == str(operator_id) and operator["coefficient"] is None
    # Сдача вне периода в отчёт не идёт.
    other_day = client.get("/api/production/norm-fact", params={"date_from": "2026-09-23", "date_to": "2026-09-30"}).json()
    assert other_day["facts"] == []


def test_submissions_made_before_the_log_are_read_from_the_session_output(production_api):
    client, operator_id = production_api
    task = _assigned(client, operator_id, key="norm-fact-old")
    norm_unit = float(task["operations"][0]["seconds_per_unit"])
    _as_operator(operator_id)
    _work_with_pause(client, task)
    # Сдача до журнала (0105): строки журнала нет, есть сдача сессии с началом, концом и паузами.
    with app.state.production_uow_factory() as uow:
        uow.session.execute(delete(ProductionWorkTimeLogRow))
        uow.commit()

    body = client.get("/api/production/norm-fact", params={"date_from": "2026-09-22", "date_to": "2026-09-22"}).json()

    [fact] = body["facts"]
    assert fact["actual_seconds"] == 98 * 60 and fact["quantity"] == "100"
    assert fact["norm_seconds"] == pytest.approx(norm_unit * 100, abs=1)
    assert fact["stage"] and fact["operation_code"] == "CUT_WIRE+2crimp"


def test_report_rejects_a_reversed_period(production_api):
    client, _operator_id = production_api
    refused = client.get("/api/production/norm-fact", params={"date_from": "2026-09-22", "date_to": "2026-09-01"})
    assert refused.status_code == 422
