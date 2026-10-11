"""Черновик генератора ТК на сервере (migration 0102): the ORM row only; use cases in generator_drafts.py.

One row per person, project and КБ: the unfinished generator form (JSON), the fingerprint of the card's build config it
was typed over, and a version the next save must name (optimistic check)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base

# The JSON text of a draft (the database refuses a longer one; the API refuses well before, see generator_drafts.py).
MAX_PAYLOAD_CHARS = 1_048_576


class GeneratorDraftRow(Base):
    __tablename__ = "techcard_generator_drafts"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_techcard_generator_drafts_version"),
        CheckConstraint("project <> ''", name="ck_techcard_generator_drafts_project"),
        CheckConstraint("item_index <> ''", name="ck_techcard_generator_drafts_item_index"),
        CheckConstraint(
            f"length(CAST(payload AS TEXT)) <= {MAX_PAYLOAD_CHARS}", name="ck_techcard_generator_drafts_payload_size",
        ),
        UniqueConstraint("user_id", "project", "item_index", name="uq_techcard_generator_drafts_user_position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("identity_users.id", ondelete="RESTRICT", name="fk_techcard_generator_drafts_user_id"),
        nullable=False,
    )
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    item_index: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    build_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
