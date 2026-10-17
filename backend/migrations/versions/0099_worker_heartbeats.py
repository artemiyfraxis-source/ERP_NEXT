"""Heartbeats of the background worker's job groups (audit U08, 10.10.2026).

One row per job group (`dispatch`, `timers`, `owner`, `arbiter`, `leave_calendar`, `autofill`, `kitset_names`): the
last start, success and failure, the run time and how many runs in a row failed. «Системный журнал» and
/api/health?detail=1 judge the worker by them (a group silent too long, or the oldest pending message too old, is red).

New table only, empty: the worker fills it on its first pass. Nothing existing changes; downgrade drops the table.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0099_worker_heartbeats"
down_revision: str | None = "0098_project_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("job_group", sa.String(64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_worker_heartbeats_duration"),
        sa.CheckConstraint("failures >= 0", name="ck_worker_heartbeats_failures"),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
