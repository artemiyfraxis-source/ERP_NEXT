"""«Готово к запуску» (идея №3, 29.10): КБ с выданной ЭТК и набранным комплектом, тираж к запуску, площадка, срок;
запуск в один клик — тот же, что «Создать задание» с доски; повтор не создаёт второе задание; права."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from app.main import app
from app.modules.fulfillment.domain import FulfillmentLine, FulfillmentRequest, FulfillmentStatus
from app.modules.production.board import BOARD_KEY_PREFIX
from app.modules.production.launch_ready import ready_rows, suggested_quantity
from app.modules.production.launchable import ReadyCard, LaunchRoute
from app.modules.production.plan import PlanProjectSnapshot
from app.modules.production.workload import Done
from app.modules.shipping.domain import FinishedGood, MoveContext
from app.modules.techcards.kitsets import KitAssembly, Kitset
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.persistence import AuditEventRow
from tests.factories import Actor

from tests.production.test_api import issue_card, production_api  # noqa: F401  (fixture)

NOW = datetime(2026, 9, 22, 9, 0)
OPS = [("PRS_TERMINAL_hand", "Опрессовка терминалов", 30, 600), ("SDR_DB", "Пайка разъёма", 10, 0)]
_numbers = iter(range(1, 1000))


def _kit_request(project: str, index: str, status: FulfillmentStatus) -> None:
    number = f"REQ.26.{next(_numbers):07d}"
    request = FulfillmentRequest.create(
        number=number, source_idempotency_key=number, source_fingerprint="f", requester="Мастер",
        purpose=f"{project} · {index} · Жгут", deadline=date(2026, 10, 20), created_at=NOW,
        lines=(FulfillmentLine.create(article="КД-1", name="Разъём", unit="шт", requirement=Decimal("1")),),
    )
    with app.state.fulfillment_uow_factory() as uow:
        uow.fulfillment.add(replace(request, status=status))
        uow.commit()


def _seed(client):
    """901К.1: КБ1 (комплект «Укомплектован»), КБ2 (заявка «Новая» — ждёт), КБ3 (запущено 4 из 10, отдано в ЖУК,
    «КБ Выдана»), КБ4 (весь тираж запущен), КБ5 (склад принял 7 мимо заданий). 902К.1 КБ1 — без заданий в проекте."""
    for index in ("КБ1", "КБ2", "КБ3", "КБ4", "КБ5"):
        issue_card("901К.1", index, OPS)
    issue_card("902К.1", "КБ1", OPS)
    with app.state.techcards_uow_factory() as uow:
        uow.kitsets.save(Kitset(
            project="901К.1", customer="Заказчик Б", calc_source="", updated_at=NOW, updated_by="Технолог", created_at=NOW,
            assemblies=tuple(KitAssembly(index, "Жгут", run=Decimal(10)) for index in ("КБ1", "КБ2", "КБ3", "КБ4", "КБ5")),
        ))
        uow.commit()
    _kit_request("901К.1", "КБ1", FulfillmentStatus.COMPLETE)
    _kit_request("901К.1", "КБ2", FulfillmentStatus.NEW)
    _kit_request("901К.1", "КБ3", FulfillmentStatus.ISSUED)
    _kit_request("901К.1", "КБ4", FulfillmentStatus.COMPLETE)
    _kit_request("901К.1", "КБ5", FulfillmentStatus.READY)
    _kit_request("902К.1", "КБ1", FulfillmentStatus.DONE)
    with app.state.shipping_uow_factory() as uow:
        good = FinishedGood.create(project="901К.1", item_index="КБ5", customer="Заказчик Б", article="", name="Жгут",
                                   ordered=Decimal(10), unit="шт")
        uow.goods.add(good.accept(Decimal(7), date(2026, 9, 21), "ГП.26.0000001"), MoveContext(actor="Кладовщик", occurred_at=NOW))
        uow.commit()
    with app.state.production_uow_factory() as uow:
        uow.plan_projects.replace((PlanProjectSnapshot("901К.1", customer="Заказчик Б", deadline=date(2026, 10, 5), imported_at=NOW),))
        uow.commit()
    cards = {(item["project"], item["index"]): item for item in client.get("/api/production/launchable").json()["items"]}
    partial = client.post("/api/production/tasks", headers={"Idempotency-Key": "ready-kb3"},
                          json={"techcard_id": cards[("901К.1", "КБ3")]["techcard_id"], "quantity": 4}).json()["task"]
    handed = client.post(f"/api/production/tasks/{partial['id']}/handover", json={"version": partial["version"], "site": "ЖУК"})
    assert handed.status_code == 200, handed.text
    full = client.post("/api/production/tasks", headers={"Idempotency-Key": "ready-kb4"},
                       json={"techcard_id": cards[("901К.1", "КБ4")]["techcard_id"], "quantity": 10})
    assert full.status_code == 200, full.text
    return cards


def _rows(client) -> dict:
    response = client.get("/api/production/launch-ready")
    assert response.status_code == 200, response.text
    return response.json()


def _item(row: dict, **extra) -> dict:
    return {"techcard_id": row["techcard_id"], "techcard_version": row["techcard_version"], "launched": row["launched"],
            "quantity": row["quantity"], **extra}


def test_list_shows_issued_cards_with_picked_kit_run_left_site_and_deadline(production_api):  # noqa: F811
    client, _operator = production_api
    _seed(client)

    body = _rows(client)

    rows = {(row["project"], row["index"]): row for row in body["items"]}
    # КБ2 ждёт комплект, КБ4 запущена целиком — их нет; по сроку проекта: 901К.1 (05.10) раньше 902К.1 (без срока).
    assert list(rows) == [("901К.1", "КБ1"), ("901К.1", "КБ3"), ("901К.1", "КБ5"), ("902К.1", "КБ1")]
    assert body["waiting_kit"] == 1 and body["no_run"] == []
    first = rows[("901К.1", "КБ1")]
    assert (first["run"], first["launched"], first["quantity"], first["kit"]) == (10, 0, 10, "Укомплектован")
    assert first["deadline"] == "2026-10-05" and first["customer"] == "Заказчик Б"
    # Площадка: заданий КБ нет — по другим КБ проекта (КБ3 отдана в ЖУК).
    assert first["site"] == "ЖУК" and first["site_note"].startswith("по другим КБ проекта")
    # КБ3: запущено 4 из 10 — к запуску остаток 6; задание КБ отдано в ЖУК.
    third = rows[("901К.1", "КБ3")]
    assert (third["launched"], third["quantity"], third["kit"], third["site"]) == (4, 6, "КБ Выдана", "ЖУК")
    # КБ5: склад принял 7 мимо заданий — к запуску 3.
    fifth = rows[("901К.1", "КБ5")]
    assert (fifth["done"], fifth["done_source"], fifth["quantity"]) == (7, "принято складом", 3)
    other = rows[("902К.1", "КБ1")]
    assert other["site"] == "не распределено" and other["deadline"] is None and other["kit"] == "Готово"
    # Операции, которые создаст запуск: этапы ЭТК с нормами.
    assert [(item["position"], item["stage"], item["code"], item["seconds_per_unit"], item["per_unit"])
            for item in first["operations"]] == [(1, "Опрессовка терминалов", "PRS_TERMINAL_hand", 30.0, 1),
                                                 (2, "Пайка разъёма", "SDR_DB", 10.0, 1)]


def test_one_click_creates_the_task_the_manual_launch_creates_and_the_row_disappears(production_api):  # noqa: F811
    client, _operator = production_api
    cards = _seed(client)
    row = next(item for item in _rows(client)["items"] if (item["project"], item["index"]) == ("901К.1", "КБ1"))

    response = client.post("/api/production/launch-ready/launch", json={"items": [_item(row)]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["refused"] == [] and len(body["created"]) == 1
    created = body["created"][0]
    assert (created["project"], created["item"], created["quantity"]) == ("901К.1", "КБ1", 10)
    task = next(item for item in client.get("/api/production/tasks").json()["tasks"] if item["id"] == created["id"])
    # Ручной запуск той же ЭТК (902К.1 КБ1 устроена так же) — те же этапы, нормы, срок техкарты, ревизия.
    manual = client.post("/api/production/tasks", headers={"Idempotency-Key": "manual-902"},
                         json={"techcard_id": cards[("902К.1", "КБ1")]["techcard_id"], "quantity": 10}).json()["task"]
    shape = lambda value: [(item["position"], item["code"], item["stage"], item["seconds_per_unit"], item["setup_seconds"],
                            [(edge["position"], edge["per_unit"]) for edge in item.get("inputs") or []])
                           for item in value["operations"]]
    assert shape(task) == shape(manual)
    assert (task["deadline"], task["techcard_revision"], task["launched_by"]) == (manual["deadline"], manual["techcard_revision"], "Мастер")
    assert task["number"].startswith("ПЗ") and task["number"] != manual["number"]
    with app.state.production_uow_factory() as uow:
        stored = uow.production.get(UUID(created["id"]))
        assert stored.source_idempotency_key == f"{BOARD_KEY_PREFIX}{row['techcard_id']}:1"
        audit = uow.session.scalars(select(AuditEventRow).where(AuditEventRow.aggregate_id == UUID(created["id"]))).all()
        assert [item.event_type for item in audit] == ["production.launched"]
    left = {(item["project"], item["index"]) for item in _rows(client)["items"]}
    assert ("901К.1", "КБ1") not in left


def test_double_click_launches_once(production_api):  # noqa: F811
    client, _operator = production_api
    _seed(client)
    row = next(item for item in _rows(client)["items"] if item["index"] == "КБ1" and item["project"] == "901К.1")

    first = client.post("/api/production/launch-ready/launch", json={"items": [_item(row)]})
    second = client.post("/api/production/launch-ready/launch", json={"items": [_item(row)]})

    assert first.status_code == 200 and len(first.json()["created"]) == 1
    assert second.status_code == 200 and second.json()["created"] == []
    assert "обновите" in second.json()["refused"][0]["reason"]
    tasks = [item for item in client.get("/api/production/tasks").json()["tasks"] if item["project"] == "901К.1" and item["item"] == "КБ1"]
    assert len(tasks) == 1


def test_batch_launches_each_row_and_reports_a_stale_one(production_api):  # noqa: F811
    client, _operator = production_api
    _seed(client)
    rows = {(item["project"], item["index"]): item for item in _rows(client)["items"]}

    response = client.post("/api/production/launch-ready/launch", json={"items": [
        _item(rows[("901К.1", "КБ1")]),
        _item(rows[("901К.1", "КБ3")]),
        _item(rows[("902К.1", "КБ1")], launched=3),  # мастер видел другое «уже запущено» — строка устарела
    ]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted((item["item"], item["quantity"]) for item in body["created"]) == [("КБ1", 10), ("КБ3", 6)]
    assert [item["techcard_id"] for item in body["refused"]] == [rows[("902К.1", "КБ1")]["techcard_id"]]
    # Запуск КБ3 — следующий номер запуска КБ, остаток тиража (6): по КБ запущено 10 из 10.
    left = {(item["project"], item["index"]) for item in _rows(client)["items"]}
    assert left == {("901К.1", "КБ5"), ("902К.1", "КБ1")}


def test_quantity_above_the_rest_is_refused(production_api):  # noqa: F811
    client, _operator = production_api
    _seed(client)
    row = next(item for item in _rows(client)["items"] if item["index"] == "КБ3")

    body = client.post("/api/production/launch-ready/launch", json={"items": [_item(row, quantity=7)]}).json()

    assert body["created"] == [] and "от 1 до 6" in body["refused"][0]["reason"]


def test_the_same_card_twice_in_one_batch_is_rejected(production_api):  # noqa: F811
    client, _operator = production_api
    _seed(client)
    row = _rows(client)["items"][0]

    response = client.post("/api/production/launch-ready/launch", json={"items": [_item(row), _item(row)]})

    assert response.status_code == 422 and "дважды" in response.json()["detail"]


def test_view_rights_see_the_list_but_cannot_launch(production_api):  # noqa: F811
    client, _operator = production_api
    _seed(client)
    row = _rows(client)["items"][0]
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Наблюдатель", department="Производство"), email="viewer@example.test",
        roles=frozenset({"foreman"}), permissions={"production": "view"},
    )

    assert client.get("/api/production/launch-ready").status_code == 200
    assert client.post("/api/production/launch-ready/launch", json={"items": [_item(row)]}).status_code == 403


def _card(launched=0, run="10") -> ReadyCard:
    return ReadyCard(
        techcard_id=UUID(int=1), version=1, revision=1, project="1К.1", index="КБ1", name="Жгут", customer="",
        run=run, deadline=None, route=LaunchRoute(()), document=None, launched=launched,
    )


def test_run_math_takes_the_larger_of_launched_and_made():
    assert suggested_quantity(_card(), Decimal(0)) == 10
    assert suggested_quantity(_card(launched=4), Decimal(2)) == 6
    assert suggested_quantity(_card(launched=4), Decimal(7)) == 3
    assert suggested_quantity(_card(launched=4), Decimal("6.5")) == 4  # дробное сделанное округляется вниз
    assert suggested_quantity(_card(), Decimal(12)) == 0
    assert suggested_quantity(_card(run=""), Decimal(0)) == 0


def test_rows_need_a_picked_kit_and_a_run():
    picked, waiting, no_run, made = _card(), replace(_card(), index="КБ2"), replace(_card(run=""), index="КБ3"), replace(_card(), index="КБ4")
    summary = ready_rows(
        [picked, waiting, no_run, made],
        {picked.key: "Укомплектован", no_run.key: "Укомплектован", made.key: "Готово"},
        {made.key: Done(Decimal(10), "отгружено")}, {}, {},
    )
    assert [row.card.index for row in summary.rows] == ["КБ1"]
    assert summary.waiting_kit == 1 and summary.no_run == ("1К.1 КБ3",)
