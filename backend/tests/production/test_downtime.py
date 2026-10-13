"""Простои с причинами (владелец, 13.10.2026): начало / конец, один открытый простой на задание, отчёт за период."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.production.domain import ProductionValidationError
from app.modules.production.downtime import Downtime, DowntimeConflict, DowntimeReason, report
from app.modules.production.downtime_infrastructure import ProductionDowntimeRow, SqlAlchemyDowntimeRepository
from app.shared.clock import MOSCOW, moscow_now
from app.shared.persistence import AuditEventRow
from tests.production.test_api import MASTER_ID, _as, launch_from_card, production_api  # noqa: F401 — фикстура
from tests.production.test_work_time_log import _assigned_task

FITTER = {"myops": "edit"}


def _at(hour: int, minute: int = 0, day: int = 22) -> None:
    app.dependency_overrides[moscow_now] = lambda: datetime(2026, 9, day, hour, minute)


def _downtime(reason=DowntimeReason.NO_MATERIAL, start=(22, 9, 0), end=None, operator_id=None, name="Оператор", comment=""):
    started = datetime(2026, 9, start[0], start[1], start[2], tzinfo=MOSCOW)
    ended = datetime(2026, 9, end[0], end[1], end[2], tzinfo=MOSCOW) if end else None
    return Downtime(
        id=uuid4(), task_id=uuid4(), operation_id=None, operator_id=operator_id, operator_name=name if operator_id else "",
        reason=reason, comment=comment, started_at=started, started_by_id=MASTER_ID, started_by_name="Мастер",
        project="П3-0007", task_number="ПЗ-0001", item="КБ1", ended_at=ended, ended_by_id=MASTER_ID if end else None,
        ended_by_name="Мастер" if end else "",
    )


def _period(day_from: int, day_to: int) -> tuple[datetime, datetime]:
    return datetime(2026, 9, day_from, tzinfo=MOSCOW), datetime(2026, 9, day_to + 1, tzinfo=MOSCOW)


# --- правила простоя ---

def test_other_reason_needs_a_comment_and_end_is_not_before_start():
    with pytest.raises(ProductionValidationError, match="Другое"):
        _downtime(DowntimeReason.OTHER, comment="   ")
    assert _downtime(DowntimeReason.OTHER, comment=" сломался стол ").comment == "сломался стол"
    with pytest.raises(ProductionValidationError, match="раньше начала"):
        _downtime(start=(22, 10, 0), end=(22, 9, 59))
    opened = _downtime(start=(22, 10, 0))
    with pytest.raises(ProductionValidationError, match="раньше начала"):
        opened.close(datetime(2026, 9, 22, 9, 0), MASTER_ID, "Мастер")
    closed = opened.close(datetime(2026, 9, 22, 10, 30), MASTER_ID, "Мастер")  # без пояса — московское время
    assert not closed.open and closed.seconds_within(*_period(22, 22), datetime(2026, 9, 23, tzinfo=MOSCOW)) == 1800
    with pytest.raises(DowntimeConflict):
        closed.close(datetime(2026, 9, 22, 11, 0), MASTER_ID, "Мастер")


def test_report_sums_only_the_part_inside_the_period_and_counts_open_until_now():
    fitter = uuid4()
    items = [
        _downtime(DowntimeReason.EQUIPMENT, start=(22, 9, 0), end=(22, 10, 0), operator_id=fitter),
        _downtime(DowntimeReason.NO_MATERIAL, start=(21, 23, 0), end=(22, 1, 0), operator_id=fitter),  # 1 ч из 2 — в периоде
        _downtime(DowntimeReason.NO_MATERIAL, start=(22, 14, 0)),  # открыт, задание целиком: до «сейчас» 15:30
        _downtime(DowntimeReason.WAITING_QC, start=(20, 9, 0), end=(20, 10, 0)),  # вне периода
    ]
    since, until = _period(22, 22)
    now = datetime(2026, 9, 22, 15, 30, tzinfo=MOSCOW)
    rows = report(items, by="reason", since=since, until=until, now=now)
    # Порядок — как в списке причин; пустые причины не показываются.
    assert [(row.key, row.label, row.count, row.seconds, row.open) for row in rows] == [
        ("no_material", "Нет материала", 2, 3600 + 5400, 1),
        ("equipment", "Оборудование", 1, 3600, 0),
    ]
    people = report(items, by="operator", since=since, until=until, now=now)
    assert [(row.label, row.seconds) for row in people] == [("Оператор", 7200), ("Задание целиком", 5400)]
    with pytest.raises(ProductionValidationError):
        report(items, by="project", since=since, until=until, now=now)


# --- база ---

def test_database_keeps_one_open_downtime_per_task(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id)
    first = _downtime()
    first = Downtime(**{**first.__dict__, "task_id": UUID(task["id"]), "started_at": datetime(2026, 9, 22, 9, 0)})
    second = Downtime(**{**first.__dict__, "id": uuid4()})
    with app.state.production_uow_factory() as uow:
        repository = SqlAlchemyDowntimeRepository(uow.session)
        repository.add(first)
        with pytest.raises(IntegrityError):
            repository.add(second)  # второй открытый по тому же заданию
    with app.state.production_uow_factory() as uow:
        repository = SqlAlchemyDowntimeRepository(uow.session)
        repository.add(first)
        repository.close(first.close(datetime(2026, 9, 22, 9, 30), MASTER_ID, "Мастер"))
        repository.add(second)  # закрытый не мешает новому
        uow.commit()
    with app.state.production_uow_factory() as uow:
        rows = list(uow.session.scalars(select(ProductionDowntimeRow).order_by(ProductionDowntimeRow.started_at)))
        assert len(rows) == 2 and sum(row.ended_at is None for row in rows) == 1
        with pytest.raises(DowntimeConflict):  # закрыть уже закрытый — отказ, а не тихая перезапись
            SqlAlchemyDowntimeRepository(uow.session).close(first.close(datetime(2026, 9, 22, 10, 0), MASTER_ID, "Мастер"))


# --- HTTP ---

def test_fitter_starts_and_stops_a_downtime_on_his_task(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id)
    operation = task["operations"][0]
    _as(operator_id, "Оператор", {"operator"}, FITTER)
    _at(10, 0)
    started = client.post("/api/production/downtime", json={
        "task_id": task["id"], "operation_id": operation["id"], "reason": "no_material", "comment": "нет провода МГТФ",
    })
    assert started.status_code == 200, started.text
    downtime = started.json()["downtime"]
    assert downtime["open"] and downtime["operator_name"] == "Оператор" and downtime["reason_name"] == "Нет материала"
    assert downtime["task_number"] == task["number"] and downtime["stage"] == operation["stage"]

    _at(10, 20)
    opened = client.get("/api/production/downtime/open").json()["downtimes"]
    assert [item["id"] for item in opened] == [downtime["id"]] and opened[0]["seconds"] == 1200

    # Второй открытый простой по тому же заданию — отказ, и от мастера тоже.
    again = client.post("/api/production/downtime", json={"task_id": task["id"], "reason": "equipment"})
    assert again.status_code == 409 and "уже открыт" in again.json()["detail"]
    _as(MASTER_ID, "Мастер", {"foreman"}, {"production": "edit", "myops": "edit"})
    assert client.post("/api/production/downtime", json={"task_id": task["id"], "reason": "equipment", "whole_task": True}).status_code == 409

    _as(operator_id, "Оператор", {"operator"}, FITTER)
    _at(10, 45)
    stopped = client.post(f"/api/production/downtime/{downtime['id']}/stop")
    assert stopped.status_code == 200, stopped.text
    assert not stopped.json()["downtime"]["open"] and stopped.json()["downtime"]["seconds"] == 45 * 60
    assert client.post(f"/api/production/downtime/{downtime['id']}/stop").status_code == 409
    assert client.get("/api/production/downtime/open").json()["downtimes"] == []
    # После конца — можно новый.
    assert client.post("/api/production/downtime", json={"task_id": task["id"], "reason": "waiting_qc"}).status_code == 200

    with app.state.production_uow_factory() as uow:
        events = [row.event_type for row in uow.session.scalars(select(AuditEventRow)) if row.event_type.startswith("production.downtime")]
    assert events.count("production.downtime_started") == 2 and events.count("production.downtime_ended") == 1


def test_downtime_refuses_bad_reason_foreign_task_and_closed_task(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id)
    foreign = launch_from_card(client, "foreign-task", "П3-0008", "КБ2", 10, [("CUT_WIRE", "Резка", 10, 0)]).json()["task"]
    _as(operator_id, "Оператор", {"operator"}, FITTER)
    post = lambda **body: client.post("/api/production/downtime", json={"task_id": task["id"], **body})
    assert post(reason="lunch").status_code == 422
    other = post(reason="other", comment="  ")
    assert other.status_code == 422 and "Другое" in other.json()["detail"]
    assert post(reason="no_drawing", operation_id=foreign["operations"][0]["id"]).status_code == 422
    assert post(reason="equipment", whole_task=True).status_code == 403  # задание целиком — только мастер
    not_mine = client.post("/api/production/downtime", json={"task_id": foreign["id"], "reason": "equipment"})
    assert not_mine.status_code == 403

    # Мастер отмечает простой всего задания; монтажник его не закрывает, мастер — да.
    _as(MASTER_ID, "Мастер", {"foreman"}, {"production": "edit", "myops": "edit"})
    whole = client.post("/api/production/downtime", json={"task_id": task["id"], "reason": "equipment", "whole_task": True})
    assert whole.status_code == 200 and whole.json()["downtime"]["whole_task"]
    _as(operator_id, "Оператор", {"operator"}, FITTER)
    assert client.post(f"/api/production/downtime/{whole.json()['downtime']['id']}/stop").status_code == 403
    _as(MASTER_ID, "Мастер", {"foreman"}, {"production": "edit", "myops": "edit"})
    assert client.post(f"/api/production/downtime/{whole.json()['downtime']['id']}/stop").status_code == 200

    cancelled = client.post(f"/api/production/tasks/{task['id']}/cancel", json={"version": task["version"], "cancelled": True})
    assert cancelled.status_code == 200, cancelled.text
    closed = client.post("/api/production/downtime", json={"task_id": task["id"], "reason": "equipment", "whole_task": True})
    assert closed.status_code == 422 and "отменено" in closed.json()["detail"]
    # Только смотрящий «Задания» простой не отмечает.
    _as(operator_id, "Оператор", {"operator"}, {"myops": "view"})
    assert client.post("/api/production/downtime", json={"task_id": foreign["id"], "reason": "equipment"}).status_code == 403


def test_master_records_for_the_fitter_and_the_report_sums_reasons_and_people(production_api):
    client, operator_id = production_api
    task = _assigned_task(client, operator_id)
    other = _assigned_task(client, operator_id, key="second-task", project="П3-0009")
    _as(MASTER_ID, "Мастер", {"foreman"}, {"production": "edit", "myops": "edit"})
    behalf = {"on_behalf_of": str(operator_id)}

    _at(9, 0)
    first = client.post("/api/production/downtime", params=behalf, json={"task_id": task["id"], "reason": "no_material"}).json()["downtime"]
    assert first["operator_name"] == "Оператор" and first["started_by_name"] == "Мастер"
    _at(10, 30)
    assert client.post(f"/api/production/downtime/{first['id']}/stop", params=behalf).status_code == 200
    _at(11, 0)
    client.post("/api/production/downtime", json={"task_id": other["id"], "reason": "other", "comment": "свет", "whole_task": True})
    _at(12, 0)
    second = client.post("/api/production/downtime", params=behalf, json={"task_id": task["id"], "reason": "no_material"}).json()["downtime"]
    _at(12, 15)
    client.post(f"/api/production/downtime/{second['id']}/stop")

    _at(13, 0)  # простой «Другое» ещё открыт: 2 ч до «сейчас»
    result = client.get("/api/production/downtime/report", params={"date_from": "2026-09-22", "date_to": "2026-09-22"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert [(row["label"], row["count"], row["seconds"], row["open"]) for row in body["rows"]] == [
        ("Нет материала", 2, 5400 + 900, 0), ("Другое", 1, 7200, 1),
    ]
    assert body["total_seconds"] == 5400 + 900 + 7200 and body["total_count"] == 3 and len(body["entries"]) == 3
    people = client.get("/api/production/downtime/report", params={"date_from": "2026-09-22", "date_to": "2026-09-22", "by": "operator"}).json()
    assert [(row["label"], row["seconds"]) for row in people["rows"]] == [("Задание целиком", 7200), ("Оператор", 6300)]
    # Другой день — пусто; чужая группировка — отказ; отчёт — для планирования.
    assert client.get("/api/production/downtime/report", params={"date_from": "2026-09-21", "date_to": "2026-09-21"}).json()["rows"] == []
    assert client.get("/api/production/downtime/report", params={"date_from": "2026-09-22", "date_to": "2026-09-22", "by": "x"}).status_code == 422
    _as(operator_id, "Оператор", {"operator"}, FITTER)
    assert client.get("/api/production/downtime/report", params={"date_from": "2026-09-22", "date_to": "2026-09-22"}).status_code == 403
