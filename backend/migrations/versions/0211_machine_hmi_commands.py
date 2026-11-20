"""ОБ.019 «Закрыть / Открыть пульт на ПК» (owner 20.11.2026): two more machine commands and the last state seen.

- `production_machine_writes`: the command CHECK also allows `hmi_close` and `hmi_open` (purpose `control`, the same
  queue, journal and one-controller index as Стоп / Сброс / Пуск / Сброс аварии of 0197).
- `production_machine_snapshots`: `last_state` / `last_state_at` — the machine state D766 of the last snapshot read
  «На связи» and when. A snapshot without words (the operator panel holds the port) keeps them, so «Закрыть пульт на ПК»
  is refused when the machine was in a cycle before the panel took the port. CHECK: a 16-bit word, both set or neither.
  Existing rows start with NULL (never read since this revision) and fill on the next report «На связи».
Not destructive; the downgrade drops the panel commands (their history stays in the audit journal) and the columns.

Revision after 0208_marking_outside_prints; when merged after parallel ones only `down_revision` changes.
"""

from alembic import op
import sqlalchemy as sa

revision = "0211_machine_hmi_commands"
down_revision = "0208_marking_outside_prints"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

WRITES = "production_machine_writes"
SNAPSHOTS = "production_machine_snapshots"
# Frozen copies of production/machine_writes.py COMMANDS as of 0197 and of this revision.
OLD_COMMANDS = "'stop', 'reset', 'start', 'alarm_clear'"
COMMANDS = "'stop', 'reset', 'start', 'alarm_clear', 'hmi_close', 'hmi_open'"


def upgrade():
    with op.batch_alter_table(WRITES) as batch:
        batch.drop_constraint("ck_production_machine_writes_command", type_="check")
        batch.create_check_constraint("ck_production_machine_writes_command", f"command IS NULL OR command IN ({COMMANDS})")
    with op.batch_alter_table(SNAPSHOTS) as batch:
        batch.add_column(sa.Column("last_state", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_state_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint("ck_production_machine_snapshots_last_state",
                                      "last_state IS NULL OR last_state BETWEEN 0 AND 65535")
        batch.create_check_constraint("ck_production_machine_snapshots_last_state_at",
                                      "(last_state IS NULL) = (last_state_at IS NULL)")


def downgrade():
    connection = op.get_bind()
    removed = connection.execute(sa.text(
        f"DELETE FROM {WRITES} WHERE command IN ('hmi_close', 'hmi_open')")).rowcount or 0
    with op.batch_alter_table(SNAPSHOTS) as batch:
        batch.drop_constraint("ck_production_machine_snapshots_last_state_at", type_="check")
        batch.drop_constraint("ck_production_machine_snapshots_last_state", type_="check")
        batch.drop_column("last_state_at")
        batch.drop_column("last_state")
    with op.batch_alter_table(WRITES) as batch:
        batch.drop_constraint("ck_production_machine_writes_command", type_="check")
        batch.create_check_constraint("ck_production_machine_writes_command",
                                      f"command IS NULL OR command IN ({OLD_COMMANDS})")
    print(f"0211_machine_hmi_commands (откат): удалено команд пульту: {removed}")
