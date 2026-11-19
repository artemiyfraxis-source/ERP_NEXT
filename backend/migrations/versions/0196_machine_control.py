"""ОБ.019 machine commands Стоп / Сброс / Пуск / Сброс аварии (plan «Станки», slice 3) on the write queue of 0195.

`production_machine_writes` gets the column `command` (stop / reset / start / alarm_clear, only for the new purpose
`control`) and the statuses `unknown` (no answer — never assumed done) and `cancelled` (a Stop cancelled it). The
one-open index now leaves out a Stop — it may queue while a write or a Start runs — and a second index allows one queued
Stop per machine. The access section «Управление станками» (`machine_control`) is not seeded: only the full levels hold
it until the owner grants it in «Доступы». Not destructive; the downgrade drops the machine commands (their history
stays in the audit journal).

Revision after 0195_machine_writes (PR #948); when merged after parallel ones only `down_revision` changes.
"""

from alembic import op
import sqlalchemy as sa

revision = "0196_machine_control"
down_revision = "0195_machine_writes"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_machine_writes"
# Frozen copies of production/machine_writes.py as of this revision.
STATUSES = "'queued', 'taken', 'done', 'failed', 'expired', 'unknown', 'cancelled'"
COMMANDS = "'stop', 'reset', 'start', 'alarm_clear'"
OPEN_WHERE = "status IN ('queued', 'taken') AND (command IS NULL OR command <> 'stop')"
STOP_WHERE = "status = 'queued' AND command = 'stop'"
OLD_OPEN = "status IN ('queued', 'taken')"


def upgrade():
    op.drop_index("ux_production_machine_writes_open", table_name=TABLE)
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("command", sa.String(16), nullable=True))
        batch.drop_constraint("ck_production_machine_writes_status", type_="check")
        batch.drop_constraint("ck_production_machine_writes_purpose", type_="check")
        batch.drop_constraint("ck_production_machine_writes_finished", type_="check")
        batch.create_check_constraint("ck_production_machine_writes_status", f"status IN ({STATUSES})")
        batch.create_check_constraint("ck_production_machine_writes_purpose",
                                      "purpose IN ('settings', 'transfer', 'control')")
        batch.create_check_constraint("ck_production_machine_writes_finished",
                                      "status IN ('queued', 'taken') OR finished_at IS NOT NULL")
        batch.create_check_constraint("ck_production_machine_writes_control",
                                      "(purpose = 'control') = (command IS NOT NULL)")
        batch.create_check_constraint("ck_production_machine_writes_command",
                                      f"command IS NULL OR command IN ({COMMANDS})")
    op.create_index("ux_production_machine_writes_open", TABLE, ["equipment_id"], unique=True,
                    sqlite_where=sa.text(OPEN_WHERE), postgresql_where=sa.text(OPEN_WHERE))
    op.create_index("ux_production_machine_writes_stop", TABLE, ["equipment_id"], unique=True,
                    sqlite_where=sa.text(STOP_WHERE), postgresql_where=sa.text(STOP_WHERE))


def downgrade():
    connection = op.get_bind()
    removed = connection.execute(sa.text(f"DELETE FROM {TABLE} WHERE purpose = 'control'")).rowcount or 0
    # A write cancelled by a Stop had no such status before: it reads as expired (it never ran).
    connection.execute(sa.text(f"UPDATE {TABLE} SET status = 'expired' WHERE status IN ('cancelled', 'unknown')"))
    op.drop_index("ux_production_machine_writes_stop", table_name=TABLE)
    op.drop_index("ux_production_machine_writes_open", table_name=TABLE)
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint("ck_production_machine_writes_command", type_="check")
        batch.drop_constraint("ck_production_machine_writes_control", type_="check")
        batch.drop_constraint("ck_production_machine_writes_finished", type_="check")
        batch.drop_constraint("ck_production_machine_writes_purpose", type_="check")
        batch.drop_constraint("ck_production_machine_writes_status", type_="check")
        batch.create_check_constraint("ck_production_machine_writes_status",
                                      "status IN ('queued', 'taken', 'done', 'failed', 'expired')")
        batch.create_check_constraint("ck_production_machine_writes_purpose", "purpose IN ('settings', 'transfer')")
        batch.create_check_constraint("ck_production_machine_writes_finished",
                                      "status NOT IN ('done', 'failed', 'expired') OR finished_at IS NOT NULL")
        batch.drop_column("command")
    op.create_index("ux_production_machine_writes_open", TABLE, ["equipment_id"], unique=True,
                    sqlite_where=sa.text(OLD_OPEN), postgresql_where=sa.text(OLD_OPEN))
    print(f"0196_machine_control (откат): удалено команд станку: {removed}")
