"""ОБ.099 — BZW crimp press, read only (owner 18.11.2026): its agent reports like the ОБ.019 bridge and adds per-crimp records.

- `production_machine_links.kind` also allows `ob099_fins` (the CHECK is replaced; existing rows are `ob019_modbus`);
- `production_crimp_records` — one crimp from the press log (append-only, UNIQUE per press and source line);
- `production_machine_alarm_records` — one line of the machine's own alarm journal (append-only, UNIQUE per source).
Nothing existing is rewritten. Not destructive."""

from alembic import op
import sqlalchemy as sa

revision = "0197_ob099_crimp_records"
down_revision = "0196_machine_writes"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade() -> None:
    with op.batch_alter_table("production_machine_links") as batch:
        batch.drop_constraint("ck_production_machine_links_kind", type_="check")
        batch.create_check_constraint("ck_production_machine_links_kind", "kind IN ('ob019_modbus', 'ob099_fins')")
    op.create_table(
        "production_crimp_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_production_crimp_records_equipment"),
                  nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("crimped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(2), nullable=False),
        sa.Column("peak", sa.Float(), nullable=True),
        sa.Column("equipment_no", sa.String(64), nullable=False, server_default=""),
        sa.Column("product", sa.String(64), nullable=False, server_default=""),
        sa.Column("wire", sa.String(64), nullable=False, server_default=""),
        sa.Column("terminal", sa.String(64), nullable=False, server_default=""),
        sa.Column("applicator", sa.String(64), nullable=False, server_default=""),
        sa.Column("scan_code", sa.String(128), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("equipment_id", "source_key", name="uq_production_crimp_records_source"),
        sa.CheckConstraint("trim(source_key) <> ''", name="ck_production_crimp_records_key"),
        sa.CheckConstraint("result IN ('OK', 'NG')", name="ck_production_crimp_records_result"),
        sa.CheckConstraint("peak IS NULL OR peak >= 0", name="ck_production_crimp_records_peak"),
    )
    op.create_index("ix_production_crimp_records_time", "production_crimp_records", ["equipment_id", "crimped_at"])
    op.create_table(
        "production_machine_alarm_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_production_machine_alarm_records_equipment"),
                  nullable=False),
        sa.Column("source_key", sa.String(400), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.String(300), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("equipment_id", "source_key", name="uq_production_machine_alarm_records_source"),
        sa.CheckConstraint("trim(source_key) <> ''", name="ck_production_machine_alarm_records_key"),
        sa.CheckConstraint("trim(text) <> ''", name="ck_production_machine_alarm_records_text"),
    )
    op.create_index("ix_production_machine_alarm_records_time", "production_machine_alarm_records",
                    ["equipment_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_production_machine_alarm_records_time", table_name="production_machine_alarm_records")
    op.drop_table("production_machine_alarm_records")
    op.drop_index("ix_production_crimp_records_time", table_name="production_crimp_records")
    op.drop_table("production_crimp_records")
    # A press link cannot stay under the old CHECK: its agent binds again after a re-upgrade.
    op.execute("DELETE FROM production_machine_snapshots WHERE equipment_id IN "
               "(SELECT equipment_id FROM production_machine_links WHERE kind = 'ob099_fins')")
    op.execute("DELETE FROM production_machine_links WHERE kind = 'ob099_fins'")
    with op.batch_alter_table("production_machine_links") as batch:
        batch.drop_constraint("ck_production_machine_links_kind", type_="check")
        batch.create_check_constraint("ck_production_machine_links_kind", "kind IN ('ob019_modbus')")
