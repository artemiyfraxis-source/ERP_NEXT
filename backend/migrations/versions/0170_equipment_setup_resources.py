"""Add exact resource accounting and equipment setup; never infer legacy allocations."""

from alembic import op
import sqlalchemy as sa

revision = "0170_equipment_setup_resources"
down_revision = "0169_shipping_pack_permissions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("equipment_setup_recipe_versions",
        sa.Column("recipe_id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("machine_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("geometry", sa.JSON(), nullable=False),
        sa.Column("panel_settings", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(2000), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_setup_recipe_version"),
        sa.CheckConstraint("length(machine_id) > 0", name="ck_setup_recipe_machine"),
        sa.CheckConstraint("length(source) > 0", name="ck_setup_recipe_source"))
    op.create_table("equipment_setup_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("technician_id", sa.Uuid(), nullable=False),
        sa.Column("machine_id", sa.String(128), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_setup_task_version"),
        sa.CheckConstraint("status IN ('New','InProgress','Blocked','Ready','Cancelled')", name="ck_setup_task_status"),
        sa.ForeignKeyConstraint(["recipe_id", "recipe_version"],
            ["equipment_setup_recipe_versions.recipe_id", "equipment_setup_recipe_versions.version"], name="fk_setup_task_recipe"))
    op.create_index("ix_equipment_setup_tasks_technician_id", "equipment_setup_tasks", ["technician_id"])
    op.create_index("ix_equipment_setup_tasks_machine_id", "equipment_setup_tasks", ["machine_id"])
    op.create_table("equipment_setup_trial_measurements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("value", sa.String(128), nullable=False),
        sa.Column("unit", sa.String(160), nullable=False),
        sa.Column("measured_by", sa.Uuid(), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("recipe_version >= 1", name="ck_setup_trial_recipe_version"),
        sa.ForeignKeyConstraint(["recipe_id", "recipe_version"],
            ["equipment_setup_recipe_versions.recipe_id", "equipment_setup_recipe_versions.version"], name="fk_setup_trial_recipe"))
    op.create_index("ix_equipment_setup_trial_measurements_task_id", "equipment_setup_trial_measurements", ["task_id"])
    op.create_table("equipment_setup_commands",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False))
    op.create_table("equipment_setup_transitions",
        sa.Column("task_id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.CheckConstraint("version >= 2", name="ck_setup_transition_version"),
        sa.ForeignKeyConstraint(["task_id"], ["equipment_setup_tasks.id"], name="fk_setup_transition_task"))
    op.create_table("production_resource_reservations",
        sa.Column("owner_kind", sa.String(16), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), primary_key=True),
        sa.Column("resource_kind", sa.String(16), primary_key=True),
        sa.Column("resource_key", sa.String(128), primary_key=True),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finish", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("resource_kind IN ('machine', 'person')", name="ck_resource_reservation_kind"),
        sa.CheckConstraint("owner_kind IN ('setup', 'production')", name="ck_resource_reservation_owner"),
        sa.CheckConstraint("finish > start", name="ck_resource_reservation_window"))
    op.create_table("production_active_resources",
        sa.Column("resource_kind", sa.String(16), primary_key=True),
        sa.Column("resource_key", sa.String(128), primary_key=True),
        sa.Column("owner_kind", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("resource_kind IN ('machine', 'person')", name="ck_active_resource_kind"),
        sa.CheckConstraint("owner_kind IN ('setup', 'production')", name="ck_active_resource_owner"))
    generation = op.create_table("production_resource_generation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_resource_generation_singleton"),
        sa.CheckConstraint("version >= 0", name="ck_resource_generation_version"))
    op.bulk_insert(generation, [{"id": 1, "version": 0}])
    op.create_table("production_resource_allocations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("production_tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("crew", sa.Boolean(), nullable=False),
        sa.Column("machines", sa.JSON(), nullable=False))
    op.create_index("ix_production_resource_allocations_task_id", "production_resource_allocations", ["task_id"])
    op.create_index("ix_production_resource_allocations_operation_id", "production_resource_allocations", ["operation_id"])
    op.create_table("production_resource_allocation_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("allocation_id", sa.Uuid(), sa.ForeignKey("production_resource_allocations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator_id", sa.Uuid(), sa.ForeignKey("identity_users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("current", sa.Boolean(), nullable=False))
    op.create_index("ix_production_resource_allocation_members_allocation_id", "production_resource_allocation_members", ["allocation_id"])
    op.create_index("ux_resource_member_operation_person", "production_resource_allocation_members", ["operation_id", "operator_id"],
        unique=True, postgresql_where=sa.text("current = true"), sqlite_where=sa.text("current = 1"))
    op.create_table("production_resource_group_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("allocation_id", sa.Uuid(), sa.ForeignKey("production_resource_allocations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False))
    op.create_index("ix_production_resource_group_runs_allocation_id", "production_resource_group_runs", ["allocation_id"])
    op.create_index("ux_resource_group_open_run", "production_resource_group_runs", ["allocation_id"],
        unique=True, postgresql_where=sa.text("closed = false"), sqlite_where=sa.text("closed = 0"))
    op.create_table("production_resource_session_members",
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("production_work_sessions.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("production_resource_allocation_members.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("production_resource_group_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False))
    op.create_index("ix_production_resource_session_members_member_id", "production_resource_session_members", ["member_id"])
    op.create_index("ix_production_resource_session_members_run_id", "production_resource_session_members", ["run_id"])


TABLES = (
    "production_resource_session_members", "production_resource_group_runs", "production_resource_allocation_members",
    "production_resource_allocations", "production_active_resources", "production_resource_reservations",
    "equipment_setup_transitions", "equipment_setup_commands", "equipment_setup_trial_measurements",
    "equipment_setup_tasks", "equipment_setup_recipe_versions",
)


def downgrade():
    connection = op.get_bind()
    if any(connection.execute(sa.text(f'SELECT 1 FROM "{name}" LIMIT 1')).first() for name in TABLES):
        raise RuntimeError("Cannot erase equipment setup/resource history; restore a prior copy instead.")
    if connection.execute(sa.text("SELECT version FROM production_resource_generation WHERE id = 1")).scalar() != 0:
        raise RuntimeError("Cannot erase resource generation history; restore a prior copy instead.")
    for name in TABLES:
        op.drop_table(name)
    op.drop_table("production_resource_generation")
