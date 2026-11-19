"""Marking printer control from ERP (owner 19.11.2026, ОБ.101 Godex EZ2350i incidents).

1. A marking job gets the final status `cancelled`: «Отменить оставшиеся» moves only `pending` jobs of a batch there
   (never handed to an agent: no taken_at, attempt or claim token), with the reason as `error`.
2. `warehouse_printer_commands`: «Очистить принтер» — one command per printer queue, pending → taken → done/failed,
   with at most one open command per queue (partial UNIQUE). While it is open the queue hands out no marking jobs.
3. `warehouse_agent_printers.hold`: why the agent holds that marking queue (peel-off check failed), from the heartbeat.
4. ОБ.101 profiles get `geometry.peel = true` and a new `version` (the flag machine always works with the peel-off dispenser): the print
   agent then keeps the driver's peel-off DevMode and refuses a job whose driver output lacks `^O1`.
Not destructive. 0202 is taken by the manual-jobs PR (#964); if it lands first, this one is re-parented onto it.
"""

import json

from alembic import op
import sqlalchemy as sa

revision = "0203_marking_printer_control"
down_revision = "0201_instrument_reference_curve"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

# Frozen copies of label_jobs.LabelJobRow checks as of this revision.
CHECKS = {
    "ck_warehouse_label_jobs_status": "status IN ('pending', 'taken', 'printed', 'failed', 'spooled', 'unknown', 'cancelled')",
    "ck_warehouse_label_jobs_taken_at": "status IN ('pending', 'cancelled') OR taken_at IS NOT NULL",
    "ck_warehouse_label_jobs_finished_only_final": "finished_at IS NULL OR status IN ('printed', 'failed', 'spooled', 'unknown', 'cancelled')",
    "ck_warehouse_label_jobs_error_only_failed": "error IS NULL OR status IN ('failed', 'unknown', 'cancelled')",
    "ck_label_job_attempt": "format <> 'gdi_png' OR status IN ('pending', 'cancelled') OR (attempt IS NOT NULL AND claim_token_id IS NOT NULL)",
}
OLD_CHECKS = {
    "ck_warehouse_label_jobs_status": "status IN ('pending', 'taken', 'printed', 'failed', 'spooled', 'unknown')",
    "ck_warehouse_label_jobs_taken_at": "status = 'pending' OR taken_at IS NOT NULL",
    "ck_warehouse_label_jobs_finished_only_final": "finished_at IS NULL OR status IN ('printed', 'failed', 'spooled', 'unknown')",
    "ck_warehouse_label_jobs_error_only_failed": "error IS NULL OR status IN ('failed', 'unknown')",
    "ck_label_job_attempt": "format <> 'gdi_png' OR status = 'pending' OR (attempt IS NOT NULL AND claim_token_id IS NOT NULL)",
}
CANCELLED = ("status <> 'cancelled' OR (format = 'gdi_png' AND taken_at IS NULL AND attempt IS NULL "
             "AND claim_token_id IS NULL AND finished_at IS NOT NULL AND error IS NOT NULL AND trim(error) <> '')")
COMMAND_CHECKS = {
    # The CI seeder takes the first value of an IN list: a finished 'done' command needs no open-state columns empty.
    "ck_printer_command_kind": "kind IN ('clear')",
    "ck_printer_command_status": "status IN ('done', 'pending', 'taken', 'failed')",
    "ck_printer_command_reason": "trim(reason) <> ''",
    "ck_printer_command_pending": "status <> 'pending' OR (taken_at IS NULL AND attempt IS NULL AND lease_until IS NULL AND finished_at IS NULL)",
    "ck_printer_command_taken": "status <> 'taken' OR (taken_at IS NOT NULL AND attempt IS NOT NULL AND claim_token_id IS NOT NULL "
                                "AND lease_until IS NOT NULL AND finished_at IS NULL)",
    "ck_printer_command_final": "status IN ('pending', 'taken') OR finished_at IS NOT NULL",
    "ck_printer_command_failed": "status <> 'failed' OR (message IS NOT NULL AND trim(message) <> '')",
}
OPEN = "status IN ('pending', 'taken')"
PEEL_INVENTORY = "ОБ.101"


def _set_peel(value):
    """Upgrade: peel = true on ОБ.101 profiles. Downgrade: the key leaves every profile (older code forbids it)."""
    bind = op.get_bind()
    profiles = sa.table("warehouse_marking_profiles", sa.column("id", sa.Uuid()), sa.column("inventory_number", sa.String()),
                        sa.column("geometry", sa.JSON()), sa.column("version", sa.Integer()))
    query = sa.select(profiles.c.id, profiles.c.geometry, profiles.c.version, profiles.c.inventory_number)
    if value:
        query = query.where(profiles.c.inventory_number == PEEL_INVENTORY)
    for row in bind.execute(query):
        geometry = row.geometry if isinstance(row.geometry, dict) else json.loads(row.geometry)
        if not value and "peel" not in geometry:
            continue
        geometry = {key: item for key, item in geometry.items() if key != "peel"}
        if value:
            geometry["peel"] = True
            # A changed profile gets a new version: an editor still holding the old one gets the usual 409, never saves
            # over it.
            version = row.version + 1
        else:
            # Downgrade mirrors the upgrade exactly (seeded roundtrip): the ОБ.101 bump is taken back; a profile whose
            # flag an operator set later keeps its version — the older code that runs next cannot hold `peel` anyway.
            version = max(row.version - 1, 1) if row.inventory_number == PEEL_INVENTORY and geometry_had_peel(row) else row.version
        bind.execute(sa.update(profiles).where(profiles.c.id == row.id).values(geometry=geometry, version=version))


def geometry_had_peel(row):
    geometry = row.geometry if isinstance(row.geometry, dict) else json.loads(row.geometry)
    return geometry.get("peel") is True


def upgrade() -> None:
    with op.batch_alter_table("warehouse_label_jobs") as batch:
        for name, expression in CHECKS.items():
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(name, expression)
        batch.create_check_constraint("ck_label_job_cancelled", CANCELLED)
    with op.batch_alter_table("warehouse_agent_printers") as batch:
        batch.add_column(sa.Column("hold", sa.String(500), nullable=False, server_default=""))
    op.create_table(
        "warehouse_printer_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("machine_id", sa.String(80), nullable=False),
        sa.Column("printer", sa.String(255), nullable=False),
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("requested_by_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("attempt", sa.String(36)),
        sa.Column("claim_token_id", sa.Uuid()),
        sa.Column("message", sa.Text()),
        sa.Column("result", sa.JSON()),
        sa.ForeignKeyConstraint(["machine_id", "printer"], ["warehouse_agent_printers.machine_id", "warehouse_agent_printers.name"],
                                name="fk_printer_command_printer", ondelete="RESTRICT"),
        *[sa.CheckConstraint(expression, name=name) for name, expression in COMMAND_CHECKS.items()],
    )
    op.create_index("ix_printer_commands_target", "warehouse_printer_commands", ["machine_id", "printer", "created_at"])
    op.create_index("uq_printer_command_open", "warehouse_printer_commands", ["machine_id", "printer"], unique=True,
                    sqlite_where=sa.text(OPEN), postgresql_where=sa.text(OPEN))
    _set_peel(True)


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT 1 FROM warehouse_label_jobs WHERE status = 'cancelled' LIMIT 1")).first():
        raise RuntimeError("Нельзя отменить миграцию: есть отменённые задания маркировки")
    _set_peel(False)
    op.drop_index("uq_printer_command_open", table_name="warehouse_printer_commands")
    op.drop_index("ix_printer_commands_target", table_name="warehouse_printer_commands")
    op.drop_table("warehouse_printer_commands")
    with op.batch_alter_table("warehouse_agent_printers") as batch:
        batch.drop_column("hold")
    with op.batch_alter_table("warehouse_label_jobs") as batch:
        batch.drop_constraint("ck_label_job_cancelled", type_="check")
        for name, expression in OLD_CHECKS.items():
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(name, expression)
