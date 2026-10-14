"""Внутренние проекты (стенд Вити, 06.10.2026): отдельная ветвь проектов рядом с коммерческими.

Branch vitya/hybrid only. A request, a procurement line and a lot refer to an internal project by its number
«ВП-001» in the same `project_reference` field a commercial project uses («591К.3»); the purpose of the internal
project decides the GRN prefix. Commercial projects stay where they are (projects / plan registry).

Downgrade drops the table with its rows.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0122_internal_projects"
down_revision: str | None = "0121_purchasing_lot_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchasing_internal_projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("responsible_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("number", name="uq_purchasing_internal_projects_number"),
        sa.CheckConstraint("number LIKE 'ВП-%'", name="ck_purchasing_internal_projects_number"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_purchasing_internal_projects_name"),
    )


def downgrade() -> None:
    op.drop_table("purchasing_internal_projects")
