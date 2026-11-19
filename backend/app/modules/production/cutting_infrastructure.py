"""Production-owned cutting group history; children remain ordinary production operations."""

from datetime import datetime
from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, Numeric, String, UniqueConstraint, Uuid, select, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.persistence import Base
from .cutting import CuttingRecipe, digest
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


class CuttingWorkRow(Base):
    """Extends one ordinary session; its task/operation are the stable resource anchor."""
    __tablename__ = "production_cutting_work"
    __table_args__ = (
        Index("ux_cutting_work_open_group", "group_id", unique=True,
              sqlite_where=text("closed = 0"), postgresql_where=text("closed = false")),
        CheckConstraint("accounted_seconds >= 0", name="ck_cutting_work_seconds"),
    )
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_work_sessions.id", ondelete="RESTRICT"), primary_key=True)
    group_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_cutting_groups.id", ondelete="RESTRICT"), nullable=False)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accounted_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downtime_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("production_downtimes.id", ondelete="RESTRICT"))


class CuttingActionRow(Base):
    __tablename__ = "production_cutting_actions"
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    group_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_cutting_groups.id", ondelete="RESTRICT"), nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)


class CuttingOutputRow(Base):
    """A group submission has per-member outputs without inventing child sessions."""
    __tablename__ = "production_cutting_outputs"
    __table_args__ = (UniqueConstraint("action_key", "member_id", name="ux_cutting_output_action_member"),)
    output_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_outputs.id", ondelete="RESTRICT"), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_cutting_work.session_id", ondelete="RESTRICT"), nullable=False)
    member_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_cutting_members.id", ondelete="RESTRICT"), nullable=False)
    action_key: Mapped[str] = mapped_column(String(255), ForeignKey("production_cutting_actions.key", ondelete="RESTRICT"), nullable=False)


class SqlAlchemyCuttingRepository:
    def __init__(self, session):
        self.session = session

    def reserve_number(self):
        # Same global, transactional numbering as PZ; migration seeds the row on existing databases.
        from .infrastructure import ProductionSequenceRow

        row = self.session.scalar(select(ProductionSequenceRow).where(
            ProductionSequenceRow.name == "cutting_group").with_for_update().execution_options(populate_existing=True))
        if row is None:
            row = ProductionSequenceRow(name="cutting_group", value=0)
            self.session.add(row)
            self.session.flush()
        row.value += 1
        self.session.flush()
        return f"РЗ-{row.value:04d}"

    def get(self, group_id, *, lock=False):
        query = select(CuttingGroupRow).where(CuttingGroupRow.id == group_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def work(self, group_id):
        return self.session.scalar(select(CuttingWorkRow).where(
            CuttingWorkRow.group_id == group_id, CuttingWorkRow.closed.is_(False)))

    def for_session(self, session_id):
        return self.session.get(CuttingWorkRow, session_id)

    def assert_standalone(self, operation_id):
        member = self.for_operation(operation_id)
        if member and self.work(member.group_id):
            raise ProductionValidationError("По этой ПФ выполняется группа резки — используйте карточку группы")

    def check_task_change(self, old, task):
        for operation in old.operations:
            member = self.for_operation(operation.id)
            if not member or not self.work(member.group_id):
                continue
            updated = task.operation(operation.id)
            if ((task.status != old.status and task.status is not ProductionStatus.DONE) or task.handed_to != old.handed_to
                    or task.fulfillment_request_id != old.fulfillment_request_id
                    or set(updated.operator_ids) != set(operation.operator_ids)
                    or [(s.operator_id, s.quantity) for s in updated.shares] != [(s.operator_id, s.quantity) for s in operation.shares]):
                raise ProductionValidationError("Сначала завершите работу группы резки")

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

    def execution_reason(self, operation):
        # Include cancelled sessions and corrected outputs: absence of active work is insufficient.
        from .infrastructure import ProductionWorkSessionRow, ProductionOutputRow, ProductionBoxRow

        if any(share.started_at or share.finished_at for share in operation.shares):
            return "ПФ уже начат"
        for model, reason in ((ProductionWorkSessionRow, "У ПФ есть рабочая сессия"),
                              (ProductionOutputRow, "У ПФ есть выпуск"),
                              (ProductionBoxRow, "У ПФ есть коробки")):
            if self.session.scalar(select(model.id).where(model.operation_id == operation.id).limit(1)):
                return reason
        return ""

    def create_existing(self, candidates, now):
        """Caller holds project/task locks and has rechecked every member; never late-join another group."""
        group = self.create_explicit(candidates[0][1], now)
        for task, recipe, document, card_version in candidates:
            self.place(group, task, recipe, document, card_version)
        return group

    def create_explicit(self, recipe, now):
        """A new open group that later launches do not join (grouped existing work, regrouping)."""
        identifier = uuid4()
        group = CuttingGroupRow(id=identifier, number=self.reserve_number(),
            idempotency_key=f"cutting-existing:{identifier}", project_key=recipe.snapshot["project"],
            site=recipe.snapshot["site"], recipe_key=recipe.key, recipe_version=recipe.snapshot["version"],
            recipe=recipe.snapshot, version=1, state="open", joinable=False, created_at=now)
        self.session.add(group)
        self.session.flush()
        return group

    def place(self, group, task, recipe, document, card_version):
        """Make the PF an active member of `group` with fresh row values. A row this group already had for the PF
        (split earlier, e.g. a rerun) is reactivated: one row per group and PF. Returns True when anything changed.
        The caller deactivated any other active membership of the PF and holds the project/group/task locks."""
        snapshot = {**recipe.row, "document": deepcopy(document), "validated_card_version": card_version,
                    "techcard_revision": task.techcard_revision}
        blanks = task.quantity * Decimal(recipe.row["cables_per_assembly"])
        row = self.session.scalar(select(CuttingMemberRow).where(
            CuttingMemberRow.group_id == group.id, CuttingMemberRow.operation_id == recipe.operation_id))
        if row is None:
            self.session.add(CuttingMemberRow(id=uuid4(), group_id=group.id, task_id=task.id,
                operation_id=recipe.operation_id,
                position=max((m.position for m in self.members(group.id, active=False)), default=0) + 1,
                assembly_quantity=task.quantity, blank_quantity=blanks, row_snapshot=snapshot, state="active"))
            self.session.flush()
            return True
        changed = (row.state != "active" or row.row_snapshot != snapshot or row.assembly_quantity != task.quantity
                   or Decimal(row.blank_quantity) != blanks)
        if changed:
            row.state, row.row_snapshot, row.assembly_quantity, row.blank_quantity = "active", snapshot, task.quantity, blanks
            self.session.flush()
        return changed

    def attach(self, task, recipe: CuttingRecipe, now, production, *, separate=False, document=None, card_version=None):
        """Caller holds the normalized project lock; use the same transaction as task/serial creation."""
        existing = self.for_operation(recipe.operation_id)
        if existing:
            return self.get(existing.group_id)
        group = None
        if not separate:
            # Lock by id (the one group lock order); the oldest compatible group is still chosen.
            candidates = self.session.scalars(select(CuttingGroupRow).where(
                CuttingGroupRow.recipe_key == recipe.key, CuttingGroupRow.state == "open",
                CuttingGroupRow.joinable.is_(True)).order_by(CuttingGroupRow.id).with_for_update()).all()
            for candidate in sorted(candidates, key=lambda row: (row.created_at, str(row.id))):
                members = self.members(candidate.id)
                if members and all(not production.get(m.task_id).operation(m.operation_id).operator_ids for m in members):
                    group = candidate
                    break
        if group is None:
            identifier = uuid4()
            # The first PF keeps the original launch key; other PFs of the same launch (labels, tubes) get their own.
            first = min(task.operations, key=lambda op: op.position).id == recipe.operation_id
            source = task.source_idempotency_key if first else [task.source_idempotency_key, str(recipe.operation_id)]
            group = CuttingGroupRow(id=identifier, number=self.reserve_number(),
                idempotency_key=f"cutting:{digest(source)}", project_key=recipe.snapshot["project"],
                site=recipe.snapshot["site"], recipe_key=recipe.key, recipe_version=recipe.snapshot["version"],
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
        # Groups by id, like every cutting path (project → groups → tasks); cutting paths hold them already.
        for group_id in sorted({member.group_id for member in members}, key=str):
            self.get(group_id, lock=True)
        for member in sorted(members, key=lambda item: (str(item.group_id), item.position)):
            group = self.get(member.group_id)
            if group.state == "cancelled":
                continue
            if task.status is not ProductionStatus.CANCELLED:
                operation = task.operation(member.operation_id)
                if (task.quantity != member.assembly_quantity or operation.inputs
                        or [op.code for op in operation.etk_ops] != [op["code"] for op in group.recipe["operations"]]
                        or Decimal(task.unit_factors[operation.id]) != Decimal(group.recipe["pf_per_assembly"])):
                    raise ProductionValidationError("Маршрут или тираж группы резки изменился — сначала отделите строку")
                if task.techcard_revision != member.row_snapshot.get("techcard_revision") and (
                        group.state != "open" or self.work(group.id)):
                    raise ProductionValidationError(
                        "Группа резки уже начата и работает по закреплённой ЭТК — новую ревизию к строке не применить")
                # Re-pinned row of an unstarted group: it keeps its frozen document until «Обновить из ЭТК» moves it
                # to the group of its new recipe (refresh_group_etk); the group cannot start before that.
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
