"""Готовая продукция: «уехало с завода» и «отгружено до ERP».

`sent` — набранное в отправках «Отгружена»/«Закрыта»: этап КБ «Отгружено» считается по нему, а не по `shipped`
(отобранное и упакованное ещё стоит на складе). Заполняется из строк уже уехавших отправок.
`before_erp` — отгружено ещё в Google по листу цеха «ЗАГРУЗ» (частичные отгрузки, перенос 30.09.2026); входит в
accepted/shipped/sent. Откат снимает обе колонки.

Revision ID: 0071_finished_goods_sent
Revises: 0070_purpose_rnd_merge
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0071_finished_goods_sent"
down_revision: str | None = "0070_purpose_rnd_merge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("finished_goods") as batch:
        batch.add_column(sa.Column("sent", sa.Numeric(18, 6), nullable=False, server_default="0"))
        batch.add_column(sa.Column("before_erp", sa.Numeric(18, 6), nullable=False, server_default="0"))
    op.execute(sa.text(
        "UPDATE finished_goods SET sent = COALESCE(("
        " SELECT SUM(l.picked) FROM shipment_lines l JOIN shipments s ON s.id = l.shipment_id"
        " WHERE l.finished_good_id = finished_goods.id AND s.status IN ('Отгружена', 'Закрыта')), 0)"
    ))


def downgrade() -> None:
    with op.batch_alter_table("finished_goods") as batch:
        batch.drop_column("before_erp")
        batch.drop_column("sent")
