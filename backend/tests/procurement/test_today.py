"""Stage option `procurement.today`: pure section logic and the endpoint behind the option."""

import json
from dataclasses import replace
from datetime import date

from app.modules.procurement.domain import ProcurementStatus, create_line_from_requested
from app.modules.procurement.infrastructure import ProcurementLineRow
from app.modules.procurement.today import build_today
from app.shared import stage_options
from tests.procurement.test_api import message, procurement_api  # noqa: F401 — fixture

TODAY = date(2026, 10, 5)


def line(status, **fields):
    return replace(create_line_from_requested(message()), status=status, **fields)


def test_sections_come_in_fixed_priority_order_and_sort_oldest_first():
    lines = [
        line(ProcurementStatus.TO_PAY, supplier="C"),
        line(ProcurementStatus.AWAITING_INVOICE, order_date=date(2026, 10, 3)),
        line(ProcurementStatus.AWAITING_INVOICE, order_date=date(2026, 9, 20)),
        line(ProcurementStatus.IN_TRANSIT, expected_arrival=date(2026, 10, 6)),
        line(ProcurementStatus.IN_TRANSIT, expected_arrival=date(2026, 10, 5)),
        line(ProcurementStatus.IN_TRANSIT, expected_arrival=date(2026, 10, 1)),
        line(ProcurementStatus.IN_TRANSIT, expected_arrival=date(2026, 10, 9)),
        line(ProcurementStatus.QUARANTINE, actual_arrival=date(2026, 10, 4)),
        line(ProcurementStatus.DONE),
    ]
    sections = build_today(lines, TODAY, available_count=3)

    assert [s["key"] for s in sections] == [
        "overdue", "arriving", "awaiting_invoice", "to_pay", "quarantine", "available",
    ]
    overdue, arriving, awaiting = sections[0], sections[1], sections[2]
    assert overdue["items"][0]["days"] == 4
    assert [i["date"] for i in arriving["items"]] == ["2026-10-05", "2026-10-06"]
    assert [i["days"] for i in awaiting["items"]] == [15, 2]
    assert sections[-1]["count"] == 3 and len(sections[-1]["items"]) == 1


def test_empty_sections_are_omitted_and_missing_dates_degrade():
    assert build_today([], TODAY, 0) == []
    only = build_today([line(ProcurementStatus.IN_TRANSIT), line(ProcurementStatus.QUARANTINE)], TODAY)
    assert [s["key"] for s in only] == ["quarantine"]
    assert only[0]["items"][0]["days"] is None


def _options(monkeypatch, tmp_path, **states):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(states), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))


def test_today_endpoint_is_404_when_option_is_off(procurement_api, monkeypatch, tmp_path):  # noqa: F811
    client, _, _ = procurement_api
    _options(monkeypatch, tmp_path)
    assert client.get("/api/procurement/today").status_code == 404


def test_today_endpoint_lists_the_buyers_lines_when_on(procurement_api, monkeypatch, tmp_path):  # noqa: F811
    client, free_line, sessions = procurement_api
    _options(monkeypatch, tmp_path, **{"procurement.today": True})
    assert client.post("/api/procurement/take", json={"line_ids": [str(free_line.id)]}).status_code == 200
    with sessions.begin() as session:
        row = session.get(ProcurementLineRow, free_line.id)
        row.status = ProcurementStatus.IN_TRANSIT.value
        row.expected_arrival = date(2026, 9, 10)

    response = client.get("/api/procurement/today")

    assert response.status_code == 200, response.text
    sections = response.json()["sections"]
    assert [s["key"] for s in sections] == ["overdue"]
    assert sections[0]["items"][0]["id"] == str(free_line.id)
    assert sections[0]["items"][0]["days"] == 5  # the stubbed clock is 2026-09-15


def test_board_puts_project_cards_in_fields_and_one_project_can_sit_in_several():
    from datetime import timedelta
    from uuid import uuid4

    from app.modules.procurement.today import build_board

    def own(status, **fields):
        return line(status, id=uuid4(), **fields)

    lines = [
        own(ProcurementStatus.IN_TRANSIT, expected_arrival=TODAY - timedelta(days=5), project_reference="591К.3"),
        own(ProcurementStatus.IN_TRANSIT, expected_arrival=TODAY - timedelta(days=2), project_reference="591К.3"),
        own(ProcurementStatus.TO_PAY, project_reference="591К.3"),
        own(ProcurementStatus.IN_TRANSIT, expected_arrival=TODAY - timedelta(days=1), project_reference="700К.1"),
        own(ProcurementStatus.DONE, project_reference="591К.3"),
    ]
    board = build_board(lines, TODAY)
    fields = {section["key"]: section for section in board["sections"]}
    overdue = fields["overdue"]["projects"]
    assert [card["project"] for card in overdue] == ["591К.3", "700К.1"]  # the worst delay first
    assert overdue[0]["count"] == 2 and overdue[0]["days"] == 5
    assert [card["project"] for card in fields["to_pay"]["projects"]] == ["591К.3"]
    drill = board["projects"]["591К.3"]
    assert len(drill) == 3  # the finished line stays out
    assert {row["field"] for row in drill} == {"Просрочена поставка", "Ждёт оплаты"}
