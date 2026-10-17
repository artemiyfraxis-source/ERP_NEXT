"""Create native Identity users, permissions, sessions, and login throttles."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_identity"
down_revision: str | None = "0002_procurement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("login", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("department", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("site", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("login"),
    )
    op.create_index("ix_identity_users_role", "identity_users", ["role"])
    op.create_table(
        "identity_identifiers",
        sa.Column("identifier", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["identity_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("identifier"),
    )
    op.create_index(
        "ix_identity_identifiers_user_id", "identity_identifiers", ["user_id"]
    )
    op.create_table(
        "identity_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["identity_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "identity_role_access",
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.PrimaryKeyConstraint("role", "section"),
    )
    op.create_table(
        "identity_user_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["identity_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "section"),
    )
    op.create_table(
        "identity_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["identity_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_identity_sessions_expires_at", "identity_sessions", ["expires_at"])
    op.create_index(
        "ix_identity_sessions_revoked_at", "identity_sessions", ["revoked_at"]
    )
    op.create_index("ix_identity_sessions_user_id", "identity_sessions", ["user_id"])
    op.create_table(
        "identity_login_throttles",
        sa.Column("key", sa.String(length=512), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("rate_limit_audited_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(
        "ix_identity_login_throttles_window_started_at",
        "identity_login_throttles",
        ["window_started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_login_throttles_window_started_at",
        table_name="identity_login_throttles",
    )
    op.drop_table("identity_login_throttles")
    op.drop_index("ix_identity_sessions_user_id", table_name="identity_sessions")
    op.drop_index("ix_identity_sessions_expires_at", table_name="identity_sessions")
    op.drop_index("ix_identity_sessions_revoked_at", table_name="identity_sessions")
    op.drop_table("identity_sessions")
    op.drop_table("identity_user_access")
    op.drop_table("identity_role_access")
    op.drop_table("identity_credentials")
    op.drop_index("ix_identity_identifiers_user_id", table_name="identity_identifiers")
    op.drop_table("identity_identifiers")
    op.drop_index("ix_identity_users_role", table_name="identity_users")
    op.drop_table("identity_users")
