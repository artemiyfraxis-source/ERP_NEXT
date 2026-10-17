"""Create portal notifications and operational access."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011_operations"
down_revision: str | None = "0010_references"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for role, mode in (("admin", "edit"), ("management", "view")):
        op.execute(sa.text("""
            INSERT INTO identity_role_access (role, section, mode)
            VALUES (:role, 'operations', :mode)
            ON CONFLICT (role, section) DO NOTHING
        """).bindparams(role=role, mode=mode))
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("reference", sa.String(255), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_notifications_recipient_email", "notifications", ["recipient_email"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_recipient_email", table_name="notifications")
    op.drop_table("notifications")
    op.execute(sa.text("DELETE FROM identity_role_access WHERE section = 'operations'"))
