"""ЭТК задания идёт по ревизии, с которой его запустили (T4, 19.10): карта получила новую ревизию — задание видит
документ своей ревизии и уведомление; новый запуск берёт последнюю; без закреплённой ревизии — текущая ЭТК с пояснением."""

from datetime import datetime
from uuid import UUID, uuid4

from app.main import app
from app.modules.production.infrastructure import ProductionTaskRow
from app.modules.techcards.project_cards import TechcardRevision
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_job_flow_api import _launch

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
    assert flow["norms"]["outdated"] is True and flow["norms"]["revision"] == 1


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
