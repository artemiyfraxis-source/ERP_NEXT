"""Документы с ознакомлением — «Личная карточка», фаза 3 (identity/documents.py).

New tables only, empty; nothing existing changes its rows (the production database is live):
- `identity_documents` — a document «Кадры» upload: title, kind (job_description · regulation · other), the scope it
  is aimed at (all · department · job_title) and its value ('' exactly when the scope is everyone), archived flag;
- `identity_document_versions` — its versions (редакции): the PDF itself (≤ 20 МБ), its SHA-256, who uploaded it and
  when; one row per document and version number;
- `identity_document_acks` — «Ознакомлен»: who signed which version, when, how (PIN or password typed again; the
  secret is never stored) and the SHA-256 of the PDF signed. One signature per person and version; a signed version
  cannot be deleted (RESTRICT).

Downgrade drops the three tables with their rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0100_identity_documents"
down_revision: str | None = "0099_worker_heartbeats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _hex_sha256(column: str) -> str:
    # trim(x, chars) is btrim on PostgreSQL and the two-argument trim on SQLite: '' only when every char is hex.
    return f"length({column}) = 64 AND trim({column}, '0123456789abcdef') = ''"


def upgrade() -> None:
    op.create_table(
        "identity_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_value", sa.String(255), nullable=False, server_default=""),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["identity_users.id"], name="fk_identity_documents_created_by", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("kind IN ('job_description', 'regulation', 'other')", name="ck_identity_documents_kind"),
        sa.CheckConstraint("scope IN ('all', 'department', 'job_title')", name="ck_identity_documents_scope"),
        sa.CheckConstraint(
            "(scope = 'all' AND scope_value = '') OR (scope <> 'all' AND scope_value <> '')",
            name="ck_identity_documents_scope_value",
        ),
        sa.CheckConstraint("title <> ''", name="ck_identity_documents_title"),
    )

    op.create_table(
        "identity_document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["identity_documents.id"], name="fk_identity_document_versions_document_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["identity_users.id"], name="fk_identity_document_versions_uploaded_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_identity_document_versions_version_no"),
        sa.CheckConstraint("size > 0 AND size <= 20971520", name="ck_identity_document_versions_size"),
        sa.CheckConstraint(_hex_sha256("sha256"), name="ck_identity_document_versions_sha256"),
        sa.UniqueConstraint("document_id", "version_no", name="uq_identity_document_versions_document_version"),
    )

    op.create_table(
        "identity_document_acks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"], ["identity_document_versions.id"], name="fk_identity_document_acks_version_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["identity_users.id"], name="fk_identity_document_acks_user_id", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("method IN ('pin', 'password')", name="ck_identity_document_acks_method"),
        sa.CheckConstraint(_hex_sha256("sha256"), name="ck_identity_document_acks_sha256"),
        sa.UniqueConstraint("version_id", "user_id", name="uq_identity_document_acks_version_user"),
    )
    op.create_index("ix_identity_document_acks_user_id", "identity_document_acks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_identity_document_acks_user_id", table_name="identity_document_acks")
    op.drop_table("identity_document_acks")
    op.drop_table("identity_document_versions")
    op.drop_table("identity_documents")
