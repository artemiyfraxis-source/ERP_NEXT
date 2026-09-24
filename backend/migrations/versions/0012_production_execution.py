"""Add production output, boxes, and consumption genealogy."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_production_execution"
down_revision: str | None = "0011_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "production_boxes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(16), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("item", sa.String(255), nullable=False),
        sa.Column("operation_position", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("consumed", sa.Numeric(18, 6), nullable=False),
        sa.Column("reserved", sa.Numeric(18, 6), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(255), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(255), nullable=True),
        sa.Column("return_reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["production_operations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
    )
    op.create_index("ix_production_boxes_task_id", "production_boxes", ["task_id"])
    op.create_index("ix_production_boxes_operation_id", "production_boxes", ["operation_id"])
    op.create_index("ix_production_boxes_operator_id", "production_boxes", ["operator_id"])
    op.create_index("ix_production_boxes_status", "production_boxes", ["status"])
    op.create_index("ix_production_boxes_task_status", "production_boxes", ["task_id", "status"])
    op.create_table(
        "production_work_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("pause_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_seconds", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["production_operations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_work_sessions_task_id", "production_work_sessions", ["task_id"])
    op.create_index("ix_production_work_sessions_operation_id", "production_work_sessions", ["operation_id"])
    op.create_index("ix_production_work_sessions_operator_id", "production_work_sessions", ["operator_id"])
    op.create_index("ix_production_work_sessions_status", "production_work_sessions", ["status"])
    op.create_table(
        "production_work_inputs",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("box_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.ForeignKeyConstraint(["box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["production_work_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "box_id"),
    )
    op.create_table(
        "production_outputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pause_seconds", sa.Integer(), nullable=False),
        sa.Column("good_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("defect_quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("output_box_id", sa.Uuid(), nullable=False),
        sa.Column("correction_of_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["correction_of_id"], ["production_outputs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operation_id"], ["production_operations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_id"], ["identity_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["output_box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["production_work_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["production_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index("ix_production_outputs_task_id", "production_outputs", ["task_id"])
    op.create_index("ix_production_outputs_operation_id", "production_outputs", ["operation_id"])
    op.create_index("ix_production_outputs_operator_id", "production_outputs", ["operator_id"])
    op.create_index("ix_production_outputs_output_box_id", "production_outputs", ["output_box_id"])
    op.create_table(
        "production_consumption",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_box_id", sa.Uuid(), nullable=False),
        sa.Column("target_box_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("output_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["output_id"], ["production_outputs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_box_id"], ["production_boxes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_consumption_source_box_id", "production_consumption", ["source_box_id"])
    op.create_index("ix_production_consumption_target_box_id", "production_consumption", ["target_box_id"])
    op.create_index("ix_production_consumption_output_id", "production_consumption", ["output_id"])


def downgrade() -> None:
    op.drop_index("ix_production_consumption_output_id", table_name="production_consumption")
    op.drop_index("ix_production_consumption_target_box_id", table_name="production_consumption")
    op.drop_index("ix_production_consumption_source_box_id", table_name="production_consumption")
    op.drop_table("production_consumption")
    op.drop_index("ix_production_outputs_output_box_id", table_name="production_outputs")
    op.drop_index("ix_production_outputs_operator_id", table_name="production_outputs")
    op.drop_index("ix_production_outputs_operation_id", table_name="production_outputs")
    op.drop_index("ix_production_outputs_task_id", table_name="production_outputs")
    op.drop_table("production_outputs")
    op.drop_table("production_work_inputs")
    op.drop_index("ix_production_work_sessions_status", table_name="production_work_sessions")
    op.drop_index("ix_production_work_sessions_operator_id", table_name="production_work_sessions")
    op.drop_index("ix_production_work_sessions_operation_id", table_name="production_work_sessions")
    op.drop_index("ix_production_work_sessions_task_id", table_name="production_work_sessions")
    op.drop_table("production_work_sessions")
    op.drop_index("ix_production_boxes_task_status", table_name="production_boxes")
    op.drop_index("ix_production_boxes_status", table_name="production_boxes")
    op.drop_index("ix_production_boxes_operator_id", table_name="production_boxes")
    op.drop_index("ix_production_boxes_operation_id", table_name="production_boxes")
    op.drop_index("ix_production_boxes_task_id", table_name="production_boxes")
    op.drop_table("production_boxes")
