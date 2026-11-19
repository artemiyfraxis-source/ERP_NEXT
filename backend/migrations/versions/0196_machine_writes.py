"""ОБ.019 writes from ERP (plan «Станки», slice 4; owner 19.11.2026): queued machine commands and machine corrections.

Two new tables, nothing existing changes:
- `production_machine_writes` — parameter writes the command runner on the machine PC claims, executes and confirms
  by read-back; one open (queued/taken) command per machine (UNIQUE partial index).
- `production_machine_offsets` — «станок режет не так»: one stored offset per machine and wire parameter (0,01 мм),
  added to the ETK nominal on every later transfer. Not destructive."""

from alembic import op
import sqlalchemy as sa

revision = "0196_machine_writes"
down_revision = "0195_vna_check_journal"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

OPEN = sa.text("status IN ('queued', 'taken')")
OFFSET_KEYS = "'length_mm', 'strip_front_mm', 'pull_front_mm', 'strip_back_mm', 'pull_back_mm', 'diameter_mm', " \
              "'knife_back_mm'"


def upgrade():
    op.create_table(
        "production_machine_writes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("production_machine_links.equipment_id", ondelete="CASCADE",
                                                           name="fk_production_machine_writes_link"),
                  nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_by_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("readback", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(500), nullable=False, server_default=""),
        sa.CheckConstraint("status IN ('queued', 'taken', 'done', 'failed', 'expired')",
                           name="ck_production_machine_writes_status"),
        sa.CheckConstraint("purpose IN ('settings', 'transfer')", name="ck_production_machine_writes_purpose"),
        sa.CheckConstraint("status <> 'queued' OR (taken_at IS NULL AND finished_at IS NULL)",
                           name="ck_production_machine_writes_queued"),
        sa.CheckConstraint("status <> 'taken' OR (taken_at IS NOT NULL AND finished_at IS NULL)",
                           name="ck_production_machine_writes_taken"),
        sa.CheckConstraint("status NOT IN ('done', 'failed', 'expired') OR finished_at IS NOT NULL",
                           name="ck_production_machine_writes_finished"),
        sa.CheckConstraint("trim(summary) <> ''", name="ck_production_machine_writes_summary"),
    )
    op.create_index("ix_production_machine_writes_equipment_id", "production_machine_writes", ["equipment_id"])
    op.create_index("ux_production_machine_writes_open", "production_machine_writes", ["equipment_id"],
                    unique=True, sqlite_where=OPEN, postgresql_where=OPEN)
    op.create_table(
        "production_machine_offsets",
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_production_machine_offsets_equipment"),
                  primary_key=True),
        sa.Column("parameter", sa.String(32), primary_key=True),
        sa.Column("offset_hundredths", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("updated_by_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(f"parameter IN ({OFFSET_KEYS})", name="ck_production_machine_offsets_parameter"),
        sa.CheckConstraint("offset_hundredths BETWEEN -10000 AND 10000", name="ck_production_machine_offsets_range"),
        sa.CheckConstraint("version >= 1", name="ck_production_machine_offsets_version"),
    )


def downgrade():
    op.drop_table("production_machine_offsets")
    op.drop_index("ux_production_machine_writes_open", table_name="production_machine_writes")
    op.drop_index("ix_production_machine_writes_equipment_id", table_name="production_machine_writes")
    op.drop_table("production_machine_writes")
