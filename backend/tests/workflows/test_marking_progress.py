"""Printed / remaining labels per KB come from the print queue; «Печатать оставшиеся» skips what was printed."""

from collections import Counter
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import update

from app.modules.warehouse.label_jobs import LabelJobRow
import pytest

from app.workflows.marking_progress import label_size, remaining_rows, size_summary, task_progress
from tests.production.test_api import production_api  # noqa: F401
from tests.workflows.test_marking_labels import BASE, NOW, enqueue, marking, preview, set_material  # noqa: F401


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
    # Group card counters (owner 23.11): label pieces, not items — total over the serial range, printed = total − left.
    assert (row["total_labels"], row["printed_labels"]) == (3 * task.quantity, 1)
    assert (progress.json()["total_labels"], progress.json()["printed_labels"]) == (3 * task.quantity, 1)
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
    assert (view["total_labels"], view["printed_labels"], view["remaining_labels"]) == (3, 1, 2)
    released = task_progress(SimpleNamespace(id="t1", number="ПЗ-1", item="КБ1", quantity=3), document, None, Counter())
    assert (released["total_labels"], released["printed_labels"], released["remaining_labels"]) == (0, 0, 0)
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
    assert (body["total_labels"], body["printed_labels"]) == (3 * task.quantity, task.quantity)
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
    body = _progress(marking)
    # The reprint replaces the label it repeats: printed pieces never exceed the total.
    assert (body["remaining_labels"], body["total_labels"], body["printed_labels"]) == (1, last, last - 1)
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


def test_plain_fixed_row_never_takes_a_numbered_label_with_the_same_text():
    """Fixed «1» and numbered «{№}»: the printed numbered label «1» of item 1 is not a fixed label."""
    from app.workflows.marking_progress import numbered_keys

    task = SimpleNamespace(id="t1", quantity=3)
    document = {"operations": [{"fields": {"marks": [{"text": "{№}", "qty": "1"}, {"text": "1", "qty": "1"}]}}]}
    fixed_only = [{"key": "t1:0:1", "task_id": "t1", "text": "1", "quantity": 3, "serial": None}]  # only «1» selected
    printed = Counter({("t1", 1, "1"): 1})
    assert remaining_rows(fixed_only, printed, {"t1:0:1"}, numbered_keys(document, task, (1, 3))) == fixed_only


def test_a_serial_marked_printed_outside_the_erp_counts_as_printed(marking):
    """708К.: №1 of every КБ was printed outside the ERP — marked, «Осталось» drops by one and «Печатать оставшиеся»
    skips it; «Снять отметку» brings it back."""
    from tests.workflows.test_marking_outside import OUTSIDE, body, make_group

    client, _factory, payload, task, *_ = marking
    group_id, _tasks = make_group(marking)
    group_source = {**payload, "source": {"group_id": group_id, "version": 1}}
    full = preview(marking, group_source)
    assert client.post(OUTSIDE, json=body(group_id)).status_code == 200
    progress = client.get(BASE + "/progress", params={"group_id": group_id})
    assert progress.status_code == 200, progress.text
    row = progress.json()["rows"][0]
    assert (row["printed"], row["remaining"], row["printed_serials"]) == (1, task.quantity - 1, [1])
    assert row["remaining_labels"] == 3 * task.quantity - 1
    assert (row["total_labels"], row["printed_labels"]) == (3 * task.quantity, 1)
    rest = preview(marking, {**group_source, "remaining": True})
    texts = [label["text"] for label in rest["labels"]]
    assert "КБ-001" not in texts and texts[0] == "КБ-002"
    assert rest["total"] == full["total"] - 1
    assert client.post(OUTSIDE + "/undo", json=body(group_id)).status_code == 200
    again = client.get(BASE + "/progress", params={"group_id": group_id}).json()["rows"][0]
    assert (again["printed"], again["remaining"]) == (0, task.quantity)


# --- Пульт маркировки (plan 21.11): размер этикетки и Ø провода из ЭТК ------------------------------------------------

@pytest.mark.parametrize("material, size", [
    ("Этикетка 25×9 мм", (25.0, 9.0)),
    ("Этикетка 25x9мм", (25.0, 9.0)),
    ("Этикетка 25 мм × 9 мм", (25.0, 9.0)),  # «мм» after each number
    ("Этикетка 25мм×9мм", (25.0, 9.0)),
    ("Кабель 4х0,75 этикетка 25×9", (25.0, 9.0)),  # the first size of a label, not the cable section
    ("Лента VL-S641x12, этикетка 25×9", (25.0, 9.0)),
    ("Риббон 40х74 м", None),  # metres: a roll, not a label
    ("Лента 12 x 8 м", None),
    ("Этикетка 25×9 шт", None),
    ("Этикетка 25×9 м.", None),
    ("Этикетка 25×9 матовая", (25.0, 9.0)),  # a word that only starts with «м» is not metres
    ("Наклейка 25×9 мелованная", (25.0, 9.0)),
    ("Этикетка 25×9 штучная", (25.0, 9.0)),
    ("Этикетка 25×9 мм матовая", (25.0, 9.0)),
    ("Флажок 92,5×20", (92.5, 20.0)),
    ("Хлястик", None),
    ("Наклейка 25 Х 9 мм, белая", (25.0, 9.0)),
    ("Флажок 92,5*20", (92.5, 20.0)),
    ("Флажок 100×20, рулон", (100.0, 20.0)),
    ("Этикетка", None),
    ("Лента Vell VL-S641, 18 мм", None),
    ("Этикетка 3×1 мм", None),  # smaller than any Godex label: not a label size
    ("Этикетка 25×900 мм", None),
    ("Термоусадка 25×9 мм", None),  # a tube is never a label
    ("", None),
    (None, None),
])
def test_label_size_is_parsed_from_the_mark_material(material, size):
    parsed = label_size(material)
    if size is None:
        assert parsed is None
    else:
        assert (parsed["width_mm"], parsed["height_mm"], parsed["material"]) == (*size, material.strip())


def test_sizes_agree_in_either_orientation_and_differ_otherwise():
    a = {"width_mm": 25.0, "height_mm": 9.0, "material": "Этикетка 25×9 мм"}
    turned = {"width_mm": 9.0, "height_mm": 25.0, "material": "Этикетка 9×25 мм"}
    other = {"width_mm": 30.0, "height_mm": 9.0, "material": "Этикетка 30×9 мм"}
    assert size_summary([a, None, turned]) == (a, False)
    assert size_summary([None, None]) == (None, False)
    assert size_summary([]) == (None, False)
    assert size_summary([a, other]) == (None, True)


def _with_wires_and_material(marking, material, wires):
    from copy import deepcopy

    from app.modules.techcards.infrastructure import ProjectTechcardRow

    set_material(marking, material)
    factory, card = marking[1], marking[4]
    with factory() as uow:
        row = uow.session.get(ProjectTechcardRow, card.id)
        document = deepcopy(row.document)
        document["operations"][1]["fields"] = {"wires": wires}
        row.document = document
        uow.commit()


def test_progress_shows_the_etk_label_size_and_wire_diameters(marking):
    _with_wires_and_material(marking, "Этикетка 25×9 мм", [
        {"name": "МГШВ 0,35", "outerDiameter": 1.6}, {"name": "МГШВ 0,5", "outerDiameter": 2.2},
        {"name": "МГШВ 0,35", "outerDiameter": 1.6}, {"name": "Без размера"}])
    body = _progress(marking)
    size = {"width_mm": 25.0, "height_mm": 9.0, "material": "Этикетка 25×9 мм"}
    row = body["rows"][0]
    assert (row["label_size"], row["sizes_differ"], row["wire_mm"]) == (size, False, [1.6, 2.2])
    assert (body["label_size"], body["sizes_differ"], body["wire_mm"]) == (size, False, [1.6, 2.2])


def test_progress_without_a_size_or_wires_says_null(marking):
    body = _progress(marking)  # the fixture material is just «Этикетка», no wires
    assert (body["rows"][0]["label_size"], body["rows"][0]["wire_mm"]) == (None, [])
    assert (body["label_size"], body["sizes_differ"], body["wire_mm"]) == (None, False, [])


def test_group_rows_with_different_sizes_leave_the_top_size_empty(marking):
    from copy import deepcopy

    from app.modules.production.cutting_infrastructure import CuttingMemberRow
    from sqlalchemy import select
    from tests.workflows.test_marking_outside import make_group

    client, factory, *_ = marking
    _with_wires_and_material(marking, "Этикетка 25×9 мм", [{"outerDiameter": 1.6}])
    group_id, tasks = make_group(marking, "second")
    with factory() as uow:
        member = uow.session.scalar(select(CuttingMemberRow).where(CuttingMemberRow.task_id == tasks[1].id))
        snapshot = deepcopy(member.row_snapshot)
        for mark in snapshot["document"]["operations"][0]["fields"]["marks"]:
            mark["material"] = "Этикетка 30×9 мм"
        snapshot["document"]["operations"][1]["fields"]["wires"] = [{"outerDiameter": 2.2}]
        member.row_snapshot = snapshot
        uow.commit()
    response = client.get(BASE + "/progress", params={"group_id": group_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["label_size"]["width_mm"] for row in body["rows"]] == [25.0, 30.0]
    assert [row["wire_mm"] for row in body["rows"]] == [[1.6], [2.2]]
    assert (body["label_size"], body["sizes_differ"], body["wire_mm"]) == (None, True, [1.6, 2.2])
