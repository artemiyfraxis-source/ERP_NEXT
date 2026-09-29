"""Шаблоны-листы БД.ТК в ERP: описание и след сохранения у шаблона, черновики шаблонов
(SUPPLYMAP TcTemplates.gs tcTemplateOpenDraft/NewDraft/SaveFromDraft/DropDraft, deleteTemplate)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0061_techcard_template_sheets"
down_revision: str | None = "0060_drop_calculation_section"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("techcard_operation_templates") as batch:
        batch.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("saved_by", sa.String(length=255), nullable=True))
    op.create_table(
        "techcard_operation_template_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=600), nullable=False, unique=True),
        sa.Column("existing_template_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("images", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("techcard_operation_template_drafts")
    with op.batch_alter_table("techcard_operation_templates") as batch:
        batch.drop_column("saved_by")
        batch.drop_column("saved_at")
        batch.drop_column("description")
