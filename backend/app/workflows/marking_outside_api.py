"""«Отметить напечатанные вне ERP» of a labels cutting group (owner 20.11.2026).

708К.: label №1 of every КБ was printed outside the ERP; the print queue knows nothing of it, so «Печатать оставшиеся»
would print it again. The master marks the item serials printed outside — every numbered label of those serials of
every (or the chosen) КБ of the group — and «Снять отметку» takes a mark back. Both are journaled as
`production.marking_printed_outside` and need the right of «Печатать оставшиеся».
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.modules.production.application import (ConcurrentProductionUpdate, allocate_factory_numbers_in,
                                                issue_mark_serials_in)
from app.modules.production.cutting_application import locked_group
from app.modules.production.marking_outside import MarkingOutsidePrintRow, numbered_mark_keys, outside_labels
from app.workflows.marking_labels import Factory, Now, PrintUser, ReadUser, audit, checked, task_access
from app.workflows.marking_progress import factory_scope, queue_counts

router = APIRouter(prefix="/api/production/marking/outside", tags=["production"])
Serial = Annotated[int, Field(ge=1, le=100_000, strict=True)]


class OutsideInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: UUID
    version: int = Field(ge=1, strict=True)
    # Empty — every КБ of the group; otherwise only these tasks (КБ) of it.
    task_ids: list[UUID] = Field(default_factory=list, max_length=500)
    serials: list[Serial] = Field(min_length=1, max_length=500)
    # Pieces of each label printed outside; empty — as many as the ETK has.
    quantity: int | None = Field(default=None, ge=1, le=500, strict=True)
    comment: str = Field(default="", max_length=1000)
    # «Что будет отмечено»: the same answer without writing anything.
    dry_run: bool = False


def _scope(payload, principal, uow, now):
    """The group locked like every cutting path (project → group → tasks), its chosen КБ with the ETK they print."""
    group = locked_group(payload.group_id, payload.version, uow, principal.actor, now)
    members = uow.cutting.members(group.id)
    chosen = set(payload.task_ids)
    if chosen - {member.task_id for member in members}:
        raise ValueError("Выбранный КБ не входит в группу")
    entries = []
    for member in members:
        if chosen and member.task_id not in chosen:
            continue
        task = uow.production.get(member.task_id)
        task_access(task, member.operation_id, principal, uow)
        document = member.row_snapshot.get("document")
        if not document:
            raise ConcurrentProductionUpdate("В группе нет сохранённой ЭТК")
        entries.append((task, document))
    return group, entries


def _row(task, serial, text, quantity, state):
    return {"task_id": str(task.id), "item": task.item, "task_number": task.number, "serial": serial, "text": text,
            "quantity": quantity, "state": state}


def _journal(uow, principal, now, group, change, rows, payload, detail):
    """`labels_detail` — every label marked or taken back, so the change can be rebuilt (undo deletes the rows)."""
    audit(uow, principal.actor, now, group.id, "printed_outside", change=change, group_number=group.number,
          serials=sorted(set(payload.serials)), items=sorted({row["item"] for row in rows}),
          labels=sum(row["quantity"] for row in rows), comment=payload.comment.strip(), labels_detail=detail)


def _state(label, existing, queued):
    """new / exists (marked before) / printed (the ERP queue printed it: marking it too would count it twice) /
    out_of_range (not an item of this task); `quantity` — the pieces a new mark takes."""
    if label.get("out_of_range"):
        return "out_of_range", 0
    if (label["serial"], label["text"]) in existing:
        return "exists", label["quantity"]
    left = label["count"] - queued
    return ("new", min(label["quantity"], left)) if left > 0 else ("printed", label["quantity"])


@router.post("")
def mark(payload: OutsideInput, principal: PrintUser, factory: Factory, now: Now):
    def run():
        with factory() as uow:
            group, entries = _scope(payload, principal, uow, now)
            # «Все КБ группы»: a task that does not number this item is skipped; a chosen КБ refuses it.
            strict = bool(payload.task_ids)
            queue = queue_counts(uow.session, [task.id for task, _document in entries])
            rows, new, pending = [], [], {}
            for task, document in entries:
                if not numbered_mark_keys(document, task):
                    continue
                designation, template, legacy = factory_scope(task, document, uow)
                count_by = None if legacy else designation
                serial_range = issue_mark_serials_in(task, False, principal.actor, now, uow, count_by, pending)
                if serial_range and count_by:
                    pending[task.id] = (*serial_range, count_by)
                planned = outside_labels(document, task, serial_range, payload.serials, payload.quantity, strict=strict)
                existing = set(uow.session.execute(
                    select(MarkingOutsidePrintRow.serial, MarkingOutsidePrintRow.text)
                    .where(MarkingOutsidePrintRow.task_id == task.id)).all())
                fresh = []
                for label in planned:
                    state, quantity = _state(label, existing, queue[(str(task.id), label["serial"], label["text"])])
                    rows.append(_row(task, label["serial"], label["text"], quantity, state))
                    if state == "new":
                        fresh.append({**label, "quantity": quantity})
                new.extend((task, label) for label in fresh)
                if fresh and not payload.dry_run:
                    issue_mark_serials_in(task, True, principal.actor, now, uow, count_by, pending)  # spent outside
                    allocate_factory_numbers_in(task, serial_range, designation, principal.actor, now, uow,
                                                template=template)  # 0222: printed outside — stored too
            if not rows:
                raise ValueError("В ЭТК выбранных КБ нет номерных этикеток — отмечать нечего")
            if not payload.dry_run and new:
                for task, label in new:
                    uow.session.add(MarkingOutsidePrintRow(
                        id=uuid4(), task_id=task.id, serial=label["serial"], text=label["text"],
                        quantity=label["quantity"], actor_id=principal.actor.id, actor_name=principal.actor.name,
                        at=now, comment=payload.comment.strip()))
                _journal(uow, principal, now, group, "mark", [row for row in rows if row["state"] == "new"], payload,
                         [{"task_id": str(task.id), "serial": label["serial"], "text": label["text"],
                           "quantity": label["quantity"]} for task, label in new])
                uow.commit()
            return {"dry_run": payload.dry_run, "rows": rows, "added": sum(row["state"] == "new" for row in rows),
                    "existing": sum(row["state"] == "exists" for row in rows)}
    return checked(run)


@router.post("/undo")
def undo(payload: OutsideInput, principal: PrintUser, factory: Factory, now: Now):
    """«Снять отметку»: the marks of these serials of the chosen КБ go away (their labels read as not printed)."""
    def run():
        with factory() as uow:
            group, entries = _scope(payload, principal, uow, now)
            serials = sorted(set(payload.serials))
            rows, ids, detail = [], [], []
            for task, _document in entries:
                for row in uow.session.scalars(select(MarkingOutsidePrintRow).where(
                        MarkingOutsidePrintRow.task_id == task.id, MarkingOutsidePrintRow.serial.in_(serials))
                        .order_by(MarkingOutsidePrintRow.serial, MarkingOutsidePrintRow.text)):
                    rows.append(_row(task, row.serial, row.text, row.quantity, "removed"))
                    ids.append(row.id)
                    detail.append({"task_id": str(task.id), "serial": row.serial, "text": row.text,
                                   "quantity": row.quantity, "actor_id": str(row.actor_id), "actor": row.actor_name,
                                   "at": row.at.isoformat(), "comment": row.comment})
            if ids and not payload.dry_run:
                uow.session.execute(delete(MarkingOutsidePrintRow).where(MarkingOutsidePrintRow.id.in_(ids)))
                _journal(uow, principal, now, group, "undo", rows, payload, detail)
                uow.commit()
            return {"dry_run": payload.dry_run, "rows": rows, "removed": len(rows)}
    return checked(run)


@router.get("")
def marks(group_id: UUID, principal: ReadUser, factory: Factory):
    """The marks of the group's КБ, for the window and «Снять отметку»."""
    def run():
        with factory() as uow:
            group = uow.cutting.get(group_id)
            if group is None:
                raise KeyError("Группа резки не найдена")
            rows = []
            for member in uow.cutting.members(group.id):
                task = uow.production.get(member.task_id)
                task_access(task, member.operation_id, principal, uow, historical=True)
                for row in uow.session.scalars(select(MarkingOutsidePrintRow)
                                               .where(MarkingOutsidePrintRow.task_id == task.id)
                                               .order_by(MarkingOutsidePrintRow.serial, MarkingOutsidePrintRow.text)):
                    rows.append({**_row(task, row.serial, row.text, row.quantity, "exists"),
                                 "actor": row.actor_name, "at": row.at.isoformat(), "comment": row.comment})
            return {"rows": rows}
    return checked(run)
