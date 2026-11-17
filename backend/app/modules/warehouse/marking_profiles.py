"""Versioned marking geometry, exact printer bindings and immutable batch manifests."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base


class MarkingProfileRow(Base):
    __tablename__ = "warehouse_marking_profiles"
    __table_args__ = (
        UniqueConstraint("inventory_number", "name", name="uq_marking_profile_preset"),
        CheckConstraint("version >= 1", name="ck_marking_profile_version"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    equipment_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT"))
    inventory_number: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    calibrated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    geometry: Mapped[dict] = mapped_column(JSON, nullable=False)


class MarkingBindingRow(Base):
    __tablename__ = "warehouse_marking_bindings"
    __table_args__ = (
        CheckConstraint("version >= 1 AND trim(printer) <> ''", name="ck_marking_binding_values"),
        ForeignKeyConstraint(["machine_id", "printer"], ["warehouse_agent_printers.machine_id", "warehouse_agent_printers.name"],
                             name="fk_marking_binding_printer", ondelete="RESTRICT"),
    )
    equipment_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    machine_id: Mapped[str] = mapped_column(String(80), ForeignKey("warehouse_scan_agents.machine_id", ondelete="RESTRICT"), nullable=False)
    printer: Mapped[str] = mapped_column(String(255), nullable=False)


class MarkingBatchRow(Base):
    __tablename__ = "warehouse_marking_batches"
    __table_args__ = (
        CheckConstraint("total BETWEEN 1 AND 500", name="ck_marking_batch_total"),
        UniqueConstraint("created_by", "idempotency_key", name="uq_marking_batch_request"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("warehouse_marking_profiles.id", ondelete="RESTRICT"), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    reprint_of: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("warehouse_marking_batches.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
