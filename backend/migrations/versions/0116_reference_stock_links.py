"""Журнал «склад → база данных» — таблица `reference_stock_links` (T14, 13.10.2026).

Когда позицию склада привязывают к записи «Базы данных», делают из неё новую запись или правят по ней запись, событие
пишется сюда в одной транзакции с самой записью: кто, когда, какая партия (склад, ГРН, пакет, артикул, наименование —
слепок на момент события), какая запись (база и текст) и что поменялось (поле, было, стало).

Правила в базе:
- журнал только дописывается (в коде нет правки и удаления строк);
- событие — одно из `linked` (привязана, запись не менялась), `created` (новая запись), `updated` (запись поправлена);
- база записи — одна из редактируемых (БД.ОБ, БД.ОП, БД.ТЕР, БД.КОАКС, БД.ПРОВ, БД.МАТ);
- партия обязательна и существует (FK на `warehouse_lots`, ON DELETE RESTRICT — партии склада не удаляются);
- запись — FK на `tech_operation_records` с ON DELETE SET NULL: полная перезагрузка зеркала удаляет строки, которые не
  правили в ERP, и не должна падать на журнале; база и текст записи в строке журнала остаются;
- ГРН, текст записи и автор не пустые; список изменений (JSON), время и автор обязательны.

Ничего существующего не меняется: новая пустая таблица, заполнения нет. DESTRUCTIVE = False.
Откат снимает таблицу (журнал теряется, записи и партии остаются).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0116_reference_stock_links"
down_revision: str | None = "0112_harness_route_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "reference_stock_links"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse", sa.String(8), nullable=False),
        sa.Column("grn", sa.String(32), nullable=False),
        sa.Column("package", sa.String(32), nullable=False),
        sa.Column("article", sa.String(1024), nullable=False),
        sa.Column("name", sa.String(1024), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=True),
        sa.Column("record_tab", sa.String(16), nullable=False),
        sa.Column("record_text", sa.String(2000), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_name", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["warehouse_lots.id"], ondelete="RESTRICT",
                                name="fk_reference_stock_links_lot_id"),
        sa.ForeignKeyConstraint(["record_id"], ["tech_operation_records.id"], ondelete="SET NULL",
                                name="fk_reference_stock_links_record_id"),
        sa.CheckConstraint("event IN ('linked', 'created', 'updated')", name="ck_reference_stock_links_event"),
        sa.CheckConstraint("record_tab IN ('coax', 'mat', 'ob', 'op', 'prov', 'ter')", name="ck_reference_stock_links_tab"),
        sa.CheckConstraint("length(trim(grn)) > 0", name="ck_reference_stock_links_grn"),
        sa.CheckConstraint("length(trim(record_text)) > 0", name="ck_reference_stock_links_record_text"),
        sa.CheckConstraint("length(trim(actor_name)) > 0", name="ck_reference_stock_links_actor"),
    )
    op.create_index("ix_reference_stock_links_record_id", TABLE, ["record_id"])
    op.create_index("ix_reference_stock_links_lot_id", TABLE, ["lot_id"])
    op.create_index("ix_reference_stock_links_occurred_at", TABLE, ["occurred_at"])
    print(f"0116_reference_stock_links: создана таблица {TABLE} (пустая)")


def downgrade() -> None:
    op.drop_index("ix_reference_stock_links_occurred_at", table_name=TABLE)
    op.drop_index("ix_reference_stock_links_lot_id", table_name=TABLE)
    op.drop_index("ix_reference_stock_links_record_id", table_name=TABLE)
    op.drop_table(TABLE)
