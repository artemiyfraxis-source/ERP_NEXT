"""Day gates and dedup marks of the daily owner jobs (OP-4 digest, disposal escalation, OP-5 alert).

SUPPLYMAP kept them in ScriptProperties (`last_overdue_digest`, `last_quar_escalation`,
`quarantine_notified`, `error_alerted`); ERP keeps the same keys in one small table.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0046_operations_job_marks"
down_revision: str | None = "0042_shipping_project_addressees"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operations_job_marks",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("operations_job_marks")
