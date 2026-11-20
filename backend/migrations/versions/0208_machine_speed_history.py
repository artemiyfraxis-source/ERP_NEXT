"""ОБ.019 speed history (owner 20.11.2026: «история скорости станков», plan machine-speed-history S1).

- production_machine_speed_rows: one row each time an ОБ.019 report changes the counter T.Finish (D649/650), the batch
  (D645/646) or order (D643/644) counter, the state D766, the open run or the setup key; `start` on the first report
  and after the bridge restarts. Written by production/machine_history.record from the bridge's report; append-only.
- Every value has its range (CHECK): 32-bit counters and UPH, 16-bit state and alarm; a run has its kind and write
  (both or neither); the reason is one of five; `(equipment_id, boot, seq)` is UNIQUE so a re-sent snapshot is written
  once. `id` is a BIGINT identity on PostgreSQL, the rowid on SQLite.
- Starts empty: the snapshot table keeps only the latest reading, there is nothing to backfill.

Not destructive; the downgrade drops the table (the history since this revision is lost with it).
"""

from alembic import op
import sqlalchemy as sa

revision = "0208_machine_speed_history"
down_revision = "0207_vna_screens"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_machine_speed_rows"
U32_MAX = 4294967295
# Frozen copy of machine_history.MachineSpeedRow checks as of this revision: never import app code here.
CHECKS = {
    "ck_production_machine_speed_rows_boot": "trim(boot) <> ''",
    "ck_production_machine_speed_rows_seq": "seq >= 0",
    **{f"ck_production_machine_speed_rows_{name}": f"{name} BETWEEN 0 AND {U32_MAX}"
       for name in ("counter_total", "counter_batch", "counter_order", "uph")},
    "ck_production_machine_speed_rows_state": "state_code BETWEEN 0 AND 65535",
    "ck_production_machine_speed_rows_alarm": "alarm_code IS NULL OR alarm_code BETWEEN 0 AND 65535",
    "ck_production_machine_speed_rows_run_kind": "run_kind IS NULL OR run_kind IN ('cutting', 'manual')",
    "ck_production_machine_speed_rows_run": "(run_kind IS NULL) = (run_write_id IS NULL)",
    "ck_production_machine_speed_rows_setup": "trim(setup_key) <> ''",
    "ck_production_machine_speed_rows_reason": "reason IN ('start', 'counter', 'state', 'run', 'setup')",
}
INDEX = "ix_production_machine_speed_rows_time"
LATEST_INDEX = "ix_production_machine_speed_rows_equipment_id_id"  # the newest row of a machine


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(), nullable=False),
        sa.Column("equipment_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("boot", sa.String(64), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("counter_total", sa.BigInteger(), nullable=False),
        sa.Column("counter_batch", sa.BigInteger(), nullable=False),
        sa.Column("counter_order", sa.BigInteger(), nullable=False),
        sa.Column("uph", sa.BigInteger(), nullable=False),
        sa.Column("state_code", sa.Integer(), nullable=False),
        sa.Column("alarm_code", sa.Integer(), nullable=True),
        sa.Column("run_write_id", sa.Uuid(), nullable=True),
        sa.Column("run_kind", sa.String(16), nullable=True),
        sa.Column("wire_key", sa.String(200), nullable=False, server_default=""),
        sa.Column("setup_key", sa.String(300), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_production_machine_speed_rows"),
        sa.ForeignKeyConstraint(["equipment_id"], ["equipment_units.id"], ondelete="RESTRICT",
                                name="fk_production_machine_speed_rows_equipment"),
        sa.ForeignKeyConstraint(["run_write_id"], ["production_machine_writes.id"], ondelete="RESTRICT",
                                name="fk_production_machine_speed_rows_run"),
        sa.UniqueConstraint("equipment_id", "boot", "seq", name="uq_production_machine_speed_rows_seq"),
        *(sa.CheckConstraint(sql, name=name) for name, sql in CHECKS.items()),
    )
    op.create_index(INDEX, TABLE, ["equipment_id", "recorded_at"])
    op.create_index(LATEST_INDEX, TABLE, ["equipment_id", "id"])


def downgrade() -> None:
    op.drop_index(LATEST_INDEX, table_name=TABLE)
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
