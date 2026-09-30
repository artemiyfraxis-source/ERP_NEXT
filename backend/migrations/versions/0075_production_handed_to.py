"""Задание, отданное площадке целиком (владелец, 30.09: ЖУК работает не нормированно — «отдать все в жук»)."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0075_production_handed_to"
down_revision: str | None = "0074_shift_effective_seven_hours"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("production_tasks") as batch:
        batch.add_column(sa.Column("handed_to", sa.String(32), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("production_tasks") as batch:
        batch.drop_column("handed_to")
