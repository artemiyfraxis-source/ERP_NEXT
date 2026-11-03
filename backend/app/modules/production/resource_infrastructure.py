"""Production-owned resources. The caller owns the transaction and exact identity resolution.

Not activated until all production writers use this store. SQLite callers must begin an
IMMEDIATE transaction before reading; PostgreSQL uses transaction advisory locks.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Uuid, delete, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.advisory_lock import lock_key
from app.shared.persistence import Base

from .resource_claims import ResourceConflict, ResourceKey, ResourceKind, ResourceOwner, ResourceWindow, instant


class ResourceReservationRow(Base):
    __tablename__ = "production_resource_reservations"
    __table_args__ = (
        CheckConstraint("resource_kind IN ('machine', 'person')", name="ck_resource_reservation_kind"),
        CheckConstraint("owner_kind IN ('setup', 'production')", name="ck_resource_reservation_owner"),
        CheckConstraint("finish > start", name="ck_resource_reservation_window"),
    )
    owner_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    resource_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finish: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActiveResourceRow(Base):
    __tablename__ = "production_active_resources"
    __table_args__ = (
        CheckConstraint("resource_kind IN ('machine', 'person')", name="ck_active_resource_kind"),
        CheckConstraint("owner_kind IN ('setup', 'production')", name="ck_active_resource_owner"),
    )
    # Database uniqueness prevents two owners from holding the same physical resource.
    resource_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    execution_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResourceGenerationRow(Base):
    __tablename__ = "production_resource_generation"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_resource_generation_singleton"),
        CheckConstraint("version >= 0", name="ck_resource_generation_version"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


def _owned(model, owner):
    return (model.owner_kind == owner.kind, model.owner_id == owner.id)


class SqlAlchemyResourceClaims:
    """No commits, no machine guessing, and no implicit release on replan.

    The transaction-wide global generation lock serializes every resource mutation. This
    intentionally simple lock also makes multi-resource reassignment deadlock-free, covers
    absent keys, and lets an optimizer reject a stale resource-generation snapshot.
    """

    def __init__(self, session: Session):
        self.session = session

    def acquire(self):
        """Call at command transaction entry, before any production task/session row locks.

        All writers follow this ordering. Internal calls only retain the already held lock.
        Do not hold it across network requests. SQLite UOWs begin IMMEDIATE before reads.
        """
        if self.session.get_bind().dialect.name == "postgresql":
            with self.session.no_autoflush:
                self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {
                    "key": lock_key("production-resources", "generation"),
                })

    def generation(self) -> int:
        self.acquire()
        row = self.session.get(ResourceGenerationRow, 1, populate_existing=True)
        if row is None:
            raise ResourceConflict("Реестр занятости ещё не инициализирован")
        return row.version

    def _changed(self):
        row = self.session.get(ResourceGenerationRow, 1, populate_existing=True)
        if row is None:
            raise ResourceConflict("Реестр занятости ещё не инициализирован")
        row.version += 1
        self.session.flush()

    @staticmethod
    def _resources(resources):
        values = tuple(resources)
        if not values or any(not isinstance(item, ResourceKey) for item in values) or len(set(values)) != len(values):
            raise ValueError("Нужен непустой список разных точных ресурсов")
        return values

    def replace_plan(self, owner: ResourceOwner, resources: tuple[ResourceKey, ...], window: ResourceWindow):
        self.replace_plans(((owner, resources, window),))

    def replace_plans(self, plans):
        """Atomically replace a batch, permitting swaps but never dropping active occupancy."""
        plans = tuple((owner, self._resources(resources), window) for owner, resources, window in plans)
        if not plans or any(not isinstance(owner, ResourceOwner) or not isinstance(window, ResourceWindow)
                            for owner, _, window in plans):
            raise ValueError("Нужны владельцы и окна назначений")
        owners = {owner for owner, _, _ in plans}
        if len(owners) != len(plans):
            raise ValueError("Владелец повторяется в пакете назначений")
        self.generation()
        planned = {}
        for owner, resources, window in plans:
            for resource in resources:
                active = self.session.get(ActiveResourceRow, (resource.kind.value, resource.key))
                if active is not None and (active.owner_kind, active.owner_id) != (owner.kind, owner.id):
                    raise ResourceConflict("Ресурс занят незавершённой работой")
                others = self.session.scalars(select(ResourceReservationRow).where(
                    ResourceReservationRow.resource_kind == resource.kind.value,
                    ResourceReservationRow.resource_key == resource.key,
                    ResourceReservationRow.start < window.finish,
                    ResourceReservationRow.finish > window.start,
                ))
                if any(ResourceOwner(row.owner_kind, row.owner_id) not in owners for row in others):
                    raise ResourceConflict("Окно ресурса уже занято")
                if any(window.overlaps(other) for other in planned.get(resource, ())):
                    raise ResourceConflict("Окна ресурса пересекаются внутри пакета")
                planned.setdefault(resource, []).append(window)
        # Validate the whole batch before touching any reservation.
        for owner in owners:
            self.session.execute(delete(ResourceReservationRow).where(*_owned(ResourceReservationRow, owner)))
        for owner, resources, window in plans:
            for resource in resources:
                self.session.add(ResourceReservationRow(owner_kind=owner.kind, owner_id=owner.id,
                    resource_kind=resource.kind.value, resource_key=resource.key, start=window.start, finish=window.finish))
        self._changed()

    def activate(self, owner: ResourceOwner, execution_id: UUID, resources: tuple[ResourceKey, ...], now: datetime):
        resources = self._resources(resources)
        if not isinstance(execution_id, UUID) or not execution_id.int:
            raise ValueError("Нужен UUID исполнения")
        now = instant(now)
        self.generation()
        missing = []
        for resource in resources:
            row = self.session.get(ActiveResourceRow, (resource.kind.value, resource.key), populate_existing=True)
            if row is not None:
                if (row.owner_kind, row.owner_id, row.execution_id) != (owner.kind, owner.id, execution_id):
                    raise ResourceConflict("Ресурс уже занят другим исполнением")
            else:
                missing.append(resource)
            # Activation must correspond to an explicit assignment. No first-machine fallback.
            assigned = self.session.get(ResourceReservationRow, (owner.kind, owner.id, resource.kind.value, resource.key))
            if assigned is None:
                raise ResourceConflict("Сначала назначьте точный ресурс")
            overlaps_now = self.session.scalars(select(ResourceReservationRow).where(
                ResourceReservationRow.resource_kind == resource.kind.value,
                ResourceReservationRow.resource_key == resource.key,
                ResourceReservationRow.start <= now, ResourceReservationRow.finish > now,
            ))
            if any((item.owner_kind, item.owner_id) != (owner.kind, owner.id) for item in overlaps_now):
                raise ResourceConflict("Текущее окно ресурса назначено другой работе")
        if missing:
            for resource in missing:
                self.session.add(ActiveResourceRow(resource_kind=resource.kind.value, resource_key=resource.key,
                    owner_kind=owner.kind, owner_id=owner.id, execution_id=execution_id, activated_at=now))
            self._changed()

    def release_active(self, owner: ResourceOwner, execution_id: UUID, *, kinds: tuple[ResourceKind, ...]):
        if not isinstance(execution_id, UUID) or not execution_id.int:
            raise ValueError("Нужен UUID исполнения")
        if not kinds or any(not isinstance(kind, ResourceKind) for kind in kinds):
            raise ValueError("Укажите освобождаемые виды ресурсов")
        self.generation()
        result = self.session.execute(delete(ActiveResourceRow).where(
            *_owned(ActiveResourceRow, owner), ActiveResourceRow.execution_id == execution_id,
            ActiveResourceRow.resource_kind.in_([kind.value for kind in kinds]),
        ))
        if result.rowcount:
            self._changed()

    def release_plan(self, owner: ResourceOwner, *, kinds: tuple[ResourceKind, ...] | None = None):
        if kinds is not None and (not kinds or any(not isinstance(kind, ResourceKind) for kind in kinds)):
            raise ValueError("Укажите освобождаемые виды ресурсов")
        self.generation()
        statement = delete(ResourceReservationRow).where(*_owned(ResourceReservationRow, owner))
        if kinds is not None:
            statement = statement.where(ResourceReservationRow.resource_kind.in_([kind.value for kind in kinds]))
        result = self.session.execute(statement)
        if result.rowcount:
            self._changed()
