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
from app.shared.persistence import AuditEventRow
from tests.production.test_autoplan_api import (  # noqa: F401 — фикстура client
    ANNA, BORIS, NOW, apply, client, issue, launch, preview, tasks_of,
)
from tests.production.test_launch_race import by_hand, interleave, race, stored  # noqa: F401 — фикстура race

NO_RUN = "Нет тиража КБ — задайте тираж в наборе проекта"


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


# --- Тираж нового задания и «Уже сделано» по КБ без задания (владелец 22.10) ------------------------------------------


def board_output(http, future: dict, position: int, good: int, **extra):
    return http.post("/api/production/board/manual-output", json={
        "techcard_id": future["techcard_id"], "techcard_version": future["techcard_version"],
        "position": position, "good_quantity": good, **extra,
    })


def test_assignment_launches_the_task_with_the_run_the_master_typed(library):
    http, _ = library
    issue_route("КБ2")
    [future] = board(http)["future"]

    response = assign(http, ANNA, [{**item, "run": 4} for item in future_items(future, 1, 2)])

    assert response.status_code == 200, response.text
    [created] = response.json()["created"]
    assert tasks_of(http)[created["id"]]["quantity"] == 4


def test_run_above_the_kb_run_or_two_runs_for_one_kb_are_refused(library):
    http, _ = library
    issue_route("КБ2")
    [future] = board(http)["future"]

    above = assign(http, ANNA, [{**item, "run": 11} for item in future_items(future, 1)])
    mixed = assign(http, ANNA, [{**future_items(future, 1)[0], "run": 4}, {**future_items(future, 2)[0], "run": 5}])

    assert above.status_code == 422 and "от 1 до 10" in above.json()["detail"]
    assert mixed.status_code == 422 and "разные тиражи" in mixed.json()["detail"]
    assert tasks_of(http) == {}


def test_ready_pieces_on_a_kb_without_task_create_the_task_and_record_them_in_one_go(library):
    http, sessions = library
    card = issue_route("КБ2")
    [future] = board(http)["future"]

    response = board_output(http, future, 1, 4, run=6)

    assert response.status_code == 200, response.text
    body = response.json()
    task = tasks_of(http)[body["task"]["id"]]
    assert task["quantity"] == 6 and task["techcard_id"] == str(card.id)
    # Никто не назначен: штуки — запись мастера, без исполнителя.
    assert all(not operation["operator_ids"] for operation in task["operations"])
    assert body["operation_id"] == task["operations"][0]["id"]
    assert body["warnings"][0].startswith(f"Создано задание {task['number']}. Записано 4 шт на этап 1")
    # Запущено 6 из 10 — остальные 4 снова «новое задание».
    assert [(item["quantity"], item["launched"]) for item in board(http)["future"]] == [(4, 6)]
    with sessions() as session:
        assert session.scalar(select(ProductionTaskRow)).source_idempotency_key == f"board:{card.id}:1"
    # Доска устарела: по КБ с тех пор запустили — 409, второго задания нет.
    again = board_output(http, future, 1, 1, launched=future["launched"])
    assert again.status_code == 409, again.text
    assert len(tasks_of(http)) == 1


def test_refused_ready_pieces_leave_no_task_behind(library):
    http, _ = library
    issue_route("КБ2")
    [future] = board(http)["future"]

    too_many = board_output(http, future, 1, 7, run=6)
    no_inputs = board_output(http, future, 2, 3)

    assert too_many.status_code == 422, too_many.text
    assert no_inputs.status_code == 422, no_inputs.text
    assert tasks_of(http) == {} and len(board(http)["future"]) == 1
    filled = board_output(http, future, 2, 3, fill_inputs=True)
    assert filled.status_code == 200, filled.text
    assert [item["operation_position"] for item in filled.json()["filled"]] == [1]


def test_autoplan_of_a_project_launches_new_tasks_with_the_run_from_the_project_window(library):
    http, _ = library
    card = issue_route("КБ2")
    plan = {"mode": "project", "project": "708К", "people": [str(ANNA)], "runs": {str(card.id): 3}}

    planned = preview(http, **plan)

    assert [item["quantity"] for item in planned["new_tasks"]] == [3]
    # Тираж больше тиража КБ — 422.
    assert http.post("/api/production/autoplan/preview", json={**plan, "runs": {str(card.id): 99}}).status_code == 422
    applied = apply(http, planned, **plan)
    assert applied.status_code == 200, applied.text
    [task] = tasks_of(http).values()
    assert task["quantity"] == 3


# --- Остаток тиража и «запущено — когда поставили на кого-то» (владелец 22.10) ---------------------------------------


def remove_share(http, task: dict, position: int, operator: UUID):
    operation = next(item for item in task["operations"] if item["position"] == position)
    return http.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/share", json={
        "version": task["version"], "operator_id": str(operator), "remove": True,
    })


def cancel_reasons(sessions) -> list[str]:
    with sessions() as session:
        return [(row.details or {}).get("reason", "") for row in session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "production.cancelled"))]


def test_a_partial_run_leaves_the_rest_of_the_kb_run_as_a_new_task(library):
    http, sessions = library
    card = issue_route("КБ2")
    [future] = board(http)["future"]
    assert (future["quantity"], future["launched"], future["run"]) == (10, 0, "10")

    first = assign(http, ANNA, [{**item, "run": 4} for item in future_items(future, 1)])

    assert first.status_code == 200, first.text
    shown = board(http)
    [rest] = shown["future"]
    # Запущено 4 из 10 — остальные 6 снова «новое задание»; id другие: доска до запуска устарела.
    assert (rest["quantity"], rest["launched"], rest["techcard_version"]) == (6, 4, card.version)
    assert rest["id"] != future["id"] and len(shown["tasks"]) == 1
    second = assign(http, BORIS, future_items(rest, 1))
    assert second.status_code == 200, second.text
    [created] = second.json()["created"]
    assert tasks_of(http)[created["id"]]["quantity"] == 6
    with sessions() as session:
        rows = session.scalars(select(ProductionTaskRow).order_by(ProductionTaskRow.launch_seq)).all()
        assert [(row.quantity, row.launch_seq, row.source_idempotency_key) for row in rows] == [
            (4, 1, f"board:{card.id}:1"), (6, 2, f"board:{card.id}:2")]
    # Весь тираж запущен — нового задания нет; доска, прочитанная раньше, — 409, третьего задания нет.
    assert board(http)["future"] == []
    stale = assign(http, ANNA, future_items(rest, 2))
    assert stale.status_code == 409, stale.text
    assert len(tasks_of(http)) == 2


def test_the_rest_of_the_run_cannot_be_launched_above_it_or_from_a_stale_board(library):
    http, _ = library
    issue_route("КБ2")
    [future] = board(http)["future"]
    assert board_output(http, future, 1, 2, run=7, launched=future["launched"]).status_code == 200
    [rest] = board(http)["future"]
    assert (rest["quantity"], rest["launched"]) == (3, 7)

    above = assign(http, ANNA, [{**item, "run": 4} for item in future_items(rest, 1)])
    # Доска видела «запущено 0» (до первого задания) — по КБ запустили ещё: 409, а не второе задание мимо остатка.
    stale = board_output(http, future, 1, 1, launched=0)

    assert above.status_code == 422 and "от 1 до 3 (остаток: тираж КБ 10, уже запущено 7)" in above.json()["detail"]
    assert stale.status_code == 409 and "уже запущено 7 шт из 10" in stale.json()["detail"], stale.text
    assert sorted(task["quantity"] for task in tasks_of(http).values()) == [7]


def test_removing_the_last_fitter_of_an_untouched_task_cancels_it_and_the_kb_is_new_again(library):
    http, sessions = library
    issue_route("КБ2")
    [future] = board(http)["future"]
    done = assign(http, ANNA, [{**item, "run": 6} for item in future_items(future, 1, 2)])
    assert done.status_code == 200, done.text
    [task] = board(http)["tasks"]
    assert board(http)["future"][0]["quantity"] == 4

    once = remove_share(http, task, 1, ANNA)
    assert once.status_code == 200, once.text
    assert once.json()["task"]["status"] == "Запущено"  # на этапе 2 Анна ещё стоит
    last = remove_share(http, once.json()["task"], 2, ANNA)

    assert last.status_code == 200, last.text
    body = last.json()
    assert body["task"]["status"] == "Отменено"
    assert body["warnings"][0] == (
        f"Задание {task['number']} отменено: снят последний исполнитель, работы по нему не было — 708К КБ2 снова к запуску на 6 шт")
    assert cancel_reasons(sessions) == ["Снят последний исполнитель — задание не начато"]
    shown = board(http)
    assert shown["tasks"] == []
    [again] = shown["future"]
    assert (again["quantity"], again["launched"]) == (10, 0)


def test_removing_the_last_fitter_after_work_started_keeps_the_task(library):
    http, sessions = library
    issue_route("КБ2")
    [future] = board(http)["future"]
    assert assign(http, ANNA, future_items(future, 1)).status_code == 200
    [task] = board(http)["tasks"]
    operation = task["operations"][0]
    made = http.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/manual-output", json={
        "good_quantity": 2, "operator_id": None, "fill_inputs": False,
    })
    assert made.status_code == 200, made.text
    [task] = board(http)["tasks"]

    response = remove_share(http, task, 1, ANNA)

    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "Запущено"
    assert all(not item["operator_ids"] for item in response.json()["task"]["operations"])
    assert cancel_reasons(sessions) == []
    shown = board(http)
    assert [item["id"] for item in shown["tasks"]] == [task["id"]] and shown["future"] == []


def test_a_started_share_is_work_too_and_a_crew_keeps_the_task(library):
    http, sessions = library
    issue_route("КБ2")
    [future] = board(http)["future"]
    assert assign(http, ANNA, future_items(future, 1)).status_code == 200
    [task] = board(http)["tasks"]
    assert assign(http, BORIS, [{"task_id": task["id"], "task_version": task["version"], "position": 2}]).status_code == 200
    [task] = board(http)["tasks"]
    # Снят один из двух исполнителей задания — задание остаётся.
    first = remove_share(http, task, 2, BORIS)
    assert first.status_code == 200 and first.json()["task"]["status"] == "Запущено"
    with sessions() as session:
        row = session.scalar(select(ProductionOperationOperatorRow).where(ProductionOperationOperatorRow.operator_id == ANNA))
        row.started_at = NOW
        session.commit()
    [task] = board(http)["tasks"]

    response = remove_share(http, task, 1, ANNA)

    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "Запущено"
    assert cancel_reasons(sessions) == []


def test_clearing_the_crew_of_the_only_assigned_operation_cancels_an_untouched_task(library):
    http, sessions = library
    issue_route("КБ2")
    [future] = board(http)["future"]
    assert assign(http, ANNA, future_items(future, 1)).status_code == 200
    [task] = board(http)["tasks"]
    operation = task["operations"][0]

    response = http.post(f"/api/production/tasks/{task['id']}/operations/{operation['id']}/assign", json={
        "version": task["version"], "operator_ids": [],
    })

    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "Отменено"
    assert cancel_reasons(sessions) == ["Снят последний исполнитель — задание не начато"]
    assert board(http)["future"][0]["quantity"] == 10


def test_a_cancelled_task_is_not_restored_above_the_kb_run(library):
    http, _ = library
    issue_route("КБ2")
    [future] = board(http)["future"]
    assert assign(http, ANNA, [{**item, "run": 6} for item in future_items(future, 1)]).status_code == 200
    [task] = board(http)["tasks"]
    cancelled = remove_share(http, task, 1, ANNA).json()["task"]
    assert cancelled["status"] == "Отменено"
    [again] = board(http)["future"]
    assert assign(http, BORIS, [{**item, "run": 5} for item in future_items(again, 1)]).status_code == 200

    restore = http.post(f"/api/production/tasks/{cancelled['id']}/cancel", json={"version": cancelled["version"], "cancelled": False})

    assert restore.status_code == 422, restore.text
    assert restore.json()["detail"] == (
        f"Задание {cancelled['number']} не вернуть в работу: по 708К КБ2 уже запущено 5 из тиража 10 шт, а в задании 6 шт")
    # Вернуть 6 шт к запущенным 5 из 10 — выше тиража КБ: задание остаётся отменённым.
    assert tasks_of(http)[cancelled["id"]]["status"] == "Отменено"


def test_autoplan_launches_the_rest_of_a_partly_launched_run(library):
    http, _ = library
    card = issue_route("КБ2")
    [future] = board(http)["future"]
    assert assign(http, ANNA, [{**item, "run": 3} for item in future_items(future, 1)]).status_code == 200
    plan = {"mode": "project", "project": "708К", "people": [str(ANNA), str(BORIS)]}

    planned = preview(http, **plan)

    assert [(item["techcard_id"], item["quantity"]) for item in planned["new_tasks"]] == [(str(card.id), 7)]
    context = http.post("/api/production/autoplan/context", json=plan)
    assert context.status_code == 200, context.text
    assert [(item["quantity"], item["launched"], item["run"]) for item in context.json()["new"]] == [(7, 3, "10")]
    assert http.post("/api/production/autoplan/preview", json={**plan, "runs": {str(card.id): 8}}).status_code == 422
    applied = apply(http, planned, **plan)
    assert applied.status_code == 200, applied.text
    assert sorted(task["quantity"] for task in tasks_of(http).values()) == [3, 7]
    assert board(http)["future"] == []
    # Весь цех видит то же правило: остатка нет — нового задания нет.
    assert preview(http)["new_tasks"] == []
