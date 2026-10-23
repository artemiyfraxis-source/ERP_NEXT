"""Saved label templates (owner 23.10.2026, the label designer): one row per kind × format, no row — the built-in
template (`labels/defaults.py`). Saving is optimistic: the designer sends the version it loaded (null — it edited the
built-in one) and a newer save in between is a conflict, never overwritten. Migration 0143."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import JSON, CheckConstraint, DateTime, Integer, String, UniqueConstraint, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import Base

from .labels.template import KIND_LABELS, LabelTemplate, TemplateError, check_template

STALE = "Шаблон уже изменили: обновите страницу и повторите правку"


class StaleTemplate(RuntimeError):
    """The template was saved or reset since the designer loaded it."""

    def __init__(self, message: str = STALE):
        super().__init__(message)


class LabelTemplateRow(Base):
    """One saved template of a label kind × format (migration 0143)."""

    __tablename__ = "warehouse_label_templates"
    __table_args__ = (
        CheckConstraint("kind IN ('lot', 'intake')", name="ck_warehouse_label_templates_kind"),
        CheckConstraint("format IN ('58x30', '58x40', '100x50')", name="ck_warehouse_label_templates_format"),
        CheckConstraint("version >= 1", name="ck_warehouse_label_templates_version"),
        UniqueConstraint("kind", "format", name="uq_warehouse_label_templates_kind_format"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    template: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")


@dataclass(frozen=True)
class StoredTemplate:
    kind: str
    format: str
    template: LabelTemplate | None  # None — the stored JSON is no longer a valid template: the built-in one prints
    version: int
    updated_at: datetime
    updated_by: str


def _session(uow) -> Session:
    session = getattr(uow, "session", None)
    if session is None:
        raise RuntimeError("Unit of Work has not been entered")
    return session


def _stored(row: LabelTemplateRow) -> StoredTemplate:
    try:
        template = check_template(LabelTemplate.model_validate(row.template), row.kind, row.format)
    except (ValidationError, TemplateError):
        template = None
    return StoredTemplate(row.kind, row.format, template, row.version, row.updated_at, row.updated_by)


def stored_templates(uow, kind: str) -> dict[str, StoredTemplate]:
    """format → the saved template of the kind."""
    with uow:
        rows = _session(uow).scalars(select(LabelTemplateRow).where(LabelTemplateRow.kind == kind)).all()
        return {row.format: _stored(row) for row in rows}


def stored_template(uow, kind: str, label_format: str) -> StoredTemplate | None:
    with uow:
        return stored_template_in(_session(uow), kind, label_format)


def stored_template_in(session: Session, kind: str, label_format: str) -> StoredTemplate | None:
    """The same read inside a transaction already open (a receipt line queues its label together with the lot)."""
    row = session.scalar(select(LabelTemplateRow).where(
        LabelTemplateRow.kind == kind, LabelTemplateRow.format == label_format))
    return _stored(row) if row is not None else None


def _journal(uow, event_type: str, actor: Actor, now: datetime, details: dict) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=UUID(int=0), event_type=event_type, actor=actor, occurred_at=now, details=details,
    ))


def save_template(uow, kind: str, label_format: str, template: LabelTemplate, expected_version: int | None,
                  actor: Actor, now: datetime) -> StoredTemplate:
    """Save over the version the designer loaded (None — the built-in one). StaleTemplate — someone saved or reset it
    in between."""
    payload = template.model_dump(mode="json")
    name = f"{KIND_LABELS[kind]}, {label_format}"
    try:
        with uow:
            session = _session(uow)
            row = session.scalar(select(LabelTemplateRow).where(
                LabelTemplateRow.kind == kind, LabelTemplateRow.format == label_format))
            if row is None:
                if expected_version is not None:
                    raise StaleTemplate()
                version = 1
                session.add(LabelTemplateRow(
                    kind=kind, format=label_format, template=payload, version=version, updated_at=now,
                    updated_by=actor.name[:255],
                ))
            else:
                if expected_version != row.version:
                    raise StaleTemplate()
                version = row.version + 1
                done = session.execute(
                    update(LabelTemplateRow)
                    .where(LabelTemplateRow.id == row.id, LabelTemplateRow.version == expected_version)
                    .values(template=payload, version=version, updated_at=now, updated_by=actor.name[:255])
                    .execution_options(synchronize_session=False)
                )
                if done.rowcount != 1:
                    raise StaleTemplate()
            _journal(uow, "warehouse.label_template_saved", actor, now, {
                "kind": kind, "format": label_format, "version": version,
                "message": f"шаблон этикетки «{name}» сохранён (версия {version})",
            })
            uow.commit()
    except IntegrityError as error:  # a concurrent first save of the same kind × format
        raise StaleTemplate() from error
    return StoredTemplate(kind, label_format, template, version, now, actor.name[:255])


def reset_template(uow, kind: str, label_format: str, actor: Actor, now: datetime) -> bool:
    """Back to the built-in template; False — it was not saved."""
    with uow:
        done = _session(uow).execute(delete(LabelTemplateRow).where(
            LabelTemplateRow.kind == kind, LabelTemplateRow.format == label_format))
        if not done.rowcount:
            return False
        _journal(uow, "warehouse.label_template_reset", actor, now, {
            "kind": kind, "format": label_format,
            "message": f"шаблон этикетки «{KIND_LABELS[kind]}, {label_format}» — снова встроенный",
        })
        uow.commit()
    return True
