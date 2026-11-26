"""Warehouse-owned GRN persistence, using only the caller's SQLAlchemy transaction."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.persistence import Base
from .grn import GrnCapacityExceeded, GrnCategory, GrnConflict, GrnContext, GrnIdentity, GrnRequest, MAX_SERIAL


PREFIX_SQL = "'PPM', 'PAM', 'GOS', 'SUB', 'CSM', 'FRX', 'R&D'"
CONTEXT_CHECKS = {
    "pam": "category <> 'PAM' OR purpose IS NOT NULL OR equipment IS NOT NULL",
    "gos": "category <> 'GOS' OR purpose IS NOT NULL",
    "sub": "category <> 'SUB' OR (subcontractor IS NOT NULL AND production_order IS NOT NULL AND acceptance_document IS NOT NULL)",
    "csm": "category <> 'CSM' OR (owner_id IS NOT NULL AND customer_order IS NOT NULL AND transfer_document IS NOT NULL)",
    "frx": "category <> 'FRX' OR (production_order IS NOT NULL AND acceptance_document IS NOT NULL)",
    "rd": "category <> 'R&D' OR (project IS NOT NULL AND purpose IS NOT NULL)",
    "owner_category": "owner_id IS NULL OR category = 'CSM'",
}
NUMBER_CHECK = (
    "number = category || '.' || substr(CAST(full_year AS VARCHAR), 3, 2) || '.' || "
    "substr('000000', 1, 6 - length(CAST(serial AS VARCHAR))) || CAST(serial AS VARCHAR)"
)


class MaterialOwnerRow(Base):
    __tablename__ = "warehouse_material_owners"
    __table_args__ = (
        CheckConstraint("trim(display_name) <> ''", name="ck_material_owner_name"),
        CheckConstraint("trim(legal_identity) <> ''", name="ck_material_owner_identity"),
        UniqueConstraint("legal_identity", name="uq_material_owner_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(512))
    legal_identity: Mapped[str] = mapped_column(String(512))


class GrnCounterRow(Base):
    __tablename__ = "warehouse_grn_counters"
    __table_args__ = (
        CheckConstraint(f"category IN ({PREFIX_SQL})", name="ck_grn_counter_category"),
        CheckConstraint("full_year BETWEEN 2000 AND 9999", name="ck_grn_counter_year"),
        CheckConstraint("last_serial BETWEEN 0 AND 999999", name="ck_grn_counter_serial"),
    )

    category: Mapped[str] = mapped_column(String(3), primary_key=True)
    full_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_serial: Mapped[int] = mapped_column(Integer, nullable=False)


class GrnRegistryRow(Base):
    __tablename__ = "warehouse_grn_registry"
    __table_args__ = (
        UniqueConstraint("number", name="uq_grn_number"),
        UniqueConstraint("category", "full_year", "serial", name="uq_grn_category_year_serial"),
        UniqueConstraint("source_kind", "source_op_id", name="uq_grn_source_operation"),
        UniqueConstraint("id", "category", "number", name="uq_grn_reference"),
        UniqueConstraint("id", "owner_id", name="uq_grn_owner_reference"),
        CheckConstraint(f"category IS NULL OR category IN ({PREFIX_SQL})", name="ck_grn_category"),
        CheckConstraint("full_year IS NULL OR full_year BETWEEN 2000 AND 9999", name="ck_grn_year"),
        CheckConstraint("serial IS NULL OR serial BETWEEN 1 AND 999999", name="ck_grn_serial"),
        CheckConstraint("length(number) > 0", name="ck_grn_number"),
        CheckConstraint("trim(source_kind) <> '' AND trim(source_op_id) <> ''", name="ck_grn_source"),
        CheckConstraint("(is_legacy AND serial IS NULL AND source_kind = 'legacy') OR "
                        "(NOT is_legacy AND category IS NOT NULL AND full_year IS NOT NULL AND serial IS NOT NULL "
                        "AND registered_at IS NOT NULL AND request_fingerprint IS NOT NULL "
                        "AND length(request_fingerprint) = 64 AND source_kind <> 'legacy')", name="ck_grn_new_identity"),
        CheckConstraint(f"is_legacy OR ({NUMBER_CHECK})", name="ck_grn_canonical_number"),
        *(CheckConstraint(f"is_legacy OR ({sql})", name=f"ck_grn_context_{name}") for name, sql in CONTEXT_CHECKS.items()),
        *(CheckConstraint(f"{name} IS NULL OR trim({name}) <> ''", name=f"ck_grn_text_{name}")
          for name in GrnContext.__dataclass_fields__ if name != "owner_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    number: Mapped[str] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(3))
    full_year: Mapped[int | None] = mapped_column(Integer)
    serial: Mapped[int | None] = mapped_column(Integer)
    is_legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source_kind: Mapped[str] = mapped_column(String(32))
    source_op_id: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("warehouse_material_owners.id", ondelete="RESTRICT"))
    customer_order: Mapped[str | None] = mapped_column(String(512))
    transfer_document: Mapped[str | None] = mapped_column(String(512))
    project: Mapped[str | None] = mapped_column(String(512))
    production_order: Mapped[str | None] = mapped_column(String(512))
    subcontractor: Mapped[str | None] = mapped_column(String(512))
    acceptance_document: Mapped[str | None] = mapped_column(String(512))
    purpose: Mapped[str | None] = mapped_column(String(512))
    equipment: Mapped[str | None] = mapped_column(String(512))


class SqlAlchemyGrnAllocator:
    def __init__(self, session: Session):
        self.session = session

    def _transaction(self) -> None:
        if not self.session.in_transaction():
            raise RuntimeError("GRN allocator requires the caller's open transaction")
        connection = self.session.connection()
        if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
            # Python sqlite's legacy mode does not BEGIN for SELECT/SAVEPOINT. Without a real outer
            # transaction RELEASE SAVEPOINT would commit the number before the caller commits stock.
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    def _insert(self, table):
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            return pg_insert(table)
        if dialect == "sqlite":
            return sqlite_insert(table)
        raise RuntimeError(f"Unsupported GRN database: {dialect}")

    def _existing(self, request: GrnRequest) -> GrnIdentity | None:
        row = self.session.execute(select(GrnRegistryRow.__table__).where(
            GrnRegistryRow.source_kind == request.source_kind,
            GrnRegistryRow.source_op_id == request.source_op_id,
        )).mappings().one_or_none()
        if row is None:
            return None
        if row["request_fingerprint"] != request.fingerprint:
            raise GrnConflict("Операция уже получила ГРН с другой категорией, контекстом или содержимым")
        registered = row["registered_at"]
        if registered.tzinfo is None:  # SQLite drops the offset; stored timestamps are always UTC.
            registered = registered.replace(tzinfo=UTC)
        registered = registered.astimezone(UTC)
        return GrnIdentity(id=row["id"], number=row["number"], category=GrnCategory(row["category"]),
                           full_year=row["full_year"], serial=row["serial"], registered_at=registered,
                           source_kind=row["source_kind"], source_op_id=row["source_op_id"],
                           context=GrnContext(**{name: row[name] for name in GrnContext.__dataclass_fields__}))

    def allocate(self, request: GrnRequest, *, registered_at: datetime) -> GrnIdentity:
        self._transaction()
        existing = self._existing(request)
        if existing is not None:
            return existing
        if registered_at.tzinfo is None or registered_at.utcoffset() is None:
            raise ValueError("Для регистрации ГРН требуется серверное время с часовым поясом")
        year = registered_at.astimezone(ZoneInfo("Europe/Moscow")).year
        if not 2000 <= year <= 9999:
            raise ValueError("Год регистрации ГРН должен быть в пределах 2000–9999")
        registered_at = registered_at.astimezone(UTC)
        # A losing concurrent retry rolls back only its speculative counter increment. The caller's
        # transaction stays intact; a genuine failure is propagated for the caller to handle.
        with self.session.begin_nested() as savepoint:
            counter = GrnCounterRow.__table__
            serial = self.session.scalar(self._insert(counter).values(
                category=request.category.value, full_year=year, last_serial=1,
            ).on_conflict_do_update(
                index_elements=[counter.c.category, counter.c.full_year],
                set_={"last_serial": counter.c.last_serial + 1},
                where=counter.c.last_serial < MAX_SERIAL,
            ).returning(counter.c.last_serial))
            if serial is None:
                # The winner may have taken the last available serial for this very operation.
                existing = self._existing(request)
                if existing is not None:
                    return existing
                raise GrnCapacityExceeded(f"{request.category}.{year % 100:02d}: исчерпано 999999 номеров ГРН")
            identity = GrnIdentity(id=uuid4(), number=f"{request.category}.{year % 100:02d}.{serial:06d}",
                                   category=request.category, full_year=year, serial=serial,
                                   registered_at=registered_at, source_kind=request.source_kind,
                                   source_op_id=request.source_op_id, context=request.context)
            registry = GrnRegistryRow.__table__
            inserted = self.session.scalar(self._insert(registry).values(
                id=identity.id, number=identity.number, category=identity.category.value, full_year=year,
                serial=serial, is_legacy=False, source_kind=request.source_kind, source_op_id=request.source_op_id,
                request_fingerprint=request.fingerprint, registered_at=registered_at, **asdict(request.context),
            ).on_conflict_do_nothing(index_elements=[registry.c.source_kind, registry.c.source_op_id]).returning(registry.c.id))
            if inserted is not None:
                return identity
            savepoint.rollback()
        existing = self._existing(request)
        if existing is None:
            raise RuntimeError("Concurrent GRN disappeared; retry the entire transaction")
        return existing

    def allocate_many(self, requests: tuple[GrnRequest, ...], *, registered_at: datetime) -> tuple[GrnIdentity, ...]:
        self._transaction()
        results: dict[int, GrnIdentity] = {}
        with self.session.begin_nested():
            for index, request in sorted(enumerate(requests), key=lambda pair: (
                    pair[1].category.value, pair[1].source_kind, pair[1].source_op_id)):
                results[index] = self.allocate(request, registered_at=registered_at)
        return tuple(results[index] for index in range(len(requests)))
