"""Create production tasks, route snapshots, assignments, and calendar."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008_production_planning"
down_revision: str | None = "0007_fulfillment_returns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for role, mode in (
        ("admin", "edit"), ("master", "edit"), ("prodhead", "view"),
        ("management", "view"), ("staff", "view"),
    ):
        op.execute(sa.text("""
            INSERT INTO identity_role_access (role, section, mode)
            VALUES (:role, 'production', :mode)
            ON CONFLICT (role, section) DO NOTHING
        """).bindparams(role=role, mode=mode))
    op.create_table(
        "production_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("source_idempotency_key", sa.String(255), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item", sa.String(255), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("fulfillment_request_id", sa.Uuid(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("launched_by", sa.String(255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
        sa.UniqueConstraint("source_idempotency_key"),
    )
    op.create_index("ix_production_tasks_project", "production_tasks", ["project"])
    op.create_index("ix_production_tasks_item", "production_tasks", ["item"])
    op.create_index("ix_production_tasks_fulfillment_request_id", "production_tasks", ["fulfillment_request_id"])
    op.create_index("ix_production_tasks_status", "production_tasks", ["status"])
    op.create_index("ix_production_status_created_id", "production_tasks", ["status", "created_at", "id"])
    op.create_table(
        "production_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(255), nullable=False),
        sa.Column("stage", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("seconds_per_unit", sa.Numeric(18, 6), nullable=False),
        sa.Column("setup_seconds", sa.Numeric(18, 6), nullable=False),
        sa.Column("norm_seconds", sa.Integer(), nullable=False),
        sa.Column("planned_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_finish", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_operations_task_id", "production_operations", ["task_id"])
    op.create_index("ux_production_operation_position", "production_operations", ["task_id", "position"], unique=True)
    op.create_table(
        "production_operation_operators",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["production_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("operation_id", "operator_id"),
    )
    op.create_table("production_holidays", sa.Column("day", sa.Date(), nullable=False), sa.PrimaryKeyConstraint("day"))
    op.create_table(
        "production_absences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_absences_operator_id", "production_absences", ["operator_id"])
    op.create_table(
        "production_sequences",
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("production_sequences")
    op.drop_index("ix_production_absences_operator_id", table_name="production_absences")
    op.drop_table("production_absences")
    op.drop_table("production_holidays")
    op.drop_table("production_operation_operators")
    op.drop_index("ux_production_operation_position", table_name="production_operations")
    op.drop_index("ix_production_operations_task_id", table_name="production_operations")
    op.drop_table("production_operations")
    op.drop_index("ix_production_status_created_id", table_name="production_tasks")
    op.drop_index("ix_production_tasks_status", table_name="production_tasks")
    op.drop_index("ix_production_tasks_fulfillment_request_id", table_name="production_tasks")
    op.drop_index("ix_production_tasks_item", table_name="production_tasks")
    op.drop_index("ix_production_tasks_project", table_name="production_tasks")
    op.drop_table("production_tasks")
    op.execute(sa.text("""
        DELETE FROM identity_role_access
        WHERE section = 'production' AND role IN ('admin', 'master', 'prodhead', 'management', 'staff')
    """))
