"""Экран мастера: длинная операция с остатка дня и хвостом на следующий, доли по штукам, план по факту (владелец, 30.09)."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.main import app
from app.modules.identity.infrastructure import IdentityUserRow
from app.modules.production.dispatch import split_shares
from app.modules.production.domain import OperationShare, ProductionOperation, ProductionValidationError
from app.shared.clock import MOSCOW, moscow_now
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.production.test_api import launch_from_card, production_api  # noqa: F401 — фикстура

SECOND = UUID("10000000-0000-0000-0000-000000000003")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=MOSCOW)


def share(client, task, position, operator_id, start, quantity=None):
    operation = next(item for item in task["operations"] if item["position"] == position)
    response = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/share",
        json={"version": task["version"], "operator_id": str(operator_id), "quantity": quantity, "requested_start": start},
    )
    assert response.status_code == 200, response.text
    return response.json()


def fresh(client, task_id):
    tasks = client.get("/api/production/tasks").json()["tasks"]
    return next(item for item in tasks if item["id"] == task_id)


def window(task, position, operator_id):
    operation = next(item for item in task["operations"] if item["position"] == position)
    item = next(value for value in operation["shares"] if value["operator_id"] == str(operator_id))
    return item["planned_start"][:16], item["planned_finish"][:16]


def as_operator(operator_id):
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=operator_id, name="Оператор", department="Производство"),
        email="operator@example.test", roles=frozenset({"operator"}), permissions={"myops": "edit"},
    )


def test_long_operation_takes_the_rest_of_the_day_and_tails_into_the_next(production_api):
    # Скрин владельца: у монтажника занято 3,8 из 7 ч, операцию 5,6 ч нельзя было поставить на этот день.
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    # Смена 9:00–17:30, обед 13:00–13:30, чистых 7 из 8 ч: норма растягивается на 8/7.
    task = launch_from_card(client, "long-v1", "713К.2.1", "КБ1", 10, [
        ("CUT", "Резка", 1260, 0),      # 3,5 ч чистых → 4 ч на часах
        ("INS", "Монтаж", 2016, 0),     # 5,6 ч чистых → 6 ч 24 мин на часах
    ]).json()["task"]
    other = launch_from_card(client, "long-other-v1", "708К.4", "КБ2", 10, [("RUN", "Прозвонка", 720, 0)]).json()["task"]
    task = share(client, task, 1, operator_id, "2026-09-22T10:00:00+03:00")["task"]
    other = share(client, other, 1, operator_id, "2026-09-23T09:00:00+03:00")["task"]
    assert window(task, 1, operator_id) == ("2026-09-22T10:00", "2026-09-22T14:30")

    # Мастер нажал «9:00»: этап 2 не встаёт раньше своего этапа 1 — с 14:30 до конца дня, хвост на 23.09.
    placed = share(client, task, 2, operator_id, "2026-09-22T09:00:00+03:00")

    assert placed["plan"]["start"][:16] == "2026-09-22T14:30"
    assert placed["plan"]["finish"][:16] == "2026-09-23T12:24"
    # Работа монтажника на 23.09 (2 ч чистых = 2 ч 18 мин) сдвинута за хвост, через обед.
    assert window(fresh(client, other["id"]), 1, operator_id) == ("2026-09-23T12:24", "2026-09-23T15:12")
    assert any(line.startswith("Сдвинуто: ") and "Прозвонка" in line for line in placed["warnings"])
    assert window(fresh(client, task["id"]), 2, operator_id) == ("2026-09-22T14:30", "2026-09-23T12:24")


def test_operation_is_split_between_installers_by_pieces(production_api):
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    with app.state.production_uow_factory() as uow:
        uow.session.add(IdentityUserRow(
            id=SECOND, login="second", email="second@example.test", name="Второй", department="Монтаж",
            role="staff", site="ЦАР", active=True, shop_worker=True,
        ))
        uow.commit()
    task = launch_from_card(client, "split-v1", "591K.3", "КБ1", 10, [("OP", "Обжим", 315, 0)]).json()["task"]

    task = share(client, task, 1, operator_id, "2026-09-22T09:00:00+03:00", quantity=6)["task"]
    task = share(client, task, 1, SECOND, "2026-09-23T09:00:00+03:00", quantity=4)["task"]
    operation = task["operations"][0]

    assert [(item["operator_id"], item["quantity"]) for item in operation["shares"]] == [(str(operator_id), 6), (str(SECOND), 4)]
    # У каждого своё время по норме: 6 × 315 с = 31,5 мин чистых → 36 мин; 4 × 315 с = 21 мин → 24 мин.
    assert window(task, 1, operator_id) == ("2026-09-22T09:00", "2026-09-22T09:36")
    assert window(task, 1, SECOND) == ("2026-09-23T09:00", "2026-09-23T09:24")
    assert operation["planned_start"][:16] == "2026-09-22T09:00" and operation["planned_finish"][:16] == "2026-09-23T09:24"

    # Свободных штук нет: недостающие 4 штуки второму забираются у первого.
    task = share(client, task, 1, SECOND, "2026-09-23T09:00:00+03:00", quantity=8)["task"]
    assert [(item["operator_id"], item["quantity"]) for item in task["operations"][0]["shares"]] == [(str(operator_id), 2), (str(SECOND), 8)]
    assert window(task, 1, operator_id) == ("2026-09-22T09:00", "2026-09-22T09:12")

    removed = client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/share",
        json={"version": task["version"], "operator_id": str(operator_id), "remove": True},
    )
    assert removed.status_code == 200, removed.text
    assert [item["quantity"] for item in removed.json()["task"]["operations"][0]["shares"]] == [8]


def test_plan_follows_the_installer_taking_and_finishing_work(production_api):
    # «Взял» позже плана — его следующая работа сдвигается; «закончил» раньше — подтягивается.
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    first = launch_from_card(client, "fact-a-v1", "591K.3", "КБ1", 10, [("", "Подготовка", 315, 0)]).json()["task"]
    second = launch_from_card(client, "fact-b-v1", "591K.3", "КБ2", 10, [("", "Подготовка", 315, 0)]).json()["task"]
    first = share(client, first, 1, operator_id, "2026-09-22T09:00:00+03:00")["task"]
    assert window(first, 1, operator_id) == ("2026-09-22T09:00", "2026-09-22T10:00")
    share(client, second, 1, operator_id, "2026-09-22T10:00:00+03:00")
    master = app.dependency_overrides[authenticated_principal]
    as_operator(operator_id)

    app.dependency_overrides[moscow_now] = lambda: at(22, 9, 30)
    started = client.post(f"/api/production/tasks/{first['id']}/operations/{first['operations'][0]['id']}/work/start", json={})
    assert started.status_code == 200, started.text
    app.dependency_overrides[authenticated_principal] = master
    assert window(fresh(client, first["id"]), 1, operator_id) == ("2026-09-22T09:30", "2026-09-22T10:30")
    assert window(fresh(client, second["id"]), 1, operator_id) == ("2026-09-22T10:30", "2026-09-22T11:30")
    assert fresh(client, first["id"])["operations"][0]["shares"][0]["started_at"][:16] == "2026-09-22T09:30"

    as_operator(operator_id)
    app.dependency_overrides[moscow_now] = lambda: at(22, 10, 5)
    submitted = client.post(f"/api/production/work/{started.json()['session']['id']}/submit", json={"good_quantity": "10"})
    assert submitted.status_code == 200, submitted.text
    app.dependency_overrides[authenticated_principal] = master

    done = fresh(client, first["id"])["operations"][0]["shares"][0]
    assert (done["planned_finish"][:16], done["finished_at"][:16]) == ("2026-09-22T10:05", "2026-09-22T10:05")
    assert window(fresh(client, second["id"]), 1, operator_id) == ("2026-09-22T10:05", "2026-09-22T11:05")


def test_partial_output_moves_the_expected_finish(production_api):
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    task = launch_from_card(client, "partial-v1", "591K.3", "КБ1", 10, [("", "Подготовка", 315, 0)]).json()["task"]
    task = share(client, task, 1, operator_id, "2026-09-22T09:00:00+03:00")["task"]
    master = app.dependency_overrides[authenticated_principal]
    as_operator(operator_id)
    started = client.post(f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/work/start", json={})
    app.dependency_overrides[moscow_now] = lambda: at(22, 9, 50)
    # 4 штуки за 50 минут: осталось 6 × 315 с = 31,5 мин чистых → 36 мин на часах от «сейчас».
    client.post(f"/api/production/work/{started.json()['session']['id']}/submit", json={"good_quantity": "4"})
    app.dependency_overrides[authenticated_principal] = master

    item = fresh(client, task["id"])["operations"][0]["shares"][0]
    assert (item["planned_start"][:16], item["planned_finish"][:16], item["finished_at"]) == ("2026-09-22T09:00", "2026-09-22T10:26", None)


def test_started_share_is_not_moved_by_the_master(production_api):
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    task = launch_from_card(client, "started-v1", "591K.3", "КБ1", 10, [("", "Подготовка", 315, 0)]).json()["task"]
    task = share(client, task, 1, operator_id, "2026-09-22T09:00:00+03:00")["task"]
    master = app.dependency_overrides[authenticated_principal]
    as_operator(operator_id)
    client.post(f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/work/start", json={})
    app.dependency_overrides[authenticated_principal] = master
    task = fresh(client, task["id"])

    refused = client.post(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/share",
        json={"version": task["version"], "operator_id": str(operator_id), "requested_start": "2026-09-23T09:00:00+03:00"},
    )

    assert refused.status_code == 422, refused.text
    assert "время идёт по факту" in refused.json()["detail"]


def test_split_turns_a_crew_into_pieces_and_takes_from_the_latest_finish():
    base = dict(position=1, code="OP", stage="Обжим", description="", seconds_per_unit=Decimal(1), setup_seconds=Decimal(0), norm_seconds=10)
    first, second, third = (UUID(int=value) for value in (1, 2, 3))
    crew = ProductionOperation(id=UUID(int=9), operator_ids=(first, second), planned_start=at(22, 9), planned_finish=at(22, 10), **base)

    split = split_shares(crew, 10, third, 4)

    assert [(item.operator_id, item.quantity) for item in split.others] == [(first, 3), (second, 3)]
    assert split.resized == {first, second}
    pieces = ProductionOperation(id=UUID(int=9), shares=(
        OperationShare(first, 5, at(22, 9), at(22, 10)),
        OperationShare(second, 5, at(23, 9), at(23, 10)),
    ), **base)
    taken = split_shares(pieces, 10, third, 7)
    # Сначала у второго (заканчивает позже) — все 5, остальные 2 — у первого.
    assert [(item.operator_id, item.quantity) for item in taken.others] == [(first, 3)]
    assert taken.dropped == {second}
    # У того, кто уже взял работу, штуки не забираются.
    started = ProductionOperation(id=UUID(int=9), shares=(
        OperationShare(first, 5, at(22, 9), at(22, 10), started_at=at(22, 9)),
        OperationShare(second, 5, at(23, 9), at(23, 10)),
    ), **base)
    assert [(item.operator_id, item.quantity) for item in split_shares(started, 10, third, 5).others] == [(first, 5)]
    with pytest.raises(ProductionValidationError, match="у начатых"):
        split_shares(started, 10, third, 7)


def test_stage_name_drops_the_code_prefix():
    from app.modules.production.launchable import stage_name

    assert stage_name("CUT_WIRE+1crimp | Автомат резка + опрессовка") == "Автомат резка + опрессовка"
    assert stage_name("cutWireStrip") == "Резка с разделкой"
    assert stage_name("Резка | по месту") == "Резка | по месту"


def test_next_stage_of_the_same_assembly_follows_the_fact(production_api):
    # «Позже — сдвигаются, в том числе следующие операции той же КБ»; работа, поставленная мастером на другой день, стоит.
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    with app.state.production_uow_factory() as uow:
        uow.session.add(IdentityUserRow(
            id=SECOND, login="second", email="second@example.test", name="Второй", department="Монтаж",
            role="staff", site="ЦАР", active=True, shop_worker=True,
        ))
        uow.commit()
    task = launch_from_card(client, "chain-v1", "591K.3", "КБ1", 10, [("", "Резка", 315, 0), ("", "Монтаж", 315, 0)]).json()["task"]
    later = launch_from_card(client, "chain-later-v1", "591K.3", "КБ2", 10, [("", "Резка", 315, 0)]).json()["task"]
    task = share(client, task, 1, operator_id, "2026-09-22T09:00:00+03:00")["task"]
    task = share(client, task, 2, SECOND, "2026-09-22T10:00:00+03:00")["task"]
    share(client, later, 1, operator_id, "2026-09-23T09:00:00+03:00")
    master = app.dependency_overrides[authenticated_principal]
    as_operator(operator_id)
    started = client.post(f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/work/start", json={})
    session = started.json()["session"]["id"]

    # Сдал половину к 9:40: остальное до 10:10 — второй этап у другого монтажника сдвигается.
    app.dependency_overrides[moscow_now] = lambda: at(22, 9, 40)
    client.post(f"/api/production/work/{session}/submit", json={"good_quantity": "5"})
    app.dependency_overrides[authenticated_principal] = master
    assert window(fresh(client, task["id"]), 2, SECOND) == ("2026-09-22T10:10", "2026-09-22T11:10")

    # Сдал остальное в 10:05 — раньше нового конца: второй этап подтягивается.
    as_operator(operator_id)
    again = client.post(f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/work/start", json={})
    app.dependency_overrides[moscow_now] = lambda: at(22, 10, 5)
    client.post(f"/api/production/work/{again.json()['session']['id']}/submit", json={"good_quantity": "5"})
    app.dependency_overrides[authenticated_principal] = master
    assert window(fresh(client, task["id"]), 2, SECOND) == ("2026-09-22T10:05", "2026-09-22T11:05")
    assert window(fresh(client, later["id"]), 1, operator_id) == ("2026-09-23T09:00", "2026-09-23T10:00")


def add_second():
    with app.state.production_uow_factory() as uow:
        uow.session.add(IdentityUserRow(
            id=SECOND, login="second", email="second@example.test", name="Второй", department="Монтаж",
            role="staff", site="ЦАР", active=True, shop_worker=True,
        ))
        uow.commit()


def post_share(client, task, operator_id, **body):
    operation = task["operations"][0]
    return client.post(
        f"/api/production/tasks/{task['id']}/operations/{operation['id']}/share",
        json={"version": task["version"], "operator_id": str(operator_id), **body},
    )


def test_installer_who_took_the_work_keeps_his_pieces(production_api):
    # Проверка PR: мастер мог отдать другому все штуки и снять того, кто уже работает, — выработку тогда не сдать.
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    add_second()
    task = launch_from_card(client, "keep-v1", "591K.3", "КБ1", 10, [("OP", "Обжим", 315, 0)]).json()["task"]
    task = share(client, task, 1, operator_id, "2026-09-22T09:00:00+03:00", quantity=6)["task"]
    task = share(client, task, 1, SECOND, "2026-09-22T10:00:00+03:00", quantity=4)["task"]
    master = app.dependency_overrides[authenticated_principal]
    as_operator(operator_id)
    started = client.post(f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/work/start", json={})
    assert started.status_code == 200, started.text
    app.dependency_overrides[authenticated_principal] = master
    task = fresh(client, task["id"])

    taken = post_share(client, task, SECOND, quantity=10, requested_start="2026-09-22T10:00:00+03:00")
    removed = post_share(client, task, operator_id, remove=True)

    assert taken.status_code == 422 and "у начатых" in taken.json()["detail"]
    assert removed.status_code == 422 and "сейчас работает" in removed.json()["detail"]
    # На паузе работа ещё открыта; «Отменить начало» — снять можно.
    as_operator(operator_id)
    assert client.post(f"/api/production/work/{started.json()['session']['id']}/pause", json={}).status_code == 200
    app.dependency_overrides[authenticated_principal] = master
    assert post_share(client, fresh(client, task["id"]), operator_id, remove=True).status_code == 422
    as_operator(operator_id)
    assert client.post(f"/api/production/work/{started.json()['session']['id']}/cancel", json={}).status_code == 200
    app.dependency_overrides[authenticated_principal] = master
    task = fresh(client, task["id"])
    # Отменил начало, ничего не сдав, — «взял» снят.
    assert task["operations"][0]["shares"][0]["started_at"] is None
    after_pause = post_share(client, task, operator_id, remove=True)
    assert after_pause.status_code == 200, after_pause.text


def test_removing_from_a_crew_stretches_the_window_of_the_rest(production_api):
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    add_second()
    task = launch_from_card(client, "crew-v1", "591K.3", "КБ1", 10, [("OP", "Обжим", 630, 0)]).json()["task"]
    other = launch_from_card(client, "crew-other-v1", "591K.3", "КБ2", 10, [("OP", "Обжим", 315, 0)]).json()["task"]
    crew = client.post(
        f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/assign",
        json={"version": task["version"], "operator_ids": [str(operator_id), str(SECOND)], "requested_start": "2026-09-22T09:00:00+03:00"},
    )
    assert crew.status_code == 200, crew.text
    task = crew.json()["task"]
    # Вдвоём: 10 × 630 с / 2 = 52,5 мин чистых → час на часах.
    assert window(task, 1, operator_id) == ("2026-09-22T09:00", "2026-09-22T10:00")
    share(client, other, 1, operator_id, "2026-09-22T10:00:00+03:00")

    removed = post_share(client, task, SECOND, remove=True)

    assert removed.status_code == 200, removed.text
    # Один: 105 мин чистых → 2 ч на часах; его следующая работа сдвинута за новый конец.
    assert window(removed.json()["task"], 1, operator_id) == ("2026-09-22T09:00", "2026-09-22T11:00")
    assert window(fresh(client, other["id"]), 1, operator_id) == ("2026-09-22T11:00", "2026-09-22T12:00")
    assert any(line.startswith("Сдвинуто: ") for line in removed.json()["warnings"])


def test_new_installer_joins_instead_of_replacing(production_api):
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    add_second()
    task = launch_from_card(client, "join-v1", "591K.3", "КБ1", 10, [("OP", "Обжим", 315, 0)]).json()["task"]
    task = share(client, task, 1, operator_id, "2026-09-22T09:00:00+03:00")["task"]

    # Штуки не указаны — поровну с уже назначенным.
    joined = share(client, task, 1, SECOND, "2026-09-22T09:00:00+03:00")
    assert [(item["operator_id"], item["quantity"]) for item in joined["task"]["operations"][0]["shares"]] == [(str(operator_id), 5), (str(SECOND), 5)]

    # Мастер явно дал второму всё — первый снят, и это сказано.
    replaced = share(client, joined["task"], 1, SECOND, "2026-09-22T09:00:00+03:00", quantity=10)
    assert [item["quantity"] for item in replaced["task"]["operations"][0]["shares"]] == [10]
    assert any(line.startswith("Снят(а) с операции: ") for line in replaced["warnings"])


def test_auto_plan_after_an_assigned_stage(production_api):
    # Проверка PR: «Автоплан» падал с 500, когда у задания уже был назначенный этап (сроки из SQLite без пояса).
    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    client.put(f"/api/production/skills/{operator_id}", json={"skills": [
        {"operation_code": "OP-1", "allowed": True, "stars": 5, "coefficient": "1"},
        {"operation_code": "OP-2", "allowed": True, "stars": 5, "coefficient": "1"},
    ]})
    task = launch_from_card(client, "auto-aware-v1", "708К.2", "КБ1", 10, [("OP-1", "Резка", 60, 0), ("OP-2", "Сборка", 120, 0)]).json()["task"]
    task = share(client, task, 1, operator_id, "2026-09-22T09:00:00+03:00")["task"]

    preview = client.post(f"/api/production/tasks/{task['id']}/schedule", json={"version": task["version"], "replan": False, "max_crew": 1})

    assert preview.status_code == 200, preview.text
    assert preview.json()["variants"][0]["operations"][0]["start"][:16] == "2026-09-22T09:00"


def test_plan_failure_never_loses_the_output(production_api, monkeypatch):
    from app.modules.production import application

    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    task = launch_from_card(client, "safe-v1", "591K.3", "КБ1", 10, [("", "Подготовка", 315, 0)]).json()["task"]
    task = share(client, task, 1, operator_id, "2026-09-22T09:00:00+03:00")["task"]
    master = app.dependency_overrides[authenticated_principal]
    monkeypatch.setattr(application, "_record_fact", lambda *args, **kwargs: None)
    as_operator(operator_id)
    started = client.post(f"/api/production/tasks/{task['id']}/operations/{task['operations'][0]['id']}/work/start", json={})

    def broken(*args, **kwargs):
        raise RuntimeError("план не сдвинуть")

    monkeypatch.setattr(application, "_record_fact", broken)
    submitted = client.post(f"/api/production/work/{started.json()['session']['id']}/submit", json={"good_quantity": "10"})

    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["output_id"]
    # Выработка сдана, план остался прежним.
    app.dependency_overrides[authenticated_principal] = master
    item = fresh(client, task["id"])["operations"][0]["shares"][0]
    assert (item["planned_start"][:16], item["planned_finish"][:16], item["finished_at"]) == ("2026-09-22T09:00", "2026-09-22T10:00", None)


def test_failed_replan_is_rolled_back_but_the_start_stays(production_api, monkeypatch):
    # План пишется в точке сохранения: сбой после записи откатывает только план.
    from app.modules.production import application

    client, operator_id = production_api
    app.dependency_overrides[moscow_now] = lambda: at(22, 9)
    first = launch_from_card(client, "rollback-a-v1", "591K.3", "КБ1", 10, [("", "Подготовка", 315, 0)]).json()["task"]
    second = launch_from_card(client, "rollback-b-v1", "591K.3", "КБ2", 10, [("", "Подготовка", 315, 0)]).json()["task"]
    first = share(client, first, 1, operator_id, "2026-09-22T09:00:00+03:00")["task"]
    share(client, second, 1, operator_id, "2026-09-22T10:00:00+03:00")
    master = app.dependency_overrides[authenticated_principal]
    original = application._save_moves

    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("сбой после записи")

    monkeypatch.setattr(application, "_save_moves", broken)
    as_operator(operator_id)
    app.dependency_overrides[moscow_now] = lambda: at(22, 9, 30)
    started = client.post(f"/api/production/tasks/{first['id']}/operations/{first['operations'][0]['id']}/work/start", json={})
    app.dependency_overrides[authenticated_principal] = master

    assert started.status_code == 200, started.text
    kept = fresh(client, first["id"])
    assert kept["version"] == first["version"]
    assert kept["operations"][0]["shares"][0]["started_at"] is None
    assert window(fresh(client, second["id"]), 1, operator_id) == ("2026-09-22T10:00", "2026-09-22T11:00")
