"""Production-owned cutting group history; children remain ordinary production operations."""

from datetime import datetime
from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, Numeric, String, UniqueConstraint, Uuid, select, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base
from .cutting import CuttingRecipe, RECIPE_VERSION, digest
from .domain import ProductionStatus, ProductionValidationError


class CuttingGroupRow(Base):
    __tablename__ = "production_cutting_groups"
    __table_args__ = (
        CheckConstraint("version > 0 AND recipe_version > 0", name="ck_cutting_group_versions"),
        CheckConstraint("state IN ('open', 'frozen', 'cancelled')", name="ck_cutting_group_state"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    project_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    site: Mapped[str] = mapped_column(String(32), nullable=False)
    recipe_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False)
    recipe: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    joinable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class CuttingMemberRow(Base):
    __tablename__ = "production_cutting_members"
    __table_args__ = (
        ForeignKeyConstraint(["task_id", "operation_id"], ["production_operations.task_id", "production_operations.id"],
                             ondelete="RESTRICT", name="fk_cutting_member_operation_task"),
        UniqueConstraint("group_id", "operation_id", name="ux_cutting_member_group_operation"),
        UniqueConstraint("group_id", "position", name="ux_cutting_member_position"),
        CheckConstraint("assembly_quantity > 0 AND blank_quantity > 0 AND position > 0", name="ck_cutting_member_quantities"),
        CheckConstraint("state IN ('active', 'split', 'cancelled')", name="ck_cutting_member_state"),
        Index("ux_cutting_member_active_operation", "operation_id", unique=True,
              sqlite_where=text("state = 'active'"), postgresql_where=text("state = 'active'")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    group_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_cutting_groups.id", ondelete="RESTRICT"), nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    assembly_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    blank_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    row_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class SqlAlchemyCuttingRepository:
    def __init__(self, session):
        self.session = session

    def get(self, group_id, *, lock=False):
        query = select(CuttingGroupRow).where(CuttingGroupRow.id == group_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def list(self, *, task_ids=None):
        query = select(CuttingGroupRow)
        if task_ids is not None:
            query = query.where(CuttingGroupRow.id.in_(select(CuttingMemberRow.group_id).where(
                CuttingMemberRow.task_id.in_(task_ids), CuttingMemberRow.state == "active")))
        return list(self.session.scalars(query.order_by(CuttingGroupRow.created_at, CuttingGroupRow.id)))

    def members(self, group_id, *, active=True):
        query = select(CuttingMemberRow).where(CuttingMemberRow.group_id == group_id)
        if active:
            query = query.where(CuttingMemberRow.state == "active")
        return list(self.session.scalars(query.order_by(CuttingMemberRow.position)))

    def for_operation(self, operation_id):
        return self.session.scalar(select(CuttingMemberRow).where(
            CuttingMemberRow.operation_id == operation_id, CuttingMemberRow.state == "active"))

    def fingerprint(self):
        return [[str(row.id), row.version, row.recipe_key, row.recipe_version] for row in self.list()]

    def attach(self, task, recipe: CuttingRecipe, now, production, *, separate=False, document=None, card_version=None):
        """Caller holds the normalized project lock; use the same transaction as task/serial creation."""
        existing = self.for_operation(recipe.operation_id)
        if existing:
            return self.get(existing.group_id)
        group = None
        if not separate:
            candidates = self.session.scalars(select(CuttingGroupRow).where(
                CuttingGroupRow.recipe_key == recipe.key, CuttingGroupRow.state == "open",
                CuttingGroupRow.joinable.is_(True)).order_by(CuttingGroupRow.created_at, CuttingGroupRow.id).with_for_update()).all()
            for candidate in candidates:
                members = self.members(candidate.id)
                if members and all(not production.get(m.task_id).operation(m.operation_id).operator_ids for m in members):
                    group = candidate
                    break
        if group is None:
            identifier = uuid4()
            group = CuttingGroupRow(id=identifier, number=f"РЗ-{identifier.hex[:16].upper()}",
                idempotency_key=f"cutting:{digest(task.source_idempotency_key)}", project_key=recipe.snapshot["project"],
                site=recipe.snapshot["site"], recipe_key=recipe.key, recipe_version=RECIPE_VERSION,
                recipe=recipe.snapshot, version=1, state="open", joinable=not separate, created_at=now)
            self.session.add(group)
            self.session.flush()
        else:
            group.version += 1
        self.session.add(CuttingMemberRow(id=uuid4(), group_id=group.id, task_id=task.id,
            operation_id=recipe.operation_id, position=max((m.position for m in self.members(group.id, active=False)), default=0) + 1,
            assembly_quantity=task.quantity, blank_quantity=task.quantity * Decimal(recipe.row["cables_per_assembly"]),
            row_snapshot={**recipe.row, "document": deepcopy(document), "validated_card_version": card_version,
                          "techcard_revision": task.techcard_revision}, state="active"))
        self.session.flush()
        return group

    def task_changed(self, task):
        """Any child edit invalidates group previews; cancellation keeps restrictive history references."""
        members = self.session.scalars(select(CuttingMemberRow).where(
            CuttingMemberRow.task_id == task.id, CuttingMemberRow.state.in_(("active", "cancelled")))).all()
        for member in members:
            group = self.get(member.group_id, lock=True)
            if group.state == "cancelled":
                continue
            if task.status is not ProductionStatus.CANCELLED:
                operation = task.operation(member.operation_id)
                if (task.quantity != member.assembly_quantity or operation.inputs
                        or task.techcard_revision != member.row_snapshot.get("techcard_revision")
                        or [op.code for op in operation.etk_ops] != [op["code"] for op in group.recipe["operations"]]
                        or Decimal(task.unit_factors[operation.id]) != Decimal(group.recipe["pf_per_assembly"])):
                    raise ProductionValidationError("Маршрут или тираж группы резки изменился — сначала отделите строку")
                if task.handed_to and group.site and task.handed_to != group.site:
                    raise ProductionValidationError("Перед передачей на другую площадку отделите строку от группы резки")
            member.state = "cancelled" if task.status is ProductionStatus.CANCELLED else "active"
            group.version += 1

    def freeze(self, operation_id, now):
        member = self.for_operation(operation_id)
        if member:
            group = self.get(member.group_id, lock=True)
            if group.state == "open":
                group.state, group.started_at = "frozen", now
                group.version += 1

    def preserve_task(self, task_id):
        return self.session.scalar(select(CuttingMemberRow.id).where(CuttingMemberRow.task_id == task_id).limit(1)) is not None
