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
    ("FRX.591К.3.120907/AA.{№}", [f"FRX.591К.3.120907/AA.{k}" for k in range(1, 6)]),       # PN заказчика
    ("FRX.591К.3.КБ1.{№}", KB),
])
def test_the_designation_comes_from_the_label_template(marking, template, expected):
    _client, factory, _payload, task, *_ = marking
    _mark_text(marking, template)
    _batch, _request, shown = enqueue(marking)
    assert [number for *_rest, number in _numbers(factory, task.id)] == expected
    printed = [row["text"] for row in shown["labels"] if row["serial"] is not None]
    assert [text.replace("591K", "591К") for text in printed] == expected   # на этикетке — тот же номер


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


def test_labels_printed_before_0222_keep_their_numbers(marking, monkeypatch):
    """Этикетки, напечатанные до 0222 (номера по позиции: у обоих КБ 1–5), при допечатке не сдвигаются; номер,
    уже занятый другим КБ того же изделия, не выдаётся второй раз — у такого задания заводских номеров нет."""
    import app.workflows.marking_labels as workflow

    _client, factory, *_ = marking
    _mark_text(marking, "FRX.591К.3.34.226.{№}")
    group_id, (first, second) = make_group(marking, "second")
    with monkeypatch.context() as old:
        old.setattr(workflow, "factory_scope", lambda task, document, uow: (None, False))
        old.setattr(workflow, "allocate_factory_numbers_in", lambda *args, **kwargs: [])
        enqueue(marking, key="before", payload=_group_payload(marking, group_id))
    _batch, _request, shown = enqueue(marking, key="after", payload=_group_payload(marking, group_id))
    serials = {(row["task_id"], row["serial"]) for row in shown["labels"] if row["serial"] is not None}
    assert serials == {(str(task.id), k) for task in (first, second) for k in range(1, 6)}
    assert [n for *_r, n in _numbers(factory, first.id)] == [f"FRX.591К.3.34.226.{k}" for k in range(1, 6)]
    assert all(n is None for *_r, n in _numbers(factory, second.id))
