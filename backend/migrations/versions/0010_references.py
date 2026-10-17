"""Create editable reference maps and the technology catalog snapshot."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010_references"
down_revision: str | None = "0009_shipping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    roles = (
        ("admin", "edit"),
        ("buyer", "view"),
        ("receiver", "view"),
        ("engineer", "view"),
        ("prodhead", "view"),
        ("master", "view"),
        ("management", "view"),
        ("pm", "view"),
        ("staff", "view"),
        ("estimator", "view"),
        ("qc", "view"),
    )
    for role, mode in roles:
        op.execute(sa.text("""
            INSERT INTO identity_role_access (role, section, mode)
            VALUES (:role, 'references', :mode)
            ON CONFLICT (role, section) DO NOTHING
        """).bindparams(role=role, mode=mode))

    op.create_table(
        "reference_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("key", sa.String(2048), nullable=False),
        sa.Column("normalized_key", sa.String(512), nullable=False),
        sa.Column("value", sa.String(512), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reference_entries_kind", "reference_entries", ["kind"])
    op.create_index(
        "ux_reference_entries_kind_key",
        "reference_entries",
        ["kind", "normalized_key"],
        unique=True,
    )
    op.create_table(
        "tech_operation_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tab", sa.String(16), nullable=False),
        sa.Column("display_text", sa.String(2000), nullable=False),
        sa.Column("normalized_search", sa.Text(), nullable=False),
        sa.Column("source_sheet", sa.String(255), nullable=False),
        sa.Column("sort_key", sa.String(2000), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tech_operation_records_tab", "tech_operation_records", ["tab"])
    op.create_index(
        "ix_tech_operation_records_tab_sort",
        "tech_operation_records",
        ["tab", "sort_key"],
    )
    op.create_table(
        "tech_operation_snapshots",
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("tech_operation_snapshots")
    op.drop_index("ix_tech_operation_records_tab_sort", table_name="tech_operation_records")
    op.drop_index("ix_tech_operation_records_tab", table_name="tech_operation_records")
    op.drop_table("tech_operation_records")
    op.drop_index("ux_reference_entries_kind_key", table_name="reference_entries")
    op.drop_index("ix_reference_entries_kind", table_name="reference_entries")
    op.drop_table("reference_entries")
    op.execute(sa.text("DELETE FROM identity_role_access WHERE section = 'references'"))
