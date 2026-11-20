"""«Кто налаживает» (owner 20.11.2026): a БД.ОБ machine is set up either by the operator himself («Сам оператор») or by
the наладчик («Наладчик»). Setup tasks for the наладчик come later and only where the machine says «Наладчик»; the start
of a task or of the cutting queue never waits for a setup task.

`equipment_machines.setup_by` — VARCHAR(16), NOT NULL, CHECK IN ('operator', 'technician'), server default 'operator'.
Backfill: every existing machine → 'operator' (the owner's default for ALL machines; the NOT NULL server default fills
them during the ALTER, so no separate UPDATE is needed).
One plain ALTER on both dialects (PostgreSQL: a constant default is metadata-only, no table rewrite); no table
recreation. Not destructive: the downgrade drops only the new column (its CHECK goes with it)."""

from alembic import op
import sqlalchemy as sa

revision = "0213_equipment_setup_by"
down_revision = "0211_machine_hmi_commands"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE equipment_machines ADD COLUMN setup_by VARCHAR(16) DEFAULT 'operator' NOT NULL "
        "CONSTRAINT ck_equipment_machines_setup_by CHECK (setup_by IN ('operator', 'technician'))"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE equipment_machines DROP COLUMN setup_by"))
