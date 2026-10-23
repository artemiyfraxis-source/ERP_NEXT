"""«Отсканировать УПД» со сканера склада (owner 23.10.2026): the scan queue.

- `warehouse_scan_jobs` — one press of the button: the agent of the warehouse PC (API token, scope `scan:upload`)
  claims the oldest pending job, scans the paper on the Pantum (eSCL) and uploads the file. status CHECK pending /
  claimed / done / failed; a done job and only a done one points at its file (`file_id` → warehouse_receipt_files.id,
  ON DELETE RESTRICT); a failed one says why (`error` <> ''); `result` — the recognition answer (JSON text) stored
  once, '' until made. Index (status, created_at) for the claim of the oldest pending job.

The pairs are written as `x IS NULL OR …` / `status <> … OR …` checks, one `IN (…)` read only — on the status
itself: the head-1 seeder of CI takes the first value of an `IN` list and leaves a `x IS NULL OR …` column empty.
Non-destructive: one new empty table. DESTRUCTIVE = False. Downgrade drops it.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0145_warehouse_scan_jobs"
down_revision: str | None = "0144_warehouse_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False
TABLE = "warehouse_scan_jobs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "file_id", sa.Uuid(), sa.ForeignKey("warehouse_receipt_files.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("result", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint("status IN ('pending', 'claimed', 'done', 'failed')", name="ck_warehouse_scan_jobs_status"),
        # (status = 'done') = (file_id IS NOT NULL), as two checks.
        sa.CheckConstraint("file_id IS NULL OR status = 'done'", name="ck_warehouse_scan_jobs_file_only_done"),
        sa.CheckConstraint("status <> 'done' OR file_id IS NOT NULL", name="ck_warehouse_scan_jobs_done_file"),
        sa.CheckConstraint("claimed_at IS NULL OR status <> 'pending'", name="ck_warehouse_scan_jobs_pending_unclaimed"),
        sa.CheckConstraint(
            "(status <> 'claimed' AND status <> 'done') OR claimed_at IS NOT NULL",
            name="ck_warehouse_scan_jobs_claimed_at",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR status IN ('done', 'failed')", name="ck_warehouse_scan_jobs_finished_only_final",
        ),
        sa.CheckConstraint(
            "(status <> 'done' AND status <> 'failed') OR finished_at IS NOT NULL",
            name="ck_warehouse_scan_jobs_final_finished",
        ),
        sa.CheckConstraint("status <> 'failed' OR error <> ''", name="ck_warehouse_scan_jobs_failed_error"),
        sa.CheckConstraint("result = '' OR status = 'done'", name="ck_warehouse_scan_jobs_result_only_done"),
    )
    op.create_index("ix_warehouse_scan_jobs_status_created", TABLE, ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_warehouse_scan_jobs_status_created", table_name=TABLE)
    op.drop_table(TABLE)
