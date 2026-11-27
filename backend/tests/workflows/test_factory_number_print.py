"""Заводской номер изделия выдаётся при печати этикетки (владелец 27.11.2026, решение 4): хранится, не переиспользуется,
перепечатка берёт тот же; стенд ВАЦ находит изделие по номеру с этикетки."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import sqlite3
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.modules.production.application import FACTORY_NUMBERS_ISSUED_EVENT
from app.modules.production.domain import ProductionValidationError
from app.modules.production.infrastructure import ProductionSerialRow, SqlAlchemyProductionUnitOfWork
from app.modules.techcards.infrastructure import ProjectTechcardRow
from app.shared.http_auth import authenticated_principal
from app.modules.production.traceability import factory_number_key
from app.shared.persistence import AuditEventRow
from tests.production.test_api import issue_card, production_api  # noqa: F401
from tests.workflows.test_marking_labels import BASE, NOW, enqueue, marking, preview  # noqa: F401
from tests.workflows.test_marking_outside import OUTSIDE, body, make_group

KB = [f"FRX.591К.3.КБ1.{k}" for k in range(1, 6)]


def _numbers(factory, task_id):
    with factory() as uow:
        return [(row.position, row.item_serial, row.product_designation, row.factory_number)
                for row in uow.session.scalars(select(ProductionSerialRow).where(ProductionSerialRow.task_id == task_id)
                                               .order_by(ProductionSerialRow.position))]


def _issued_events(factory):
    with factory() as uow:
        return [row.details for row in uow.session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == FACTORY_NUMBERS_ISSUED_EVENT))]


def _mark_text(marking, text):
    factory, card = marking[1], marking[4]
    with factory() as uow:
        row = uow.session.get(ProjectTechcardRow, card.id)
        document = deepcopy(row.document)
        document["operations"][0]["fields"]["marks"][0]["text"] = text
        row.document = document
        uow.commit()


def test_print_allocates_and_stores_the_numbers_a_reprint_reuses_them(marking):
    client, factory, payload, task, *_ = marking
    preview(marking)
    assert all(number is None for *_rest, number in _numbers(factory, task.id))   # предпросмотр ничего не выдаёт
    first, request, _shown = enqueue(marking)
    stored = _numbers(factory, task.id)
    assert stored == [(k, k, "КБ1", KB[k - 1]) for k in range(1, 6)]
    assert _issued_events(factory) == [{"first": KB[0], "last": KB[-1], "count": 5}]
    reprint = {**request, "idempotency_key": "repeat", "reprint_of": first["id"], "reason": "Повреждена этикетка"}
    assert client.post(BASE + "/batches", json=reprint).status_code == 200
    assert _numbers(factory, task.id) == stored and len(_issued_events(factory)) == 1
    # API: номер на изделии и в списке изделий задания.
    serials = client.get(f"/api/production/tasks/{task.id}/serials").json()["serials"]
    assert [(item["position"], item["item_serial"], item["factory_number"]) for item in serials] == [
        (k, k, KB[k - 1]) for k in range(1, 6)]


def test_the_next_task_of_the_item_continues(marking):
    _client, factory, payload, task, *_ = marking
    _group, (_first, second) = make_group(marking, "again", relaunch=True)
    enqueue(marking)
    source = {"task_id": str(second.id), "operation_id": str(second.operations[0].id), "version": second.version}
    enqueue(marking, key="second", payload={**payload, "source": source})
    assert [number for *_rest, number in _numbers(factory, second.id)] == [f"FRX.591К.3.КБ1.{k}" for k in range(6, 11)]


def test_a_task_with_printed_numbers_is_never_deleted(marking):
    _client, factory, _payload, task, *_ = marking
    enqueue(marking)
    with factory() as uow, pytest.raises(ProductionValidationError, match="заводской номер"):
        uow.production.delete(task.id)


@pytest.mark.parametrize(("template", "expected"), [
    ("FRX.591K.3.34.226.{№}", [f"FRX.591К.3.34.226.{k}" for k in range(1, 6)]),            # НПК РТС
    ("FRX.591К.3.120907/AA.{№}", [f"FRX.591К.3.120907/АА.{k}" for k in range(1, 6)]),       # PN заказчика
    ("FRX.591К.3.КБ1.{№}", KB),
])
def test_the_designation_comes_from_the_label_template(marking, template, expected):
    _client, factory, _payload, task, *_ = marking
    _mark_text(marking, template)
    _batch, _request, shown = enqueue(marking)
    assert [number for *_rest, number in _numbers(factory, task.id)] == expected
    printed = [row["text"] for row in shown["labels"] if row["serial"] is not None]
    assert [factory_number_key(text) for text in printed] == [factory_number_key(n) for n in expected]  # тот же номер


@pytest.mark.parametrize("scan", ["FRX.591К.3.КБ1.3", "frx.591k.3.kb1.03", "FRX.591K.3.34.226.3", "FRX.591К.3.120907/aa.3"])
def test_the_vna_bench_finds_the_item_by_its_factory_number(marking, scan):
    client, factory, _payload, task, *_ = marking
    principal = app.dependency_overrides[authenticated_principal]()
    app.dependency_overrides[authenticated_principal] = lambda: replace(
        principal, permissions={**principal.permissions, "quality": "edit"})
    if "34.226" in scan:
        _mark_text(marking, "FRX.591К.3.34.226.{№}")
    elif "120907" in scan:
        _mark_text(marking, "FRX.591К.3.120907/AA.{№}")
    enqueue(marking)
    found = client.post("/api/production/vna-bench/resolve", json={"query": scan})
    assert found.status_code == 200, found.text
    [candidate] = found.json()["candidates"]
    stored = dict((position, number) for position, *_rest, number in _numbers(factory, task.id))
    assert (candidate["task_id"], candidate["position"], candidate["serial"]) == (str(task.id), 3, stored[3])
    by_task = client.post("/api/production/vna-bench/resolve", json={"query": f"{task.number}/2"}).json()["candidates"]
    assert [item["serial"] for item in by_task] == [stored[2]]
    assert client.post("/api/production/vna-bench/resolve", json={"query": "FRX.591К.3.КБ1.9"}).json()["candidates"] == []


def test_marking_printed_outside_stores_the_numbers_too(marking):
    client, factory, *_ = marking
    _group_id, tasks = make_group(marking, "second")
    group_id = _group_id
    assert client.post(OUTSIDE, json=body(group_id)).status_code == 200
    assert [number for *_rest, number in _numbers(factory, tasks[1].id)] == [f"FRX.591К.3.КБ2.{k}" for k in range(1, 6)]


def test_a_factory_number_is_unique_in_the_database(marking):
    _client, factory, _payload, task, *_ = marking
    _group, (_first, second) = make_group(marking, "again", relaunch=True)
    enqueue(marking)
    with factory() as uow:
        uow.serials.ensure_task(second.id, second.number, second.quantity)
        uow.commit()
    with factory() as uow, pytest.raises(IntegrityError):
        row = uow.session.scalar(select(ProductionSerialRow).where(ProductionSerialRow.task_id == second.id,
                                                                    ProductionSerialRow.position == 1))
        row.item_serial, row.product_designation, row.factory_number = 6, "КБ1", KB[0]
        uow.session.flush()
    with factory() as uow, pytest.raises(ProductionValidationError, match="уже выдан"):
        uow.serials.assign_factory_numbers(second.id, "КБ1", {1: (1, KB[0])})


def test_two_simultaneous_prints_of_a_task_issue_its_numbers_once(marking, tmp_path):
    """Две печати одного задания одновременно (разные ключи): номера выданы один раз, у каждого изделия — один."""
    from app.workflows.marking_labels import BatchInput, enqueue as enqueue_direct

    _client, factory, payload, task, *_ = marking
    shown = preview(marking)
    principal = app.dependency_overrides[authenticated_principal]()
    path = tmp_path / "concurrent.db"
    with factory().session_factory.kw["bind"].connect() as connection, sqlite3.connect(path) as target:
        connection.connection.driver_connection.backup(target)
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"timeout": 15, "check_same_thread": False})
    sessions = sessionmaker(engine, expire_on_commit=False)
    concurrent = lambda: SqlAlchemyProductionUnitOfWork(sessions)
    try:
        barrier = Barrier(2)

        def run(key):
            barrier.wait()
            return enqueue_direct(BatchInput(**payload, idempotency_key=key, manifest_hash=shown["manifest_hash"]),
                                  principal, concurrent, NOW)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.exception() or future.result() for future in [pool.submit(run, "a"), pool.submit(run, "b")]]
        assert any(isinstance(item, dict) for item in outcomes), outcomes
        with sessions() as session:
            rows = list(session.scalars(select(ProductionSerialRow).where(ProductionSerialRow.task_id == task.id)
                                        .order_by(ProductionSerialRow.position)))
            assert [row.factory_number for row in rows] == KB
            assert len(list(session.scalars(select(AuditEventRow).where(
                AuditEventRow.event_type == FACTORY_NUMBERS_ISSUED_EVENT)))) == 1
    finally:
        engine.dispose()


def _group_payload(marking, group_id):
    return {**marking[2], "source": {"group_id": group_id, "version": 1}}


def test_two_kb_of_one_designation_in_one_print_count_on(marking):
    """Счёт по (проект, изделие): два КБ с одним своим обозначением в одной партии — 1–5 и 6–10, этикетки те же."""
    _client, factory, *_ = marking
    _mark_text(marking, "FRX.591К.3.34.226.{№}")
    group_id, (first, second) = make_group(marking, "second")
    _batch, _request, shown = enqueue(marking, payload=_group_payload(marking, group_id))
    assert [n for *_r, n in _numbers(factory, first.id)] == [f"FRX.591К.3.34.226.{k}" for k in range(1, 6)]
    assert [n for *_r, n in _numbers(factory, second.id)] == [f"FRX.591К.3.34.226.{k}" for k in range(6, 11)]
    printed = sorted(row["text"] for row in shown["labels"] if row["serial"] is not None)
    assert printed == sorted(f"FRX.591К.3.34.226.{k}" for k in range(1, 11))


def test_labels_printed_before_0222_never_share_a_number(marking, monkeypatch):
    """Этикетки, напечатанные до 0222 (номера по позиции: у обоих КБ одного изделия 1–5), не сдвигаются; номер, уже
    принадлежащий другому заданию, печать не повторяет — отказ с номером того задания, ничего не выдано."""
    import app.workflows.marking_labels as workflow

    _client, factory, *_ = marking
    _mark_text(marking, "FRX.591К.3.34.226.{№}")
    group_id, (first, second) = make_group(marking, "second")
    with monkeypatch.context() as old:
        old.setattr(workflow, "factory_scope", lambda task, document, uow: (None, None, False))
        old.setattr(workflow, "allocate_factory_numbers_in", lambda *args, **kwargs: [])
        enqueue(marking, key="before", payload=_group_payload(marking, group_id))
    body = _group_payload(marking, group_id)
    shown = preview(marking, body)
    serials = {(row["task_id"], row["serial"]) for row in shown["labels"] if row["serial"] is not None}
    assert serials == {(str(task.id), k) for task in (first, second) for k in range(1, 6)}   # не сдвинулись
    refused = _client.post(BASE + "/batches", json={**body, "idempotency_key": "after",
                                                    "manifest_hash": shown["manifest_hash"]})
    assert refused.status_code == 422, refused.text
    assert "уже выдан" in refused.json()["detail"] and first.number in refused.json()["detail"]
    for task in (first, second):
        assert all(n is None for *_r, n in _numbers(factory, task.id))


def _set_serial_field(marking, serial):
    factory, card = marking[1], marking[4]
    with factory() as uow:
        row = uow.session.get(ProjectTechcardRow, card.id)
        document = deepcopy(row.document)
        document["operations"][0]["fields"]["serial"] = serial
        row.document = document
        uow.commit()


def test_a_designation_disagreeing_with_the_template_refuses_the_print(marking):
    client, factory, payload, task, *_ = marking
    _mark_text(marking, "FRX.591К.3.34.226.{№}")
    _set_serial_field(marking, {"template": "FRX.591К.3.34.226.{№}", "designation": "КБ1"})
    refused = client.post(BASE + "/preview", json=payload)
    assert refused.status_code == 422 and "не совпадает с шаблоном" in refused.json()["detail"], refused.text
    _mark_text(marking, "FRX.591К.120907/AA.{№}")   # проект 591К., задание — 591K.3: не «КБ1»
    _set_serial_field(marking, {})
    refused = client.post(BASE + "/preview", json=payload)
    assert refused.status_code == 422 and "не разобрать" in refused.json()["detail"], refused.text
    assert all(number is None for *_rest, number in _numbers(factory, task.id))


def test_a_label_that_no_longer_reads_as_the_stored_number_is_not_printed(marking):
    """ЭТК сменила изделие после первой печати: номера изделий уже выданы (КБ1), этикетка читалась бы как 34.226."""
    client, factory, payload, task, *_ = marking
    _mark_text(marking, "FRX.591К.3.КБ1.{№}")
    enqueue(marking)
    _mark_text(marking, "FRX.591К.3.34.226.{№}")
    shown = preview(marking)
    refused = client.post(BASE + "/batches", json={**payload, "idempotency_key": "changed",
                                                   "manifest_hash": shown["manifest_hash"]})
    assert refused.status_code == 422 and "не совпадает с заводским номером" in refused.json()["detail"], refused.text
    assert [number for *_rest, number in _numbers(factory, task.id)] == KB


def test_the_etk_screen_counts_per_designation_and_says_provisional(marking):
    """Два КБ с одним обозначением «34.226»: экран ЭТК второго показывает 6–10 (первый напечатал 1–5) — предварительно."""
    from decimal import Decimal

    from app.modules.production.domain import link_inputs
    from tests.production.test_my_readiness import _etk_url, _launch
    from tests.production.test_network_execution import _op

    client, factory, _payload, task, _card, _equipment, operator = marking
    _mark_text(marking, "FRX.591К.3.34.226.{№}")
    second_card = issue_card("591K.3", "КБ2", [("OP-01", "Нарезка", "10", "0"), ("OP-02", "Маркировка", "10", "0")])
    with factory() as uow:
        row = uow.session.get(ProjectTechcardRow, second_card.id)
        document = deepcopy(row.document)
        document["operations"][0]["fields"] = {"marks": [{"text": "FRX.591К.3.34.226.{№}", "qty": "1",
                                                          "material": "Этикетка"}]}
        row.document = document
        uow.commit()
    route = link_inputs((_op(1, "Нарезка"), _op(2, "Маркировка")), {2: ((1, Decimal(1)),)})
    second = _launch(factory, operator, "kb2-shared", route, item="591K.3.КБ2")
    before = client.get(_etk_url(second, 1)).json()["serial"]
    assert (before["first"], before["last"], before["provisional"]) == (1, 5, True)
    enqueue(marking)
    shown = client.get(_etk_url(second, 1)).json()["serial"]
    assert (shown["first"], shown["last"], shown["provisional"]) == (6, 10, True)
    own = client.get(_etk_url(task, 1)).json()["serial"]
    assert (own["first"], own["last"], own["provisional"]) == (1, 5, False)


def test_printed_numbers_are_never_reused_after_cancel(marking):
    """Печать → отмена задания (даже с освобождением номеров) → новый запуск той же позиции печатает 6–10."""
    from decimal import Decimal
    from uuid import uuid4

    from app.modules.production.domain import MARKS_RELEASED_EVENT, link_inputs
    from tests.production.test_my_readiness import _launch
    from tests.production.test_network_execution import _op

    client, factory, payload, task, _card, _equipment, operator = marking
    enqueue(marking)
    with factory() as uow:
        version = uow.production.get(task.id).version
    cancelled = client.post(f"/api/production/tasks/{task.id}/cancel", json={"version": version, "cancelled": True})
    assert cancelled.status_code == 200, cancelled.text
    with factory() as uow:   # даже событие освобождения номеров (отмена без работы) не отдаёт напечатанные
        uow.session.add(AuditEventRow(id=uuid4(), aggregate_id=task.id, event_type=MARKS_RELEASED_EVENT,
                                      actor_id=operator, actor_name="Мастер", actor_department="", occurred_at=NOW,
                                      details={"after": task.number}))
        uow.commit()
    route = link_inputs((_op(1, "Нарезка"), _op(2, "Маркировка")), {2: ((1, Decimal(1)),)})
    again = _launch(factory, operator, "after-cancel", route, item=task.item)
    source = {"task_id": str(again.id), "operation_id": str(again.operations[0].id), "version": again.version}
    enqueue(marking, key="after-cancel", payload={**payload, "source": source})
    assert [number for *_rest, number in _numbers(factory, again.id)] == [f"FRX.591К.3.КБ1.{k}" for k in range(6, 11)]
    assert [number for *_rest, number in _numbers(factory, task.id)] == KB


def test_a_quantity_raised_after_the_print_refuses_instead_of_numbering_a_part(marking):
    """Тираж после запуска не меняется (правки нет); правка в БД — печать отказывает, номера 1–5 не трогаются."""
    from sqlalchemy import update

    from app.modules.production.infrastructure import ProductionTaskRow

    client, factory, payload, task, *_ = marking
    enqueue(marking)
    with factory() as uow:
        uow.session.execute(update(ProductionTaskRow).where(ProductionTaskRow.id == task.id).values(quantity=7))
        uow.commit()
    shown = preview(marking)
    refused = client.post(BASE + "/batches", json={**payload, "idempotency_key": "bigger",
                                                   "manifest_hash": shown["manifest_hash"]})
    assert refused.status_code == 422 and "тираж" in refused.json()["detail"], refused.text
    assert [number for *_rest, number in _numbers(factory, task.id)] == KB


def test_a_template_with_a_prefix_is_still_the_factory_number(marking):
    """«№ FRX.591К.3.34.226.{№}»: шаблон — часть от «FRX.», номер не падает на «КБ1», этикетка читается как номер."""
    client, factory, _payload, task, *_ = marking
    _mark_text(marking, "№ FRX.591К.3.34.226.{№}")
    _batch, _request, shown = enqueue(marking)
    expected = [f"FRX.591К.3.34.226.{k}" for k in range(1, 6)]
    assert [number for *_rest, number in _numbers(factory, task.id)] == expected
    printed = [row["text"] for row in shown["labels"] if row["serial"] is not None]
    assert printed[0].startswith("№") and [factory_number_key(t) for t in printed] == [factory_number_key(n) for n in expected]
