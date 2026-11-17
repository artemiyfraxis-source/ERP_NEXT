"""Instrument label templates/jobs and unique nonempty instrument serial numbers."""

from alembic import op
import sqlalchemy as sa

revision = "0188_instrument_labels"
down_revision = "0187_cutting_execution"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

SERIAL_INDEX = "uq_production_instruments_serial_number"


def _duplicate_serials() -> list[str]:
    bind = op.get_bind()
    duplicates = bind.execute(sa.text(
        "SELECT serial_number FROM production_instruments WHERE trim(serial_number) <> '' "
        "GROUP BY serial_number HAVING count(*) > 1 ORDER BY serial_number"
    )).scalars().all()
    result = []
    for serial in duplicates:
        items = bind.execute(sa.text(
            "SELECT inventory_number FROM production_instruments WHERE serial_number = :serial ORDER BY inventory_number"
        ), {"serial": serial}).scalars().all()
        result.append(f"{serial}: {', '.join(items)}")
    return result


def upgrade() -> None:
    duplicates = _duplicate_serials()
    if duplicates:
        raise RuntimeError("Повторяющиеся серийные номера СИ; данные сохранены, исправьте дубли перед миграцией: "
                           + "; ".join(duplicates))
    with op.batch_alter_table("warehouse_label_templates") as batch:
        batch.drop_constraint("ck_warehouse_label_templates_kind", type_="check")
        batch.create_check_constraint("ck_warehouse_label_templates_kind", "kind IN ('lot', 'intake', 'instrument')")
    with op.batch_alter_table("warehouse_label_jobs") as batch:
        batch.drop_constraint("ck_warehouse_label_jobs_kind", type_="check")
        batch.create_check_constraint("ck_warehouse_label_jobs_kind", "kind IN ('sample', 'lot', 'instrument')")
    op.create_index(SERIAL_INDEX, "production_instruments", ["serial_number"], unique=True,
                    sqlite_where=sa.text("trim(serial_number) <> ''"),
                    postgresql_where=sa.text("trim(serial_number) <> ''"))


def downgrade() -> None:
    bind = op.get_bind()
    if any(bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE kind = 'instrument' LIMIT 1")).first()
           for table in ("warehouse_label_templates", "warehouse_label_jobs")):
        raise RuntimeError("Нельзя отменить миграцию: есть шаблоны или задания этикеток СИ.")
    op.drop_index(SERIAL_INDEX, table_name="production_instruments")
    with op.batch_alter_table("warehouse_label_jobs") as batch:
        batch.drop_constraint("ck_warehouse_label_jobs_kind", type_="check")
        batch.create_check_constraint("ck_warehouse_label_jobs_kind", "kind IN ('sample', 'lot')")
    with op.batch_alter_table("warehouse_label_templates") as batch:
        batch.drop_constraint("ck_warehouse_label_templates_kind", type_="check")
        batch.create_check_constraint("ck_warehouse_label_templates_kind", "kind IN ('lot', 'intake')")
