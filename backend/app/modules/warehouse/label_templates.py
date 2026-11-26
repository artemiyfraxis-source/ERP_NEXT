"""Saved templates, keyed by kind/category/format (0143/0218).

Category rows hold printer profiles beside their form metadata in JSON, under a shared
optimistic version. Reset retains that version to prevent stale saves after reset/recreate.
The empty category preserves historical templates and their existing reset contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import JSON, CheckConstraint, Date, DateTime, Integer, String, UniqueConstraint, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.events import AuditEvent
from app.shared.identity import Actor
from app.shared.persistence import Base

from .labels.template import KIND_LABELS, LabelTemplate, TemplateError, check_template
from .grn_registry import PREFIX_SQL


class FormMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    form_id: str = Field(min_length=1, max_length=128)
    form_revision: str = Field(min_length=1, max_length=64)
    form_effective_date: date

    def footer(self) -> str:
        return f"Форма {self.form_id} Rev.{self.form_revision} {self.form_effective_date:%d.%m.%Y}"

STALE = "Шаблон уже изменили: обновите страницу и повторите правку"


class StaleTemplate(RuntimeError):
    """The template was saved or reset since the designer loaded it."""

    def __init__(self, message: str = STALE):
        super().__init__(message)


class LabelTemplateRow(Base):
    """One versioned category/format row; printer overrides live in its JSON envelope."""

    __tablename__ = "warehouse_label_templates"
    __table_args__ = (
        CheckConstraint("kind IN ('lot', 'intake', 'instrument')", name="ck_warehouse_label_templates_kind"),
        CheckConstraint("format IN ('58x30', '58x40', '100x50')", name="ck_warehouse_label_templates_format"),
        CheckConstraint("version >= 1", name="ck_warehouse_label_templates_version"),
        UniqueConstraint("kind", "category", "format", name="uq_label_templates_kind_category_format"),
        CheckConstraint(f"category = '' OR category IN ({PREFIX_SQL})", name="ck_label_templates_category"),
        CheckConstraint("kind <> 'instrument' OR category = ''", name="ck_label_templates_instrument_category"),
        CheckConstraint("(form_id IS NULL AND form_revision IS NULL AND form_effective_date IS NULL) OR "
                        "(form_id IS NOT NULL AND trim(form_id) <> '' AND form_revision IS NOT NULL "
                        "AND trim(form_revision) <> '' AND form_effective_date IS NOT NULL)", name="ck_label_templates_form"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    category: Mapped[str] = mapped_column(String(3), nullable=False, default="", server_default="")
    form_id: Mapped[str | None] = mapped_column(String(128))
    form_revision: Mapped[str | None] = mapped_column(String(64))
    form_effective_date: Mapped[date | None] = mapped_column(Date)
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
    category: str = ""
    form: FormMetadata | None = None


def _session(uow) -> Session:
    session = getattr(uow, "session", None)
    if session is None:
        raise RuntimeError("Unit of Work has not been entered")
    return session


def _stored(row: LabelTemplateRow, printer_name: str = "") -> StoredTemplate:
    raw = row.template
    form = None
    try:
        if row.form_id:
            form = FormMetadata(form_id=row.form_id, form_revision=row.form_revision,
                                form_effective_date=row.form_effective_date)
        if "profiles" in raw:
            profiles = raw["profiles"]
            if not isinstance(profiles, dict):
                raise TemplateError("Некорректные профили принтеров")
            if printer_name in profiles:
                profile = profiles[printer_name]
                raw = profile["template"]
                if not raw:
                    raise TemplateError("Пустой шаблон принтера")
                form = FormMetadata.model_validate(profile["form"]) if profile.get("form") else None
            else:
                raw, form = None, None
        template = check_template(LabelTemplate.model_validate(raw), row.kind, row.format) if raw else None
    except (ValidationError, TemplateError, KeyError, TypeError, AttributeError):
        if row.category:
            raise TemplateError("Сохранённый шаблон ГРН повреждён: исправьте или сбросьте его")
        template = None
    return StoredTemplate(row.kind, row.format, template, row.version, row.updated_at, row.updated_by, row.category, form)


def stored_templates(uow, kind: str, category: str = "", printer_name: str = "") -> dict[str, StoredTemplate]:
    """format → the saved template of the kind."""
    with uow:
        rows = _session(uow).scalars(select(LabelTemplateRow).where(LabelTemplateRow.kind == kind,
                                                                  LabelTemplateRow.category == category)).all()
        return {row.format: _stored(row, printer_name) for row in rows}


def stored_template(uow, kind: str, label_format: str, category: str = "", printer_name: str = "") -> StoredTemplate | None:
    with uow:
        return stored_template_in(_session(uow), kind, label_format, category, printer_name)


def stored_template_in(session: Session, kind: str, label_format: str, category: str = "",
                       printer_name: str = "") -> StoredTemplate | None:
    """The same read inside a transaction already open (a receipt line queues its label together with the lot)."""
    row = session.scalar(select(LabelTemplateRow).where(
        LabelTemplateRow.kind == kind, LabelTemplateRow.format == label_format, LabelTemplateRow.category == category))
    return _stored(row, printer_name) if row is not None else None


def _journal(uow, event_type: str, actor: Actor, now: datetime, details: dict) -> None:
    uow.audit.add(AuditEvent(
        id=uuid4(), aggregate_id=UUID(int=0), event_type=event_type, actor=actor, occurred_at=now, details=details,
    ))


def save_template(uow, kind: str, label_format: str, template: LabelTemplate, expected_version: int | None,
                  actor: Actor, now: datetime, *, category: str = "", printer_name: str = "",
                  form: FormMetadata | None = None) -> StoredTemplate:
    """Save over the version the designer loaded (None — the built-in one). StaleTemplate — someone saved or reset it
    in between."""
    payload = template.model_dump(mode="json")
    name = f"{KIND_LABELS[kind]}, {label_format}"
    try:
        with uow:
            session = _session(uow)
            row = session.scalar(select(LabelTemplateRow).where(
                LabelTemplateRow.kind == kind, LabelTemplateRow.format == label_format, LabelTemplateRow.category == category))
            profile = {"template": payload, "form": form.model_dump(mode="json") if form else None}
            if category:
                # 0218 has one row per category/kind/format. Printer variants share its CAS version.
                profiles = dict((row.template if row else {}).get("profiles", {}))
                profiles[printer_name] = profile
                payload = {"profiles": profiles}
            form_values = {key: getattr(form, key) if form and not category else None
                           for key in ("form_id", "form_revision", "form_effective_date")}
            if row is None:
                if expected_version is not None:
                    raise StaleTemplate()
                version = 1
                session.add(LabelTemplateRow(
                    kind=kind, format=label_format, template=payload, version=version, updated_at=now,
                    updated_by=actor.name[:255], category=category, **form_values,
                ))
            else:
                if expected_version != row.version:
                    raise StaleTemplate()
                version = row.version + 1
                done = session.execute(
                    update(LabelTemplateRow)
                    .where(LabelTemplateRow.id == row.id, LabelTemplateRow.version == expected_version)
                    .values(template=payload, version=version, updated_at=now, updated_by=actor.name[:255], **form_values)
                    .execution_options(synchronize_session=False)
                )
                if done.rowcount != 1:
                    raise StaleTemplate()
            _journal(uow, "warehouse.label_template_saved", actor, now, {
                "kind": kind, "format": label_format, "version": version, "category": category, "printer_name": printer_name,
                "message": f"шаблон этикетки «{name}» сохранён (версия {version})",
            })
            uow.commit()
    except IntegrityError as error:  # a concurrent first save of the same kind × format
        raise StaleTemplate() from error
    return StoredTemplate(kind, label_format, template, version, now, actor.name[:255], category, form)


def reset_template(uow, kind: str, label_format: str, actor: Actor, now: datetime, *, category: str = "",
                   printer_name: str = "", expected_version: int | None = None) -> bool:
    """Back to the built-in template; False — it was not saved."""
    with uow:
        if category:
            row = _session(uow).scalar(select(LabelTemplateRow).where(LabelTemplateRow.kind == kind,
                LabelTemplateRow.format == label_format, LabelTemplateRow.category == category))
            if row is None:
                if expected_version is not None:
                    raise StaleTemplate()
                return False
            if row.version != expected_version:
                raise StaleTemplate()
            profiles = dict(row.template.get("profiles", {}))
            profiles.pop(printer_name, None)
            done = _session(uow).execute(update(LabelTemplateRow).where(LabelTemplateRow.id == row.id,
                LabelTemplateRow.version == expected_version).values(template={"profiles": profiles},
                version=row.version + 1, updated_at=now, updated_by=actor.name[:255])
                .execution_options(synchronize_session=False))
            if done.rowcount != 1:
                raise StaleTemplate()
            _journal(uow, "warehouse.label_template_reset", actor, now,
                     {"kind": kind, "format": label_format, "category": category, "printer_name": printer_name})
            uow.commit()
            return True
        done = _session(uow).execute(delete(LabelTemplateRow).where(
            LabelTemplateRow.kind == kind, LabelTemplateRow.format == label_format, LabelTemplateRow.category == ""))
        if not done.rowcount:
            return False
        _journal(uow, "warehouse.label_template_reset", actor, now, {
            "kind": kind, "format": label_format,
            "message": f"шаблон этикетки «{KIND_LABELS[kind]}, {label_format}» — снова встроенный",
        })
        uow.commit()
    return True
