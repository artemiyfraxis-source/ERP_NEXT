"""Production allocations and crew membership, inside the command's existing transaction.

The UoW acquires the shared resource lock before reading tasks and calls flush before commit.
There are no SQLAlchemy listeners, commits, automatic physical-machine choices or elapsed-time releases.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Index, JSON, Uuid, delete, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.clock import MOSCOW
from app.shared.persistence import Base

from .resource_assignments import PhysicalAllocation
from .resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceOwner, ResourceWindow
from .resource_infrastructure import ResourceReservationRow, SqlAlchemyResourceClaims


class ResourceAllocationRow(Base):
    __tablename__ = "production_resource_allocations"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_tasks.id", ondelete="RESTRICT"), nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False, index=True)
    crew: Mapped[bool] = mapped_column(Boolean, nullable=False)
    machines: Mapped[list] = mapped_column(JSON, nullable=False)


class ResourceAllocationMemberRow(Base):
    __tablename__ = "production_resource_allocation_members"
    __table_args__ = (Index("ux_resource_member_operation_person", "operation_id", "operator_id", unique=True,
        postgresql_where=text("current = true"), sqlite_where=text("current = 1")),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    allocation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_resource_allocations.id", ondelete="RESTRICT"), nullable=False, index=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False)
    operator_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False)
    current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ResourceGroupRunRow(Base):
    __tablename__ = "production_resource_group_runs"
    __table_args__ = (Index("ux_resource_group_open_run", "allocation_id", unique=True,
        postgresql_where=text("closed = false"), sqlite_where=text("closed = 0")),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    allocation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_resource_allocations.id", ondelete="RESTRICT"), nullable=False, index=True)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ResourceSessionMemberRow(Base):
    __tablename__ = "production_resource_session_members"
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_work_sessions.id", ondelete="RESTRICT"), primary_key=True)
    member_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_resource_allocation_members.id", ondelete="RESTRICT"), nullable=False, index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("production_resource_group_runs.id", ondelete="RESTRICT"), nullable=False, index=True)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


def _owner(identifier):
    return ResourceOwner("production", identifier)


def _moment(value: datetime):
    # Production's legacy naive wall times are shop times, not fabricated UTC timestamps.
    return value.replace(tzinfo=MOSCOW) if value.tzinfo is None else value


class SqlAlchemyProductionResources:
    def __init__(self, session: Session, resolver):
        self.session = session
        self.resolver = resolver
        self.claims = SqlAlchemyResourceClaims(session)
        self.tasks = {}
        self.sessions = {}

    def acquire(self):
        self.claims.acquire()

    def record_task(self, task):
        self.tasks[task.id] = None

    def record_session(self, work):
        self.sessions[work.id] = work

    def describe(self, task, operation_id, operator_ids=None):
        operation = task.operation(operation_id)
        current = []
        rows = self.session.scalars(select(ResourceAllocationRow).where(ResourceAllocationRow.operation_id == operation_id))
        for row in rows:
            members = [member for member in self._members(row.id) if member.current]
            if members:
                current.append({"id": str(row.id), "crew": row.crew,
                    "operator_ids": [str(member.operator_id) for member in members], "machine_bindings": row.machines})
        try:
            required = self.resolver.requirements(task, operation)
            site = self.resolver.site(task, tuple(operator_ids) if operator_ids is not None else operation.operator_ids)
            catalog = self.resolver.catalog()
            choices = [{"etk_position": position, "family": family,
                "machines": [{"id": machine.id, "name": machine.name, "model": machine.model, "site": machine.site}
                    for machine in catalog if self.resolver.matches(machine, family, site)]}
                for position, family in required.items() if family]
            error = None
        except ResourceConflict as failure:
            choices, site, error = [], None, str(failure)
        return {"enabled": True, "task_version": task.version, "site": site, "requirements": choices,
            "allocations": current, "blocked_reason": error,
            "occupancy_rule": "Все выбранные станки заняты до завершения всей работы операции; пауза не освобождает станок"}

    def remove_task(self, task_id):
        self.acquire()
        rows = list(self.session.scalars(select(ResourceAllocationRow).where(ResourceAllocationRow.task_id == task_id)))
        for row in rows:
            members = self._members(row.id)
            history = self.session.scalar(select(ResourceSessionMemberRow.session_id).where(
                ResourceSessionMemberRow.member_id.in_([member.id for member in members]),
            ).limit(1))
            if history is not None:
                raise ResourceConflict("Назначение имеет историю работы; удалять задание нельзя")
        for row in rows:
            for member in self._members(row.id):
                self.claims.release_plan(_owner(member.id))
                self.session.delete(member)
            self.claims.release_plan(_owner(row.id))
            self.session.execute(delete(ResourceGroupRunRow).where(ResourceGroupRunRow.allocation_id == row.id))
            self.session.flush()
            self.session.delete(row)
        self.session.flush()
        self.tasks.pop(task_id, None)

    def ensure_assignment(self, task, operation_id, operator_ids, machines=None, *, crew=False):
        if not operator_ids:
            self.record_task(task)
            return
        if machines is None:
            existing = set(self.session.scalars(select(ResourceAllocationMemberRow.operator_id).where(
                ResourceAllocationMemberRow.operation_id == operation_id,
                ResourceAllocationMemberRow.operator_id.in_(operator_ids), ResourceAllocationMemberRow.current.is_(True),
            )))
            if existing == set(operator_ids):
                self.record_task(task)
                return
            # Empty means positively validated manual work, never the first matching machine.
            machines = ()
        self.configure(task, PhysicalAllocation(task.id, operation_id, tuple(operator_ids), tuple(machines), crew=crew))

    def _members(self, allocation_id):
        return list(self.session.scalars(select(ResourceAllocationMemberRow).where(
            ResourceAllocationMemberRow.allocation_id == allocation_id,
        )))

    def _open_memberships(self, member_id):
        return list(self.session.scalars(select(ResourceSessionMemberRow).where(
            ResourceSessionMemberRow.member_id == member_id, ResourceSessionMemberRow.closed.is_(False),
        )))

    def configure(self, task, allocation: PhysicalAllocation):
        """Explicit new allocation only. Completed allocations remain as execution provenance."""
        self.acquire()
        if task.id != allocation.task_id:
            raise ResourceConflict("Назначение относится к другому заданию")
        operation = task.operation(allocation.operation_id)
        if not set(allocation.operator_ids).issubset(operation.operator_ids):
            raise ResourceConflict("Сотрудники не назначены на операцию")
        self.resolver.validate(task, allocation)
        from .infrastructure import ProductionWorkSessionRow
        legacy_work = self.session.scalar(select(ProductionWorkSessionRow.id).where(
            ProductionWorkSessionRow.operation_id == operation.id,
            ProductionWorkSessionRow.operator_id.in_(allocation.operator_ids),
            ProductionWorkSessionRow.status.in_(("В работе", "Пауза")),
            ~ProductionWorkSessionRow.id.in_(select(ResourceSessionMemberRow.session_id)),
        ).limit(1))
        if legacy_work is not None:
            raise ResourceConflict("Сначала завершите прежнюю работу без точного назначения станка")
        existing = list(self.session.scalars(select(ResourceAllocationMemberRow).where(
            ResourceAllocationMemberRow.operation_id == allocation.operation_id,
            ResourceAllocationMemberRow.operator_id.in_(allocation.operator_ids),
            ResourceAllocationMemberRow.current.is_(True),
        )))
        if existing and len({member.allocation_id for member in existing}) == 1:
            old = self.session.get(ResourceAllocationRow, existing[0].allocation_id)
            current = {member.operator_id for member in self._members(old.id) if member.current}
            expected = [{"etk_position": item.etk_position, "machine_id": item.machine_id} for item in allocation.machines]
            if current == set(allocation.operator_ids) and old.machines == expected and old.crew == allocation.crew:
                self.record_task(task)
                return old.id
        if any(self._open_memberships(member.id) for member in existing):
            raise ResourceConflict("Сначала завершите работу на назначенном станке")
        # Retire current links without deleting historical work membership.
        for member in existing:
            member.current = False
            self.claims.release_plan(_owner(member.id))
        self.session.flush()
        row = ResourceAllocationRow(id=uuid4(), task_id=task.id, operation_id=operation.id,
            crew=allocation.crew, machines=[{"etk_position": item.etk_position, "machine_id": item.machine_id} for item in allocation.machines])
        self.session.add(row)
        self.session.flush()
        for operator in allocation.operator_ids:
            self.session.add(ResourceAllocationMemberRow(id=uuid4(), allocation_id=row.id,
                operation_id=operation.id, operator_id=operator, current=True))
        self.record_task(task)
        return row.id

    def _plans(self):
        from .infrastructure import SqlAlchemyProductionRepository
        plans = []
        releases = []
        repository = SqlAlchemyProductionRepository(self.session)
        for task_id in self.tasks:
            # A failed best-effort reflow can roll back a savepoint. Read the actual transaction state,
            # not a Python object queued before that savepoint rolled back.
            task = repository.get(task_id)
            if task is None:
                continue
            rows = self.session.scalars(select(ResourceAllocationRow).where(ResourceAllocationRow.task_id == task.id))
            for row in rows:
                operation = task.operation(row.operation_id)
                windows = []
                for member in self._members(row.id):
                    share = operation.share_of(member.operator_id)
                    if not member.current or task.status.value != "Запущено" or share is None or share.finished_at is not None or share.planned_start is None:
                        releases.append(_owner(member.id))
                        if share is None and not self._open_memberships(member.id):
                            member.current = False
                        continue
                    window = ResourceWindow(_moment(share.planned_start), _moment(share.planned_finish))
                    windows.append(window)
                    plans.append((_owner(member.id), (ResourceKey(ResourceKind.PERSON, str(member.operator_id)),), window))
                machines = tuple(ResourceKey(ResourceKind.MACHINE, key) for key in sorted({item["machine_id"] for item in row.machines}))
                if windows and machines:
                    window = ResourceWindow(min(item.start for item in windows), max(item.finish for item in windows))
                    plans.append((_owner(row.id), machines, window))
                else:
                    releases.append(_owner(row.id))
        changed = []
        for owner, resources, window in plans:
            stored = list(self.session.scalars(select(ResourceReservationRow).where(
                ResourceReservationRow.owner_kind == owner.kind, ResourceReservationRow.owner_id == owner.id,
            )))
            def utc(value):
                return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            if ({(row.resource_kind, row.resource_key) for row in stored} == {(key.kind.value, key.key) for key in resources}
                and all(utc(row.start) == window.start and utc(row.finish) == window.finish for row in stored)):
                continue
            changed.append((owner, resources, window))
        return changed, releases

    def _activate(self, work):
        membership = self.session.get(ResourceSessionMemberRow, work.id)
        if membership is not None:
            if membership.closed:
                raise ResourceConflict("Закрытое исполнение нельзя занять повторно")
            return  # Pause/resume retain the exact same live membership, including machine holds.
        member = self.session.scalar(select(ResourceAllocationMemberRow).where(
            ResourceAllocationMemberRow.operation_id == work.operation_id,
            ResourceAllocationMemberRow.operator_id == work.operator_id,
            ResourceAllocationMemberRow.current.is_(True),
        ))
        if member is None:
            raise ResourceConflict("Сначала явно назначьте оборудование операции; для ручной операции подтвердите отсутствие станков")
        allocation = self.session.get(ResourceAllocationRow, member.allocation_id)
        run = self.session.scalar(select(ResourceGroupRunRow).where(
            ResourceGroupRunRow.allocation_id == allocation.id, ResourceGroupRunRow.closed.is_(False),
        ))
        if run is not None and not allocation.crew:
            raise ResourceConflict("Это назначение не является общей бригадой")
        if run is None:
            run = ResourceGroupRunRow(id=uuid4(), allocation_id=allocation.id, closed=False)
            self.session.add(run)
            self.session.flush()
        machines = tuple(ResourceKey(ResourceKind.MACHINE, key) for key in sorted({item["machine_id"] for item in allocation.machines}))
        now = _moment(work.started_at)
        if machines:
            self.claims.activate(_owner(allocation.id), run.id, machines, now)
        self.claims.activate(_owner(member.id), work.id, (ResourceKey(ResourceKind.PERSON, str(work.operator_id)),), now)
        self.session.add(ResourceSessionMemberRow(session_id=work.id, member_id=member.id, run_id=run.id, closed=False))
        self.session.flush()

    def _close(self, work):
        membership = self.session.get(ResourceSessionMemberRow, work.id)
        if membership is None or membership.closed:
            return  # Legacy completion is always allowed; never invent a historical physical assignment.
        member = self.session.get(ResourceAllocationMemberRow, membership.member_id)
        run = self.session.get(ResourceGroupRunRow, membership.run_id)
        self.claims.release_active(_owner(member.id), work.id, kinds=(ResourceKind.PERSON,))
        membership.closed = True
        self.session.flush()
        another = self.session.scalar(select(ResourceSessionMemberRow.session_id).where(
            ResourceSessionMemberRow.run_id == run.id, ResourceSessionMemberRow.closed.is_(False),
        ).limit(1))
        if another is None:
            self.claims.release_active(_owner(run.allocation_id), run.id, kinds=(ResourceKind.MACHINE,))
            run.closed = True

    def checkpoint(self):
        """Queues are Python state, so SQL savepoint rollback cannot restore them."""
        return self.tasks.copy(), self.sessions.copy()

    def restore(self, checkpoint):
        self.tasks, self.sessions = checkpoint

    def sync_reflow(self):
        """Validate plan changes inside their savepoint without acquiring new actual work."""
        self.acquire()
        self.session.flush()
        # Release closed actual executions first; a reflow may legitimately move the freed resources.
        for work in self.sessions.values():
            if work.status.value not in {"В работе", "Пауза"}:
                self._close(work)
        plans, releases = self._plans()
        starting = tuple((work.operation_id, work.operator_id) for work in self.sessions.values()
            if work.status.value in {"В работе", "Пауза"})
        for _, resources, window in plans:
            for resource in resources:
                self.resolver.assert_legacy_available(resource, window, excluding=starting)
        for owner in releases:
            self.claims.release_plan(owner)
        if plans:
            self.claims.replace_plans(tuple(plans))
        self.session.flush()

    def flush(self):
        """Enforce actual occupancy before the enclosing transaction commits."""
        self.sync_reflow()
        for work in self.sessions.values():
            if work.status.value in {"В работе", "Пауза"}:
                self._activate(work)
        self.session.flush()
        self.tasks.clear()
        self.sessions.clear()
