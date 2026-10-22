"""Очередь печати этикеток ГРН (owner 22.10.2026): сервер ERP больше не печатает сам.

The TSC MB341 is on USB at the warehouse Windows PC, the ERP server is Linux Docker. Every print queues a ready TSPL job
in `warehouse_label_jobs`; the print agent on that PC (API token, scope `labels:print`) claims the oldest job, prints it
and reports the result. The status machine pending → taken → printed | failed is held by CHECK constraints: a taken job
has its lease, only a final job has its finish time, only a failed one its error, a pending one was never taken.
`warehouse_label_agent` is the agent's heartbeat, one row (id = 1): last call, host, printer, last error.

Two new tables, nothing existing is touched. Downgrade drops both with their rows (the queue and the heartbeat).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0141_warehouse_label_jobs"
down_revision: str | None = "0140_purchasing_rfq_rb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The same constraints as `app.modules.warehouse.label_jobs.LabelJobRow`. 'sample' leads the kind list: the CI seeder
# takes an IN list's first value, and a 'lot' job needs its lot.
JOB_CHECKS = {
    "ck_warehouse_label_jobs_kind": "kind IN ('sample', 'lot')",
    "ck_warehouse_label_jobs_sample_no_lot": "lot_id IS NULL OR kind = 'lot'",
    "ck_warehouse_label_jobs_lot": "kind <> 'lot' OR lot_id IS NOT NULL",
    "ck_warehouse_label_jobs_copies": "copies >= 1 AND copies <= 50",
    "ck_warehouse_label_jobs_tspl": "length(tspl) > 0",
    "ck_warehouse_label_jobs_status": "status IN ('pending', 'taken', 'printed', 'failed')",
    "ck_warehouse_label_jobs_pending_untaken": "taken_at IS NULL OR status <> 'pending'",
    "ck_warehouse_label_jobs_taken_at": "status = 'pending' OR taken_at IS NOT NULL",
    "ck_warehouse_label_jobs_lease_only_taken": "lease_until IS NULL OR status = 'taken'",
    "ck_warehouse_label_jobs_taken_lease": "status <> 'taken' OR lease_until IS NOT NULL",
    "ck_warehouse_label_jobs_lease_after": "lease_until IS NULL OR lease_until > taken_at",
    "ck_warehouse_label_jobs_finished_only_final": "finished_at IS NULL OR status = 'printed' OR status = 'failed'",
    "ck_warehouse_label_jobs_final_finished": "status = 'pending' OR status = 'taken' OR finished_at IS NOT NULL",
    "ck_warehouse_label_jobs_error_only_failed": "error IS NULL OR status = 'failed'",
    "ck_warehouse_label_jobs_failed_error": "status <> 'failed' OR error IS NOT NULL",
}


def upgrade() -> None:
    op.create_table(
        "warehouse_label_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("copies", sa.Integer(), nullable=False),
        sa.Column("tspl", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *(sa.CheckConstraint(text, name=name) for name, text in JOB_CHECKS.items()),
    )
    op.create_index("ix_warehouse_label_jobs_status_created", "warehouse_label_jobs", ["status", "created_at"])
    op.create_table(
        "warehouse_label_agent",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("host", sa.String(255), nullable=False, server_default=""),
        sa.Column("printer", sa.String(255), nullable=False, server_default=""),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint("id = 1", name="ck_warehouse_label_agent_single_row"),
    )


def downgrade() -> None:
    op.drop_table("warehouse_label_agent")
    op.drop_index("ix_warehouse_label_jobs_status_created", table_name="warehouse_label_jobs")
    op.drop_table("warehouse_label_jobs")
