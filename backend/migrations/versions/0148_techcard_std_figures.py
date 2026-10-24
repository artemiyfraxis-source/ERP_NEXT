"""Рисунки стандартов к строкам СТО ФРАК-001-2026: таблица `techcard_std_figures` (owner 23.10.2026).

Вырезки из ГОСТ Р 72182 и IPC/WHMA-A-620E, которые карточка «Типовые дефекты» ЭТК показывает вместо своего рисунка,
с подписью источника. Байты лежат в базе, не в git (репозиторий публичный). У строки СТО 1–6 рисунков по порядку.

Что держит база, а не код:
- код строки СТО не пустой; позиция ≥ 1 и у строки СТО одна: UNIQUE (sto_code, position);
- роль — 'norm' / 'defect' / 'ref'; источник — 'gost' / 'ipc' (IPC в комплект ТД заказчику не идёт);
- подпись источника не пустая; тип — только image/png или image/jpeg; sha256 — 64 знака;
- ширина и высота > 0; размер — от 1 байта до 2 МБ.

DESTRUCTIVE = False: создаётся пустая таблица, данные не меняются. Откат удаляет таблицу.
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0148_techcard_std_figures"
down_revision: str | None = "0147_procurement_payment_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DESTRUCTIVE = False

TABLE = "techcard_std_figures"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sto_code", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("caption", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.CheckConstraint("sto_code <> ''", name="ck_techcard_std_figures_sto_code"),
        sa.CheckConstraint("position >= 1", name="ck_techcard_std_figures_position"),
        sa.CheckConstraint("role IN ('norm', 'defect', 'ref')", name="ck_techcard_std_figures_role"),
        sa.CheckConstraint("source_kind IN ('gost', 'ipc')", name="ck_techcard_std_figures_source_kind"),
        sa.CheckConstraint("caption <> ''", name="ck_techcard_std_figures_caption"),
        sa.CheckConstraint(
            "content_type IN ('image/png', 'image/jpeg')", name="ck_techcard_std_figures_content_type",
        ),
        sa.CheckConstraint("length(sha256) = 64", name="ck_techcard_std_figures_sha256"),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_techcard_std_figures_dimensions"),
        sa.CheckConstraint("size > 0 AND size <= 2097152", name="ck_techcard_std_figures_size"),
        sa.UniqueConstraint("sto_code", "position", name="uq_techcard_std_figures_code_position"),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
