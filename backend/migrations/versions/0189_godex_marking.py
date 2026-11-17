"""Targeted Godex marking profiles, immutable batches and conservative delivery states."""

from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa

revision = "0189_godex_marking"
down_revision = "0188_instrument_labels"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

CHECKS = {
    "ck_warehouse_label_jobs_kind": "kind IN ('sample', 'lot', 'instrument', 'marking')",
    "ck_warehouse_label_jobs_tspl": "(format = 'tspl' AND length(tspl) > 0) OR (format = 'gdi_png' AND length(png) > 0)",
    "ck_warehouse_label_jobs_status": "status IN ('pending', 'taken', 'printed', 'failed', 'spooled', 'unknown')",
    "ck_warehouse_label_jobs_finished_only_final": "finished_at IS NULL OR status IN ('printed', 'failed', 'spooled', 'unknown')",
    "ck_warehouse_label_jobs_error_only_failed": "error IS NULL OR status IN ('failed', 'unknown')",
}
OLD_CHECKS = {
    "ck_warehouse_label_jobs_kind": "kind IN ('sample', 'lot', 'instrument')",
    "ck_warehouse_label_jobs_tspl": "length(tspl) > 0",
    "ck_warehouse_label_jobs_status": "status IN ('pending', 'taken', 'printed', 'failed')",
    "ck_warehouse_label_jobs_finished_only_final": "finished_at IS NULL OR status = 'printed' OR status = 'failed'",
    "ck_warehouse_label_jobs_error_only_failed": "error IS NULL OR status = 'failed'",
}
PAYLOAD = ("(format = 'tspl' AND kind <> 'marking' AND batch_id IS NULL AND png IS NULL AND status NOT IN ('spooled', 'unknown')) OR "
           "(format = 'gdi_png' AND kind = 'marking' AND copies = 1 AND png IS NOT NULL AND length(png) BETWEEN 1 AND 2097152 "
           "AND target_machine_id IS NOT NULL AND target_printer IS NOT NULL AND trim(target_printer) <> '' "
           "AND batch_id IS NOT NULL AND sequence IS NOT NULL AND sequence >= 1 "
           "AND width_mm IS NOT NULL AND width_mm BETWEEN 5 AND 150 AND height_mm IS NOT NULL AND height_mm BETWEEN 5 AND 150 "
           "AND orientation IS NOT NULL AND orientation IN ('portrait', 'landscape') AND status <> 'printed')")
JOB_COLUMNS = [
    sa.Column("format", sa.String(16), nullable=False, server_default="tspl"),
    sa.Column("png", sa.LargeBinary()), sa.Column("target_machine_id", sa.String(80)),
    sa.Column("target_printer", sa.String(255)), sa.Column("width_mm", sa.Float()), sa.Column("height_mm", sa.Float()),
    sa.Column("orientation", sa.String(16)), sa.Column("batch_id", sa.Uuid()), sa.Column("sequence", sa.Integer()),
    sa.Column("attempt", sa.String(36)), sa.Column("claim_token_id", sa.Uuid()), sa.Column("receipt", sa.JSON()),
]


def _seeds():
    common = dict(rotation=0, feed_direction="height", offset_x_mm=0, offset_y_mm=0, min_font_mm=1.6,
                  max_font_mm=5, max_lines=3, alignment="center")
    result = []
    for width, height in ((10, 60), (20, 50), (30, 60), (40, 60)):
        result.append(("ОБ.102", f"{width}×{height}", {**common, "width_mm": width, "height_mm": height,
            "zones": [{"x": 1, "y": 1, "width": width - 2, "height": 13}],
            "blank_zones": [{"x": 0, "y": 15, "width": width, "height": height - 15}]}))
    result.append(("ОБ.101", "92,5×20", {**common, "width_mm": 92.5, "height_mm": 20,
        "zones": [{"x": 1, "y": 1, "width": 38.25, "height": 18}, {"x": 53.25, "y": 1, "width": 38.25, "height": 18}],
        "blank_zones": [{"x": 40.25, "y": 0, "width": 12, "height": 20}]}))
    return [dict(id=uuid5(NAMESPACE_URL, f"fraxis:godex:{inventory}:{name}"), inventory_number=inventory,
                 name=name, version=1, calibrated=False, geometry=geometry) for inventory, name, geometry in result]


def upgrade():
    profiles = op.create_table("warehouse_marking_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT")),
        sa.Column("inventory_number", sa.String(64), nullable=False), sa.Column("name", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("calibrated", sa.Boolean(), nullable=False),
        sa.Column("geometry", sa.JSON(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_marking_profile_version"),
        sa.UniqueConstraint("inventory_number", "name", name="uq_marking_profile_preset"))
    op.bulk_insert(profiles, _seeds())
    op.create_table("warehouse_marking_bindings",
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("printer", sa.String(255), nullable=False),
        sa.CheckConstraint("version >= 1 AND trim(printer) <> ''", name="ck_marking_binding_values"))
    op.create_table("warehouse_marking_batches",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.Uuid(), sa.ForeignKey("warehouse_marking_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False), sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("reprint_of", sa.Uuid(), sa.ForeignKey("warehouse_marking_batches.id", ondelete="RESTRICT")),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.CheckConstraint("total BETWEEN 1 AND 500", name="ck_marking_batch_total"),
        sa.UniqueConstraint("created_by", "idempotency_key", name="uq_marking_batch_request"))
    op.create_table("warehouse_agent_printers",
        sa.Column("machine_id", sa.String(80), sa.ForeignKey("warehouse_scan_agents.machine_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("name", sa.String(255), primary_key=True), sa.Column("driver", sa.String(255), nullable=False),
        sa.Column("port", sa.String(255), nullable=False), sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False), sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queue_version", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("kind IN ('tspl', 'gdi_png')", name="ck_agent_printer_kind"),
        sa.CheckConstraint("trim(name) <> ''", name="ck_agent_printer_name"))
    with op.batch_alter_table("warehouse_label_agent") as batch:
        batch.add_column(sa.Column("token_id", sa.Uuid()))
    with op.batch_alter_table("warehouse_marking_bindings") as batch:
        batch.create_foreign_key("fk_marking_binding_printer", "warehouse_agent_printers", ["machine_id", "printer"], ["machine_id", "name"], ondelete="RESTRICT")
    with op.batch_alter_table("warehouse_label_jobs") as batch:
        for column in JOB_COLUMNS:
            batch.add_column(column)
        for name, expression in CHECKS.items():
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(name, expression)
        batch.create_check_constraint("ck_label_job_payload", PAYLOAD)
        batch.create_check_constraint("ck_label_job_attempt", "format <> 'gdi_png' OR status = 'pending' OR (attempt IS NOT NULL AND claim_token_id IS NOT NULL)")
        batch.create_foreign_key("fk_label_job_machine", "warehouse_scan_agents", ["target_machine_id"], ["machine_id"], ondelete="RESTRICT")
        batch.create_foreign_key("fk_label_job_batch", "warehouse_marking_batches", ["batch_id"], ["id"], ondelete="RESTRICT")
        batch.create_foreign_key("fk_label_job_printer", "warehouse_agent_printers", ["target_machine_id", "target_printer"], ["machine_id", "name"], ondelete="RESTRICT")
        batch.create_unique_constraint("uq_label_job_sequence", ["batch_id", "sequence"])
        batch.create_index("ix_label_jobs_target", ["target_machine_id", "target_printer", "status"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM warehouse_marking_batches LIMIT 1")).first():
        raise RuntimeError("Нельзя отменить миграцию: есть история печати маркировки")
    with op.batch_alter_table("warehouse_label_jobs") as batch:
        batch.drop_index("ix_label_jobs_target")
        batch.drop_constraint("uq_label_job_sequence", type_="unique")
        for name in ("fk_label_job_machine", "fk_label_job_batch", "fk_label_job_printer"):
            batch.drop_constraint(name, type_="foreignkey")
        for name in ("ck_label_job_payload", "ck_label_job_attempt"):
            batch.drop_constraint(name, type_="check")
        for name, expression in OLD_CHECKS.items():
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(name, expression)
        for column in JOB_COLUMNS:
            batch.drop_column(column.name)
    with op.batch_alter_table("warehouse_label_agent") as batch:
        batch.drop_column("token_id")
    for name in ("warehouse_marking_batches", "warehouse_marking_bindings", "warehouse_agent_printers", "warehouse_marking_profiles"):
        op.drop_table(name)
