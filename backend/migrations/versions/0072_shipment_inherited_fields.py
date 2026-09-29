"""Отправка: какие поля адресата идут за карточкой проекта.

`inherited_fields` — через запятую поля (получатель, адрес, контакт, телефон, условия, ТК, тип УПД), не заданные в
самой отправке: пока отправка открыта, правка карточки проекта переписывает их (`shipHeadEff_`). У отправок, заведённых
до миграции, список пуст: их значения остаются своими. Откат снимает колонку.

Revision ID: 0072_shipment_inherited_fields
Revises: 0071_finished_goods_sent
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0072_shipment_inherited_fields"
down_revision: str | None = "0071_finished_goods_sent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("shipments") as batch:
        batch.add_column(sa.Column("inherited_fields", sa.String(255), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("shipments") as batch:
        batch.drop_column("inherited_fields")
