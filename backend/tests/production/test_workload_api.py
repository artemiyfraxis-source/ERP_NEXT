"""«Загрузка» через HTTP: один запрос по выданным ЭТК — часы по проектам и КБ, источник «сделано», площадка по
заданию, мощность по календарю цеха, прогноз и недели. Ничего не пишет."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select

from app.main import app
from app.modules.production.application import Absence
from app.modules.production.domain import WorkShift
from app.modules.production.plan import PlanProjectSnapshot
from app.modules.shipping.domain import FinishedGood, MoveContext
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.shared.persistence import AuditEventRow

from tests.production.test_api import issue_card, production_api  # noqa: F401  (fixture)

NOW = datetime(2026, 9, 22, 9, 0)  # вторник
OPS = [("PRS_TERMINAL_hand", "Опрессовка терминалов", 30, 600), ("SDR_DB", "Пайка разъёма", 10, 0)]  # 40 с на изделие


def _seed(client):
    for index in ("КБ1", "КБ2", "КБ3"):
        issue_card("801К.1", index, OPS)
    issue_card("802К.1", "КБ1", OPS)  # без набора: тираж — партия ЭТК (10)
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(Kitset(
            project="801К.1", customer="Заказчик А", calc_source="", updated_at=NOW, updated_by="Технолог", created_at=NOW,
            assemblies=(KitAssembly("КБ1", "Жгут", run=Decimal(360)), KitAssembly("КБ2", "Жгут", run=Decimal(360)),
                        KitAssembly("КБ3", "Жгут", run=Decimal(90))),
        ))
        uow.commit()
    with app.state.shipping_uow_factory() as uow:
        good = FinishedGood.create(project="801К.1", item_index="КБ2", customer="Заказчик А", article="", name="Жгут",
                                   ordered=Decimal(360), unit="шт")
        uow.goods.add(good.accept(Decimal(90), date(2026, 9, 21), "ГП.26.0000001"), MoveContext(actor="Кладовщик", occurred_at=NOW))
        uow.commit()
    with app.state.production_uow_factory() as uow:
        uow.plan_projects.replace((PlanProjectSnapshot(
            "801К.1", customer="Заказчик А", deadline=date(2026, 9, 23), imported_at=NOW),))
        operator = next(item for item in uow.operators.list() if item.shop_worker)
        # Пятница 25.09 — праздник, в понедельник 28.09 монтажник в отпуске.
        uow.calendar.replace(frozenset({date(2026, 9, 25)}), (
            Absence(uuid4(), operator.id, date(2026, 9, 28), date(2026, 9, 28), "Отпуск"),), WorkShift())
        uow.commit()
    cards = {item["index"]: item for item in client.get("/api/production/launchable").json()["items"] if item["project"] == "801К.1"}
    task = client.post("/api/production/tasks", headers={"Idempotency-Key": "workload-kb3"},
                       json={"techcard_id": cards["КБ3"]["techcard_id"], "quantity": 90}).json()["task"]
    handed = client.post(f"/api/production/tasks/{task['id']}/handover", json={"version": task["version"], "site": "ЖУК"})
    assert handed.status_code == 200, handed.text


def test_workload_counts_hours_done_sources_sites_capacity_and_forecast(production_api):  # noqa: F811
    client, _operator = production_api
    _seed(client)
    with app.state.production_uow_factory() as uow:
        audit_before = uow.session.scalar(select(func.count()).select_from(AuditEventRow))

    response = client.get("/api/production/workload")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["today"] == "2026-09-22" and body["shift_hours"] == 7 and body["people"] == {"ЦАР": 1, "ЖУК": 0}
    rows = {row["project"]: row for row in body["projects"]}
    assert list(rows) == ["801К.1", "802К.1"]  # по сроку, без срока — в конце
    first = rows["801К.1"]
    assert first["customer"] == "Заказчик А" and first["deadline"] == "2026-09-23"
    kbs = {kb["index"]: kb for kb in first["kbs"]}
    # КБ1: 360 × 40 с = 4 ч, ничего не сделано, заданий нет.
    assert kbs["КБ1"]["hours"] == 4 and kbs["КБ1"]["done_source"] == "" and kbs["КБ1"]["site"] == "не распределено"
    assert kbs["КБ1"]["by_kind"] == {"Обжим": 3, "Пайка": 1}
    # КБ2: склад принял 90 → осталось 270 = 3 ч.
    assert (kbs["КБ2"]["done"], kbs["КБ2"]["done_source"], kbs["КБ2"]["remaining"], kbs["КБ2"]["hours"]) == (90, "принято складом", 270, 3)
    # КБ3: задание отдано в ЖУК → площадка ЖУК; там никого — мощности нет, к сроку не успеть.
    assert kbs["КБ3"]["site"] == "ЖУК" and kbs["КБ3"]["site_note"] == "задание отдано в ЖУК" and kbs["КБ3"]["hours"] == 1
    assert first["hours"] == 8 and first["by_site"] == {"не распределено": 7, "ЖУК": 1}
    assert first["ready"] is None and first["status"] == "не успевает"
    # 802К.1: 10 × 40 с — после 7 ч «не распределено» 801К, во вторник ЦАР свободен ещё на 0 ч → среда.
    assert rows["802К.1"]["hours"] == 0.1 and rows["802К.1"]["status"] == "без срока" and rows["802К.1"]["ready"] == "2026-09-23"
    # Неделя 21–27.09: вт, ср, чт по 7 ч (пт — праздник); следующая — без понедельника (отпуск).
    week = body["weeks"][0]
    assert (week["start"], week["end"]) == ("2026-09-21", "2026-09-27")
    assert week["sites"]["ЦАР"]["capacity"] == 21 and week["sites"]["Весь цех"]["capacity"] == 21
    assert week["sites"]["ЦАР"]["planned"] == 7.1
    assert body["weeks"][1]["sites"]["ЦАР"]["capacity"] == 28
    # Нужно к срокам: 8 ч 801К во вторник и среду; ЖУК без мощности — узкое место.
    assert week["sites"]["Весь цех"]["demand"] == 8 and week["sites"]["ЖУК"]["demand"] == 1
    assert any(item["site"] == "ЖУК" and item["kinds"][0][0] == "Обжим" for item in body["bottlenecks"])
    assert body["no_run"] == []
    with app.state.production_uow_factory() as uow:
        assert uow.session.scalar(select(func.count()).select_from(AuditEventRow)) == audit_before


def test_workload_without_issued_techcards_is_empty(production_api):  # noqa: F811
    client, _operator = production_api

    body = client.get("/api/production/workload").json()

    assert body["projects"] == [] and body["bottlenecks"] == [] and len(body["weeks"]) == 8
