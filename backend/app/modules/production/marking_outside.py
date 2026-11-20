"""«Отметить напечатанные вне ERP» (owner 20.11.2026): numbered labels printed outside the ERP count as printed.

A mark is one numbered label of a task — (task, item serial, expanded text) with its pieces — written by hand when the
label was printed outside the ERP (708К.: №1 of every КБ). `marking_progress.printed_counts` adds the marks to the
print queue, so «Напечатано / Осталось» and «Печатать оставшиеся» see them. Fixed (not numbered) labels are never
marked: they carry no serial to tell one item's label from another's.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base

from .marking_sources import TOKEN, labels


class MarkingOutsidePrintRow(Base):
    __tablename__ = "marking_outside_prints"
    __table_args__ = (
        UniqueConstraint("task_id", "serial", "text", name="uq_marking_outside_prints_label"),
        CheckConstraint("serial >= 1", name="ck_marking_outside_prints_serial"),
        CheckConstraint("quantity >= 1", name="ck_marking_outside_prints_quantity"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("production_tasks.id", ondelete="RESTRICT", name="fk_marking_outside_prints_task"), nullable=False)
    serial: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    comment: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")


def numbered_mark_keys(document: Mapping | None, task) -> set[str]:
    """Keys (`task:operation:mark`) of the numbered inscriptions of the ETK document."""
    return {f"{task.id}:{op_index}:{mark_index}"
            for op_index, operation in enumerate((document or {}).get("operations") or [])
            for mark_index, mark in enumerate((operation.get("fields") or {}).get("marks") or [])
            if TOKEN.search(str(mark.get("text") or ""))}


def outside_labels(document: Mapping | None, task, serial_range, serials: Iterable[int],
                   quantity: int | None = None, *, strict: bool = True) -> list[dict]:
    """The numbered labels of these item serials to mark: [{serial, text, quantity, count}], `count` — pieces of the
    label in the ETK, `quantity` — `count` unless `quantity` asks for fewer pieces. Refuses more pieces than the ETK has.
    A serial outside the task's range is refused when the КБ was chosen (`strict`); for «Все КБ группы» it comes back
    as {serial, out_of_range: True} — a relaunched task of the same КБ numbers other items (№16–30). A task without
    numbered labels gives nothing."""
    keys = numbered_mark_keys(document, task)
    if not keys:
        return []
    if serial_range is None and strict:
        raise ValueError(f"{task.item}: номера изделий освобождены — отмечать нечего")
    first, last = serial_range or (1, 0)
    result = []
    for serial in sorted(set(serials)):
        if not first <= serial <= last:
            if strict:
                raise ValueError(f"{task.item}: номер {serial} вне номеров изделий {first}–{last}")
            result.append({"serial": serial, "text": "", "quantity": 0, "count": 0, "out_of_range": True})
            continue
        counts: Counter = Counter()
        for row in labels(document, task, (serial, serial), keys):
            counts[row["text"]] += int(row["quantity"])
        for text, count in counts.items():
            if quantity is not None and quantity > count:
                raise ValueError(f"{task.item} №{serial}: «{text}» — в ЭТК {count} шт., отметить {quantity} нельзя")
            result.append({"serial": serial, "text": text, "quantity": quantity or count, "count": count})
    return result
