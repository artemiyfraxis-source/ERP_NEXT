"""«Проекты»: карточки наборов (состав и спецификация по сборкам) и КД при сборках — SUPPLYMAP «Наборы», «КД»."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0046_techcard_kitsets"
down_revision: str | None = "0045_production_box_luhn_check_digits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "techcard_kitsets",
        sa.Column("project", sa.String(255), primary_key=True),
        sa.Column("project_key", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("customer", sa.String(512), nullable=False),
        sa.Column("calc_source", sa.String(1024), nullable=False),
        sa.Column("assemblies", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
    )
    op.create_index("ux_techcard_kitsets_project_key", "techcard_kitsets", ["project_key"], unique=True)
    op.create_table(
        "techcard_kitset_documents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item_index", sa.String(64), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by", sa.String(255), nullable=False),
    )
    op.create_index(
        "ix_techcard_kitset_documents_project", "techcard_kitset_documents", ["project", "item_index"],
    )


def downgrade() -> None:
    op.drop_index("ix_techcard_kitset_documents_project", table_name="techcard_kitset_documents")
    op.drop_table("techcard_kitset_documents")
    op.drop_index("ux_techcard_kitsets_project_key", table_name="techcard_kitsets")
    op.drop_table("techcard_kitsets")
