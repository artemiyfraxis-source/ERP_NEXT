"""Append-only recipe versions and independent recorded measurements; caller commits."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKeyConstraint, Integer, String, Uuid, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.shared.advisory_lock import lock_key
from app.shared.persistence import Base

from .application import ConcurrentSetupUpdate
from .recipes import ManualRecipeVersion, RecipeField, SetupTrialMeasurement


class RecipeVersionRow(Base):
    __tablename__ = "equipment_setup_recipe_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_setup_recipe_version"),
        CheckConstraint("length(machine_id) > 0", name="ck_setup_recipe_machine"),
        CheckConstraint("length(source) > 0", name="ck_setup_recipe_source"),
    )
    recipe_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    geometry: Mapped[list] = mapped_column(JSON, nullable=False)
    panel_settings: Mapped[list] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrialMeasurementRow(Base):
    __tablename__ = "equipment_setup_trial_measurements"
    __table_args__ = (
        CheckConstraint("recipe_version >= 1", name="ck_setup_trial_recipe_version"),
        ForeignKeyConstraint(["recipe_id", "recipe_version"],
            ["equipment_setup_recipe_versions.recipe_id", "equipment_setup_recipe_versions.version"],
            name="fk_setup_trial_recipe"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    recipe_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    # Keep the exact manually recorded decimal, without choosing an instrument resolution.
    value: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(160), nullable=False)
    measured_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _fields(values):
    return [{"label": item.label, "value": item.value, "unit": item.unit} for item in values]


class SqlAlchemyRecipes:
    def __init__(self, session: Session):
        self.session = session

    def get(self, recipe_id: UUID, version: int) -> ManualRecipeVersion | None:
        row = self.session.get(RecipeVersionRow, (recipe_id, version))
        if row is None:
            return None
        return ManualRecipeVersion(row.recipe_id, row.version, row.machine_id, row.title,
            tuple(RecipeField(**item) for item in row.geometry), tuple(RecipeField(**item) for item in row.panel_settings),
            row.source, row.created_by, _utc(row.created_at))

    def append(self, value: ManualRecipeVersion, *, expected_version: int):
        if type(expected_version) is not int or expected_version < 0 or value.version != expected_version + 1:
            raise ConcurrentSetupUpdate("Неверная ожидаемая версия рецепта")
        if self.session.get_bind().dialect.name == "postgresql":
            with self.session.no_autoflush:
                self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key("setup-recipe", value.recipe_id)})
        latest = self.session.scalar(select(RecipeVersionRow).where(RecipeVersionRow.recipe_id == value.recipe_id)
            .order_by(RecipeVersionRow.version.desc()).limit(1))
        if (latest.version if latest else 0) != expected_version:
            raise ConcurrentSetupUpdate("Рецепт уже изменился — обновите данные")
        if latest is not None and latest.machine_id != value.machine_id:
            raise ConcurrentSetupUpdate("Версии рецепта относятся к одному физическому станку")
        self.session.add(RecipeVersionRow(recipe_id=value.recipe_id, version=value.version, machine_id=value.machine_id,
            title=value.title, geometry=_fields(value.geometry), panel_settings=_fields(value.panel_settings),
            source=value.source, created_by=value.created_by, created_at=value.created_at))
        self.session.flush()

    def list_versions(self, machine_id: str, *, limit: int, offset: int):
        rows = self.session.scalars(select(RecipeVersionRow).where(RecipeVersionRow.machine_id == machine_id)
            .order_by(RecipeVersionRow.created_at.desc(), RecipeVersionRow.recipe_id, RecipeVersionRow.version.desc())
            .offset(offset).limit(limit))
        return tuple(self.get(row.recipe_id, row.version) for row in rows)

    def trials(self, task_id: UUID, *, limit: int, offset: int):
        rows = self.session.scalars(select(TrialMeasurementRow).where(TrialMeasurementRow.task_id == task_id)
            .order_by(TrialMeasurementRow.measured_at.desc(), TrialMeasurementRow.id).offset(offset).limit(limit))
        return tuple(self.measurement(row.id) for row in rows)

    def add_measurement(self, value: SetupTrialMeasurement):
        if self.get(value.recipe_id, value.recipe_version) is None:
            raise ValueError("Версия рецепта не найдена")
        text_value = str(value.value)
        if len(text_value) > 128:
            raise ValueError("Слишком длинное значение измерения")
        self.session.add(TrialMeasurementRow(id=value.id, task_id=value.task_id, recipe_id=value.recipe_id,
            recipe_version=value.recipe_version, label=value.label, value=text_value, unit=value.unit,
            measured_by=value.measured_by, measured_at=value.measured_at))
        self.session.flush()

    def measurement(self, identifier: UUID) -> SetupTrialMeasurement | None:
        row = self.session.get(TrialMeasurementRow, identifier)
        if row is None:
            return None
        return SetupTrialMeasurement(row.id, row.task_id, row.recipe_id, row.recipe_version,
            row.label, Decimal(row.value), row.unit, row.measured_by, _utc(row.measured_at))
