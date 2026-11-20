"""«Показывать в «Оборудовании»» (owner 19.11.2026: «просто галочку для вытягивания»): a checkbox of a БД.ОБ machine
that puts it on the screen «Производство → Оборудование».

`equipment_machines.show_in_equipment` — boolean, NOT NULL, default false. Backfill: ON for every unit an ERP agent
reaches — a machine bridge (`production_machine_links`) or the print agent of its marking printer
(`warehouse_marking_bindings`); every other unit stays OFF until the owner ticks it. (The setup class does not switch a
unit on: the setup-model seed is on prod since 19.11, but only an agent link decides the backfill.)
Not destructive: the downgrade drops only the new column."""

from alembic import op
import sqlalchemy as sa

revision = "0206_equipment_show_flag"
down_revision = "0205_vna_bench_sessions"
branch_labels = None
depends_on = None
DESTRUCTIVE = False


def upgrade() -> None:
    op.add_column("equipment_machines", sa.Column("show_in_equipment", sa.Boolean(), nullable=False,
                                                  server_default=sa.false()))
    op.execute(sa.text(
        "UPDATE equipment_machines SET show_in_equipment = TRUE WHERE id IN ("
        "SELECT equipment_id FROM production_machine_links UNION SELECT equipment_id FROM warehouse_marking_bindings)"))


def downgrade() -> None:
    with op.batch_alter_table("equipment_machines") as batch:
        batch.drop_column("show_in_equipment")
