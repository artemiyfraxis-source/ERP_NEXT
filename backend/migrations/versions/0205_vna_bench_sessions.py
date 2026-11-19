"""VNA bench sessions (purpose='bench'): one ACM calibration, many items, protocol v2 (СТО ФРАК-002-2026 разд. 11–14).

- production_vna_sessions: `purpose` ('assembly' — the existing one-position ОТК session, 'bench' — the workplace);
  task fields become NULL for a bench session under a CHECK; the calibration of a bench session (`calibrated_at`,
  `calibration_temps`, `profile_hash`, `calibration_check_id` → its journal row) — all or nothing; states `ready`
  and `releasing` exist only for bench sessions.
- production_vna_commands: the op CHECK admits the protocol v2 ops.
- production_vna_bench_items: one accepted measurement (capture) of a bench session — what was connected (kind,
  target, standard), its connection number 1..3 within a judged series (group), its verdict and where it was recorded
  (the СИ card file on «Записать в карточку», or the ОТК VSWR attempt of a КБ position).
- warehouse_vna_control.ops: the bench ops an agent advertises (protocol v2); the server routes only to them.

Existing rows: sessions become purpose='assembly' and keep satisfying every constraint; agents' ops start empty.
Not destructive; downgrade refuses once a bench session or an item exists.
"""

from alembic import op
import sqlalchemy as sa

revision = "0205_vna_bench_sessions"
down_revision = "0203_marking_printer_control"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

SESSIONS, COMMANDS, ITEMS = "production_vna_sessions", "production_vna_commands", "production_vna_bench_items"
ACTIVE = "state NOT IN ('recorded','failed','aborted')"
OLD_STATES = ("'draft','planned','preparing','awaiting_standard','acquiring_standard','saving_calibration','awaiting_dut',"
              "'acquiring_dut','review','recorded','failed','aborting','aborted','recovery_required'")
NEW_STATES = OLD_STATES + ",'ready','releasing'"
OLD_OPS = "'prepare','apply_profile','begin_calibration','measure_standard','acm_calibrate','save_calibration','capture','abort','restore'"
NEW_OPS = OLD_OPS + ",'device_status','apply_screen','acm_confidence','store_memory','release','read_kit'"

# Frozen copies of vna_session_infrastructure.SESSION_CHECKS / ITEM_CHECKS as of this revision.
SESSION_CHECKS = {
    "ck_vna_sessions_purpose": "purpose IN ('assembly','bench')",
    "ck_vna_sessions_target": "(purpose = 'assembly' AND task_id IS NOT NULL AND operation_id IS NOT NULL AND "
                              "product_position IS NOT NULL) OR (purpose = 'bench' AND task_id IS NULL AND "
                              "operation_id IS NULL AND product_position IS NULL)",
    "ck_vna_sessions_bench_state": "purpose = 'bench' OR state NOT IN ('ready','releasing')",
    "ck_vna_sessions_bench_only": "purpose = 'bench' OR (calibrated_at IS NULL AND calibration_temps IS NULL AND "
                                  "profile_hash IS NULL AND calibration_check_id IS NULL)",
    "ck_vna_sessions_calibrated": "(calibrated_at IS NULL) = (calibration_temps IS NULL) AND "
                                  "(calibrated_at IS NULL) = (profile_hash IS NULL) AND "
                                  "(calibrated_at IS NULL) = (calibration_check_id IS NULL)",
    "ck_vna_sessions_profile_hash": "profile_hash IS NULL OR length(profile_hash) = 64",
}
ITEM_CHECKS = {
    "ck_vna_bench_items_kind": "kind IN ('instrument','assembly','load','flex','control_standard','kit_standards')",
    "ck_vna_bench_items_target": "target IN ('dut','port1','port2','thru','thru_bent')",
    "ck_vna_bench_items_kind_target": "(kind IN ('instrument','assembly','control_standard') AND target = 'dut') OR "
                                      "(kind = 'load' AND target IN ('port1','port2')) OR "
                                      "(kind = 'flex' AND target IN ('port1','port2','thru','thru_bent')) OR "
                                      "(kind = 'kit_standards' AND target IN ('port1','port2','thru'))",
    "ck_vna_bench_items_standard": "(kind = 'kit_standards' AND standard IN ('OPEN','SHORT','LOAD','THRU') AND "
                                   "(standard = 'THRU') = (target = 'thru')) OR (kind <> 'kit_standards' AND standard IS NULL)",
    "ck_vna_bench_items_connection": "connection_no BETWEEN 1 AND 3",
    "ck_vna_bench_items_verdict": "verdict IS NULL OR verdict IN ('pass','border','fail','not_judged')",
    "ck_vna_bench_items_object": "(kind IN ('instrument','control_standard','kit_standards') AND instrument_id IS NOT NULL) OR "
                                 "(kind NOT IN ('instrument','control_standard','kit_standards') AND instrument_id IS NULL)",
    "ck_vna_bench_items_assembly": "(kind = 'assembly' AND task_id IS NOT NULL AND product_position >= 1) OR "
                                   "(kind <> 'assembly' AND task_id IS NULL AND product_position IS NULL)",
    "ck_vna_bench_items_repeat": "connection_no = 1 OR kind IN ('instrument','assembly','kit_standards')",
    "ck_vna_bench_items_recorded_file": "recorded_file_id IS NULL OR kind = 'instrument'",
    "ck_vna_bench_items_vswr": "vswr_measurement_id IS NULL OR kind = 'assembly'",
    "ck_vna_bench_items_recorded": "(recorded_at IS NULL) = (recorded_file_id IS NULL AND vswr_measurement_id IS NULL)",
}


def upgrade() -> None:
    for name in ("instrument", "serial"):
        op.drop_index(f"uq_vna_sessions_{name}_active", table_name=SESSIONS)
    with op.batch_alter_table(SESSIONS) as batch:
        batch.add_column(sa.Column("purpose", sa.String(16), nullable=False, server_default="assembly"))
        batch.alter_column("task_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("operation_id", existing_type=sa.Uuid(), nullable=True)
        batch.alter_column("product_position", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("calibrated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("calibration_temps", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("profile_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("calibration_check_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_vna_sessions_calibration_check", "production_vna_checks",
                                 ["calibration_check_id"], ["id"], ondelete="RESTRICT")
        batch.drop_constraint("ck_vna_sessions_state", type_="check")
        batch.create_check_constraint("ck_vna_sessions_state", f"state IN ({NEW_STATES})")
        for name, sql in SESSION_CHECKS.items():
            batch.create_check_constraint(name, sql)
    for field, name in (("instrument_id", "instrument"), ("instrument_serial", "serial")):
        op.create_index(f"uq_vna_sessions_{name}_active", SESSIONS, [field], unique=True,
                        sqlite_where=sa.text(ACTIVE), postgresql_where=sa.text(ACTIVE))
    op.drop_index("uq_vna_commands_active", table_name=COMMANDS)
    with op.batch_alter_table(COMMANDS) as batch:
        batch.drop_constraint("ck_vna_commands_op", type_="check")
        batch.create_check_constraint("ck_vna_commands_op", f"op IN ({NEW_OPS})")
    op.create_index("uq_vna_commands_active", COMMANDS, ["session_id"], unique=True,
                    sqlite_where=sa.text("status IN ('pending','claimed')"),
                    postgresql_where=sa.text("status IN ('pending','claimed')"))
    with op.batch_alter_table("warehouse_vna_control") as batch:
        batch.add_column(sa.Column("ops", sa.JSON(), nullable=False, server_default="[]"))
    op.create_table(
        ITEMS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey(f"{SESSIONS}.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("command_id", sa.Uuid(), sa.ForeignKey(f"{COMMANDS}.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), sa.ForeignKey("production_vna_artifacts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("connection_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target", sa.String(12), nullable=False),
        sa.Column("standard", sa.String(8), nullable=True),
        sa.Column("instrument_id", sa.Uuid(), sa.ForeignKey("production_instruments.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("production_tasks.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("product_position", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(12), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_file_id", sa.Uuid(), sa.ForeignKey("production_instrument_files.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("vswr_measurement_id", sa.Uuid(), sa.ForeignKey("production_vswr_measurements.id", ondelete="RESTRICT"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(sql, name=name) for name, sql in ITEM_CHECKS.items()),
        sa.UniqueConstraint("command_id", name="uq_vna_bench_items_command"),
        sa.UniqueConstraint("artifact_id", name="uq_vna_bench_items_artifact"),
        sa.UniqueConstraint("group_id", "connection_no", name="uq_vna_bench_items_connection"),
        sa.UniqueConstraint("recorded_file_id", name="uq_vna_bench_items_recorded_file"),
        sa.UniqueConstraint("vswr_measurement_id", name="uq_vna_bench_items_vswr"),
    )
    op.create_index("ix_vna_bench_items_session", ITEMS, ["session_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if (bind.execute(sa.text(f"SELECT 1 FROM {ITEMS} LIMIT 1")).first()
            or bind.execute(sa.text(f"SELECT 1 FROM {SESSIONS} WHERE purpose = 'bench' LIMIT 1")).first()
            or bind.execute(sa.text(f"SELECT 1 FROM {COMMANDS} WHERE op NOT IN ({OLD_OPS}) LIMIT 1")).first()):
        raise RuntimeError("Сессии рабочего места ВАЦ содержат историю измерений; откат удалит доказательства")
    op.drop_index("ix_vna_bench_items_session", table_name=ITEMS)
    op.drop_table(ITEMS)
    with op.batch_alter_table("warehouse_vna_control") as batch:
        batch.drop_column("ops")
    op.drop_index("uq_vna_commands_active", table_name=COMMANDS)
    with op.batch_alter_table(COMMANDS) as batch:
        batch.drop_constraint("ck_vna_commands_op", type_="check")
        batch.create_check_constraint("ck_vna_commands_op", f"op IN ({OLD_OPS})")
    op.create_index("uq_vna_commands_active", COMMANDS, ["session_id"], unique=True,
                    sqlite_where=sa.text("status IN ('pending','claimed')"),
                    postgresql_where=sa.text("status IN ('pending','claimed')"))
    for name in ("instrument", "serial"):
        op.drop_index(f"uq_vna_sessions_{name}_active", table_name=SESSIONS)
    with op.batch_alter_table(SESSIONS) as batch:
        for name in reversed(list(SESSION_CHECKS)):
            batch.drop_constraint(name, type_="check")
        batch.drop_constraint("ck_vna_sessions_state", type_="check")
        batch.create_check_constraint("ck_vna_sessions_state", f"state IN ({OLD_STATES})")
        batch.drop_constraint("fk_vna_sessions_calibration_check", type_="foreignkey")
        batch.drop_column("calibration_check_id")
        batch.drop_column("profile_hash")
        batch.drop_column("calibration_temps")
        batch.drop_column("calibrated_at")
        batch.alter_column("product_position", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("operation_id", existing_type=sa.Uuid(), nullable=False)
        batch.alter_column("task_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_column("purpose")
    for field, name in (("instrument_id", "instrument"), ("instrument_serial", "serial")):
        op.create_index(f"uq_vna_sessions_{name}_active", SESSIONS, [field], unique=True,
                        sqlite_where=sa.text(ACTIVE), postgresql_where=sa.text(ACTIVE))
