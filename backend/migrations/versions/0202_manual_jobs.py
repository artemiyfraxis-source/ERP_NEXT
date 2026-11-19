"""«Ручная работа» (plan «Станки вне проекта», slice S1; 19.11.2026): a journal of work on any machine outside a project.

Two new tables, nothing existing changes:
- `production_manual_jobs` — one job: number (UNIQUE, «Р-000001» on screen), machine (FK RESTRICT), status
  running | done | cancelled with one running job per machine (UNIQUE partial index), required «Зачем», wire / contact
  with their keys, length > 0, settings JSON, the эталон of that same machine (composite FK), planned quantity > 0,
  count source plc | crimp_log | manual (plc needs its counter start), counts >= 0, defect needs its reason, material
  pending | issued | not_issued (not_issued needs its reason), done needs totals and finish time, running has none,
  cancelled needs its reason, the machine write that set it up (FK SET NULL), operator, version >= 1.
- `production_manual_job_issues` — «Списать с партии» of a job (filled by slice S3): job + lot UNIQUE, quantity > 0,
  its own write-off (UNIQUE FK).
Not destructive."""

from alembic import op
import sqlalchemy as sa

revision = "0202_manual_jobs"
down_revision = "0201_instrument_reference_curve"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

# Frozen copies of production/manual_jobs.py as of this revision.
RUNNING = sa.text("status = 'running'")


def upgrade() -> None:
    op.create_table(
        "production_manual_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("equipment_id", sa.Uuid(), sa.ForeignKey("equipment_units.id", ondelete="RESTRICT",
                                                           name="fk_production_manual_jobs_equipment"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("wire", sa.String(200), nullable=False, server_default=""),
        sa.Column("wire_key", sa.String(200), nullable=False, server_default=""),
        sa.Column("contact", sa.String(200), nullable=False, server_default=""),
        sa.Column("contact_key", sa.String(200), nullable=False, server_default=""),
        sa.Column("length_mm", sa.Numeric(9, 2), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("reference_id", sa.Uuid(), nullable=True),
        sa.Column("quantity_planned", sa.Integer(), nullable=True),
        sa.Column("count_source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("counter_start", sa.Integer(), nullable=True),
        sa.Column("machine_count", sa.Integer(), nullable=True),
        sa.Column("good_quantity", sa.Integer(), nullable=True),
        sa.Column("defect_quantity", sa.Integer(), nullable=True),
        sa.Column("defect_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("material", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("material_note", sa.String(300), nullable=False, server_default=""),
        sa.Column("cancel_reason", sa.String(300), nullable=False, server_default=""),
        sa.Column("write_id", sa.Uuid(), sa.ForeignKey("production_machine_writes.id", ondelete="SET NULL",
                                                       name="fk_production_manual_jobs_write"), nullable=True),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("operator_name", sa.String(200), nullable=False),
        sa.Column("finished_by_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("number", name="uq_production_manual_jobs_number"),
        sa.ForeignKeyConstraint(["reference_id", "equipment_id"],
                                ["equipment_setup_references.id", "equipment_setup_references.unit_id"],
                                name="fk_production_manual_jobs_reference", ondelete="RESTRICT"),
        sa.CheckConstraint("number >= 1", name="ck_production_manual_jobs_number"),
        sa.CheckConstraint("status IN ('running', 'done', 'cancelled')", name="ck_production_manual_jobs_status"),
        sa.CheckConstraint("trim(purpose) <> ''", name="ck_production_manual_jobs_purpose"),
        sa.CheckConstraint("(wire = '') = (wire_key = '')", name="ck_production_manual_jobs_wire_key"),
        sa.CheckConstraint("(contact = '') = (contact_key = '')", name="ck_production_manual_jobs_contact_key"),
        sa.CheckConstraint("length_mm IS NULL OR length_mm > 0", name="ck_production_manual_jobs_length"),
        sa.CheckConstraint("quantity_planned IS NULL OR quantity_planned > 0", name="ck_production_manual_jobs_planned"),
        sa.CheckConstraint("count_source IN ('plc', 'crimp_log', 'manual')", name="ck_production_manual_jobs_count_source"),
        sa.CheckConstraint("count_source <> 'plc' OR counter_start IS NOT NULL", name="ck_production_manual_jobs_counter"),
        sa.CheckConstraint("counter_start IS NULL OR counter_start >= 0", name="ck_production_manual_jobs_counter_start"),
        sa.CheckConstraint("(machine_count IS NULL OR machine_count >= 0) AND (good_quantity IS NULL OR good_quantity >= 0)"
                           " AND (defect_quantity IS NULL OR defect_quantity >= 0)", name="ck_production_manual_jobs_counts"),
        sa.CheckConstraint("COALESCE(defect_quantity, 0) = 0 OR trim(defect_reason) <> ''",
                           name="ck_production_manual_jobs_defect_reason"),
        sa.CheckConstraint("material IN ('pending', 'issued', 'not_issued')", name="ck_production_manual_jobs_material"),
        sa.CheckConstraint("material <> 'not_issued' OR trim(material_note) <> ''",
                           name="ck_production_manual_jobs_not_issued"),
        sa.CheckConstraint("status <> 'running' OR material = 'pending'", name="ck_production_manual_jobs_running_material"),
        sa.CheckConstraint("status <> 'running' OR (good_quantity IS NULL AND defect_quantity IS NULL"
                           " AND machine_count IS NULL)", name="ck_production_manual_jobs_running_totals"),
        sa.CheckConstraint("status <> 'done' OR (good_quantity IS NOT NULL AND defect_quantity IS NOT NULL"
                           " AND finished_at IS NOT NULL)", name="ck_production_manual_jobs_done"),
        sa.CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="ck_production_manual_jobs_finished"),
        sa.CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_production_manual_jobs_order"),
        sa.CheckConstraint("status <> 'cancelled' OR trim(cancel_reason) <> ''", name="ck_production_manual_jobs_cancel"),
        sa.CheckConstraint("trim(operator_name) <> ''", name="ck_production_manual_jobs_operator"),
        sa.CheckConstraint("version >= 1", name="ck_production_manual_jobs_version"),
    )
    op.create_index("ix_production_manual_jobs_equipment_id", "production_manual_jobs", ["equipment_id"])
    op.create_index("ix_production_manual_jobs_started_at", "production_manual_jobs", ["started_at"])
    op.create_index("ux_production_manual_jobs_running", "production_manual_jobs", ["equipment_id"], unique=True,
                    sqlite_where=RUNNING, postgresql_where=RUNNING)
    op.create_table(
        "production_manual_job_issues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("production_manual_jobs.id", ondelete="RESTRICT",
                                                     name="fk_production_manual_job_issues_job"), nullable=False),
        sa.Column("lot_id", sa.Uuid(), sa.ForeignKey("warehouse_lots.id", ondelete="RESTRICT",
                                                     name="fk_production_manual_job_issues_lot"), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("writeoff_id", sa.Uuid(), sa.ForeignKey("warehouse_writeoffs.id", ondelete="RESTRICT",
                                                          name="fk_production_manual_job_issues_writeoff"),
                  nullable=False),
        sa.Column("created_by_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "lot_id", name="uq_production_manual_job_issues_lot"),
        sa.UniqueConstraint("writeoff_id", name="uq_production_manual_job_issues_writeoff"),
        sa.CheckConstraint("quantity > 0", name="ck_production_manual_job_issues_quantity"),
    )
    op.create_index("ix_production_manual_job_issues_job_id", "production_manual_job_issues", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_production_manual_job_issues_job_id", table_name="production_manual_job_issues")
    op.drop_table("production_manual_job_issues")
    op.drop_index("ux_production_manual_jobs_running", table_name="production_manual_jobs")
    op.drop_index("ix_production_manual_jobs_started_at", table_name="production_manual_jobs")
    op.drop_index("ix_production_manual_jobs_equipment_id", table_name="production_manual_jobs")
    op.drop_table("production_manual_jobs")
