"""Transactional setup persistence. No router registration or migration is activated here."""

from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Uuid, select, text, update
from sqlalchemy.orm import Mapped, Session

from app.shared.advisory_lock import lock_key
from app.shared.persistence import Base, SqlAlchemyAuditRepository, SqlAlchemyOutboxRepository
from sqlalchemy.orm import mapped_column

from .application import ConcurrentSetupUpdate, SetupCommandReceipt
from .domain import IssuedOperationRef, RecipeVersionRef, SetupStatus, SetupTask, SetupTransition
from .recipe_infrastructure import SqlAlchemyRecipes


def _json(value):
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (datetime, UUID)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, SetupStatus):
        return value.value
    return value


def task_payload(task: SetupTask) -> dict:
    return _json(asdict(task))


def task_from_payload(payload: dict) -> SetupTask:
    data = dict(payload)
    operation = dict(data.pop("operation"))
    for key in ("card_id", "production_task_id", "production_operation_id"):
        operation[key] = UUID(operation[key])
    recipe = data.pop("recipe")
    for key in ("id", "technician_id", "machine_id"):
        data[key] = UUID(data[key])
    for key in ("planned_start", "planned_finish", "created_at", "updated_at"):
        data[key] = datetime.fromisoformat(data[key])
    data["status"] = SetupStatus(data["status"])
    return SetupTask(**data, operation=IssuedOperationRef(**operation),
        recipe=RecipeVersionRef(UUID(recipe["recipe_id"]), recipe["version"]))


class SetupTaskRow(Base):
    __tablename__ = "equipment_setup_tasks"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_setup_task_version"),
        CheckConstraint("status IN ('New','InProgress','Blocked','Ready','Cancelled')", name="ck_setup_task_status"),
        ForeignKeyConstraint(["recipe_id", "recipe_version"],
            ["equipment_setup_recipe_versions.recipe_id", "equipment_setup_recipe_versions.version"], name="fk_setup_task_recipe"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    technician_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    # The БД.ОБ unit (0172): renumbering it keeps the task, a unit with setup tasks can't be deleted.
    machine_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                              name="fk_setup_task_unit"), nullable=False, index=True)
    recipe_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)


class SetupCommandRow(Base):
    __tablename__ = "equipment_setup_commands"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)


class SetupTransitionRow(Base):
    __tablename__ = "equipment_setup_transitions"
    __table_args__ = (
        CheckConstraint("version >= 2", name="ck_setup_transition_version"),
        ForeignKeyConstraint(["task_id"], ["equipment_setup_tasks.id"], name="fk_setup_transition_task"),
    )
    task_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)


class SqlAlchemySetupTasks:
    def __init__(self, session: Session):
        self.session = session

    def get(self, task_id: UUID) -> SetupTask | None:
        row = self.session.get(SetupTaskRow, task_id, populate_existing=True)
        return task_from_payload(row.snapshot) if row is not None else None

    def list(self, *, technician_id=None, machine_ids=None, limit=100, offset=0):
        statement = select(SetupTaskRow)
        if technician_id is not None:
            statement = statement.where(SetupTaskRow.technician_id == technician_id)
        if machine_ids is not None:
            statement = statement.where(SetupTaskRow.machine_id.in_(machine_ids))
        statement = statement.order_by(SetupTaskRow.created_at.desc(), SetupTaskRow.id).offset(offset).limit(limit)
        return tuple(task_from_payload(row.snapshot) for row in self.session.scalars(statement))

    def save(self, task: SetupTask, *, expected_version: int):
        if task.version != expected_version + 1:
            raise ConcurrentSetupUpdate("Неверная версия задания наладки")
        values = dict(version=task.version, status=task.status.value, technician_id=task.technician_id,
            machine_id=task.machine_id, recipe_id=task.recipe.recipe_id, recipe_version=task.recipe.version,
            created_at=task.created_at, updated_at=task.updated_at,
            snapshot=task_payload(task))
        if expected_version == 0:
            self.session.add(SetupTaskRow(id=task.id, **values))
        else:
            result = self.session.execute(update(SetupTaskRow).where(SetupTaskRow.id == task.id,
                SetupTaskRow.version == expected_version).values(**values))
            if result.rowcount != 1:
                raise ConcurrentSetupUpdate("Задание наладки уже изменилось — обновите данные")
        self.session.flush()


class SqlAlchemySetupCommands:
    def __init__(self, session: Session):
        self.session = session

    def claim(self, key: str) -> SetupCommandReceipt | None:
        if self.session.get_bind().dialect.name == "postgresql":
            with self.session.no_autoflush:
                self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key("setup-command", key)})
        row = self.session.get(SetupCommandRow, key, populate_existing=True)
        return SetupCommandReceipt(row.key, row.fingerprint, task_from_payload(row.result)) if row is not None else None

    def add(self, receipt: SetupCommandReceipt):
        self.session.add(SetupCommandRow(key=receipt.key, fingerprint=receipt.fingerprint, result=task_payload(receipt.result)))


class SqlAlchemySetupTransitions:
    def __init__(self, session: Session):
        self.session = session

    def add(self, value: SetupTransition):
        self.session.add(SetupTransitionRow(task_id=value.task_id, version=value.version, snapshot=_json(asdict(value))))


class SqlAlchemySetupUnitOfWork:
    def __init__(self, session_factory, *, context_factory, reservations_factory):
        self.session_factory = session_factory
        self.context_factory = context_factory
        self.reservations_factory = reservations_factory

    def __enter__(self):
        self.session = self.session_factory()
        try:
            if self.session.get_bind().dialect.name == "sqlite":
                self.session.execute(text("BEGIN IMMEDIATE"))
            self.reservations = self.reservations_factory(self.session)
            self.reservations.acquire()
            self.tasks = SqlAlchemySetupTasks(self.session)
            self.recipes = SqlAlchemyRecipes(self.session)
            self.commands = SqlAlchemySetupCommands(self.session)
            self.transitions = SqlAlchemySetupTransitions(self.session)
            self.context = self.context_factory(self.session)
            self.audit = SqlAlchemyAuditRepository(self.session)
            self.outbox = SqlAlchemyOutboxRepository(self.session)
        except BaseException:
            self.session.close()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.session.rollback()
        finally:
            self.session.close()

    def commit(self):
        self.session.commit()
