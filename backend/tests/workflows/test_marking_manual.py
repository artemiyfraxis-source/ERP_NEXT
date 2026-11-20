"""«Свой текст» of the marking printer card (owner 20.11.2026): lines × copies, order, {№} numbering, the same queue."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.production.marking_sources import manual_labels
from app.modules.warehouse.label_jobs import LabelJobRow, claim_marking, report_marking
from app.shared.persistence import AuditEventRow
from tests.production.test_api import production_api, issue_card  # noqa: F401
from tests.production.test_my_readiness import _as_operator
from tests.workflows.test_marking_labels import BASE, NOW, marking  # noqa: F401


def body(marking, lines, **extra):
    payload = marking[2]
    return {"profile_id": payload["profile_id"], "profile_version": payload["profile_version"], "lines": lines, **extra}


def manual_print(marking, request, key="manual"):
    client = marking[0]
    shown = client.post(BASE + "/manual/preview", json=request)
    assert shown.status_code == 200, shown.text
    response = client.post(BASE + "/manual/batches", json={**request, "manifest_hash": shown.json()["manifest_hash"],
                                                            "idempotency_key": key})
    assert response.status_code == 200, response.text
    return response.json(), shown.json()


def test_lines_copies_numbering_and_order_expand_purely():
    rows = manual_labels([("X10", 1), ("X2", 2), ("A{№}", 1)], numbering=(9, 10))
    assert [(row["text"], row["quantity"]) for row in rows] == [("X10", 1), ("X2", 2), ("A9", 1), ("A10", 1)]
    assert all(row["serial"] is None for row in rows)
    ordered = manual_labels([("X10", 1), ("X2", 2), ("A{№:2}", 1)], order="number", numbering=(9, 10))
    assert [row["text"] for row in ordered] == ["A09", "A10", "X2", "X10"]
    with pytest.raises(ValueError, match="Пустая строка №2"):
        manual_labels([("X1", 1), ("   ", 1)])
    with pytest.raises(ValueError, match="укажите номера"):
        manual_labels([("A{№}", 1)])
    with pytest.raises(ValueError, match="не более 500"):
        manual_labels([("A", 300), ("B", 201)])


def test_manual_batch_prints_lines_times_copies_through_the_marking_queue(marking):
    client, factory, _payload, task, *_ = marking
    request = body(marking, [{"text": "ПИТАНИЕ +24В", "quantity": 2}, {"text": "X1-{№}", "quantity": 1}],
                   numbering={"first": 1, "last": 3})
    batch, shown = manual_print(marking, request)
    assert [(row["text"], row["quantity"]) for row in shown["labels"]] == [
        ("ПИТАНИЕ +24В", 2), ("X1-1", 1), ("X1-2", 1), ("X1-3", 1)]
    assert shown["total"] == batch["total"] == 5
    assert [job["text"] for job in batch["jobs"]] == ["ПИТАНИЕ +24В", "ПИТАНИЕ +24В", "X1-1", "X1-2", "X1-3"]
    # The same key answers the same batch; a changed request under it is refused.
    again = client.post(BASE + "/manual/batches", json={**request, "manifest_hash": shown["manifest_hash"], "idempotency_key": "manual"})
    assert again.json()["id"] == batch["id"]
    changed = {**request, "order": "number", "manifest_hash": shown["manifest_hash"], "idempotency_key": "manual"}
    assert client.post(BASE + "/manual/batches", json=changed).status_code == 409
    with factory() as uow:
        assert len(list(uow.session.scalars(select(LabelJobRow)))) == 5
        assert not uow.production.marks_issued([task.id])  # no product serials are spent
        event = uow.session.scalar(select(AuditEventRow).where(AuditEventRow.event_type == "production.marking_manual_queued"))
        assert event.details["lines"] == [{"text": "ПИТАНИЕ +24В", "quantity": 2}, {"text": "X1-{№}", "quantity": 1}]
        assert event.details["total"] == 5 and event.details["numbering"] == [1, 3]
    job = claim_marking(factory(), machine_id="godex-pc", token_id=uuid4(), now=NOW)
    assert job["png_b64"] == shown["labels"][0]["png_b64"] and job["printer"] == "Godex EZ2350i"
    assert client.get(BASE + f"/batches/{batch['id']}").status_code == 200


def test_order_by_number_trial_and_reprint(marking):
    client, factory, *_ = marking
    request = body(marking, [{"text": "X10", "quantity": 1}, {"text": "X2", "quantity": 1}, {"text": "X1", "quantity": 1}],
                   order="number")
    batch, shown = manual_print(marking, request)
    assert [row["text"] for row in shown["labels"]] == ["X1", "X2", "X10"]
    trial, trial_shown = manual_print(marking, {**request, "trial": True}, key="trial")
    assert trial["total"] == 1 and trial_shown["labels"][0]["text"] == "X1" and trial["reason"] == "Пробная печать"
    # «Перепечатать» of own text: a new batch of the stored texts, a reason required.
    token = uuid4()
    while (job := claim_marking(factory(), machine_id="godex-pc", token_id=token, now=NOW)) is not None:
        report_marking(factory(), UUID(job["id"]), machine_id="godex-pc", token_id=token, attempt=job["attempt"],
                       outcome="spooled", message="", spool_job_id=None, now=NOW)
    response = client.post(BASE + f"/batches/{batch['id']}/reprint",
                           json={"sequences": [3], "reason": "Смялась", "idempotency_key": "again"})
    assert response.status_code == 200, response.text
    assert [job["text"] for job in response.json()["jobs"]] == ["X10"]


@pytest.mark.parametrize("lines, extra", [
    ([{"text": "", "quantity": 1}], {}),
    ([{"text": "A", "quantity": 1}, {"text": "  ", "quantity": 1}], {}),
    ([{"text": "A{№}", "quantity": 1}], {}),
    ([{"text": "A", "quantity": 0}], {}),
    ([], {}),
    ([{"text": "A{№}", "quantity": 1}], {"numbering": {"first": 5, "last": 2}}),
])
def test_empty_or_invalid_lines_are_refused_without_jobs(marking, lines, extra):
    client, factory, *_ = marking
    assert client.post(BASE + "/manual/preview", json=body(marking, lines, **extra)).status_code == 422
    with factory() as uow:
        assert uow.session.scalar(select(LabelJobRow.id)) is None


def test_manual_print_needs_the_marking_print_rights(marking):
    client, _factory, _payload, task, *_ = marking
    request = body(marking, [{"text": "A", "quantity": 1}])
    _as_operator(task.operations[0].operator_ids[0])  # Задания, but no «Печать документов»
    assert client.post(BASE + "/manual/preview", json=request).status_code == 403
    assert client.post(BASE + "/manual/batches", json={**request, "manifest_hash": "0" * 64, "idempotency_key": "k"}).status_code == 403


def test_another_operator_cannot_read_my_manual_batch(marking):
    client, *_ = marking
    batch, _shown = manual_print(marking, body(marking, [{"text": "A", "quantity": 1}]))
    _as_operator(uuid4())
    assert client.get(BASE + f"/batches/{batch['id']}").status_code == 403


def test_the_marking_machine_tile_opens_the_marking_card(marking):
    client, _factory, _payload, _task, _card, equipment_id, _operator = marking
    response = client.get("/api/production/machines")
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["rows"] if row["equipment_id"] == str(equipment_id))
    assert row["marking"] is True and row["inventory"] == "ОБ.101"
