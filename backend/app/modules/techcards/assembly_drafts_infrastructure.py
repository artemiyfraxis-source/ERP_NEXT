"""Черновики схем сборки («чертёж → схема сборки», migration 0088): the ORM row only — no API or use cases yet.

A draft belongs to a project + КБ, may point at the КД drawing it was read from, and records who made it
(`source`: claude-code / server-agent / manual), the draft config, then the final config, the techcard revision it went
into and the lesson learnt from the difference."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base

from . import kitset_infrastructure as _kitsets  # noqa: F401  (the drawing FK target in the same metadata)


ASSEMBLY_DRAFT_SOURCES = ("claude-code", "server-agent", "manual")
ASSEMBLY_DRAFT_STATUSES = ("draft", "final")


class AssemblyDraftRow(Base):
    __tablename__ = "techcard_assembly_drafts"
    __table_args__ = (
        Index("ix_techcard_assembly_drafts_project_item", "project", "item_index"),
        CheckConstraint("status IN ('draft', 'final')", name="ck_techcard_assembly_drafts_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item_index: Mapped[str] = mapped_column(String(64), nullable=False)
    drawing_document_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "techcard_kitset_documents.id",
            ondelete="SET NULL",
            name="fk_techcard_assembly_drafts_drawing_document_id",
        ),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    draft_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    final_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    final_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lesson: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
