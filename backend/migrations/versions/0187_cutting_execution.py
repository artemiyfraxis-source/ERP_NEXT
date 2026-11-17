"""One work session and idempotent per-KB submissions for a cutting group."""

from alembic import op
import sqlalchemy as sa

revision = "0187_cutting_execution"
down_revision = "0186_cutting_groups"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade():
    op.create_table("production_cutting_work",
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("production_work_sessions.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("production_cutting_groups.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False),
        sa.Column("accounted_seconds", sa.Integer(), nullable=False),
        sa.Column("downtime_id", sa.Uuid(), sa.ForeignKey("production_downtimes.id", ondelete="RESTRICT")),
        sa.CheckConstraint("accounted_seconds >= 0", name="ck_cutting_work_seconds"))
    op.create_index("ux_cutting_work_open_group", "production_cutting_work", ["group_id"], unique=True,
                    sqlite_where=sa.text("closed = 0"), postgresql_where=sa.text("closed = false"))
    op.create_table("production_cutting_actions",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("production_cutting_groups.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False))
    op.create_table("production_cutting_outputs",
        sa.Column("output_id", sa.Uuid(), sa.ForeignKey("production_outputs.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("production_cutting_work.session_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("member_id", sa.Uuid(), sa.ForeignKey("production_cutting_members.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action_key", sa.String(255), sa.ForeignKey("production_cutting_actions.key", ondelete="RESTRICT"), nullable=False),
        sa.UniqueConstraint("action_key", "member_id", name="ux_cutting_output_action_member"))


def downgrade():
    bind = op.get_bind()
    if any(bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() for table in (
            "production_cutting_work", "production_cutting_actions", "production_cutting_outputs")):
        raise RuntimeError("Cannot remove cutting execution history")
    op.drop_table("production_cutting_outputs")
    op.drop_table("production_cutting_actions")
    op.drop_index("ux_cutting_work_open_group", table_name="production_cutting_work")
    op.drop_table("production_cutting_work")
