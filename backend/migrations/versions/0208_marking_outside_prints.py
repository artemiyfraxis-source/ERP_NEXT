"""«Отметить напечатанные вне ERP» (owner 20.11.2026): numbered labels printed outside the ERP count as printed.

708К.: label №1 of every КБ was printed outside the ERP, so «Печатать оставшиеся» would print it again. One new table,
nothing existing changes:
- `marking_outside_prints` — one numbered label of a production task (FK RESTRICT: a task of a cutting group is never
  deleted), its item serial >= 1, the expanded text, how many pieces (>= 1), who marked it, when and why.
  UNIQUE (task, serial, text): marking the same label again changes nothing.
Not destructive; the downgrade drops the marks (those labels then read as not printed again)."""

from alembic import op
import sqlalchemy as sa

revision = "0208_marking_outside_prints"
down_revision = "0207_vna_screens"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "marking_outside_prints"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("production_tasks.id", ondelete="RESTRICT",
                                                      name="fk_marking_outside_prints_task"), nullable=False),
        sa.Column("serial", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(1000), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_name", sa.String(255), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comment", sa.String(1000), nullable=False, server_default=""),
        sa.UniqueConstraint("task_id", "serial", "text", name="uq_marking_outside_prints_label"),
        sa.CheckConstraint("serial >= 1", name="ck_marking_outside_prints_serial"),
        sa.CheckConstraint("quantity >= 1", name="ck_marking_outside_prints_quantity"),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
