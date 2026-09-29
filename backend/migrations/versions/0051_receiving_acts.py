"""Акты приёмки (RC-9): № и дата акта ВК, акта о несоответствии, акта рекламации, разрешения на использование."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0051_receiving_acts"
down_revision: str | None = "0050_receiving_document_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTS = ("vk_act", "nc_act", "complaint_act", "use_permit")


def upgrade() -> None:
    with op.batch_alter_table("receiving_lines") as batch:
        for act in ACTS:
            batch.add_column(sa.Column(f"{act}_number", sa.String(32), nullable=False, server_default=""))
            batch.add_column(sa.Column(f"{act}_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("receiving_lines") as batch:
        for act in reversed(ACTS):
            batch.drop_column(f"{act}_date")
            batch.drop_column(f"{act}_number")
