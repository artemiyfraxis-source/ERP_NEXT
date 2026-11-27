"""Заводской номер изделия FRX.<проект>.<изделие>.<k> на `production_serials` (владелец 27.11.2026, решение 4).

<изделие> — «КБ<n>» (по умолчанию) или своё обозначение технолога: «34.226» (НПК РТС), «120907/AA» (PN заказчика 708К.).
`item_serial` — k, «№ изделия» {№} напечатанной этикетки (счёт по проекту и изделию); `product_designation` — изделие;
`factory_number` — заводской номер. Выдаются при печати этикетки, хранятся и не переиспользуются (перепечатка берёт тот
же). Все три NULL или все заданы; изделие непустое, без пробелов и точек по краям; номер начинается с «FRX.», за ним
непустой проект и кончается ровно на «.<изделие>.<k>», k ≥ 1 без нулей. UNIQUE (factory_number) — один номер на одно
изделие навсегда; UNIQUE (task_id, item_serial) — строка этикетки (задание, {№}) находит своё изделие.

Существующие изделия остаются с NULL: выданные до этой ревизии номера могли не печататься, а изделие (КБ или своё
обозначение) знает только ЭТК печати — придумывать заводские номера задним числом нельзя. Номер появится при следующей
печати этикетки задания. Данные не меняются; не DESTRUCTIVE.

Откат отказывает (до изменения таблицы), пока есть выданные заводские номера: они на изделиях и в паспортах.
"""

from alembic import op
import sqlalchemy as sa

revision = "0222_production_serial_factory_number"
down_revision = "0221_vna_calibration_only_check"
branch_labels = None
depends_on = None
DESTRUCTIVE = False

TABLE = "production_serials"
CHECK = "ck_production_serial_factory_number"
UNIQUE_NUMBER = "ux_production_serial_factory_number"
UNIQUE_ITEM = "ux_production_serial_task_item_serial"
# Frozen copy of ProductionSerialRow's rule (FACTORY_NUMBER_CHECK): never import app code here.
_K = "CAST(item_serial AS VARCHAR(12))"
FORMAT = (
    "(item_serial IS NULL AND product_designation IS NULL AND factory_number IS NULL) OR "
    "(item_serial IS NOT NULL AND product_designation IS NOT NULL AND factory_number IS NOT NULL AND item_serial >= 1 "
    "AND product_designation <> '' AND product_designation NOT LIKE '% %' AND product_designation NOT LIKE '.%' "
    "AND product_designation NOT LIKE '%.' AND factory_number LIKE 'FRX._%' "
    f"AND length(factory_number) > length(product_designation) + length({_K}) + 6 "
    f"AND substr(factory_number, length(factory_number) - length(product_designation) - length({_K}) - 1) "
    f"= '.' || product_designation || '.' || {_K})"
)


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("item_serial", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("product_designation", sa.String(64), nullable=True))
        batch.add_column(sa.Column("factory_number", sa.String(128), nullable=True))
        batch.create_check_constraint(CHECK, FORMAT)
    op.create_index(UNIQUE_NUMBER, TABLE, ["factory_number"], unique=True)
    op.create_index(UNIQUE_ITEM, TABLE, ["task_id", "item_serial"], unique=True)


def downgrade() -> None:
    count = op.get_bind().execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE factory_number IS NOT NULL")).scalar_one()
    if count:
        raise RuntimeError(f"{TABLE}: выдано {count} заводских номеров (0222) — они на изделиях и в паспортах; "
                           "откат их потерял бы, номер не переиспользуется")
    op.drop_index(UNIQUE_ITEM, table_name=TABLE)
    op.drop_index(UNIQUE_NUMBER, table_name=TABLE)
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CHECK, type_="check")
        batch.drop_column("factory_number")
        batch.drop_column("product_designation")
        batch.drop_column("item_serial")
