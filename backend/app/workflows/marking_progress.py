"""Printed and remaining production marks per KB, from the Godex print queue (owner 20.11).

There is no counter of printed labels: a label is printed when a marking job of a batch whose manifest row names the
task, the serial and the text was handed to the printer (`spooled`, the printer not reporting an error/cancel); a job
still pending, taken or with an unknown result counts as taken too, so «Печатать оставшиеся» never prints it twice.
Failed and cancelled jobs are not printed. Rows match by (task, serial, text) — not by the ETK operation index of the
label key — so a rebuilt task (cut/labels/tubes split, same serials) keeps what was printed for it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import Text, cast, or_, select

from app.modules.production.domain import mark_serial_range
from app.modules.production.marking_sources import labels
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


def printed_counts(session, task_ids: Iterable[UUID]) -> Counter:
    """{(task id, serial, text): labels printed or in the printer queue}."""
    ids = {str(identifier) for identifier in task_ids}
    counts: Counter = Counter()
    batches = task_batches(session, ids)
    if not batches:
        return counts
    jobs = session.scalars(select(LabelJobRow).where(LabelJobRow.batch_id.in_([batch.id for batch in batches])))
    rows = {batch.id: _label_rows(batch.manifest) for batch in batches}
    for job in jobs:
        labels_of_batch = rows.get(job.batch_id) or []
        if job.sequence is None or not 1 <= job.sequence <= len(labels_of_batch) or not _printed(job):
            continue
        label = labels_of_batch[job.sequence - 1]
        if str(label.get("task_id")) in ids:
            counts[(str(label.get("task_id")), label.get("serial"), str(label.get("text") or ""))] += 1
    return counts


def remaining_rows(rows: Sequence[Mapping], counts: Counter) -> list[dict]:
    """The expanded rows (`marking_sources.labels`) minus what was already printed: «Печатать оставшиеся»."""
    left = Counter(counts)
    result = []
    for row in rows:
        key = (str(row["task_id"]), row.get("serial"), str(row.get("text") or ""))
        done = min(left[key], int(row["quantity"]))
        left[key] -= done
        if int(row["quantity"]) > done:
            result.append({**row, "quantity": int(row["quantity"]) - done})
    return result


def task_progress(task, document: Mapping | None, serial_range, counts: Counter) -> dict:
    """Printed / remaining KB items: an item (serial) is printed when all its numbered labels are; without numbered
    labels — whole sets of the fixed labels."""
    view = {"task_id": str(task.id), "task_number": task.number, "item": task.item, "quantity": task.quantity,
            "serial_first": serial_range[0] if serial_range else None,
            "serial_last": serial_range[1] if serial_range else None,
            "printed": 0, "remaining": task.quantity, "printed_serials": [], "remaining_serials": []}
    if serial_range is None or not document:
        return view
    rows = labels(document, task, serial_range)
    left = remaining_rows(rows, counts)
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

