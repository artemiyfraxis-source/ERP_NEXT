"""Версия шаблона операции в задании монтажника (владелец, 17.10.2026): «Мои задания» показывают, откуда сейчас карточки
операций ЭТК (по шаблону / правлена в ТК), а первое «▶ Начать» записывает их один раз — повторное начало и продолжение
запись не меняют. Выданная ЭТК хранится как выдана (владелец 19.10): правка шаблона после начала карточку не меняет и
расхождения не даёт — до новой ревизии у карты нет другой версии."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from app.main import app
from app.modules.production.infrastructure import ProductionOperationRow
from app.modules.techcards.etk_builder import build_etk
from app.modules.techcards.etk_live import EDITED, TEMPLATE, stamp_templates
from app.modules.techcards.operation_cards import OperationRecordEdit
from app.modules.techcards.project_cards import ProjectTechcard
from tests.production.test_api import production_api  # noqa: F401 — фикстура API цеха
from tests.production.test_my_readiness import _as_operator, _launch
from tests.production.test_network_execution import _op
from tests.techcards.test_etk_live import _config, _cut_template, _parsed

PROJECT = "591K.3"
BUILT = datetime(2026, 10, 15, 9, 0)
EDITED_AT = datetime(2026, 10, 16, 14, 30)


def _seed_template_and_etk() -> None:
    card = _parsed(_cut_template())
    document = stamp_templates(build_etk(_config(PROJECT), [card], operation_library_commit="lib"), [card])
    techcard = ProjectTechcard.create(
        project=PROJECT, item_index="КБ1", product_name="Жгут", source_calculation="", deadline=None, now=BUILT,
    ).rebuild(document, BUILT).issue(BUILT)
    with app.state.techcards_uow_factory() as uow:
        uow.operation_cards.load((card,), imported_at=BUILT)
        uow.techcards.save(techcard)
        uow.commit()


def _edit_template(at: datetime) -> None:
    card = _parsed(_cut_template())
    edit = OperationRecordEdit.from_json({
        "name": card.name, "safety": "", "defects": "", "revision": "",
        "steps": [{"number": "1", "kind": "Операция", "text": "Резать {{WIRE_NAME}}", "tolerance": "-"}], "tooling": [],
    })
    with app.state.techcards_uow_factory() as uow:
        assert uow.operation_cards.edit(card.code, edit, changed_at=at, changed_by="Технолог") >= 1
        uow.commit()


def _my_operation(client) -> dict:
    rows = client.get("/api/production/my").json()["tasks"]
    assert len(rows) == 1
    return rows[0]["operations"][0]


def _stored(operation_id: str):
    with app.state.production_uow_factory() as uow:
        return uow.work.session.get(ProductionOperationRow, UUID(operation_id)).card_versions


def test_the_first_start_records_the_card_versions_once_and_a_later_template_edit_shows_against_them(production_api):  # noqa: F811
    client, operator_id = production_api
    _seed_template_and_etk()
    task = _launch(app.state.production_uow_factory, operator_id, "cv-1", (_op(1, "Резка"),), item=f"{PROJECT}.КБ1")
    operation_id = str(task.operations[0].id)
    _as_operator(operator_id)

    before = _my_operation(client)
    assert before["etk_cards"] == {"live": [{"position": 1, "source": TEMPLATE, "changed_at": ""}], "started": None}
    assert _stored(operation_id) is None

    started = client.post(f"/api/production/tasks/{task.id}/operations/{operation_id}/work/start", json={"inputs": []})
    assert started.status_code == 200, started.text
    recorded = [{"position": 1, "source": TEMPLATE, "changed_at": ""}]
    assert _stored(operation_id) == recorded

    # Технолог правит шаблон после начала: выданная ЭТК не меняется, записанное на начале остаётся прежним.
    _edit_template(EDITED_AT)
    session_id = started.json()["session"]["id"]
    assert client.post(f"/api/production/work/{session_id}/pause").status_code == 200
    assert client.post(f"/api/production/work/{session_id}/resume").status_code == 200
    after = _my_operation(client)
    assert after["etk_cards"]["live"] == recorded
    assert after["etk_cards"]["started"] == recorded

    # Повторное начало (отменили и начали снова) запись не меняет.
    assert client.post(f"/api/production/work/{session_id}/cancel").status_code == 200
    again = client.post(f"/api/production/tasks/{task.id}/operations/{operation_id}/work/start", json={"inputs": []})
    assert again.status_code == 200, again.text
    assert _stored(operation_id) == recorded
    assert _my_operation(client)["etk_cards"]["started"] == recorded


def test_the_repository_writes_the_versions_only_while_there_are_none(production_api):  # noqa: F811
    _client, operator_id = production_api
    task = _launch(app.state.production_uow_factory, operator_id, "cv-2", (_op(1, "Резка"),))
    operation_id = task.operations[0].id
    first = [{"position": 1, "source": EDITED, "changed_at": BUILT.isoformat()}]
    with app.state.production_uow_factory() as uow:
        assert uow.work.record_card_versions(operation_id, first) is True
        assert uow.work.record_card_versions(operation_id, [{"position": 1, "source": TEMPLATE, "changed_at": ""}]) is False
        uow.commit()
    with app.state.production_uow_factory() as uow:
        assert uow.work.card_versions([operation_id]) == {operation_id: first}
        # Сохранение задания (назначение, перепланирование) колонку не трогает.
        stored = uow.production.get(task.id)
        uow.production.add(stored)
        uow.commit()
    with app.state.production_uow_factory() as uow:
        assert uow.work.card_versions([operation_id]) == {operation_id: first}


def test_without_an_issued_etk_the_first_start_records_an_empty_list_and_the_fitter_sees_no_note(production_api):  # noqa: F811
    client, operator_id = production_api
    task = _launch(app.state.production_uow_factory, operator_id, "cv-3", (_op(1, "Резка"),), item="777.КБ1")
    operation_id = str(task.operations[0].id)
    _as_operator(operator_id)

    assert client.post(f"/api/production/tasks/{task.id}/operations/{operation_id}/work/start", json={"inputs": []}).status_code == 200

    assert _stored(operation_id) == []
    assert _my_operation(client)["etk_cards"] == {"live": [], "started": []}


def test_a_late_start_after_the_template_edit_records_the_stored_card_without_the_template_date(production_api):  # noqa: F811
    client, operator_id = production_api
    _seed_template_and_etk()
    _edit_template(EDITED_AT - timedelta(days=1))
    task = _launch(app.state.production_uow_factory, operator_id, "cv-4", (_op(1, "Резка"),), item=f"{PROJECT}.КБ1")
    operation_id = str(task.operations[0].id)
    _as_operator(operator_id)

    assert client.post(f"/api/production/tasks/{task.id}/operations/{operation_id}/work/start", json={"inputs": []}).status_code == 200

    expected = [{"position": 1, "source": TEMPLATE, "changed_at": ""}]
    assert _stored(operation_id) == expected
    assert _my_operation(client)["etk_cards"] == {"live": expected, "started": expected}
