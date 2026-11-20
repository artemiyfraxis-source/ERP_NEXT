"""Printed / remaining labels per KB come from the print queue; «Печатать оставшиеся» skips what was printed."""

from collections import Counter
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import update

from app.modules.warehouse.label_jobs import LabelJobRow
from app.workflows.marking_progress import remaining_rows, task_progress
from tests.production.test_api import production_api  # noqa: F401
from tests.workflows.test_marking_labels import BASE, NOW, enqueue, marking, preview  # noqa: F401


def _keep_only_first(factory):
    """The first label (serial 1, numbered) stays queued; the rest of the batch is withdrawn."""
    with factory() as uow:
        uow.session.execute(update(LabelJobRow).where(LabelJobRow.sequence > 1).values(
            status="cancelled", finished_at=NOW, error="тест"))
        uow.commit()


def test_progress_counts_printed_serials_and_remaining_prints_only_the_rest(marking):
    client, factory, payload, task, *_ = marking
    enqueue(marking)
    _keep_only_first(factory)
    progress = client.get(BASE + "/progress", params={"task_id": str(task.id), "operation_id": payload["source"]["operation_id"]})
    assert progress.status_code == 200, progress.text
    row = progress.json()["rows"][0]
    assert (row["printed"], row["remaining"], row["printed_serials"]) == (1, task.quantity - 1, [1])
    assert row["remaining_serials"] == list(range(2, task.quantity + 1))
    assert row["remaining_labels"] == progress.json()["remaining_labels"] == 3 * task.quantity - 1
    rest = preview(marking, {**payload, "remaining": True})
    texts = [(label["text"], label["quantity"]) for label in rest["labels"]]
    assert ("КБ-001", 1) not in texts
    assert texts[:4] == [("КБ-002", 1), ("КБ-003", 1), ("КБ-004", 1), ("КБ-005", 1)]
    assert texts[-1] == ("АБВ", 2 * task.quantity)
    full = preview(marking)
    assert rest["total"] == full["total"] - 1


def test_failed_and_cancelled_jobs_are_not_printed(marking):
    client, factory, payload, task, *_ = marking
    enqueue(marking)
    with factory() as uow:
        uow.session.execute(update(LabelJobRow).values(status="cancelled", finished_at=NOW, error="тест"))
        uow.commit()
    rest = preview(marking, {**payload, "remaining": True})
    assert rest["total"] == preview(marking)["total"]


def test_nothing_left_is_a_clear_refusal(marking):
    client, _factory, payload, *_ = marking
    enqueue(marking)  # every job still queued: all taken
    response = client.post(BASE + "/preview", json={**payload, "remaining": True})
    assert response.status_code == 422 and "уже напечатаны" in response.json()["detail"], response.text


def test_rows_match_by_task_serial_and_text_not_by_etk_index():
    task = SimpleNamespace(id="t1", quantity=3)
    rows = [{"key": "t1:5:0", "task_id": "t1", "text": "FRX.1", "quantity": 1, "serial": 1},
            {"key": "t1:5:0", "task_id": "t1", "text": "FRX.2", "quantity": 1, "serial": 2},
            {"key": "t1:5:1", "task_id": "t1", "text": "XS1", "quantity": 6, "serial": None}]
    printed = Counter({("t1", 1, "FRX.1"): 1, ("t1", None, "XS1"): 2})
    assert remaining_rows(rows, printed) == [rows[1], {**rows[2], "quantity": 4}]
    document = {"operations": [{"fields": {"marks": [{"text": "FRX.{№}", "qty": "1"}]}}]}
    view = task_progress(SimpleNamespace(id="t1", number="ПЗ-1", item="КБ1", quantity=3), document, (1, 3),
                         Counter({("t1", 1, "FRX.1"): 1}))
    assert (view["printed"], view["remaining"], view["remaining_serials"]) == (1, 2, [2, 3])
    assert task.quantity == 3


def test_progress_reads_the_issued_etk_without_locking_it(marking, monkeypatch):
    from app.workflows import marking_labels

    client, _factory, payload, task, *_ = marking
    original, calls = marking_labels.issued_document, []

    def spy(task, uow, *, lock=True):
        calls.append(lock)
        return original(task, uow, lock=lock)

    monkeypatch.setattr(marking_labels, "issued_document", spy)
    response = client.get(BASE + "/progress", params={"task_id": str(task.id), "operation_id": payload["source"]["operation_id"]})
    assert response.status_code == 200, response.text
    assert calls == [False]


def _keep(factory, sequences, batch_id=None):
    """Only these job numbers stay queued (taken = printed); the rest of the batch is withdrawn."""
    with factory() as uow:
        query = update(LabelJobRow).where(LabelJobRow.sequence.not_in(sequences))
        if batch_id is not None:
            query = query.where(LabelJobRow.batch_id == batch_id)
        uow.session.execute(query.values(status="cancelled", finished_at=NOW, error="тест"))
        uow.commit()


def _progress(marking):
    client, _factory, payload, task, *_ = marking
    response = client.get(BASE + "/progress", params={"task_id": str(task.id), "operation_id": payload["source"]["operation_id"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_interleaved_item_keeps_its_fixed_labels_printed(marking):
    """«Чередовать по изделию» prints item 1 as АБВ (serial 1) ×2, then КБ-001: neither preview prints them again."""
    _client, factory, payload, task, *_ = marking
    interleaved = {**payload, "interleave": True}
    _batch, _request, shown = enqueue(marking, payload=interleaved)
    assert [(label["text"], label["serial"], label["quantity"]) for label in shown["labels"][:2]] == [
        ("АБВ", 1, 2), ("КБ-001", 1, 1)]
    _keep(factory, [1, 2, 3])
    body = _progress(marking)
    row = body["rows"][0]
    assert (row["printed"], row["remaining"], row["printed_serials"]) == (1, task.quantity - 1, [1])
    assert row["remaining_labels"] == body["remaining_labels"] == 3 * task.quantity - 3
    full = preview(marking)
    plain = preview(marking, {**payload, "remaining": True})
    texts = [(label["text"], label["quantity"]) for label in plain["labels"]]
    assert ("КБ-001", 1) not in texts and texts[-1] == ("АБВ", 2 * task.quantity - 2)
    assert plain["total"] == full["total"] - 3 == row["remaining_labels"]
    rest = preview(marking, {**interleaved, "remaining": True})
    assert all(label["serial"] != 1 for label in rest["labels"])
    assert [(label["text"], label["serial"]) for label in rest["labels"][:2]] == [("АБВ", 2), ("КБ-002", 2)]
    assert rest["total"] == full["total"] - 3


def test_plain_fixed_labels_count_in_an_interleaved_preview(marking):
    """A plain batch printed КБ-001 and two АБВ: the interleaved «оставшиеся» starts at item 2."""
    _client, factory, payload, task, *_ = marking
    enqueue(marking)
    _keep(factory, [1, task.quantity + 1, task.quantity + 2])
    rest = preview(marking, {**payload, "interleave": True, "remaining": True})
    assert all(label["serial"] != 1 for label in rest["labels"])
    assert rest["total"] == preview(marking)["total"] - 3


def test_fixed_labels_left_after_every_serial_keep_the_button(marking):
    """All numbered labels printed, fixed ones withdrawn: no item left, but label pieces are."""
    _client, factory, payload, task, *_ = marking
    enqueue(marking)
    _keep(factory, list(range(1, task.quantity + 1)))
    body = _progress(marking)
    assert (body["printed"], body["remaining"], body["remaining_labels"]) == (task.quantity, 0, 2 * task.quantity)
    rest = preview(marking, {**payload, "remaining": True})
    assert [(label["text"], label["quantity"]) for label in rest["labels"]] == [("АБВ", 2 * task.quantity)]


def test_a_reprinted_label_counts_once(marking):
    """The last АБВ failed and another one was reprinted: the failed one is still left to print."""
    from tests.workflows.test_marking_control import spool_all

    client, factory, payload, task, *_ = marking
    batch, _request, _shown = enqueue(marking)
    spool_all(factory, uuid4())
    last = 3 * task.quantity
    with factory() as uow:
        uow.session.execute(update(LabelJobRow).where(LabelJobRow.sequence == last).values(status="failed", error="тест"))
        uow.commit()
    response = client.post(BASE + f"/batches/{batch['id']}/reprint",
                           json={"sequences": [last - 1], "reason": "Смялась", "idempotency_key": "rp-once"})
    assert response.status_code == 200, response.text
    assert _progress(marking)["remaining_labels"] == 1
    rest = preview(marking, {**payload, "remaining": True})
    assert [(label["text"], label["quantity"]) for label in rest["labels"]] == [("АБВ", 1)]


def test_fixed_rows_match_in_every_order():
    rows = [{"key": "t1:0:1", "task_id": "t1", "text": "XS1", "quantity": 2, "serial": 1},
            {"key": "t1:0:0", "task_id": "t1", "text": "XS1", "quantity": 1, "serial": 1},
            {"key": "t1:0:1", "task_id": "t1", "text": "XS1", "quantity": 2, "serial": 2},
            {"key": "t1:0:0", "task_id": "t1", "text": "XS2", "quantity": 1, "serial": 2}]
    # Numbered «XS{№}» of item 1 reads like the fixed «XS1»: the numbered row takes its exact match first.
    printed = Counter({("t1", 1, "XS1"): 3})
    assert remaining_rows(rows, printed, {"t1:0:1"}) == rows[2:]
    # Plain fixed labels (no serial) are shared out over the items of an interleaved preview.
    assert remaining_rows(rows, Counter({("t1", None, "XS1"): 3}), {"t1:0:1"}) == [
        rows[1], {**rows[2], "quantity": 1}, rows[3]]
    plain = [{"key": "t1:0:1", "task_id": "t1", "text": "XS1", "quantity": 4, "serial": None}]
    assert remaining_rows(plain, Counter({("t1", 1, "XS1"): 2, ("t1", None, "XS1"): 1})) == [{**plain[0], "quantity": 1}]
