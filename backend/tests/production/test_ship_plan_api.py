"""План от даты отгрузки и первая поставка через HTTP: только просмотр в автоплане «По проекту»."""

from datetime import date

from sqlalchemy import func, select

from app.shared.persistence import AuditEventRow
from tests.production.test_autoplan_api import ANNA, BORIS, client, issue, launch, preview, tasks_of  # noqa: F401 — фикстура

URL = "/api/production/autoplan/ship-plan"


def ship(http, **extra):
    return http.post(URL, json={"mode": "project", "project": "708К", "people": [str(ANNA), str(BORIS)], **extra})


def test_ship_plan_fits_and_offers_first_delivery_without_writing_anything(client):
    http, sessions = client
    first, second = launch(http, "КБ1"), launch(http, "КБ2", terminal="T-2")
    issue("КБ3", deadline=date(2026, 10, 2))
    before = tasks_of(http)

    response = ship(http, ship_date="2026-10-02")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ship_date"] == "2026-10-02" and body["due"].startswith("2026-10-02T17:30")
    assert body["fits"] is True and body["late"] == [] and body["unplaced"] == []
    assert body["slack_seconds"] > 0 and body["start_by"] >= body["start"]
    rows = {row["item"]: row for row in body["tasks"]}
    assert set(rows) == {"КБ1", "КБ2", "КБ3"} and rows["КБ3"]["new"] is True and rows["КБ1"]["number"] == first["number"]
    assert all(row["latest_finish"] <= body["due"] for row in rows.values())
    variants = body["first_delivery"]
    assert variants and variants[-1]["share"] == 1 and len(variants[-1]["lots"]) == 3
    assert all(a["ready"] < b["ready"] and a["share"] < b["share"] for a, b in zip(variants, variants[1:]))
    assert variants[0]["ready"] < variants[-1]["ready"]
    # Весь проект — тот же прямой расчёт, что предпросмотр автоплана на тех же людях.
    planned = preview(http, mode="project", project="708К", people=[str(ANNA), str(BORIS)])
    assert variants[-1]["ready"] == max(item["finish"] for item in planned["proposals"])
    # Подсказка ничего не пишет: задания те же, новых нет, аудита нет.
    assert tasks_of(http) == before and second["id"] in before
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow).where(
            AuditEventRow.event_type == "production.autoplan_applied",
        )) == 0


def test_ship_plan_shows_where_it_does_not_fit(client):
    http, _ = client
    launch(http, "КБ1", quantity=2000)

    body = ship(http, ship_date="2026-09-23").json()

    assert body["fits"] is False and body["slack_seconds"] < 0
    assert body["late"] and body["late"][0]["item"] == "КБ1" and body["late"][0]["operator_name"] in {"Анна", "Борис"}
    assert body["late"][0]["stage"] and body["late"][0]["latest_start"] < body["start"]


def test_all_work_assigned_by_hand_is_checked_against_the_ship_date(client):
    http, _ = client
    task = launch(http, "КБ1")
    for operation in task["operations"]:
        current = tasks_of(http)[task["id"]]
        response = http.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign", json={
            "version": current["version"], "operator_ids": [str(ANNA)], "requested_start": "2026-09-30T09:00:00",
        })
        assert response.status_code == 200, response.text

    late = ship(http, ship_date="2026-09-29").json()
    early = ship(http, ship_date="2026-10-02").json()

    assert late["tasks"] == [] and late["first_delivery"] == [] and late["assigned_finish"].startswith("2026-09-30")
    assert late["fits"] is False and early["fits"] is True


def test_ship_date_defaults_to_the_nearest_deadline_and_is_checked(client):
    http, _ = client
    launch(http, "КБ1", deadline="2026-10-01")

    assert ship(http).json()["ship_date"] == "2026-10-01"
    assert ship(http, ship_date="2026-09-01").status_code == 422
    assert http.post(URL, json={"mode": "shop", "ship_date": "2026-10-02"}).status_code == 422
    assert http.post(URL, json={"mode": "project", "project": "708К", "ship_date": "2026-10-02"}).status_code == 422
