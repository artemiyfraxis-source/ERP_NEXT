"""Add isolated production defect boxes and append-only decisions."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0015_production_defects"
down_revision: str | None = "0014_production_concurrency_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_defects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("output_id", sa.Uuid(), nullable=False),
        sa.Column("source_box_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("character", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["production_operations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["output_id"], ["production_outputs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
        sa.UniqueConstraint("output_id"),
    )
    op.create_index("ix_production_defects_source_box_id", "production_defects", ["source_box_id"])
    op.create_index("ix_production_defects_task_id", "production_defects", ["task_id"])
    op.create_index("ix_production_defects_operation_id", "production_defects", ["operation_id"])
    op.create_index("ix_production_defects_operator_id", "production_defects", ["operator_id"])
    op.create_index("ix_production_defects_status", "production_defects", ["status"])
    op.create_table(
        "production_defect_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("defect_id", sa.Uuid(), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("decided_by", sa.String(255), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("act_reference", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["defect_id"], ["production_defects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("defect_id"),
    )


def downgrade() -> None:
    op.drop_table("production_defect_decisions")
    op.drop_index("ix_production_defects_status", table_name="production_defects")
    op.drop_index("ix_production_defects_operator_id", table_name="production_defects")
    op.drop_index("ix_production_defects_operation_id", table_name="production_defects")
    op.drop_index("ix_production_defects_task_id", table_name="production_defects")
    op.drop_index("ix_production_defects_source_box_id", table_name="production_defects")
    op.drop_table("production_defects")

