"""Принтеры склада (owner 20.11.2026: «по принтерам тоже функционала нет никакого»): the МФУ Pantum prints documents and
scans plainly, apart from the УПД flow.

- `warehouse_agent_printers.kind` gains 'document': a Windows printer for A4 documents (the МФУ), reported by the agent
  of its PC next to the TSC ('tspl') and Godex ('gdi_png') queues.
- `warehouse_document_print_jobs` (new) — one PDF sent to such a printer: the file, its pages 1..100, copies 1..50
  (pages × copies ≤ 100), the exact PC/queue (FK RESTRICT to the printer row), who and when, and the status machine
  pending → taken → spooled | failed | unknown (lease ran out: the agent did not say), pending → cancelled. A taken job is
  never handed out again: a duplicate document is worse than a question to the operator. The person's retry key with the
  request's fingerprint (both or neither) is unique per person: a retry after a lost answer finds its job.
- `warehouse_scan_jobs.purpose` (new, NOT NULL): 'upd' — «Отсканировать УПД» (every scanner row before this revision),
  'plain' — a plain scan from the МФУ card, never recognised (result stays ''), with its options in capture_settings
  (colour, dpi, the scan it continues), 'vna' — the analyser captures (kind 'vna'). CHECKs tie purpose to kind and the
  options to the purpose.
Not destructive: no row is removed or changed except the purpose backfill of the VNA rows. The downgrade refuses while
plain scans or document print jobs exist (they would be lost) and forgets the reported document printers (the agent
reports them again)."""

from alembic import op
import sqlalchemy as sa

revision = "0214_warehouse_document_devices"
down_revision = "0213_equipment_setup_by"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

PRINTERS = "warehouse_agent_printers"
JOBS = "warehouse_document_print_jobs"
SCANS = "warehouse_scan_jobs"
KEY_WHERE = "idempotency_key IS NOT NULL"
PRINTER_KIND_OLD = "kind IN ('tspl', 'gdi_png')"
PRINTER_KIND_NEW = "kind IN ('tspl', 'gdi_png', 'document')"
PAYLOAD = "ck_warehouse_scan_jobs_kind_payload"
PAYLOAD_OLD = ("(kind = 'scan' AND capture_content IS NULL AND capture_settings IS NULL AND instrument_id IS NULL AND vna_name = '' "
               "AND capture_file_name = '' AND source = 's2vna') OR (kind = 'vna' AND file_id IS NULL AND result = '' "
               "AND target_machine_id IS NOT NULL AND target_machine_id <> '' AND target_scanner IS NULL)")
PAYLOAD_NEW = ("(kind = 'scan' AND capture_content IS NULL AND instrument_id IS NULL AND vna_name = '' "
               "AND capture_file_name = '' AND source = 's2vna' AND ((purpose = 'upd' AND capture_settings IS NULL) "
               "OR (purpose = 'plain' AND capture_settings IS NOT NULL AND result = '' AND target_machine_id IS NOT NULL "
               "AND target_scanner IS NOT NULL))) OR (kind = 'vna' AND file_id IS NULL AND result = '' "
               "AND target_machine_id IS NOT NULL AND target_machine_id <> '' AND target_scanner IS NULL)")
SCAN_CHECKS = {
    "ck_warehouse_scan_jobs_purpose": "purpose IN ('upd', 'plain', 'vna')",
    "ck_warehouse_scan_jobs_purpose_kind": "(purpose = 'vna' AND kind = 'vna') OR (purpose <> 'vna' AND kind = 'scan')",
}


def upgrade() -> None:
    with op.batch_alter_table(PRINTERS) as batch:
        batch.drop_constraint("ck_agent_printer_kind", type_="check")
        batch.create_check_constraint("ck_agent_printer_kind", PRINTER_KIND_NEW)
    with op.batch_alter_table(SCANS) as batch:
        batch.add_column(sa.Column("purpose", sa.String(8), nullable=False, server_default="upd"))
    op.execute(sa.text(f"UPDATE {SCANS} SET purpose = 'vna' WHERE kind = 'vna'"))
    with op.batch_alter_table(SCANS) as batch:
        batch.drop_constraint(PAYLOAD, type_="check")
        batch.create_check_constraint(PAYLOAD, PAYLOAD_NEW)
        for name, expression in SCAN_CHECKS.items():
            batch.create_check_constraint(name, expression)
    op.create_table(
        JOBS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("requester_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("pdf", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("copies", sa.Integer(), nullable=False),
        sa.Column("target_machine_id", sa.String(80), nullable=False),
        sa.Column("target_printer", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.String(36), nullable=True),
        sa.Column("claim_token_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["target_machine_id", "target_printer"], [f"{PRINTERS}.machine_id", f"{PRINTERS}.name"],
                                name="fk_document_print_job_printer", ondelete="RESTRICT"),
        # 'pending' first: the CI seeder takes the first value of an IN list (and leaves this queue empty anyway).
        sa.CheckConstraint("status IN ('pending', 'taken', 'spooled', 'failed', 'unknown', 'cancelled')",
                           name="ck_document_print_job_status"),
        sa.CheckConstraint("source IN ('upload', 'test', 'scan')", name="ck_document_print_job_source"),
        sa.CheckConstraint("trim(title) <> ''", name="ck_document_print_job_title"),
        sa.CheckConstraint("length(pdf) BETWEEN 1 AND 20971520", name="ck_document_print_job_pdf"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_document_print_job_sha256"),
        sa.CheckConstraint("pages BETWEEN 1 AND 100 AND copies BETWEEN 1 AND 50 AND pages * copies <= 100",
                           name="ck_document_print_job_amount"),
        sa.CheckConstraint("trim(target_printer) <> ''", name="ck_document_print_job_printer"),
        sa.CheckConstraint("(status = 'pending' AND taken_at IS NULL AND attempt IS NULL AND claim_token_id IS NULL) "
                           "OR (status = 'cancelled' AND taken_at IS NULL AND attempt IS NULL) "
                           "OR (status NOT IN ('pending', 'cancelled') AND taken_at IS NOT NULL AND attempt IS NOT NULL)",
                           name="ck_document_print_job_taken"),
        sa.CheckConstraint("(status = 'taken') = (lease_until IS NOT NULL)", name="ck_document_print_job_lease"),
        sa.CheckConstraint("(status IN ('pending', 'taken')) = (finished_at IS NULL)", name="ck_document_print_job_finished"),
        sa.CheckConstraint("status NOT IN ('failed', 'unknown', 'cancelled') OR trim(error) <> ''",
                           name="ck_document_print_job_error"),
        sa.CheckConstraint("(idempotency_key IS NULL) = (fingerprint IS NULL)", name="ck_document_print_job_request"),
        sa.CheckConstraint("idempotency_key IS NULL OR (trim(idempotency_key) <> '' AND requester_id IS NOT NULL "
                           "AND length(fingerprint) = 64)", name="ck_document_print_job_key"),
    )
    op.create_index("ix_document_print_jobs_target", JOBS, ["target_machine_id", "target_printer", "status", "created_at"])
    op.create_index("uq_document_print_job_request", JOBS, ["requester_id", "idempotency_key"], unique=True,
                    sqlite_where=sa.text(KEY_WHERE), postgresql_where=sa.text(KEY_WHERE))


def downgrade() -> None:
    bind = op.get_bind()
    # Never silently discard what people scanned or sent to print.
    if bind.execute(sa.text(f"SELECT 1 FROM {SCANS} WHERE purpose = 'plain' LIMIT 1")).first():
        raise RuntimeError("Plain scans exist; keep their files before downgrading 0214")
    if bind.execute(sa.text(f"SELECT 1 FROM {JOBS} LIMIT 1")).first():
        raise RuntimeError("Document print jobs exist; keep them before downgrading 0214")
    op.drop_index("uq_document_print_job_request", table_name=JOBS)
    op.drop_index("ix_document_print_jobs_target", table_name=JOBS)
    op.drop_table(JOBS)
    with op.batch_alter_table(SCANS) as batch:
        for name in SCAN_CHECKS:
            batch.drop_constraint(name, type_="check")
        batch.drop_constraint(PAYLOAD, type_="check")
        batch.create_check_constraint(PAYLOAD, PAYLOAD_OLD)
        batch.drop_column("purpose")
    # The agent reports its document printers again after an upgrade; older code knows no such queue.
    op.execute(sa.text(f"DELETE FROM {PRINTERS} WHERE kind = 'document'"))
    with op.batch_alter_table(PRINTERS) as batch:
        batch.drop_constraint("ck_agent_printer_kind", type_="check")
        batch.create_check_constraint("ck_agent_printer_kind", PRINTER_KIND_OLD)
