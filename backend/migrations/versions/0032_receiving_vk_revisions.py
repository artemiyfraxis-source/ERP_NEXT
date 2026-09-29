"""«Провести новый ВК»: a receiving line can be archived («Архив ВК») and continued by a `-ВN` revision."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0032_receiving_vk_revisions"
down_revision: str | None = "0031_receiving_inspected_on"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0004 created the procurement link as an unnamed UNIQUE: PostgreSQL named it by its default, SQLite
# reflects it without a name, so the batch naming convention gives it one to drop by.
_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
_LIVE = sa.text("status <> 'Архив ВК'")


def _procurement_unique_name() -> str:
    if op.get_bind().dialect.name == "postgresql":
        return "receiving_lines_procurement_line_id_key"
    return "uq_receiving_lines_procurement_line_id"


def upgrade() -> None:
    with op.batch_alter_table("receiving_lines", naming_convention=_CONVENTION) as batch:
        batch.drop_constraint(_procurement_unique_name(), type_="unique")
        batch.add_column(sa.Column("revision_of_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("revision_root_id", sa.Uuid(), nullable=True))
        batch.create_unique_constraint("uq_receiving_lines_revision_of_id", ["revision_of_id"])
        batch.create_foreign_key(
            "fk_receiving_lines_revision_of_id", "receiving_lines",
            ["revision_of_id"], ["id"], ondelete="RESTRICT",
        )
    op.create_index("ix_receiving_lines_revision_root_id", "receiving_lines", ["revision_root_id"])
    op.create_index(
        "ux_receiving_live_procurement_line", "receiving_lines", ["procurement_line_id"],
        unique=True, sqlite_where=_LIVE, postgresql_where=_LIVE,
    )


def downgrade() -> None:
    # Fails while revisions exist: two lines then share one procurement line.
    op.drop_index("ux_receiving_live_procurement_line", table_name="receiving_lines")
    op.drop_index("ix_receiving_lines_revision_root_id", table_name="receiving_lines")
    with op.batch_alter_table("receiving_lines") as batch:
        batch.drop_constraint("fk_receiving_lines_revision_of_id", type_="foreignkey")
        batch.drop_constraint("uq_receiving_lines_revision_of_id", type_="unique")
        batch.drop_column("revision_root_id")
        batch.drop_column("revision_of_id")
        batch.create_unique_constraint(_procurement_unique_name(), ["procurement_line_id"])
