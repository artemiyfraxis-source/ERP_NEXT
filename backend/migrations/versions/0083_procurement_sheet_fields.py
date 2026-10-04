"""Лист закупки как в SUPPLYMAP (владелец отдал его нам 04.10): «Комментарий» и «Факт. дата» строки закупки.

`procurement_lines.note` — КОММЕНТАРИЙ (Config.gs:330), свободная заметка закупщика. `procurement_lines.actual_arrival` —
ФАКТ. ДАТА (OwnerTick.gs `syncActualArrival_`): самая поздняя дата входного контроля среди поставок строки; дальше её
держит тик `sync_receipt_stages`. Уже принятые строки получают её здесь же из `receiving_lines.inspected_on` (все поставки
строки — живые, партии `-ПN` и архив ВК), чтобы закрытые заказы не остались без даты. Версию строки не трогаем."""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0083_procurement_sheet_fields"
down_revision: str | None = "0082_project_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("procurement_lines") as batch:
        batch.add_column(sa.Column("note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("actual_arrival", sa.Date(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE procurement_lines SET actual_arrival = ("
            " SELECT MAX(receiving_lines.inspected_on) FROM receiving_lines"
            " WHERE receiving_lines.procurement_line_id = procurement_lines.id"
            ") WHERE EXISTS ("
            " SELECT 1 FROM receiving_lines"
            " WHERE receiving_lines.procurement_line_id = procurement_lines.id"
            " AND receiving_lines.inspected_on IS NOT NULL"
            ")"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("procurement_lines") as batch:
        batch.drop_column("actual_arrival")
        batch.drop_column("note")
