"""Setup model of БД.ОБ (owner 18–19.11.2026, «Станки + наладка» for the setup technician): seven new tables, nothing
existing changes; filled later from the owner-reviewed CSV by `python -m app.workflows.equipment_setup_model --seed`.

- `equipment_setup_models` + `equipment_setup_model_parameters`: per equipment model — нет / простая / полная and the
  parameters with their source (ЭТК, контакт, провод, оснастка, ПЛК, вручную) and PLC register;
- `equipment_setup_unit_overrides` + `equipment_setup_unit_parameters`: a unit's own class / parameter changes;
- `equipment_setup_references`: эталоны per unit + wire/contact, append-only versions;
- `equipment_setup_configurations`: what a unit holds now (tooling, blades, wire/contact, reference, values, who/when);
- `equipment_setup_machine_corrections`: per-unit offset per parameter (optionally per wire), applied on every write.

Literal copies of the lists in app/modules/equipment_setup/setup_model.py (a migration never imports the app)."""

from alembic import op
import sqlalchemy as sa

revision = "0198_equipment_setup_model"
down_revision = "0197_ob099_crimp_records"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

REQUIRES_IN = "'none', 'simple', 'full'"
SOURCES_IN = "'etk', 'contact', 'wire', 'tooling', 'plc', 'manual'"


def _unit_fk(column: str, name: str, **kwargs) -> sa.Column:
    return sa.Column(column, sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT", name=name), **kwargs)


def _parameter_columns(table: str) -> list:
    return [
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(160), nullable=False, server_default=""),
        sa.Column("unit", sa.String(20), nullable=False, server_default=""),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("source_field", sa.String(200), nullable=False, server_default=""),
        sa.Column("register", sa.String(40), nullable=False, server_default=""),
        sa.CheckConstraint(f"source IN ({SOURCES_IN})", name=f"ck_{table}_source"),
        sa.CheckConstraint("position >= 0", name=f"ck_{table}_position"),
    ]


def upgrade():
    op.create_table(
        "equipment_setup_models",
        sa.Column("model_id", sa.Uuid(), sa.ForeignKey("equipment_models.id", ondelete="RESTRICT",
                                                       name="fk_equipment_setup_models_model"), primary_key=True),
        sa.Column("requires_setup", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("source", sa.String(200), nullable=False, server_default=""),
        sa.Column("updated_by_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"requires_setup IN ({REQUIRES_IN})", name="ck_equipment_setup_models_requires"),
        sa.CheckConstraint("version >= 1", name="ck_equipment_setup_models_version"),
    )
    op.create_table(
        "equipment_setup_model_parameters",
        sa.Column("model_id", sa.Uuid(), sa.ForeignKey("equipment_setup_models.model_id", ondelete="CASCADE",
                                                       name="fk_equipment_setup_model_parameters_model"),
                  primary_key=True),
        sa.Column("param_key", sa.String(64), primary_key=True),
        *_parameter_columns("equipment_setup_model_parameters"),
        sa.CheckConstraint("trim(label) <> ''", name="ck_equipment_setup_model_parameters_label"),
    )
    op.create_table(
        "equipment_setup_unit_overrides",
        _unit_fk("unit_id", "fk_equipment_setup_unit_overrides_unit", primary_key=True),
        sa.Column("requires_setup", sa.String(8), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("updated_by_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"requires_setup IS NULL OR requires_setup IN ({REQUIRES_IN})",
                           name="ck_equipment_setup_unit_overrides_requires"),
        sa.CheckConstraint("version >= 1", name="ck_equipment_setup_unit_overrides_version"),
    )
    op.create_table(
        "equipment_setup_unit_parameters",
        sa.Column("unit_id", sa.Uuid(), sa.ForeignKey("equipment_setup_unit_overrides.unit_id", ondelete="CASCADE",
                                                      name="fk_equipment_setup_unit_parameters_override"),
                  primary_key=True),
        sa.Column("param_key", sa.String(64), primary_key=True),
        sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_parameter_columns("equipment_setup_unit_parameters"),
        sa.CheckConstraint("removed OR trim(label) <> ''", name="ck_equipment_setup_unit_parameters_label"),
    )
    op.create_table(
        "equipment_setup_references",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _unit_fk("unit_id", "fk_equipment_setup_references_unit", nullable=False),
        sa.Column("wire", sa.String(200), nullable=False, server_default=""),
        sa.Column("wire_key", sa.String(200), nullable=False, server_default=""),
        sa.Column("contact", sa.String(200), nullable=False, server_default=""),
        sa.Column("contact_key", sa.String(200), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("setup_values", sa.JSON(), nullable=False),
        _unit_fk("tooling_unit_id", "fk_equipment_setup_references_tooling", nullable=True),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_by_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("unit_id", "wire_key", "contact_key", "version", name="uq_equipment_setup_references_version"),
        sa.UniqueConstraint("id", "unit_id", name="uq_equipment_setup_references_unit"),
        sa.CheckConstraint("version >= 1", name="ck_equipment_setup_references_version"),
        sa.CheckConstraint("wire_key <> '' OR contact_key <> ''", name="ck_equipment_setup_references_material"),
        sa.CheckConstraint("trim(created_by_name) <> ''", name="ck_equipment_setup_references_author"),
    )
    op.create_table(
        "equipment_setup_configurations",
        _unit_fk("unit_id", "fk_equipment_setup_configurations_unit", primary_key=True),
        _unit_fk("tooling_unit_id", "fk_equipment_setup_configurations_tooling", nullable=True),
        sa.Column("blades", sa.String(200), nullable=False, server_default=""),
        sa.Column("wire", sa.String(200), nullable=False, server_default=""),
        sa.Column("contact", sa.String(200), nullable=False, server_default=""),
        sa.Column("reference_id", sa.Uuid(), nullable=True),
        sa.Column("setup_values", sa.JSON(), nullable=False),
        sa.Column("set_by", sa.Uuid(), nullable=False),
        sa.Column("set_by_name", sa.String(255), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["reference_id", "unit_id"],
                                ["equipment_setup_references.id", "equipment_setup_references.unit_id"],
                                name="fk_equipment_setup_configurations_reference", ondelete="RESTRICT"),
        sa.CheckConstraint("version >= 1", name="ck_equipment_setup_configurations_version"),
        sa.CheckConstraint("trim(set_by_name) <> ''", name="ck_equipment_setup_configurations_author"),
    )
    op.create_table(
        "equipment_setup_machine_corrections",
        _unit_fk("unit_id", "fk_equipment_setup_machine_corrections_unit", primary_key=True),
        sa.Column("parameter_key", sa.String(64), primary_key=True),
        sa.Column("wire_key", sa.String(200), primary_key=True, server_default=""),
        sa.Column("wire", sa.String(200), nullable=False, server_default=""),
        sa.Column("offset_value", sa.Numeric(9, 3), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("set_by", sa.Uuid(), nullable=False),
        sa.Column("set_by_name", sa.String(255), nullable=False),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_equipment_setup_machine_corrections_version"),
        sa.CheckConstraint("offset_value > -1000 AND offset_value < 1000",
                           name="ck_equipment_setup_machine_corrections_offset"),
        sa.CheckConstraint("trim(set_by_name) <> ''", name="ck_equipment_setup_machine_corrections_author"),
    )


def downgrade():
    for table in ("equipment_setup_machine_corrections", "equipment_setup_configurations", "equipment_setup_references",
                  "equipment_setup_unit_parameters", "equipment_setup_unit_overrides",
                  "equipment_setup_model_parameters", "equipment_setup_models"):
        op.drop_table(table)
