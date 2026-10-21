"""Доска мастера: будущие задания КБ с выданной техкартой, группы оборудования операций и групповое назначение одному
монтажнику (владелец, 19.10.2026)."""

from datetime import date, datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.application import launch_production
from app.modules.production.infrastructure import ProductionOperationOperatorRow, ProductionTaskRow
from app.modules.references.domain import TechOperationRecord, TechOperationTab
from app.modules.techcards.operation_cards import parse_operation_template
from app.modules.techcards.project_cards import ProjectTechcard
from tests.production.test_autoplan_api import (  # noqa: F401 — фикстура client
    ANNA, BORIS, NOW, apply, client, issue, launch, preview, tasks_of,
)
from tests.production.test_launch_race import by_hand, interleave, race, stored  # noqa: F401 — фикстура race

NO_RUN = "Нет тиража — запустите через «+ Запустить задание»"


def issue_route(index: str, *, project: str = "708К", part_quantity: str = "10", deadline: date | None = None,
                press: str = "ОБ.027.X") -> ProjectTechcard:
    """Выданная техкарта на четыре операции: резка (станок только в БД.ОП), опрессовка (пресс ОБ.027 в оснастке ЭТК),
    монтаж (категория карточки) и герметизация (ничего — название этапа)."""
    config = {"ops": [
        {"type": "insMark", "stage": "Резка", "opNumber": "CUT_WIRE_auto", "tOp": 30, "tPrep": 0},
        {"type": "prsTerm", "stage": "Опрессовка терминалов", "opNumber": "PRS", "tOp": 30, "tPrep": 0},
        {"type": "insHousing", "stage": "Монтаж в корпус", "opNumber": "ASM", "tOp": 20, "tPrep": 0},
        {"type": "seal", "stage": "Герметизация", "opNumber": "TST", "tOp": 10, "tPrep": 0},
    ]}
    card = ProjectTechcard.create(
        project=project, item_index=index, product_name="Жгут", source_calculation="", deadline=deadline, now=NOW,
    ).rebuild({
        "operations": [
            {"code": "CUT_WIRE_auto", "type": "insMark", "card": {"tooling": [
                {"section": "Инструмент", "inventory_number": "ИН.001", "name": "Нож"}]}},
            {"code": "PRS", "type": "prsTerm", "card": {"tooling": [
                {"section": "Оборудование", "inventory_number": press, "name": "Пресс"}]}},
            {"code": "ASM", "type": "insHousing"},
            {"code": "TST", "type": "seal"},
        ],
        "build_config": config, "kit": {"part_quantity": part_quantity},
    }, NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def reissue(card: ProjectTechcard) -> ProjectTechcard:
    """Новая ревизия выданной техкарты (как авторевизия ЭТК): версия и ревизия +1."""
    with app.state.techcards_uow_factory() as uow:
        current = uow.techcards.get(card.id)
        rebuilt = current.rebuild(dict(current.document), NOW)
        uow.techcards.save(rebuilt)
        uow.commit()
    return rebuilt


@pytest.fixture()
def library(client):
    """БД.ОП с «Машиной» для резки и карточка операции ASM с категорией."""
    with app.state.references_uow_factory() as references:
        references.tech_operations.save(TechOperationRecord.create(
            tab=TechOperationTab.OPERATION, display_text="Резка CUT_WIRE_auto", search_text="CUT_WIRE_auto",
            source_sheet="БД.ОП", sort_key="CUT_WIRE_auto",
            fields={"opNumber": "CUT_WIRE_auto", "Номер": "CUT_WIRE_auto", "Машина": "EW-05F+M/Автомат мерной резки проводов"},
        ))
        references.commit()
    template = parse_operation_template(
        source_id="asm-card", title="ASM | Монтаж в корпус", category="Монтаж", rows=[["Операция", "1", "Собрать"]],
    )
    with app.state.techcards_uow_factory() as uow:
        uow.operation_cards.load((template,), imported_at=NOW)
        uow.commit()
    return client


def board(http, **params) -> dict:
    response = http.get("/api/production/board", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def assign(http, operator: UUID, items: list[dict], requested_start: str | None = None):
    return http.post("/api/production/board/assign", json={
        "operator_id": str(operator), "items": items,
        **({"requested_start": requested_start} if requested_start else {}),
    })


def future_items(future: dict, *positions: int) -> list[dict]:
    return [{
        "techcard_id": future["techcard_id"], "techcard_version": future["techcard_version"],
        "operation_id": operation["id"],
    } for operation in future["operations"] if operation["position"] in positions]


def window(item: dict) -> tuple[datetime, datetime]:
    return datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["finish"])


def test_issued_kb_without_task_is_a_future_task_with_equipment_groups(library):
    http, _ = library
    card = issue_route("КБ2", deadline=date(2026, 9, 30))

    shown = board(http)

    assert shown["tasks"] == [] and shown["skipped"] == []
    [future] = shown["future"]
    assert future["pending"] is True and future["number"] == "Новое · 708К КБ2"
    assert (future["techcard_id"], future["techcard_version"], future["techcard_revision"]) == (str(card.id), card.version, 1)
    assert (future["quantity"], future["deadline"]) == (10, "2026-09-30")
    assert [operation["equipment"] for operation in future["operations"]] == [
        {"key": "machine:ew-05f+m автомат мерной резки проводов", "label": "EW-05F+M Автомат мерной резки проводов", "limited": False},
        {"key": "ОБ.027", "label": "ОБ.027 Пресс", "limited": True},
        {"key": "category:монтаж", "label": "Монтаж", "limited": False},
        {"key": "category:герметизация", "label": "Герметизация", "limited": False},
    ]
    # Стабильные id: та же ревизия — те же операции; доска ничего не пишет.
    assert [operation["id"] for operation in board(http)["future"][0]["operations"]] == [operation["id"] for operation in future["operations"]]
    assert tasks_of(http) == {}


def test_kb_with_a_task_has_no_future_task_and_a_cancelled_task_brings_it_back(library):
    http, _ = library
    task = launch(http, "КБ1")

    shown = board(http)

    assert shown["future"] == []
    [row] = shown["tasks"]
    assert row["id"] == task["id"] and row["pending"] is False
    assert [operation["equipment"]["key"] for operation in row["operations"]] == ["ОБ.027", "category:монтаж"]
    cancelled = http.post(f"/api/production/tasks/{task['id']}/cancel", json={"version": task["version"], "cancelled": True})
    assert cancelled.status_code == 200, cancelled.text
    again = board(http)
    assert [item["item"] for item in again["future"]] == ["КБ1"] and again["tasks"] == []


def test_kb_without_run_is_skipped_with_the_reason_and_project_filter_applies(library):
    http, _ = library
    issue_route("КБ3", part_quantity="")
    issue_route("КБ4", project="815Р")

    shown = board(http)
    assert [item["item"] for item in shown["future"]] == ["КБ4"]
    assert [(item["index"], item["reason"]) for item in shown["skipped"]] == [("КБ3", NO_RUN)]
    only = board(http, project="815Р")
    assert [item["project"] for item in only["future"]] == ["815Р"] and only["skipped"] == []


def test_first_assignment_creates_the_task_pinned_to_the_issued_revision(library):
    http, sessions = library
    card = reissue(issue_route("КБ2"))
    assert card.revision == 2
    [future] = board(http)["future"]

    response = assign(http, ANNA, future_items(future, 1, 2))

    assert response.status_code == 200, response.text
    body = response.json()
    [created] = body["created"]
    assert created["techcard_id"] == str(card.id) and created["techcard_revision"] == 2
    task = tasks_of(http)[created["id"]]
    assert task["techcard_revision"] == 2 and task["quantity"] == 10
    assert [operation["operator_ids"] for operation in task["operations"]] == [[str(ANNA)], [str(ANNA)], [], []]
    assert [item["position"] for item in body["placed"]] == [1, 2] and body["refused"] == []
    first, second = (window(item) for item in body["placed"])
    assert first[1] <= second[0]
    with sessions() as session:
        row = session.scalar(select(ProductionTaskRow))
        assert (row.source_idempotency_key, row.launch_seq) == (f"board:{card.id}:1", 1)
    # Новая ревизия после создания задания задание не перестраивает и будущего задания не даёт.
    reissue(card)
    assert tasks_of(http)[created["id"]]["techcard_revision"] == 2
    assert board(http)["future"] == []


def test_two_assigns_on_one_kb_give_one_task(library):
    http, _ = library
    issue_route("КБ2")
    [future] = board(http)["future"]
    assert assign(http, ANNA, future_items(future, 1)).status_code == 200

    stale = assign(http, BORIS, future_items(future, 2))

    assert stale.status_code == 409, stale.text
    assert len(tasks_of(http)) == 1
    [task] = board(http)["tasks"]
    again = assign(http, BORIS, [{"task_id": task["id"], "task_version": task["version"], "position": 2}])
    assert again.status_code == 200, again.text
    assert again.json()["created"] == [] and len(tasks_of(http)) == 1
    assert tasks_of(http)[task["id"]]["operations"][1]["operator_ids"] == [str(BORIS)]


def test_reissued_techcard_after_the_board_was_read_is_409(library):
    http, _ = library
    card = issue_route("КБ2")
    [future] = board(http)["future"]
    reissue(card)

    response = assign(http, ANNA, future_items(future, 1))

    assert response.status_code == 409, response.text
    assert tasks_of(http) == {}
    [fresh] = board(http)["future"]
    assert fresh["techcard_version"] == future["techcard_version"] + 1
    assert assign(http, ANNA, future_items(fresh, 1)).status_code == 200


def test_changed_task_version_is_409(library):
    http, _ = library
    task = launch(http, "КБ1")

    response = assign(http, ANNA, [{"task_id": task["id"], "task_version": task["version"] + 1, "position": 1}])

    assert response.status_code == 409, response.text


def test_group_goes_to_one_fitter_in_deadline_order_without_overlaps_around_existing_work(library):
    http, _ = library
    existing = launch(http, "КБ1")
    share = http.post(
        f"/api/production/tasks/{existing['id']}/operations/{existing['operations'][0]['id']}/share",
        json={"version": existing["version"], "operator_id": str(ANNA)},
    )
    assert share.status_code == 200, share.text
    busy = share.json()["plan"]
    late = issue_route("КБ2", deadline=date(2026, 10, 9))
    early = issue_route("КБ3", deadline=date(2026, 9, 25))
    futures = {item["item"]: item for item in board(http)["future"]}
    # Группа «ОБ.027 Пресс»: опрессовка обеих КБ одному монтажнику.
    items = [*future_items(futures["КБ2"], 2), *future_items(futures["КБ3"], 2)]

    response = assign(http, ANNA, items, requested_start=NOW.isoformat())

    assert response.status_code == 200, response.text
    body = response.json()
    numbers = {item["techcard_id"]: item["number"] for item in body["created"]}
    assert [item["task_number"] for item in body["placed"]] == [numbers[str(early.id)], numbers[str(late.id)]]
    windows = sorted([window(item) for item in body["placed"]] + [(datetime.fromisoformat(busy["start"]), datetime.fromisoformat(busy["finish"]))])
    assert all(earlier[1] <= later[0] for earlier, later in zip(windows, windows[1:]))


def test_group_skips_an_absence_of_the_fitter(library):
    http, _ = library
    calendar = http.put("/api/production/calendar", json={"holidays": [], "absences": [{
        "operator_id": str(BORIS), "from_date": "2026-09-22", "to_date": "2026-09-22", "kind": "Отгул",
    }]})
    assert calendar.status_code == 204, calendar.text
    issue_route("КБ2")
    [future] = board(http)["future"]

    response = assign(http, BORIS, future_items(future, 1, 2, 3))

    assert response.status_code == 200, response.text
    placed = [window(item) for item in response.json()["placed"]]
    assert all(start.date() >= date(2026, 9, 23) for start, _ in placed)
    assert all(earlier[1] <= later[0] for earlier, later in zip(placed, placed[1:]))


def test_assigned_and_started_operations_are_refused_with_a_reason(library):
    http, sessions = library
    task = launch(http, "КБ1")
    share = http.post(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/share",
        json={"version": task["version"], "operator_id": str(ANNA)},
    )
    assert share.status_code == 200, share.text
    version = share.json()["task"]["version"]

    response = assign(http, BORIS, [
        {"task_id": task["id"], "task_version": version, "position": 1},
        {"task_id": task["id"], "task_version": version, "position": 2},
    ])

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["position"] for item in body["placed"]] == [2]
    assert body["refused"] == [{
        "task_id": task["id"], "techcard_id": None, "operation_id": None, "position": 1,
        "reason": f"{task['number']} · этап 1 «Опрессовка терминалов»: уже назначена (Анна)",
    }]
    with sessions() as session:
        row = session.scalar(select(ProductionOperationOperatorRow).where(ProductionOperationOperatorRow.operator_id == ANNA))
        row.started_at = NOW
        session.commit()
    current = tasks_of(http)[task["id"]]
    started = assign(http, BORIS, [{"task_id": task["id"], "task_version": current["version"], "position": 1}])
    assert started.status_code == 422, started.text
    assert started.json()["detail"] == f"{task['number']} · этап 1 «Опрессовка терминалов»: работа уже начата"


def test_skill_without_permission_is_a_warning_not_a_block(library):
    http, _ = library
    issue_route("КБ2")
    [future] = board(http)["future"]
    skills = http.put(f"/api/production/skills/{ANNA}", json={"skills": [
        {"operation_code": "PRS", "allowed": False, "stars": 0, "coefficient": "1"},
    ]})
    assert skills.status_code == 204, skills.text

    response = assign(http, ANNA, future_items(future, 2))

    assert response.status_code == 200, response.text
    assert "Анна: нет допуска к операции PRS" in response.json()["warnings"]


def test_busy_machine_is_a_warning_not_a_block(library):
    http, _ = library
    # ОБ.050 нет в БД.ОБ — станок один: две опрессовки в одно время на нём не помещаются.
    issue_route("КБ2", press="ОБ.050.1")
    issue_route("КБ3", press="ОБ.050.1")
    futures = {item["item"]: item for item in board(http)["future"]}
    assert futures["КБ2"]["operations"][1]["equipment"] == {"key": "ОБ.050", "label": "ОБ.050 Пресс", "limited": True}
    assert assign(http, ANNA, future_items(futures["КБ2"], 2), requested_start=NOW.isoformat()).status_code == 200

    response = assign(http, BORIS, future_items(futures["КБ3"], 2), requested_start=NOW.isoformat())

    assert response.status_code == 200, response.text
    assert any(text.startswith("ОБ.050 занят") for text in response.json()["warnings"]), response.json()["warnings"]


def test_board_assign_losing_to_autoplan_creates_one_task(race, monkeypatch):
    http, sessions, factory = race
    card = issue("КБ2")
    planned = preview(http)
    [future] = board(http)["future"]
    applied = []
    # Автоплан создаёт задание по КБ после того, как доска прочитала номер запуска, — перед записью её задания.
    interleave(monkeypatch, "reserve_number", lambda: applied.append(apply(http, planned)))

    response = assign(http, ANNA, future_items(future, 1))

    assert response.status_code == 409, response.text
    assert applied[0].status_code == 200, applied[0].text
    rows = stored(sessions)
    assert len(rows) == 1 and rows[0][1].startswith("autoplan:") and rows[0][2:] == (card.id, 1)


def test_board_assign_after_a_manual_launch_read_is_stale(race, monkeypatch):
    http, sessions, factory = race
    card = issue("КБ2")
    [future] = board(http)["future"]
    interleave(monkeypatch, "last_launch_seqs", lambda: launch_production(by_hand(card.id, "by-hand"), factory()))

    response = assign(http, ANNA, future_items(future, 1))

    assert response.status_code == 409, response.text
    assert [(key, seq) for _, key, _, seq in stored(sessions)] == [("by-hand", 1)]
