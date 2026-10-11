"""Черновик генератора ТК на сервере (владелец 11.10.2026: набранное в генераторе не должно теряться на другом
компьютере или в другом браузере; techcards/generator_drafts.py).

New table only, empty; nothing existing changes its rows (the production database is live):
- `techcard_generator_drafts` — the unfinished generator form of one person for one project position (КБ): the form
  state as JSON (≤ 1 MiB of text), the fingerprint of the card's build config it was typed over, a version for the
  optimistic check (≥ 1, +1 per save), created/updated time. One draft per person, project and КБ; a person with a
  draft cannot be deleted (RESTRICT) — the drafts go first.

Downgrade drops the table with its rows (unfinished forms only; the browser keeps its own copy).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0102_techcard_generator_drafts"
down_revision: str | None = "0101_production_stage_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "techcard_generator_drafts"
# Literal copy of app.modules.techcards.generator_drafts_infrastructure.MAX_PAYLOAD_CHARS (a migration never imports
# app code that moves on).
MAX_PAYLOAD_CHARS = 1_048_576


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item_index", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("build_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity_users.id"], name="fk_techcard_generator_drafts_user_id", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version >= 1", name="ck_techcard_generator_drafts_version"),
        sa.CheckConstraint("project <> ''", name="ck_techcard_generator_drafts_project"),
        sa.CheckConstraint("item_index <> ''", name="ck_techcard_generator_drafts_item_index"),
        sa.CheckConstraint(
            f"length(CAST(payload AS TEXT)) <= {MAX_PAYLOAD_CHARS}", name="ck_techcard_generator_drafts_payload_size",
        ),
        sa.UniqueConstraint("user_id", "project", "item_index", name="uq_techcard_generator_drafts_user_position"),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
