"""Printed and remaining production marks per KB, from the Godex print queue (owner 20.11).

There is no counter of printed labels: a label is printed when a marking job of a batch whose manifest row names the
task, the serial and the text was handed to the printer (`spooled`, the printer not reporting an error/cancel); a job
still pending, taken or with an unknown result counts as taken too, so «Печатать оставшиеся» never prints it twice.
Failed and cancelled jobs are not printed. Rows match by (task, serial, text) — not by the ETK operation index of the
label key — so a rebuilt task (cut/labels/tubes split, same serials) keeps what was printed for it.

A batch printed «чередовать по изделию» carries the item serial on its fixed labels too (XS1 of item 1 is
(task, 1, "XS1")); a plain batch prints them without one ((task, None, "XS1")). A fixed row of either preview counts
both, so neither way of printing makes the other reprint fixed labels. A «Перепечатать номера…» job replaces the label
it reprints: one physical label of the root batch counts once, however many times it was reprinted.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import Text, cast, or_, select

from app.modules.production.domain import mark_serial_range
from app.modules.production.marking_sources import TOKEN, labels
from app.modules.warehouse.label_jobs import LabelJobRow
from app.modules.warehouse.marking_profiles import MarkingBatchRow

TAKEN = ("pending", "taken", "unknown")
CHUNK = 200


def _printed(job) -> bool:
    if job.status in TAKEN:
        return True
    if job.status != "spooled":
        return False
    receipt = job.receipt or {}
    return not receipt.get("tracked") or receipt.get("print_state") not in ("error", "cancelled")


def _label_rows(manifest: Mapping) -> list[Mapping]:
    return [label for label in manifest.get("labels", []) for _ in range(int(label.get("quantity") or 1))]


def task_batches(session, task_ids: Iterable[UUID]) -> list[MarkingBatchRow]:
    """Marking batches whose manifest names any of the tasks (the manifest is JSON: narrowed by its text, then exact)."""
    wanted = sorted({str(identifier) for identifier in task_ids})
    found = {}
    for start in range(0, len(wanted), CHUNK):
        part = wanted[start:start + CHUNK]
        text = cast(MarkingBatchRow.manifest, Text)
        for batch in session.scalars(select(MarkingBatchRow).where(or_(*(text.contains(value) for value in part)))):
            names = {str(label.get("task_id")) for label in batch.manifest.get("labels", [])}
            if names & set(part):
                found[batch.id] = batch
    return sorted(found.values(), key=lambda batch: (batch.created_at, str(batch.id)))


def _original(batches: Mapping, batch, sequence: int) -> tuple:
    """The label a job prints, as (root batch id, sequence): a reprint job resolves to the label it reprints
    (manifest.reprint.sequences per step; an older whole-batch reprint keeps the numbering)."""
    seen = set()
    while batch.reprint_of is not None and batch.reprint_of in batches and batch.id not in seen:
        seen.add(batch.id)
        sources = ((batch.manifest or {}).get("reprint") or {}).get("sequences")
        if sources is not None:
            if not 0 < sequence <= len(sources):
                break
            sequence = sources[sequence - 1]
        batch = batches[batch.reprint_of]
    return batch.id, sequence


def printed_counts(session, task_ids: Iterable[UUID]) -> Counter:
    """{(task id, serial, text): labels printed or in the printer queue}; a reprinted label counts once."""
    ids = {str(identifier) for identifier in task_ids}
    counts: Counter = Counter()
    batches = task_batches(session, ids)
    if not batches:
        return counts
    jobs = session.scalars(select(LabelJobRow).where(LabelJobRow.batch_id.in_([batch.id for batch in batches])))
    by_id = {batch.id: batch for batch in batches}
    rows = {batch.id: _label_rows(batch.manifest) for batch in batches}
    seen = set()
    for job in jobs:
        labels_of_batch = rows.get(job.batch_id) or []
        if job.sequence is None or not 1 <= job.sequence <= len(labels_of_batch) or not _printed(job):
            continue
        label = labels_of_batch[job.sequence - 1]
        if str(label.get("task_id")) not in ids:
            continue
        original = _original(by_id, by_id[job.batch_id], job.sequence)
        if original in seen:
            continue
        seen.add(original)
        counts[(str(label.get("task_id")), label.get("serial"), str(label.get("text") or ""))] += 1
    return counts


def fixed_keys(document: Mapping | None, task) -> set[str]:
    """Keys (`task:operation:mark`) of the fixed — not numbered — inscriptions of the ETK document."""
    return {f"{task.id}:{op_index}:{mark_index}"
            for op_index, operation in enumerate((document or {}).get("operations") or [])
            for mark_index, mark in enumerate((operation.get("fields") or {}).get("marks") or [])
            if not TOKEN.search(str(mark.get("text") or ""))}


def remaining_rows(rows: Sequence[Mapping], counts: Counter, fixed: Iterable[str] = ()) -> list[dict]:
    """The expanded rows (`marking_sources.labels`) minus what was already printed: «Печатать оставшиеся».

    `fixed` — keys of fixed inscriptions (`fixed_keys`): an interleaved preview gives their rows the item serial.
    A row without a serial is always fixed. Numbered rows take exact (task, serial, text) matches first; a fixed row
    then takes its own item's labels, then labels of plain batches (no serial); a fixed row without a serial (plain
    preview) finally takes fixed labels printed inside any interleaved item."""
    fixed = set(fixed)
    left = Counter(counts)
    keys = [(str(row["task_id"]), row.get("serial"), str(row.get("text") or "")) for row in rows]
    is_fixed = [row.get("serial") is None or row.get("key") in fixed for row in rows]
    done = [0] * len(rows)

    def take(index, key):
        part = min(max(left[key], 0), int(rows[index]["quantity"]) - done[index])
        left[key] -= part
        done[index] += part

    for index in sorted(range(len(rows)), key=lambda index: is_fixed[index]):
        take(index, keys[index])
    for index, (task, _serial, text) in enumerate(keys):
        if is_fixed[index]:
            take(index, (task, None, text))
    for index, (task, serial, text) in enumerate(keys):
        if is_fixed[index] and serial is None:
            for key in [key for key in left if key[0] == task and key[2] == text and key[1] is not None]:
                take(index, key)
    return [{**row, "quantity": int(row["quantity"]) - done[index]}
            for index, row in enumerate(rows) if int(row["quantity"]) > done[index]]


def task_progress(task, document: Mapping | None, serial_range, counts: Counter) -> dict:
    """Printed / remaining KB items: an item (serial) is printed when all its numbered labels are; without numbered
    labels — whole sets of the fixed labels. `remaining_labels` — label pieces still to print, numbered and fixed
    (what «Печатать оставшиеся» would print); 0 when the task released its serials (printing unavailable)."""
    view = {"task_id": str(task.id), "task_number": task.number, "item": task.item, "quantity": task.quantity,
            "serial_first": serial_range[0] if serial_range else None,
            "serial_last": serial_range[1] if serial_range else None,
            "printed": 0, "remaining": task.quantity, "printed_serials": [], "remaining_serials": [],
            "remaining_labels": 0}
    if serial_range is None or not document:
        return view
    rows = labels(document, task, serial_range)
    left = remaining_rows(rows, counts, fixed_keys(document, task))
    view["remaining_labels"] = sum(int(row["quantity"]) for row in left)
    serials = range(serial_range[0], serial_range[1] + 1)
    numbered = [row for row in rows if row.get("serial") is not None]
    if numbered:
        open_serials = {row["serial"] for row in left if row.get("serial") is not None}
        printed = [serial for serial in serials if serial not in open_serials]
        view.update(printed=len(printed), remaining=task.quantity - len(printed), printed_serials=printed,
                    remaining_serials=[serial for serial in serials if serial in open_serials])
        return view
    sets = []
    for row in rows:
        count = int(row["quantity"]) // max(task.quantity, 1) or 1
        done = int(row["quantity"]) - sum(int(item["quantity"]) for item in left if item["key"] == row["key"])
        sets.append(done // count)
    printed = min(sets) if sets else 0
    view.update(printed=printed, remaining=task.quantity - printed)
    return view


def serial_range_of(task, uow):
    """Read-only serial range of the task (the same rule as the ETK screen, without issuing anything)."""
    project = uow.production.for_project(task.project)
    return mark_serial_range(task, project, uow.production.mark_events([item.id for item in project]))

