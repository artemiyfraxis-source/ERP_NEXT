"""НАЗНАЧЕНИЕ «Опытный образец (НИОКР)» объединено с «RND» (владелец 29.09.2026: «опытный образец и есть RND»).

Существующие заявки с этим назначением переводятся на RND. Откат не восстанавливает старое значение:
после слияния уже не отличить, какие RND были опытным образцом.

Revision ID: 0070_purpose_rnd_merge
Revises: 0069_techcard_kd_in_database
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0070_purpose_rnd_merge"
down_revision: str | None = "0069_techcard_kd_in_database"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text("UPDATE requests SET purpose = 'RND' WHERE purpose = 'Опытный образец (НИОКР)'")
    )


def downgrade() -> None:
    pass
