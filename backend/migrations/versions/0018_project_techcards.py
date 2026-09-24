"""Add persistent project cards and electronic-techcard revisions."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0018_project_techcards"
down_revision: str | None = "0017_production_qc_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO identity_role_access (role, section, mode)
            VALUES
              ('engineer', 'techcards', 'edit'),
              ('master', 'techcards', 'view'),
              ('prodhead', 'techcards', 'view'),
              ('management', 'techcards', 'view'),
              ('admin', 'techcards', 'edit')
            ON CONFLICT (role, section) DO UPDATE SET mode = EXCLUDED.mode
            """
        )
    )
    op.create_table(
        "techcard_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item_index", sa.String(255), nullable=False),
        sa.Column("product_name", sa.String(512), nullable=False),
        sa.Column("source_calculation", sa.String(512), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=True),
        sa.Column("manual_operation_keys", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_techcard_projects_project_item",
        "techcard_projects",
        ["project", "item_index"],
        unique=True,
    )
    op.create_index(
        "ix_techcard_projects_status_updated",
        "techcard_projects",
        ["status", "updated_at"],
    )
    op.create_table(
        "techcard_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_card_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_name", sa.String(255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_card_id"], ["techcard_projects.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_techcard_revisions_card_revision",
        "techcard_revisions",
        ["project_card_id", "revision"],
        unique=True,
    )
    op.create_index(
        "ix_techcard_revisions_card_time",
        "techcard_revisions",
        ["project_card_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_techcard_revisions_card_time", table_name="techcard_revisions")
    op.drop_index("ux_techcard_revisions_card_revision", table_name="techcard_revisions")
    op.drop_table("techcard_revisions")
    op.drop_index("ix_techcard_projects_status_updated", table_name="techcard_projects")
    op.drop_index("ux_techcard_projects_project_item", table_name="techcard_projects")
    op.drop_table("techcard_projects")
    op.execute(sa.text("DELETE FROM identity_role_access WHERE section = 'techcards'"))
