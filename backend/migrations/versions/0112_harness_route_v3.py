"""Маршрут v3 сборки — таблица `techcard_harness_routes` (владелец, 12.10.2026).

Маршрут изготовления жгута из TECHMAP-GRAPHER («Маршрут v3», с разрешения автора): граф карточек — заготовки резки,
сборки и одна карточка «Готовый жгут» — со слепками чертежа, количеством, операциями и частями на связях. У автора он
живёт только в браузере; в ERP — одна строка на позицию КБ (проект + индекс сборки) рядом с её чертежом жгута.

Правила в базе:
- один маршрут на позицию (UNIQUE(project, item_index)), проект и индекс не пустые;
- номер версии маршрута ≥ 1 (каждое сохранение +1; сохранение называет версию, на которой сделано, — чужое
  сохранение после неё не проходит);
- маршрут называет чертёж, на котором построен: строку КД (FK, ON DELETE RESTRICT — чертёж с маршрутом не удалить) и
  версию чертежа ≥ 0 (0 — чертёж старше своей истории);
- документ маршрута (JSON), время и автор последнего сохранения обязательны, автор не пустой.

Ничего существующего не меняется: новая пустая таблица, заполнения нет. DESTRUCTIVE = False.
Откат снимает таблицу (маршруты теряются, чертежи остаются).
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0112_harness_route_v3"
down_revision: str | None = "0111_finished_goods_register"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "techcard_harness_routes"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project", sa.String(255), nullable=False),
        sa.Column("item_index", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("drawing_document_id", sa.Uuid(), nullable=False),
        sa.Column("drawing_revision", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(
            ["drawing_document_id"], ["techcard_kitset_documents.id"], ondelete="RESTRICT",
            name="fk_techcard_harness_routes_drawing_document_id",
        ),
        sa.UniqueConstraint("project", "item_index", name="uq_techcard_harness_routes_position"),
        sa.CheckConstraint("revision >= 1", name="ck_techcard_harness_routes_revision"),
        sa.CheckConstraint("drawing_revision >= 0", name="ck_techcard_harness_routes_drawing_revision"),
        sa.CheckConstraint("length(trim(project)) > 0 AND length(trim(item_index)) > 0",
                           name="ck_techcard_harness_routes_position"),
        sa.CheckConstraint("length(trim(updated_by)) > 0", name="ck_techcard_harness_routes_updated_by"),
    )
    print(f"0112_harness_route_v3: создана таблица {TABLE} (пустая)")


def downgrade() -> None:
    op.drop_table(TABLE)
