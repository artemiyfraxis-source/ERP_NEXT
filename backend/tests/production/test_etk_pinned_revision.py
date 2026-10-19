"""ЭТК задания идёт по ревизии, с которой его запустили (T4, 19.10): карта получила новую ревизию — задание видит
документ своей ревизии и уведомление; новый запуск берёт последнюю; без закреплённой ревизии — текущая ЭТК с пояснением."""

from datetime import datetime
from uuid import UUID, uuid4

from app.main import app
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.techcards.project_cards import TechcardRevision
from tests.production.test_api import issue_card, production_api  # noqa: F401
from tests.production.test_job_flow_api import OPS, _launch

NOW = datetime(2026, 9, 22, 9, 0)


def _record_revision(card) -> None:
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.add_revision(TechcardRevision(
            id=uuid4(), project_card_id=card.id, revision=card.revision, reason="Тест", actor_name="Технолог",
            occurred_at=NOW, document=card.document,
        ))
        uow.commit()


def _journal_current(card_id: str) -> None:
    with app.state.techcards_uow_factory() as uow:
        card = uow.techcards.get(UUID(card_id))
    _record_revision(card)


def _new_revision(card_id: str, marker: str) -> None:
    """Карта получает следующую ревизию с другим документом (как при перевыпуске)."""
    with app.state.techcards_uow_factory() as uow:
        card = uow.techcards.get(UUID(card_id))
        updated = card.rebuild({**card.document, "marker": marker}, datetime(2026, 9, 23, 9, 0))
        uow.techcards.save(updated)
        uow.commit()
    _record_revision(updated)


def _etk(client, task):
    url = f"/api/production/my/tasks/{task['id']}/operations/{task['operations'][0]['id']}/etk"
    response = client.get(url)
    assert response.status_code == 200, response.text
    return response.json()


def test_task_keeps_the_document_of_its_launch_revision_and_shows_the_notice(production_api):  # noqa: F811
    client, _ = production_api
    task = _launch(client)
    _journal_current(task["techcard_id"])
    _new_revision(task["techcard_id"], "rev-2")

    body = _etk(client, task)

    assert task["techcard_revision"] == 1
    assert "marker" not in body["card"]["document"]
    assert body["revision"] == {"pinned": 1, "current": 2, "newer": True, "fallback": None}
    flow = client.get(f"/api/production/tasks/{task['id']}/flow").json()
    # Правка текста: ревизия новее, а нормы те же — «нормы устарели» не ставится, уведомление о ревизии остаётся.
    assert flow["norms"]["outdated"] is False and flow["norms"]["revision"] == 1 and flow["norms"]["current_revision"] == 2


def test_a_new_launch_takes_the_latest_revision(production_api):  # noqa: F811
    client, _ = production_api
    first = _launch(client)
    _journal_current(first["techcard_id"])
    _new_revision(first["techcard_id"], "rev-2")

    second = client.post("/api/production/tasks", headers={"Idempotency-Key": "pin-2"},
                         json={"techcard_id": first["techcard_id"], "quantity": 5})
    assert second.status_code == 200, second.text
    task = second.json()["task"]

    body = _etk(client, task)
    assert task["techcard_revision"] == 2
    assert body["card"]["document"]["marker"] == "rev-2"
    assert body["revision"]["newer"] is False and body["revision"]["fallback"] is None


def test_missing_revision_row_falls_back_to_the_current_card(production_api):  # noqa: F811
    client, _ = production_api
    task = _launch(client)
    _new_revision(task["techcard_id"], "rev-2")  # строки ревизии 1 нет в журнале

    body = _etk(client, task)

    assert body["card"]["document"]["marker"] == "rev-2"
    assert body["revision"]["pinned"] == 1 and body["revision"]["newer"] is False
    assert "не найдена" in body["revision"]["fallback"]


def test_task_without_a_pinned_revision_falls_back_to_the_current_card(production_api):  # noqa: F811
    client, _ = production_api
    task = _launch(client)
    with app.state.production_uow_factory() as uow:
        uow.production.session.get(ProductionTaskRow, UUID(task["id"])).techcard_revision = None
        uow.commit()

    body = _etk(client, task)

    assert body["revision"]["pinned"] is None and "до закрепления" in body["revision"]["fallback"]


def _with_time_rows(card_id: str, *, version: int, with_setup: bool, factor: int = 1) -> None:
    """Новая ревизия карты: операции с ЭТК-нормами (строки времени); версия 3 держит подготовку внутри нормы."""
    with app.state.techcards_uow_factory() as uow:
        card = uow.techcards.get(UUID(card_id))
        operations = [
            {**operation, "time": [{"name": "Результат", "norm": str((t_op * factor * 10 + (t_prep if with_setup else 0)) / 60)}]}
            for operation, (_code, _stage, t_op, t_prep) in zip(card.document["operations"], OPS)
        ]
        updated = card.rebuild({**card.document, "operations": operations, "schema_version": version},
                               datetime(2026, 9, 23, 9, 0))
        uow.techcards.save(updated)
        uow.commit()
    _record_revision(updated)


def _outdated(client, task) -> dict:
    return client.get(f"/api/production/tasks/{task['id']}/flow").json()["norms"]


def test_v3_card_that_only_lost_the_setup_does_not_set_the_norms_flag(production_api):  # noqa: F811
    client, _ = production_api
    card = issue_card("591K.3", "КБ1", OPS)
    _with_time_rows(str(card.id), version=3, with_setup=True)
    task = client.post("/api/production/tasks", headers={"Idempotency-Key": "v3"},
                       json={"techcard_id": str(card.id), "quantity": 10}).json()["task"]
    assert [item["seconds_per_unit"] for item in task["operations"]] == ["60", "30", "90"]  # подготовка вычтена

    _with_time_rows(str(card.id), version=4, with_setup=False)

    norms = _outdated(client, task)
    assert norms["current_revision"] > norms["revision"] and norms["outdated"] is False


def test_a_changed_operation_time_sets_the_norms_flag(production_api):  # noqa: F811
    client, _ = production_api
    card = issue_card("591K.3", "КБ1", OPS)
    _with_time_rows(str(card.id), version=4, with_setup=False)
    task = client.post("/api/production/tasks", headers={"Idempotency-Key": "chg"},
                       json={"techcard_id": str(card.id), "quantity": 10}).json()["task"]
    assert _outdated(client, task)["outdated"] is False

    _with_time_rows(str(card.id), version=4, with_setup=False, factor=2)

    assert _outdated(client, task)["outdated"] is True
