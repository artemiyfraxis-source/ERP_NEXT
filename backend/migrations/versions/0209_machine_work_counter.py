"""Счётчик и простой станка без управления (owner 20.11.2026: «у станков без управления тоже надо сделать экран
взаимодействия» — current work with a hand-entered counter, and the machine's own downtime).

- production_manual_jobs: `progress_quantity` (how many are done so far, typed at the machine; NULL — not entered, else
  >= 0) and `progress_at` (when it was typed) — both or neither (CHECK), not before the start (CHECK). Entered only while
  the job runs (the application's compare-and-swap); after the finish it stays as history. More than planned is allowed.
- production_downtimes: `equipment_id` → equipment_units (FK RESTRICT): a downtime of the machine itself, not of a task —
  such a row has no task and no operation (CHECK). One open downtime per machine (partial UNIQUE on `ended_at IS NULL`,
  the same «open» predicate as the per-task index).

SQLite rebuilds both tables (batch): the partial UNIQUE indexes are dropped before and created again after.
Not destructive; the downgrade refuses once a counter or a machine downtime was recorded (it would erase them).
"""

from alembic import op
import sqlalchemy as sa

revision = "0209_machine_work_counter"
down_revision = "0208_marking_outside_prints"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

JOBS = "production_manual_jobs"
DOWNTIMES = "production_downtimes"
RUNNING_WHERE = "status = 'running'"
OPEN_WHERE = "ended_at IS NULL"
# The nullable column comes first in each check: the CI seeder leaves a nullable column empty under `col IS NULL OR`.
JOB_CHECKS = {
    "ck_production_manual_jobs_progress": "progress_quantity IS NULL OR progress_quantity >= 0",
    "ck_production_manual_jobs_progress_at": "(progress_quantity IS NULL) = (progress_at IS NULL)",
    "ck_production_manual_jobs_progress_order": "progress_at IS NULL OR progress_at >= started_at",
}
DOWNTIME_CHECK = ("ck_production_downtimes_equipment", "equipment_id IS NULL OR (task_id IS NULL AND operation_id IS NULL)")


def _running_index() -> None:
    op.create_index("ux_production_manual_jobs_running", JOBS, ["equipment_id"], unique=True,
                    sqlite_where=sa.text(RUNNING_WHERE), postgresql_where=sa.text(RUNNING_WHERE))


def _open_task_index() -> None:
    op.create_index("uq_production_downtimes_open_task", DOWNTIMES, ["task_id"], unique=True,
                    sqlite_where=sa.text(OPEN_WHERE), postgresql_where=sa.text(OPEN_WHERE))


def upgrade() -> None:
    op.drop_index("ux_production_manual_jobs_running", table_name=JOBS)
    with op.batch_alter_table(JOBS) as batch:
        batch.add_column(sa.Column("progress_quantity", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("progress_at", sa.DateTime(timezone=True), nullable=True))
        for name, expression in JOB_CHECKS.items():
            batch.create_check_constraint(name, expression)
    _running_index()

    op.drop_index("uq_production_downtimes_open_task", table_name=DOWNTIMES)
    with op.batch_alter_table(DOWNTIMES) as batch:
        batch.add_column(sa.Column("equipment_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_production_downtimes_equipment_id", "equipment_units", ["equipment_id"], ["id"],
                                 ondelete="RESTRICT")
        batch.create_check_constraint(*DOWNTIME_CHECK)
    _open_task_index()
    op.create_index("uq_production_downtimes_open_equipment", DOWNTIMES, ["equipment_id"], unique=True,
                    sqlite_where=sa.text(OPEN_WHERE), postgresql_where=sa.text(OPEN_WHERE))
    op.create_index("ix_production_downtimes_equipment_id", DOWNTIMES, ["equipment_id"])


def downgrade() -> None:
    bind = op.get_bind()
    counted = bind.execute(sa.text(f"SELECT count(*) FROM {JOBS} WHERE progress_quantity IS NOT NULL")).scalar()
    machine = bind.execute(sa.text(f"SELECT count(*) FROM {DOWNTIMES} WHERE equipment_id IS NOT NULL")).scalar()
    if counted or machine:
        raise RuntimeError("Счётчик ручной работы или простой станка уже записаны — откат стёр бы их; решите вручную")
    op.drop_index("ix_production_downtimes_equipment_id", table_name=DOWNTIMES)
    op.drop_index("uq_production_downtimes_open_equipment", table_name=DOWNTIMES)
    op.drop_index("uq_production_downtimes_open_task", table_name=DOWNTIMES)
    with op.batch_alter_table(DOWNTIMES) as batch:
        batch.drop_constraint(DOWNTIME_CHECK[0], type_="check")
        batch.drop_constraint("fk_production_downtimes_equipment_id", type_="foreignkey")
        batch.drop_column("equipment_id")
    _open_task_index()

    op.drop_index("ux_production_manual_jobs_running", table_name=JOBS)
    with op.batch_alter_table(JOBS) as batch:
        for name in JOB_CHECKS:
            batch.drop_constraint(name, type_="check")
        batch.drop_column("progress_at")
        batch.drop_column("progress_quantity")
    _running_index()
