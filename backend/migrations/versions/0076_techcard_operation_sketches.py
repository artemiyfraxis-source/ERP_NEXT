"""Своя картинка эскиза к операции ЭТК (владелец, 30.09: «нет схемы и некуда её добавлять») — байты в базе ERP."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0076_techcard_operation_sketches"
down_revision: str | None = "0075_production_handed_to"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "techcard_operation_sketches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_card_id",
            sa.Uuid(),
            sa.ForeignKey("techcard_projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation_key", sa.String(1024), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ux_techcard_operation_sketches_card_key", "techcard_operation_sketches",
        ["project_card_id", "operation_key"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_techcard_operation_sketches_card_key", table_name="techcard_operation_sketches")
    op.drop_table("techcard_operation_sketches")
