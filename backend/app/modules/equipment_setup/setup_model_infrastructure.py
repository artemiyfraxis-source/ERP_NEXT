"""Tables of the setup model (migration 0199_equipment_setup_model); rules in `setup_model.py`. The caller commits.

Constraints, not rollbacks: the class and the source are CHECKed lists, a unit's parameters hang on its override row,
a reference is unique per unit + wire + contact + version and needs a wire or a contact, a configuration points only at
an existing reference of the same unit (composite FK), a machine correction is one row per unit + parameter + wire."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, Numeric,
                        String, UniqueConstraint, Uuid, select)
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.sql import false as sa_false

from app.shared.persistence import Base

from .setup_model import (REQUIRES_SETUP, SOURCES, CurrentConfiguration, MachineCorrection, ParameterOverride,
                          SetupModel, SetupParameter, SetupReference, UnitOverride, decimal_of)

_REQUIRES_IN = ", ".join(repr(item) for item in REQUIRES_SETUP)
_SOURCES_IN = ", ".join(repr(item) for item in SOURCES)


class SetupModelRow(Base):
    """The setup model of one equipment model (БД.ОБ «модель»)."""
    __tablename__ = "equipment_setup_models"
    __table_args__ = (
        CheckConstraint(f"requires_setup IN ({_REQUIRES_IN})", name="ck_equipment_setup_models_requires"),
        CheckConstraint("version >= 1", name="ck_equipment_setup_models_version"),
    )
    model_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_models.id", ondelete="RESTRICT",
                                                            name="fk_equipment_setup_models_model"), primary_key=True)
    requires_setup: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    updated_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SetupModelParameterRow(Base):
    __tablename__ = "equipment_setup_model_parameters"
    __table_args__ = (
        CheckConstraint(f"source IN ({_SOURCES_IN})", name="ck_equipment_setup_model_parameters_source"),
        CheckConstraint("position >= 0", name="ck_equipment_setup_model_parameters_position"),
        CheckConstraint("trim(label) <> ''", name="ck_equipment_setup_model_parameters_label"),
    )
    model_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_setup_models.model_id", ondelete="CASCADE",
                                                            name="fk_equipment_setup_model_parameters_model"),
                                           primary_key=True)
    param_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_field: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    register: Mapped[str] = mapped_column(String(40), nullable=False, default="", server_default="")


class SetupUnitOverrideRow(Base):
    """A unit that differs from its model: its own class (NULL — the model's) and/or its own parameters."""
    __tablename__ = "equipment_setup_unit_overrides"
    __table_args__ = (
        CheckConstraint(f"requires_setup IS NULL OR requires_setup IN ({_REQUIRES_IN})",
                        name="ck_equipment_setup_unit_overrides_requires"),
        CheckConstraint("version >= 1", name="ck_equipment_setup_unit_overrides_version"),
    )
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_equipment_setup_unit_overrides_unit"),
                                          primary_key=True)
    requires_setup: Mapped[str | None] = mapped_column(String(8), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    updated_by_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SetupUnitParameterRow(Base):
    """One parameter change of a unit: `removed` hides the model's parameter, otherwise this row replaces / adds it."""
    __tablename__ = "equipment_setup_unit_parameters"
    __table_args__ = (
        CheckConstraint(f"source IN ({_SOURCES_IN})", name="ck_equipment_setup_unit_parameters_source"),
        CheckConstraint("position >= 0", name="ck_equipment_setup_unit_parameters_position"),
        CheckConstraint("removed OR trim(label) <> ''", name="ck_equipment_setup_unit_parameters_label"),
    )
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_setup_unit_overrides.unit_id", ondelete="CASCADE",
                                                           name="fk_equipment_setup_unit_parameters_override"),
                                          primary_key=True)
    param_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str] = mapped_column(String(160), nullable=False, default="", server_default="")
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    source_field: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    register: Mapped[str] = mapped_column(String(40), nullable=False, default="", server_default="")


class SetupReferenceRow(Base):
    """An эталон version (append-only): unit + wire and/or contact + version."""
    __tablename__ = "equipment_setup_references"
    __table_args__ = (
        UniqueConstraint("unit_id", "wire_key", "contact_key", "version", name="uq_equipment_setup_references_version"),
        UniqueConstraint("id", "unit_id", name="uq_equipment_setup_references_unit"),
        CheckConstraint("version >= 1", name="ck_equipment_setup_references_version"),
        CheckConstraint("wire_key <> '' OR contact_key <> ''", name="ck_equipment_setup_references_material"),
        CheckConstraint("trim(created_by_name) <> ''", name="ck_equipment_setup_references_author"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_equipment_setup_references_unit"), nullable=False)
    wire: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    wire_key: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    contact: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    contact_key: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    setup_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    tooling_unit_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT", name="fk_equipment_setup_references_tooling"))
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SetupConfigurationRow(Base):
    """What a unit holds now; replaced as a whole under `version` (who/when on the row, history in the audit)."""
    __tablename__ = "equipment_setup_configurations"
    __table_args__ = (
        ForeignKeyConstraint(["reference_id", "unit_id"], ["equipment_setup_references.id",
                                                           "equipment_setup_references.unit_id"],
                             name="fk_equipment_setup_configurations_reference", ondelete="RESTRICT"),
        CheckConstraint("version >= 1", name="ck_equipment_setup_configurations_version"),
        CheckConstraint("trim(set_by_name) <> ''", name="ck_equipment_setup_configurations_author"),
    )
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_equipment_setup_configurations_unit"),
                                          primary_key=True)
    tooling_unit_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT", name="fk_equipment_setup_configurations_tooling"))
    blades: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    wire: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    contact: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    reference_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    setup_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    set_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    set_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SetupMachineCorrectionRow(Base):
    """Case (a) of a correction: an offset of one parameter for this unit (all wires: `wire_key` = '')."""
    __tablename__ = "equipment_setup_machine_corrections"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_equipment_setup_machine_corrections_version"),
        CheckConstraint("offset_value > -1000 AND offset_value < 1000", name="ck_equipment_setup_machine_corrections_offset"),
        CheckConstraint("trim(set_by_name) <> ''", name="ck_equipment_setup_machine_corrections_author"),
    )
    unit_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_equipment_setup_machine_corrections_unit"),
                                          primary_key=True)
    parameter_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    wire_key: Mapped[str] = mapped_column(String(200), primary_key=True, default="", server_default="")
    wire: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    offset_value: Mapped[Decimal] = mapped_column(Numeric(9, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    set_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    set_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite gives naive timestamps back; they were stored as UTC."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _parameter(row) -> SetupParameter:
    return SetupParameter(row.param_key, row.label, row.unit, row.source, row.source_field, row.register, row.position)


class SqlAlchemySetupModels:
    """Reads and the seed's writes of the setup model. Writes of configurations / references / corrections come with
    the setup screen (they need its permissions and journal)."""

    def __init__(self, session: Session):
        self.session = session

    # --- reads ---
    def models(self) -> dict[UUID, SetupModel]:
        parameters: dict[UUID, list[SetupParameter]] = {}
        for row in self.session.scalars(select(SetupModelParameterRow)):
            parameters.setdefault(row.model_id, []).append(_parameter(row))
        return {row.model_id: SetupModel(row.requires_setup, tuple(parameters.get(row.model_id, ())), row.reason)
                for row in self.session.scalars(select(SetupModelRow))}

    def overrides(self) -> dict[UUID, UnitOverride]:
        changes: dict[UUID, list[ParameterOverride]] = {}
        for row in self.session.scalars(select(SetupUnitParameterRow)):
            change = ParameterOverride(row.param_key, removed=True) if row.removed else ParameterOverride(row.param_key, parameter=_parameter(row))
            changes.setdefault(row.unit_id, []).append(change)
        return {row.unit_id: UnitOverride(row.requires_setup, tuple(changes.get(row.unit_id, ())), row.reason)
                for row in self.session.scalars(select(SetupUnitOverrideRow))}

    def configuration(self, unit_id: UUID) -> CurrentConfiguration | None:
        row = self.session.get(SetupConfigurationRow, unit_id)
        if row is None:
            return None
        return CurrentConfiguration(row.unit_id, dict(row.setup_values or {}), row.tooling_unit_id, row.blades, row.wire,
                                    row.contact, row.reference_id, row.set_by_name, _aware(row.set_at), row.version)

    def configured_units(self) -> set[UUID]:
        return set(self.session.scalars(select(SetupConfigurationRow.unit_id)))

    def references(self, unit_id: UUID) -> list[SetupReference]:
        rows = self.session.scalars(select(SetupReferenceRow).where(SetupReferenceRow.unit_id == unit_id)
                                    .order_by(SetupReferenceRow.wire_key, SetupReferenceRow.contact_key,
                                              SetupReferenceRow.version.desc()))
        return [SetupReference(row.id, row.unit_id, row.wire, row.contact, row.version, dict(row.setup_values or {}),
                               row.tooling_unit_id, row.note, row.created_by_name, _aware(row.created_at)) for row in rows]

    def corrections(self, unit_id: UUID) -> list[MachineCorrection]:
        rows = self.session.scalars(select(SetupMachineCorrectionRow).where(SetupMachineCorrectionRow.unit_id == unit_id)
                                    .order_by(SetupMachineCorrectionRow.parameter_key, SetupMachineCorrectionRow.wire_key))
        return [MachineCorrection(row.parameter_key, decimal_of(row.offset_value), row.wire, row.reason) for row in rows]

    # --- the seed (owner-reviewed CSV) ---
    def put_model(self, model_id: UUID, model: SetupModel, *, source: str, actor_name: str, now: datetime) -> str:
        """Create or replace a model's setup model; returns «новая» / «изменена» / «без изменений»."""
        row = self.session.get(SetupModelRow, model_id)
        current = self.models().get(model_id) if row is not None else None
        if current == model:
            return "без изменений"
        if row is None:
            row = SetupModelRow(model_id=model_id, version=1, updated_at=now)
            self.session.add(row)
            outcome = "новая"
        else:
            row.version += 1
            outcome = "изменена"
        row.requires_setup, row.reason, row.source = model.requires_setup, model.reason, source[:200]
        row.updated_by_name, row.updated_at = actor_name, now
        for old in self.session.scalars(select(SetupModelParameterRow).where(SetupModelParameterRow.model_id == model_id)):
            self.session.delete(old)
        self.session.flush()
        for item in model.parameters:
            self.session.add(SetupModelParameterRow(model_id=model_id, param_key=item.key, position=item.position,
                                                    label=item.label, unit=item.unit, source=item.source,
                                                    source_field=item.source_field, register=item.register))
        return outcome

    def put_override(self, unit_id: UUID, override: UnitOverride | None, *, actor_name: str, now: datetime) -> str:
        row = self.session.get(SetupUnitOverrideRow, unit_id)
        if override is None:
            if row is None:
                return "без изменений"
            for old in self.session.scalars(select(SetupUnitParameterRow).where(SetupUnitParameterRow.unit_id == unit_id)):
                self.session.delete(old)
            self.session.flush()
            self.session.delete(row)
            return "снята"
        if row is not None and self.overrides().get(unit_id) == override:
            return "без изменений"
        if row is None:
            row = SetupUnitOverrideRow(unit_id=unit_id, version=1, updated_at=now)
            self.session.add(row)
            outcome = "новая"
        else:
            row.version += 1
            outcome = "изменена"
        row.requires_setup, row.reason, row.updated_by_name, row.updated_at = (override.requires_setup, override.reason,
                                                                               actor_name, now)
        for old in self.session.scalars(select(SetupUnitParameterRow).where(SetupUnitParameterRow.unit_id == unit_id)):
            self.session.delete(old)
        self.session.flush()
        for change in override.parameters:
            item = change.parameter
            self.session.add(SetupUnitParameterRow(
                unit_id=unit_id, param_key=change.key, removed=change.removed, position=item.position if item else 0,
                label=item.label if item else "", unit=item.unit if item else "", source=item.source if item else "manual",
                source_field=item.source_field if item else "", register=item.register if item else ""))
        return outcome
