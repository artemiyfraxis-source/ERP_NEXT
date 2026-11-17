"""Add cutting groups without replacing KB tasks or production history."""

from alembic import op
import sqlalchemy as sa

revision = "0186_cutting_groups"
down_revision = "0185_shared_accessories"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    # A unique index is also a candidate key for the composite FK on both supported databases.
    op.create_index("ux_production_operation_task_id", "production_operations", ["task_id", "id"], unique=True)
    op.create_table("production_cutting_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.String(64), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("project_key", sa.String(255), nullable=False),
        sa.Column("site", sa.String(32), nullable=False),
        sa.Column("recipe_key", sa.String(64), nullable=False),
        sa.Column("recipe_version", sa.Integer(), nullable=False),
        sa.Column("recipe", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("joinable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("version > 0 AND recipe_version > 0", name="ck_cutting_group_versions"),
        sa.CheckConstraint("state IN ('open', 'frozen', 'cancelled')", name="ck_cutting_group_state"),
    )
    for column in ("project_key", "recipe_key"):
        op.create_index(f"ix_production_cutting_groups_{column}", "production_cutting_groups", [column])
    op.create_table("production_cutting_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("production_cutting_groups.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("assembly_quantity", sa.Integer(), nullable=False),
        sa.Column("blank_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("row_snapshot", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(["task_id", "operation_id"], ["production_operations.task_id", "production_operations.id"],
                                name="fk_cutting_member_operation_task", ondelete="RESTRICT"),
        sa.UniqueConstraint("group_id", "operation_id", name="ux_cutting_member_group_operation"),
        sa.UniqueConstraint("group_id", "position", name="ux_cutting_member_position"),
        sa.CheckConstraint("assembly_quantity > 0 AND blank_quantity > 0 AND position > 0", name="ck_cutting_member_quantities"),
        sa.CheckConstraint("state IN ('active', 'split', 'cancelled')", name="ck_cutting_member_state"),
    )
    op.create_index("ix_production_cutting_members_task_id", "production_cutting_members", ["task_id"])
    op.create_index("ux_cutting_member_active_operation", "production_cutting_members", ["operation_id"], unique=True,
                    sqlite_where=sa.text("state = 'active'"), postgresql_where=sa.text("state = 'active'"))


def downgrade():
    bind = op.get_bind()
    if any(bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() for table in (
        "production_cutting_members", "production_cutting_groups",
    )):
        raise RuntimeError("Нельзя отменить миграцию: существует история групп резки")
    op.drop_table("production_cutting_members")
    op.drop_table("production_cutting_groups")
    op.drop_index("ux_production_operation_task_id", table_name="production_operations")
