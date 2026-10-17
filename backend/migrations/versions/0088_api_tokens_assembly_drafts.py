"""Токены API и черновики схем сборки (владелец, 05.10.2026).

Schema only, two new empty tables (the production database is live, nothing existing changes):
- `identity_api_tokens` — named service tokens for Claude Code and a future server agent: only the sha256 of the
  secret, the scopes (JSON list: `tcgen`, `assembly`), the expiry, last use, who issued / revoked it and when.
- `techcard_assembly_drafts` — «чертёж → схема сборки»: a draft config per project + КБ, optionally tied to its КД
  drawing, then the final config, the techcard revision it went into and the lesson learnt. No API yet.

Downgrade drops both tables with their rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0088_api_tokens_assembly_drafts"
down_revision: str | None = "0087_fulfillment_off_book_kit_reserves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_api_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["identity_users.id"], name="fk_identity_api_tokens_created_by", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["identity_users.id"], name="fk_identity_api_tokens_revoked_by", ondelete="SET NULL",
        ),
    )
    op.create_index("ux_identity_api_tokens_token_hash", "identity_api_tokens", ["token_hash"], unique=True)

    op.create_table(
        "techcard_assembly_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item_index", sa.String(64), nullable=False),
        sa.Column("drawing_document_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("draft_config", sa.JSON(), nullable=False),
        sa.Column("final_config", sa.JSON(), nullable=True),
        sa.Column("final_revision", sa.Integer(), nullable=True),
        sa.Column("lesson", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["drawing_document_id"],
            ["techcard_kitset_documents.id"],
            name="fk_techcard_assembly_drafts_drawing_document_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("status IN ('draft', 'final')", name="ck_techcard_assembly_drafts_status"),
    )
    op.create_index(
        "ix_techcard_assembly_drafts_project_item", "techcard_assembly_drafts", ["project", "item_index"],
    )


def downgrade() -> None:
    op.drop_index("ix_techcard_assembly_drafts_project_item", table_name="techcard_assembly_drafts")
    op.drop_table("techcard_assembly_drafts")
    op.drop_index("ux_identity_api_tokens_token_hash", table_name="identity_api_tokens")
    op.drop_table("identity_api_tokens")
