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

Labels marked «напечатаны вне ERP» (`production.marking_outside`, owner 20.11) count as printed too.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import re
from uuid import UUID

from sqlalchemy import Text, cast, or_, select

from app.modules.production.domain import mark_serial_range
from app.modules.production.marking_outside import MarkingOutsidePrintRow
from app.modules.production.marking_sources import TOKEN, expand_serial, labels, serial_designation
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
    """{(task id, serial, text): labels printed or in the printer queue, or marked printed outside the ERP};
    a reprinted label counts once."""
    ids = {str(identifier) for identifier in task_ids}
    return outside_counts(session, ids) + queue_counts(session, ids)


def queue_counts(session, task_ids: Iterable) -> Counter:
    """{(task id, serial, text): labels printed or in the printer queue of the ERP}; a reprinted label counts once."""
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


def outside_counts(session, task_ids: Iterable) -> Counter:
    """{(task id, serial, text): pieces} marked «напечатаны вне ERP»."""
    wanted = sorted({UUID(str(identifier)) for identifier in task_ids}, key=str)
    counts: Counter = Counter()
    for start in range(0, len(wanted), CHUNK):
        query = select(MarkingOutsidePrintRow).where(MarkingOutsidePrintRow.task_id.in_(wanted[start:start + CHUNK]))
        for row in session.scalars(query):
            counts[(str(row.task_id), row.serial, row.text)] += row.quantity
    return counts


def fixed_keys(document: Mapping | None, task) -> set[str]:
    """Keys (`task:operation:mark`) of the fixed — not numbered — inscriptions of the ETK document."""
    return {f"{task.id}:{op_index}:{mark_index}"
            for op_index, operation in enumerate((document or {}).get("operations") or [])
            for mark_index, mark in enumerate((operation.get("fields") or {}).get("marks") or [])
            if not TOKEN.search(str(mark.get("text") or ""))}


def numbered_keys(document: Mapping | None, task, serial_range) -> set[tuple]:
    """(task, serial, text) of every numbered label of the ETK document over the serial range."""
    if serial_range is None:
        return set()
    return {(str(task.id), serial, expand_serial(str(mark.get("text") or ""), serial))
            for operation in (document or {}).get("operations") or []
            for mark in (operation.get("fields") or {}).get("marks") or []
            if TOKEN.search(str(mark.get("text") or ""))
            for serial in range(serial_range[0], serial_range[1] + 1)}


def remaining_rows(rows: Sequence[Mapping], counts: Counter, fixed: Iterable[str] = (),
                   numbered: Iterable[tuple] = ()) -> list[dict]:
    """The expanded rows (`marking_sources.labels`) minus what was already printed: «Печатать оставшиеся».

    `fixed` — keys of fixed inscriptions (`fixed_keys`): an interleaved preview gives their rows the item serial.
    A row without a serial is always fixed. Numbered rows take exact (task, serial, text) matches first; a fixed row
    then takes its own item's labels, then labels of plain batches (no serial); a fixed row without a serial (plain
    preview) finally takes fixed labels printed inside any interleaved item — never a numbered label whose expanded
    text happens to equal the fixed text (`numbered`, `numbered_keys`, plus the numbered rows themselves)."""
    fixed = set(fixed)
    left = Counter(counts)
    keys = [(str(row["task_id"]), row.get("serial"), str(row.get("text") or "")) for row in rows]
    is_fixed = [row.get("serial") is None or row.get("key") in fixed for row in rows]
    done = [0] * len(rows)
    numbered = set(numbered) | {key for key, fixed_row in zip(keys, is_fixed) if not fixed_row}

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
            for key in [key for key in left if key[0] == task and key[2] == text and key[1] is not None
                        and key not in numbered]:
                take(index, key)
    return [{**row, "quantity": int(row["quantity"]) - done[index]}
            for index, row in enumerate(rows) if int(row["quantity"]) > done[index]]


def task_progress(task, document: Mapping | None, serial_range, counts: Counter) -> dict:
    """Printed / remaining KB items: an item (serial) is printed when all its numbered labels are; without numbered
    labels — whole sets of the fixed labels. `remaining_labels` — label pieces still to print, numbered and fixed
    (what «Печатать оставшиеся» would print); 0 when the task released its serials (printing unavailable).
    `total_labels` / `printed_labels` — label pieces of the ETK over the serial range and those of them printed (the group
    card counters, owner 23.11): the same rows and matching as `remaining_labels`, printed = total − remaining.
    `label_size` / `sizes_differ` — the label size of the ETK marks; `wire_mm` — the ETK wire diameters (plan 21.11)."""
    size, differ = document_label_size(document)
    view = {"task_id": str(task.id), "task_number": task.number, "item": task.item, "quantity": task.quantity,
            "label_size": size, "sizes_differ": differ, "wire_mm": wire_diameters(document),
            "serial_first": serial_range[0] if serial_range else None,
            "serial_last": serial_range[1] if serial_range else None,
            "printed": 0, "remaining": task.quantity, "printed_serials": [], "remaining_serials": [],
            "remaining_labels": 0, "total_labels": 0, "printed_labels": 0}
    if serial_range is None or not document:
        return view
    rows = labels(document, task, serial_range)
    left = remaining_rows(rows, counts, fixed_keys(document, task), numbered_keys(document, task, serial_range))
    view["remaining_labels"] = sum(int(row["quantity"]) for row in left)
    view["total_labels"] = sum(int(row["quantity"]) for row in rows)
    view["printed_labels"] = view["total_labels"] - view["remaining_labels"]
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


# «W×H мм» in the ETK mark material (plan 21.11): × or x/х/X/Х/* between the numbers, decimals with a comma or a dot,
# «мм» optional after each number. A size followed by «м» (metres: a ribbon or tape roll) or «шт» is not a label.
# Only Godex label sizes (5…150 мм, marking_render.Geometry) count; a tube material is never a label.
_NUMBER = r"(\d{1,3}(?:[.,]\d+)?)(?!\d)(?![.,]\d)(?:\s*мм)?"
# «м» / «шт» as whole words only: «матовая», «мелованная», «штучная» after the size do not make it a roll.
_SIZE = re.compile(rf"(?<![\d.,]){_NUMBER}\s*[×xXхХ*]\s*{_NUMBER}(?!\s*(?:м|шт)(?![а-яёa-z]))", re.IGNORECASE)
_TUBE = re.compile(r"термоусад|трубк|\bтут\b|tube|sleeve", re.IGNORECASE)
SIZE_TOLERANCE_MM = 0.5


def label_size(material) -> dict | None:
    """{width_mm, height_mm, material} of the first «W×H» in the material that is a label size (a cable section
    «4х0,75» before it is skipped); None — the ETK does not say the size."""
    text = str(material or "").strip()
    if _TUBE.search(text):
        return None
    for match in _SIZE.finditer(text):
        width, height = (float(value.replace(",", ".")) for value in match.groups())
        if 5 <= width <= 150 and 5 <= height <= 150:
            return {"width_mm": width, "height_mm": height, "material": text}
    return None


def same_size(size: Mapping, width: float, height: float) -> bool:
    """The label fits the size ±0,5 мм in either orientation (a flag 92,5×20 is a 20×92,5 roll turned)."""
    return any(abs(float(size["width_mm"]) - w) <= SIZE_TOLERANCE_MM + 1e-9
               and abs(float(size["height_mm"]) - h) <= SIZE_TOLERANCE_MM + 1e-9
               for w, h in ((width, height), (height, width)))


def size_summary(sizes: Iterable[Mapping | None]) -> tuple[dict | None, bool]:
    """(the one size, False) when every known size agrees; (None, True) when they differ; (None, False) — none known."""
    distinct: list[Mapping] = []
    for size in sizes:
        if size is not None and not any(same_size(size, item["width_mm"], item["height_mm"]) for item in distinct):
            distinct.append(size)
    if len(distinct) == 1:
        return dict(distinct[0]), False
    return None, len(distinct) > 1


def _marks(document: Mapping | None):
    for operation in (document or {}).get("operations") or []:
        for mark in (operation.get("fields") or {}).get("marks") or []:
            if isinstance(mark, Mapping) and str(mark.get("text") or "").strip():
                yield mark


def document_label_size(document: Mapping | None) -> tuple[dict | None, bool]:
    """The label size of the ETK marks (label_size of each mark material) and whether the marks disagree."""
    return size_summary(label_size(mark.get("material")) for mark in _marks(document))


def wire_diameters(document: Mapping | None) -> list[float]:
    """Distinct outer wire diameters, mm, of the ETK operations (fields.wires[].outerDiameter, techcard_build)."""
    values = set()
    for operation in (document or {}).get("operations") or []:
        for wire in (operation.get("fields") or {}).get("wires") or []:
            value = wire.get("outerDiameter") if isinstance(wire, Mapping) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                values.add(round(float(value), 3))
    return sorted(values)


def factory_scope(task, document, uow):
    """(изделие, legacy) of the task's factory numbers (0222): изделие from the ETK (`serial_designation`); legacy —
    numbered labels of the task were printed before it got stored numbers: its count stays per item (the numbers on
    those labels must not move), and its numbers are stored only where no other item holds them."""
    designation = serial_designation(document, task) if document else None
    if not designation or task.id in uow.serials.stored_ranges([task.id]):
        return designation, False
    printed = printed_counts(uow.session, [task.id])
    return designation, any(serial is not None and count > 0 for (_task, serial, _text), count in printed.items())


def serial_range_of(task, uow, document=None):
    """Read-only serial range of the task (the same rule as the ETK screen, without issuing anything); ``document`` —
    the task's ETK: the factory-number count runs per (project, изделие) (0222, `factory_scope`)."""
    project = uow.production.for_project(task.project)
    ids = [item.id for item in project]
    designation, legacy = factory_scope(task, document, uow)
    return mark_serial_range(task, project, uow.production.mark_events(ids), uow.serials.stored_ranges(ids),
                             None if legacy else designation)

