"""«РАЗРЕШЕНИЕ НА ЗАМЕНУ»: № and date of the printable document generated on decision «Разрешено»."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0037_procurement_replacement_permission"
down_revision: str | None = "0036_identity_departments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("procurement_lines", sa.Column("permission_number", sa.String(32), nullable=True))
    op.add_column("procurement_lines", sa.Column("permission_date", sa.Date(), nullable=True))
    op.create_index(
        "ix_procurement_lines_permission_number",
        "procurement_lines",
        ["permission_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_procurement_lines_permission_number", table_name="procurement_lines")
    op.drop_column("procurement_lines", "permission_date")
    op.drop_column("procurement_lines", "permission_number")
